import os
import time
import threading
import asyncio
import uuid
import json
import base64
import subprocess
import re
import sqlite3
import random
import boto3
import requests
from botocore.client import Config
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_file, render_template, redirect, url_for, session, render_template_string
from werkzeug.utils import secure_filename
from authlib.integrations.flask_client import OAuth
from login_notification_email import send_login_notification
from paypal_subscriptions import paypal_bp, init_paypal_db

from werkzeug.middleware.proxy_fix import ProxyFix
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.register_blueprint(paypal_bp)
init_paypal_db()

# In-memory job store for async clip generation. Fine with a single
# gunicorn worker (see Procfile --workers 1); each job is only read by the
# user who created it.

_cookies_b64 = os.environ.get('YOUTUBE_COOKIES_B64')
if _cookies_b64:
    try:
        import base64
        _cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')
        with open(_cookies_path, 'wb') as _f:
            _f.write(base64.b64decode(_cookies_b64))
        print('[startup] cookies.txt written from YOUTUBE_COOKIES_B64', flush=True)
    except Exception as _e:
        print(f'[startup] FAILED to write cookies.txt: {_e}', flush=True)

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
# DATA_DIR should point at a mounted Railway Volume (persistent disk) so the
# database survives redeploys. Falls back to the app's own directory (the
# old, non-persistent behavior) if DATA_DIR isn't set yet.
DATA_DIR = os.environ.get('DATA_DIR', '.')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'users.db')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm'}

# ── Cloudflare R2 ─────────────────────────────────────────────────────────────

R2_ACCOUNT_ID = os.environ.get('R2_ACCOUNT_ID')
R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
R2_PUBLIC_URL = (os.environ.get('R2_PUBLIC_URL') or '').rstrip('/')

r2_client = None
if R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY:
    r2_client = boto3.client(
        's3',
        endpoint_url=f'https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com',
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version='s3v4'),
        region_name='auto',
    )

def upload_to_r2(local_path, r2_key, content_type):
    """Upload a local file to R2. Returns True on success."""
    if not r2_client:
        print("R2 not configured — skipping upload, file stays local only.")
        return False
    try:
        r2_client.upload_file(
            local_path, R2_BUCKET_NAME, r2_key,
            ExtraArgs={'ContentType': content_type}
        )
        try:
            log_usage('r2_bytes', os.path.getsize(local_path))
        except Exception:
            pass
        return True
    except Exception as e:
        print("R2 upload failed:", e)
        return False

def delete_from_r2(r2_key):
    if not r2_client or not r2_key:
        return
    try:
        r2_client.delete_object(Bucket=R2_BUCKET_NAME, Key=r2_key)
    except Exception as e:
        print("R2 delete failed:", e)

def r2_public_url(r2_key):
    if not r2_key:
        return None
    return f"{R2_PUBLIC_URL}/{r2_key}"

def r2_presigned_download_url(r2_key, download_name):
    """Generate a short-lived URL that forces a download (attachment) instead of inline playback."""
    if not r2_client or not r2_key:
        return None
    try:
        return r2_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': R2_BUCKET_NAME,
                'Key': r2_key,
                'ResponseContentDisposition': f'attachment; filename="{download_name}"',
            },
            ExpiresIn=3600,
        )
    except Exception as e:
        print("R2 presign failed:", e)
        return None

# ── Claude virality scoring ────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
FREE_DAILY_LIMIT = 3  # free-plan generations per rolling 24h
YOUTUBE_CLIENT_ID = os.environ.get('YOUTUBE_CLIENT_ID')
YOUTUBE_CLIENT_SECRET = os.environ.get('YOUTUBE_CLIENT_SECRET')
YOUTUBE_REDIRECT_URI = os.environ.get('YOUTUBE_REDIRECT_URI', 'https://viralcut.xyz/youtube/callback')
PEXELS_API_KEY = os.environ.get('PEXELS_API_KEY')  # https://www.pexels.com/api/ (free)
CLAUDE_MODEL = 'claude-haiku-4-5-20251001'

FALLBACK_CAPTIONS = [
    "This part will blow your mind 🤯",
    "You won't believe what happens next 👀",
    "The most viral moment 🔥",
    "Watch this until the end ✨",
    "Everyone is talking about this 💬",
    "This changed everything 🚀",
]

def extract_preview_frame(video_path, timestamp, out_path):
    """Grab a single frame so Claude has something to look at before the clip is cut."""
    cmd = [
        'ffmpeg', '-y', '-ss', str(max(timestamp, 0)), '-i', video_path,
        '-vframes', '1',
        '-vf', 'scale=640:-1',
        '-q:v', '4', out_path
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception as e:
        print("Preview frame error:", e)
        return False

def score_and_caption_clip(frame_path, duration, fallback_index):
    """
    Sends the preview frame to Claude and asks for a hook caption, a virality
    score, and a one-line reason. Falls back to the old random caption/score
    if the API key isn't set or the call fails, so clip generation never breaks.
    """
    fallback = {
        'caption': FALLBACK_CAPTIONS[fallback_index % len(FALLBACK_CAPTIONS)],
        'virality_score': random.randint(62, 90),
        'reasoning': None,
    }

    if not ANTHROPIC_API_KEY or not os.path.exists(frame_path):
        return fallback

    try:
        with open(frame_path, 'rb') as f:
            image_b64 = base64.b64encode(f.read()).decode('utf-8')

        prompt = (
            "You're a short-form video strategist reviewing a single frame from a "
            f"{duration:.0f}-second vertical clip intended for TikTok/Reels/Shorts. "
            "Based only on this frame, respond with ONLY a JSON object (no markdown, "
            "no preamble) in this exact shape:\n"
            '{"caption": "a punchy 5-10 word hook/caption for this clip", '
            '"virality_score": <integer 1-100>, '
            '"reasoning": "one short sentence explaining the score"}'
        )

        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': CLAUDE_MODEL,
                'max_tokens': 300,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'image', 'source': {
                            'type': 'base64', 'media_type': 'image/jpeg', 'data': image_b64
                        }},
                        {'type': 'text', 'text': prompt},
                    ],
                }],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        _log_anthropic_usage(data)
        text = ''.join(
            block.get('text', '') for block in data.get('content', [])
            if block.get('type') == 'text'
        ).strip()
        text = re.sub(r'^```(json)?|```$', '', text.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(text)

        caption = str(parsed.get('caption', '')).strip()
        score = int(parsed.get('virality_score', fallback['virality_score']))
        score = min(max(score, 1), 100)
        reasoning = str(parsed.get('reasoning', '')).strip() or None

        if not caption:
            caption = fallback['caption']

        return {'caption': caption, 'virality_score': score, 'reasoning': reasoning}

    except Exception as e:
        print("Claude virality scoring failed, using fallback:", e)
        return fallback

# ── Google OAuth ─────────────────────────────────────────────────────────────

_whisper_model = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        print('[whisper] loading model...', flush=True)
        _whisper_model = WhisperModel('base', device='cpu', compute_type='int8')
        print('[whisper] model loaded', flush=True)
    return _whisper_model

def transcribe_clip_window(video_path, start, duration):
    """Transcribes just ONE clip's audio window (via a tight ffmpeg -ss/-t
    seek), instead of the whole source video. This keeps peak memory/CPU
    bounded by clip_duration no matter how long the original video is --
    a 2-hour source video costs the same as a 2-minute one here. Word
    timestamps come back already relative to the clip's own start (no
    separate full-video-to-clip slicing step needed).

    Returns [] on any failure so clip generation never breaks because of
    transcription.
    """
    try:
        import numpy as np
        cmd = [
            'ffmpeg', '-y', '-ss', str(start), '-i', video_path, '-t', str(duration),
            '-f', 's16le', '-acodec', 'pcm_s16le',
            '-ac', '1', '-ar', '16000',
            '-'
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if not result.stdout:
            print('[whisper] ffmpeg produced no audio output for clip window:', result.stderr[-500:] if result.stderr else '')
            return []
        audio = np.frombuffer(result.stdout, np.int16).astype(np.float32) / 32768.0

        model = get_whisper_model()
        segments, _info = model.transcribe(audio, word_timestamps=True, vad_filter=True)
        words = []
        for seg in segments:
            if not seg.words:
                continue
            for w in seg.words:
                words.append({'text': w.word.strip(), 'start': w.start, 'end': w.end})
        return words
    except Exception as e:
        print('[whisper] clip transcription failed:', e, flush=True)
        return []

def generate_metadata_with_claude(transcript_text, duration, fallback_index):
    """Ask Claude for a title, hook caption, hashtags, and virality score
    based on what's actually said in the clip. Falls back to generic values
    if the API key is missing, there's no transcript, or the call fails."""
    fallback = {
        'title': 'Untitled clip',
        'caption': FALLBACK_CAPTIONS[fallback_index % len(FALLBACK_CAPTIONS)],
        'hashtags': ['#shorts', '#viral', '#fyp'],
        'virality_score': random.randint(62, 90),
        'reasoning': None,
    }

    if not ANTHROPIC_API_KEY or not transcript_text.strip():
        return fallback

    try:
        prompt = (
            "You're a short-form video strategist. Here is the spoken transcript of a "
            f"{duration:.0f}-second clip intended for TikTok/Reels/Shorts:\n\n"
            f'"{transcript_text}"\n\n'
            "Respond with ONLY a JSON object (no markdown, no preamble) in this exact shape:\n"
            '{"title": "a short 3-6 word title for this clip", '
            '"caption": "a punchy 5-10 word hook/caption", '
            '"hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"], '
            '"virality_score": <integer 1-100>, '
            '"reasoning": "one short sentence explaining the score"}'
        )

        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': CLAUDE_MODEL,
                'max_tokens': 400,
                'messages': [{'role': 'user', 'content': prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        _log_anthropic_usage(data)
        text = ''.join(
            block.get('text', '') for block in data.get('content', [])
            if block.get('type') == 'text'
        ).strip()
        text = re.sub(r'^```(json)?|```$', '', text.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(text)

        title = str(parsed.get('title', '')).strip() or fallback['title']
        caption = str(parsed.get('caption', '')).strip() or fallback['caption']
        hashtags = parsed.get('hashtags') or fallback['hashtags']
        if not isinstance(hashtags, list):
            hashtags = fallback['hashtags']
        hashtags = [str(h).strip() for h in hashtags if str(h).strip()][:8]
        score = int(parsed.get('virality_score', fallback['virality_score']))
        score = min(max(score, 1), 100)
        reasoning = str(parsed.get('reasoning', '')).strip() or None

        return {
            'title': title, 'caption': caption, 'hashtags': hashtags,
            'virality_score': score, 'reasoning': reasoning,
        }
    except Exception as e:
        print('Claude metadata generation failed, using fallback:', e)
        return fallback

def generate_ass_captions_from_words(words, duration, ass_path):
    """Build karaoke-style burned captions from real transcribed words with
    their real timestamps, instead of evenly splitting a caption string."""
    def ts(t):
        t = max(t, 0)
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h:01d}:{m:02d}:{s:05.2f}"

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,Arial Black,90,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,6,0,2,60,60,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for w in words:
        start = ts(w['start'])
        end = ts(max(w['end'], w['start'] + 0.05))
        text = (
            r"{\fscx80\fscy80\t(0,80,\fscx105\fscy105)\t(80,150,\fscx100\fscy100)}"
            + w['text']
        )
        lines.append(f"Dialogue: 0,{start},{end},Word,,0,0,0,,{text}\n")

    with open(ass_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def current_user_id():
    return session.get('user', {}).get('id')

# ── Database ─────────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            google_id   TEXT UNIQUE NOT NULL,
            email       TEXT,
            name        TEXT,
            picture     TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    ''')
    # NOTE: filename / thumbnail columns now store R2 object keys
    # (e.g. "clips/clip_abc_1.mp4"), not local paths.
    db.execute('''
        CREATE TABLE IF NOT EXISTS clips (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER,
            filename       TEXT NOT NULL,
            caption        TEXT,
            duration       REAL,
            start_time     REAL,
            virality_score INTEGER,
            virality_reason TEXT,
            thumbnail      TEXT,
            created_at     TEXT DEFAULT (datetime('now'))
        )
    ''')
    # Migration for existing DBs created before virality_reason existed.
    try:
        db.execute('ALTER TABLE clips ADD COLUMN virality_reason TEXT')
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        db.execute('ALTER TABLE clips ADD COLUMN title TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        db.execute('ALTER TABLE clips ADD COLUMN hashtags TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        db.execute('ALTER TABLE clips ADD COLUMN transcript TEXT')
    except sqlite3.OperationalError:
        pass
    db.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            id         TEXT PRIMARY KEY,
            user_id    INTEGER,
            status     TEXT NOT NULL,
            clips_json TEXT,
            error      TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS preferences (
            user_id INTEGER,
            key     TEXT,
            value   TEXT,
            PRIMARY KEY (user_id, key)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS youtube_tokens (
            user_id       INTEGER PRIMARY KEY,
            refresh_token TEXT NOT NULL,
            access_token  TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        )
    ''')
    db.commit()
    db.close()

init_db()

# ── Helpers ──────────────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_video_duration(path):
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', path],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    return float(data['format']['duration'])

def _validate_video_url(url):
    """Returns None if the URL is safe to hand to yt-dlp, else a reason string."""
    from urllib.parse import urlparse
    import socket, ipaddress
    if not isinstance(url, str) or url.startswith('-') or any(c.isspace() for c in url) or len(url) > 2000:
        return 'invalid url'
    try:
        u = urlparse(url)
    except Exception:
        return 'invalid url'
    if u.scheme not in ('http', 'https') or not u.hostname:
        return 'invalid url'
    try:
        for info in socket.getaddrinfo(u.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                return 'blocked address'
    except Exception:
        return 'could not resolve host'
    return None

def download_from_url(url, job_id):
    _bad = _validate_video_url(url)
    if _bad:
        return None, _bad
    out_path = os.path.join(UPLOAD_FOLDER, f'{job_id}.mp4')
    cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')

    cmd_primary = [
        'yt-dlp', '--no-playlist',
        '--extractor-args', 'youtube:player_client=android',
        '-f', 'best[height<=1080][ext=mp4]/best[ext=mp4]/best',
        '--merge-output-format', 'mp4',
        '-o', out_path, '--no-warnings',
        '--', url
    ]
    result = subprocess.run(cmd_primary, capture_output=True, text=True, timeout=300)

    if not os.path.exists(out_path):
        for f in os.listdir(UPLOAD_FOLDER):
            if f.startswith(job_id) and f.endswith('.mp4'):
                return os.path.join(UPLOAD_FOLDER, f), None

    if not os.path.exists(out_path):
        cmd_fallback = [
            'yt-dlp', '--no-playlist',
            '-f', 'best[height<=1080][ext=mp4]/best[ext=mp4]/best',
            '--merge-output-format', 'mp4',
            '-o', out_path, '--no-warnings',
            '--cookies', cookies_path,
            '--', url
        ]
        result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=300)

    if os.path.exists(out_path):
        return out_path, None
    for f in os.listdir(UPLOAD_FOLDER):
        if f.startswith(job_id) and f.endswith('.mp4'):
            return os.path.join(UPLOAD_FOLDER, f), None
    error = result.stderr.strip().split('\n')[-1] if result.stderr else 'Download failed'
    return None, error

def detect_scenes(video_path, threshold=0.35):
    cmd = [
        'ffmpeg', '-i', video_path,
        '-vf', f'select=gt(scene\\,{threshold}),showinfo',
        '-f', 'null', '-'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    timestamps = []
    for line in result.stderr.split('\n'):
        if 'pts_time' in line:
            match = re.search(r'pts_time:([\d.]+)', line)
            if match:
                timestamps.append(float(match.group(1)))
    return timestamps

def generate_ass_captions(caption, duration, ass_path):
    words = caption.split()
    if not words:
        words = [""]
    per_word = duration / len(words)

    def ts(t):
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h:01d}:{m:02d}:{s:05.2f}"

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,Arial Black,90,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,6,0,2,60,60,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    t = 0.0
    for w in words:
        start = ts(t)
        end = ts(t + per_word)
        text = (
            r"{\fscx80\fscy80\t(0,80,\fscx105\fscy105)\t(80,150,\fscx100\fscy100)}"
            + w
        )
        lines.append(f"Dialogue: 0,{start},{end},Word,,0,0,0,,{text}\n")
        t += per_word

    with open(ass_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)

def detect_speaker_crop_keyframes(video_path, clip_start, clip_duration, max_samples=15):
    """Samples frames across a clip's time window from the SOURCE video,
    finds the largest detected face in each sample, and returns a list of
    (t_relative_to_clip, frac_x) keyframes describing where to horizontally
    center the 9:16 crop over time, so the reframe follows the speaker
    instead of doing a dumb center-crop.

    Returns None if OpenCV/the video can't be read or no face is ever
    found, so the caller falls back to a plain center crop.
    """
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

        interval = max(clip_duration / max_samples, 1.0)
        t = 0.0
        keyframes = []
        while t <= clip_duration:
            cap.set(cv2.CAP_PROP_POS_MSEC, (clip_start + t) * 1000)
            ok, frame = cap.read()
            if ok and frame is not None:
                h, w = frame.shape[:2]
                scale = 320.0 / w if w > 320 else 1.0
                small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1.0 else frame
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(30, 30))
                if len(faces):
                    fx, fy, fw, fh = max(faces, key=lambda fc: fc[2] * fc[3])
                    center_x = (fx + fw / 2) / small.shape[1]
                    keyframes.append((round(t, 2), round(center_x, 4)))
            t += interval
        cap.release()
        return keyframes if len(keyframes) >= 1 else None
    except Exception as e:
        print('[reframe] speaker detection failed, falling back to center crop:', e, flush=True)
        return None

def build_crop_x_expr(keyframes):
    """Builds an ffmpeg crop-filter x expression (eval=frame) that pans the
    crop window horizontally between detected speaker positions over time,
    clamped to stay inside the scaled frame. With no keyframes this reduces
    to the same plain center crop as before."""
    if not keyframes:
        raw = "in_w/2-out_w/2"
    elif len(keyframes) == 1:
        raw = f"in_w*{keyframes[0][1]}-out_w/2"
    else:
        raw = f"in_w*{keyframes[-1][1]}-out_w/2"
        for i in range(len(keyframes) - 2, -1, -1):
            t0, x0 = keyframes[i]
            t1, x1 = keyframes[i + 1]
            if t1 <= t0:
                continue
            slope = (x1 - x0) / (t1 - t0)
            seg = f"(in_w*({x0}+({slope})*(t-{t0}))-out_w/2)"
            raw = f"if(lt(t,{t1}),{seg},{raw})"
        raw = f"if(lt(t,{keyframes[0][0]}),in_w*{keyframes[0][1]}-out_w/2,{raw})"
    return f"clip({raw},0,in_w-out_w)"

def get_broll_segments(transcript_text, clip_duration):
    """Asks Claude to pick at most 2 short moments in the clip where generic
    stock B-roll footage would enhance the content, each with a short
    visual search query. Returns a list of {'query','start','end'} dicts,
    non-overlapping, sorted by start, clamped inside [0, clip_duration],
    capped to ~35% of the clip's total time.

    Returns [] if B-roll isn't configured (missing API keys), there's no
    transcript, the clip is too short, or anything about the request fails
    -- extract_clip() falls back to a plain clip with no B-roll in that case.
    """
    if not ANTHROPIC_API_KEY or not PEXELS_API_KEY or not transcript_text.strip() or clip_duration < 8:
        return []
    try:
        prompt = (
            "You're editing a short vertical video. Here is its spoken transcript "
            f"({clip_duration:.0f} seconds total):\n\n"
            f'"{transcript_text}"\n\n'
            "Pick at most 2 short moments (2-3.5 seconds each) where generic stock "
            "B-roll footage (not showing the speaker) would visually enhance what's "
            "being said -- e.g. if they mention money, a city, nature, technology, etc. "
            "Only pick moments that have an obvious, concrete visual. If nothing fits, "
            "return an empty array. Respond with ONLY a JSON array (no markdown, no "
            "preamble) shaped like: "
            '[{"query": "2-4 word stock footage search term", "start": <seconds into '
            'the clip>, "end": <seconds into the clip>}]'
        )
        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={'model': CLAUDE_MODEL, 'max_tokens': 300,
                  'messages': [{'role': 'user', 'content': prompt}]},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        _log_anthropic_usage(data)
        text = ''.join(
            b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text'
        ).strip()
        text = re.sub(r'^```(json)?|```$', '', text.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return []

        segments = []
        for item in parsed:
            try:
                q = str(item.get('query', '')).strip()
                s = float(item.get('start'))
                e = float(item.get('end'))
            except (TypeError, ValueError, AttributeError):
                continue
            s = max(0.0, min(s, clip_duration))
            e = max(0.0, min(e, clip_duration))
            if not q or e - s < 1.0 or e - s > 4.0:
                continue
            segments.append({'query': q, 'start': round(s, 2), 'end': round(e, 2)})

        segments.sort(key=lambda seg: seg['start'])
        kept = []
        last_end = 0.0
        total = 0.0
        for seg in segments:
            if seg['start'] < last_end + 0.5:
                continue
            if total + (seg['end'] - seg['start']) > clip_duration * 0.35:
                continue
            kept.append(seg)
            last_end = seg['end']
            total += seg['end'] - seg['start']
            if len(kept) >= 2:
                break
        return kept
    except Exception as e:
        print('[broll] segment selection failed:', e, flush=True)
        return []

def fetch_broll_clip(query, out_path, min_duration):
    """Searches Pexels for a short portrait-friendly stock video matching
    `query` and downloads it to out_path. Returns True only if the
    download succeeded AND the clip is long enough to cover min_duration,
    so the caller can safely trim it in the filter graph."""
    try:
        resp = requests.get(
            'https://api.pexels.com/videos/search',
            headers={'Authorization': PEXELS_API_KEY},
            params={'query': query, 'orientation': 'portrait', 'per_page': 5},
            timeout=20,
        )
        resp.raise_for_status()
        videos = resp.json().get('videos', [])
        if not videos:
            return False

        best_url = None
        for v in videos:
            if (v.get('duration') or 0) < min_duration + 0.5:
                continue
            files = sorted(v.get('video_files', []) or [], key=lambda f: f.get('width', 0) or 0)
            portrait = [f for f in files if (f.get('height') or 0) > (f.get('width') or 0)]
            pick = portrait or files
            if pick:
                best_url = pick[len(pick) // 2].get('link')
                if best_url:
                    break
        if not best_url:
            return False

        with requests.get(best_url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(out_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=262144):
                    f.write(chunk)

        if not (os.path.exists(out_path) and os.path.getsize(out_path) > 1000):
            return False

        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', out_path],
            capture_output=True, text=True, timeout=15,
        )
        dl_duration = float(probe.stdout.strip() or 0)
        if dl_duration < min_duration:
            return False
        return True
    except Exception as e:
        print('[broll] fetch failed for query', repr(query), ':', e, flush=True)
        return False

def build_broll_filter_complex(segments, clip_duration, target_w, target_h, main_crop_filter, ass_path=None):
    """Builds an ffmpeg filter_complex string that: applies main_crop_filter
    to the main clip (input 0), scales/center-crops each B-roll clip
    (inputs 1..N) to the same target size, splices the B-roll clips into
    the main clip's timeline at `segments`' windows via concat, and (if
    ass_path is given) burns captions on top of the whole result. Returns
    (filter_complex_str, output_video_label). Audio is left untouched --
    the caller maps input 0's audio directly, so it stays continuous
    underneath the B-roll cutaways."""
    broll_crop = f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h}"
    lines = [f"[0:v]{main_crop_filter}[mainbase]"]

    n_main_segments = len(segments) + 1
    split_outs = ''.join(f"[m{i}]" for i in range(n_main_segments))
    lines.append(f"[mainbase]split={n_main_segments}{split_outs}")

    concat_inputs = []
    cursor = 0.0
    for i, seg in enumerate(segments):
        lines.append(f"[m{i}]trim=start={cursor}:end={seg['start']},setpts=PTS-STARTPTS[v{i}]")
        concat_inputs.append(f"[v{i}]")
        broll_idx = i + 1
        broll_dur = seg['end'] - seg['start']
        lines.append(f"[{broll_idx}:v]{broll_crop},trim=start=0:end={broll_dur},setpts=PTS-STARTPTS[b{i}]")
        concat_inputs.append(f"[b{i}]")
        cursor = seg['end']

    last_i = len(segments)
    lines.append(f"[m{last_i}]trim=start={cursor}:end={clip_duration},setpts=PTS-STARTPTS[v{last_i}]")
    concat_inputs.append(f"[v{last_i}]")

    concat_str = ''.join(concat_inputs)
    lines.append(f"{concat_str}concat=n={len(concat_inputs)}:v=1:a=0[vconcat]")

    out_label = 'vconcat'
    if ass_path:
        escaped_ass = ass_path.replace('\\', '/').replace(':', '\\:')
        lines.append(f"[vconcat]ass='{escaped_ass}'[vout]")
        out_label = 'vout'

    return ';'.join(lines), out_label

def generate_voiceover_tts(text, out_path, voice="en-US-GuyNeural"):
    """Synthesizes `text` into speech using Microsoft Edge's free TTS
    engine (via edge-tts) and saves it as an mp3 at out_path. Returns True
    on success, False on any failure (missing/empty text, network issue,
    the service being unreachable, etc.) so callers can skip the
    voice-over entirely and keep a normal clip.
    """
    text = (text or '').strip()
    if not text:
        return False
    try:
        import edge_tts

        async def _synthesize():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(out_path)

        asyncio.run(_synthesize())
        return os.path.exists(out_path) and os.path.getsize(out_path) > 500
    except Exception as e:
        print('[voiceover] TTS synthesis failed:', e, flush=True)
        return False

def apply_voiceover(clip_path, voiceover_path, output_path):
    """Mixes a narration track (voiceover_path) over the start of an
    already-rendered clip (clip_path): the clip's own audio is ducked to
    25% volume only while the narration plays, then returns to full volume.
    Video is copied through untouched (no re-encode). Writes the result to
    output_path. Returns True on success, False on any failure -- callers
    should keep the original clip_path untouched in that case.
    """
    try:
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', voiceover_path],
            capture_output=True, text=True, timeout=15,
        )
        vo_duration = float(probe.stdout.strip() or 0)
        if vo_duration <= 0:
            return False

        filter_complex = (
            f"[0:a]volume=0.25:enable='between(t,0,{vo_duration})'[a0];"
            f"[1:a]apad[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        cmd = [
            'ffmpeg', '-y',
            '-i', clip_path,
            '-i', voiceover_path,
            '-filter_complex', filter_complex,
            '-map', '0:v', '-map', '[aout]',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k',
            '-shortest',
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        ok = os.path.exists(output_path) and os.path.getsize(output_path) > 1000
        if not ok:
            print('[voiceover] mixing failed. RETURNCODE:', result.returncode, flush=True)
            print('[voiceover] STDERR:', (result.stderr or '')[-2000:], flush=True)
        return ok
    except Exception as e:
        print('[voiceover] mixing failed:', e, flush=True)
        return False

def extract_clip(video_path, start, duration, output_path, caption="", words=None, transcript_text="", broll_enabled=False):
    target_w, target_h = 1080, 1920
    ass_path = None

    if words:
        ass_path = output_path.replace('.mp4', '.ass')
        generate_ass_captions_from_words(words, duration, ass_path)
    elif caption:
        ass_path = output_path.replace('.mp4', '.ass')
        generate_ass_captions(caption, duration, ass_path)

    keyframes = detect_speaker_crop_keyframes(video_path, start, duration)
    crop_x_expr = build_crop_x_expr(keyframes)
    crop_part = (
        f"crop=w={target_w}:h={target_h}:"
        f"x='{crop_x_expr}':y='in_h/2-out_h/2'"
    )
    main_crop_filter = f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,{crop_part}"

    def plain_vf():
        if ass_path:
            escaped_ass = ass_path.replace('\\', '/').replace(':', '\\:')
            return f"{main_crop_filter},ass='{escaped_ass}'"
        return main_crop_filter

    def plain_cmd():
        return [
            'ffmpeg', '-y',
            '-ss', str(start), '-i', video_path,
            '-t', str(duration),
            '-vf', plain_vf(),
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-threads', '2',
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart',
            output_path
        ]

    broll_segments = []
    broll_files = []
    if broll_enabled:
        broll_segments = get_broll_segments(transcript_text, duration)
        for idx, seg in enumerate(broll_segments):
            tmp_path = output_path.replace('.mp4', f'_broll{idx}.mp4')
            if fetch_broll_clip(seg['query'], tmp_path, seg['end'] - seg['start']):
                broll_files.append(tmp_path)
            else:
                broll_files.append(None)
        if not broll_files or any(f is None for f in broll_files):
            for f in broll_files:
                if f and os.path.exists(f):
                    os.remove(f)
            broll_segments = []
            broll_files = []

    use_broll = bool(broll_segments and broll_files)

    if use_broll:
        escaped_ass = ass_path.replace('\\', '/').replace(':', '\\:') if ass_path else None
        try:
            filter_complex, out_label = build_broll_filter_complex(
                broll_segments, duration, target_w, target_h, main_crop_filter, escaped_ass
            )
            cmd = ['ffmpeg', '-y', '-ss', str(start), '-t', str(duration), '-i', video_path]
            for bf in broll_files:
                cmd += ['-i', bf]
            cmd += [
                '-filter_complex', filter_complex,
                '-map', f'[{out_label}]', '-map', '0:a',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-threads', '2',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',
                output_path
            ]
        except Exception as e:
            print('[broll] failed to build filter graph, falling back to plain clip:', e, flush=True)
            use_broll = False
            cmd = plain_cmd()
    else:
        cmd = plain_cmd()

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=150 if use_broll else 120)
        returncode = result.returncode
        stderr = result.stderr
    except subprocess.TimeoutExpired as e:
        returncode = "TIMEOUT"
        stderr = (e.stderr or b"").decode(errors="ignore") if isinstance(e.stderr, bytes) else (e.stderr or "")

    ok = os.path.exists(output_path) and os.path.getsize(output_path) > 1000

    if not ok and use_broll:
        print("FFMPEG B-ROLL FAILED, falling back to plain clip. RETURNCODE:", returncode)
        print("FFMPEG STDERR:", (stderr or '')[-3000:])
        for f in broll_files:
            if f and os.path.exists(f):
                os.remove(f)
        broll_files = []
        cmd = plain_cmd()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            returncode = result.returncode
            stderr = result.stderr
        except subprocess.TimeoutExpired as e:
            returncode = "TIMEOUT"
            stderr = (e.stderr or b"").decode(errors="ignore") if isinstance(e.stderr, bytes) else (e.stderr or "")
        ok = os.path.exists(output_path) and os.path.getsize(output_path) > 1000

    if not ok:
        print("FFMPEG FAILED. RETURNCODE:", returncode)
        print("FFMPEG STDERR:", (stderr or '')[-3000:])

    for f in broll_files:
        if f and os.path.exists(f):
            os.remove(f)
    if ass_path and os.path.exists(ass_path):
        os.remove(ass_path)

    return ok
def generate_thumbnail(clip_path, thumb_path):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', clip_path],
            capture_output=True, text=True
        )
        data = json.loads(result.stdout)
        duration = float(data['format']['duration'])
        seek = duration * 0.2
        cmd = [
            'ffmpeg', '-y', '-ss', str(seek), '-i', clip_path,
            '-vframes', '1',
            '-vf', 'scale=360:640:force_original_aspect_ratio=increase,crop=360:640',
            '-q:v', '3', thumb_path
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)
        return os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0
    except Exception as e:
        print("Thumbnail error:", e)
        return False

def generate_clips(video_path, num_clips=3, clip_duration=30, broll_enabled=False, voiceover_enabled=False):
    duration = get_video_duration(video_path)
    clips = []
    job_id = str(uuid.uuid4())[:8]
    scene_times = detect_scenes(video_path)

    if len(scene_times) >= num_clips:
        step = len(scene_times) // num_clips
        selected = [scene_times[i * step] for i in range(num_clips)]
    else:
        margin = clip_duration
        usable = max(duration - margin, clip_duration)
        step = usable / num_clips
        selected = [margin / 2 + i * step for i in range(num_clips)]

    for i, start in enumerate(selected[:num_clips]):
        actual_start = min(max(start, 0), duration - clip_duration)
        actual_dur = min(clip_duration, duration - actual_start)

        print(f'[transcribe] clip {i+1}: transcribing {actual_dur:.0f}s window starting at {actual_start:.0f}s...', flush=True)
        clip_words = transcribe_clip_window(video_path, actual_start, actual_dur)
        print(f'[transcribe] clip {i+1}: got {len(clip_words)} words', flush=True)
        transcript_text = ' '.join(w['text'] for w in clip_words).strip()

        meta = generate_metadata_with_claude(transcript_text, actual_dur, i)
        title = meta['title']
        caption = meta['caption']
        hashtags = meta['hashtags']
        virality_score = meta['virality_score']
        virality_reason = meta['reasoning']

        out_filename = f"clip_{job_id}_{i+1}.mp4"
        out_path = os.path.join(OUTPUT_FOLDER, out_filename)
        success = extract_clip(video_path, actual_start, actual_dur, out_path, caption, words=clip_words, transcript_text=transcript_text, broll_enabled=broll_enabled)
        if success and voiceover_enabled:
            vo_path = out_path.replace('.mp4', '_vo.mp3')
            if generate_voiceover_tts(title, vo_path):
                mixed_path = out_path.replace('.mp4', '_mixed.mp4')
                if apply_voiceover(out_path, vo_path, mixed_path):
                    os.replace(mixed_path, out_path)
                elif os.path.exists(mixed_path):
                    os.remove(mixed_path)
            if os.path.exists(vo_path):
                os.remove(vo_path)

        if success:
            thumb_filename = f"thumb_{job_id}_{i+1}.jpg"
            thumb_path = os.path.join(OUTPUT_FOLDER, thumb_filename)
            thumb_ok = generate_thumbnail(out_path, thumb_path)

            clip_r2_key = f"clips/{out_filename}"
            clip_uploaded = upload_to_r2(out_path, clip_r2_key, 'video/mp4')
            if clip_uploaded and os.path.exists(out_path):
                os.remove(out_path)

            thumb_r2_key = None
            if thumb_ok:
                thumb_r2_key = f"thumbnails/{thumb_filename}"
                thumb_uploaded = upload_to_r2(thumb_path, thumb_r2_key, 'image/jpeg')
                if thumb_uploaded and os.path.exists(thumb_path):
                    os.remove(thumb_path)
                if not thumb_uploaded:
                    thumb_r2_key = None

            clips.append({
                'id': i + 1,
                'filename': clip_r2_key if clip_uploaded else out_filename,
                'title': title,
                'caption': caption,
                'hashtags': hashtags,
                'transcript': transcript_text,
                'start': round(actual_start, 1),
                'duration': round(actual_dur, 1),
                'virality_score': virality_score,
                'virality_reason': virality_reason,
                'thumbnail': thumb_r2_key,
                'download_url': f'/download/{job_id}_{i+1}',
                'thumbnail_url': r2_public_url(thumb_r2_key) if thumb_r2_key else None
            })
    return clips

@app.route('/login')
def login():
    if 'user' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/auth/google')
def auth_google():
    redirect_uri = os.environ.get('GOOGLE_REDIRECT_URI', url_for('auth_google_callback', _external=True))
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/google/callback')
def auth_google_callback():
    token = google.authorize_access_token()
    user_info = token.get('userinfo')
    if not user_info:
        return redirect(url_for('login'))

    google_id = user_info['sub']
    email = user_info.get('email', '')
    name = user_info.get('name', '')
    picture = user_info.get('picture', '')

    db = get_db()
    existing = db.execute('SELECT id FROM users WHERE google_id = ?', (google_id,)).fetchone()
    if existing:
        user_id = existing['id']
        db.execute('UPDATE users SET email=?, name=?, picture=? WHERE id=?', (email, name, picture, user_id))
    else:
        cur = db.execute('INSERT INTO users (google_id, email, name, picture) VALUES (?, ?, ?, ?)',
                         (google_id, email, name, picture))
        user_id = cur.lastrowid
    db.commit()
    db.close()

    session.permanent = True
    session['user'] = {'id': user_id, 'email': email, 'name': name, 'picture': picture}
    try:
        send_login_notification(email, name, request)
        log_usage('emails', 1, user_id)
    except Exception:
        pass
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── Page routes ───────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    return render_template('index.html', user=session.get('user'))

@app.route('/my-clips')
@login_required
def my_clips():
    return render_template('my_clips.html', user=session.get('user'))

@app.route('/analytics')
@login_required
def analytics():
    return render_template('analytics.html', user=session.get('user'))

@app.route('/preferences')
@login_required
def preferences():
    return render_template('preferences.html', user=session.get('user'))

@app.route('/about')
def about():
    return render_template('about.html', user=session.get('user'))

@app.route('/privacy')
def privacy():
    return render_template('privacy.html', user=session.get('user'))

@app.route('/terms')
def terms():
    return render_template('terms.html', user=session.get('user'))
@app.route('/contact')
def contact():
    return render_template('contact.html', user=session.get('user'))

# API routes
@app.route('/upload', methods=['POST'])
@login_required
def upload():
    num_clips = int(request.form.get('num_clips', 3))
    clip_duration = int(request.form.get('clip_duration', 30))
    voiceover_enabled = request.form.get('voiceover', 'false').lower() in ('1', 'true', 'on', 'yes')
    broll_enabled = request.form.get('broll', 'false').lower() in ('1', 'true', 'on', 'yes')
    from paypal_subscriptions import get_user_plan_limits
    _limits = get_user_plan_limits(current_user_id())
    num_clips = min(max(num_clips, 1), _limits['max_clips'])
    clip_duration = min(max(clip_duration, 15), _limits['max_duration'])
    job_id = uuid.uuid4().hex
    user_id = current_user_id()

    # Free users: limited number of generations per rolling 24 hours.
    from paypal_subscriptions import PLAN_LIMITS as _PLAN_LIMITS
    if _limits is _PLAN_LIMITS[None]:
        _cdb = get_db()
        _used = _cdb.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE user_id = ? AND status != 'error' AND created_at >= datetime('now', '-1 day')",
            (user_id,)
        ).fetchone()['n']
        _cdb.close()
        if _used >= FREE_DAILY_LIMIT:
            return jsonify({'error': f'Free limit reached ({FREE_DAILY_LIMIT} generations per day). Upgrade your plan on the Pricing page for more, or try again tomorrow.'}), 429

    video_path = None
    source_url = request.form.get('video_url', '').strip()

    if source_url:
        video_path, error = download_from_url(source_url, job_id)
        if not video_path:
            friendly_error = "This video couldn't be downloaded. It may be restricted, age-limited, or protected by YouTube. Please try a different video."
            print(f'[download] raw error for {source_url}: {error}', flush=True)
            return jsonify({'error': friendly_error}), 400
    elif 'video' in request.files and request.files['video'].filename:
        file = request.files['video']
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type'}), 400
        filename = f"{job_id}_{secure_filename(file.filename)}"
        video_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(video_path)
    else:
        return jsonify({'error': 'Please upload a video file or paste a URL'}), 400

    _jdb = get_db()
    _jdb.execute('INSERT INTO jobs (id, user_id, status) VALUES (?, ?, ?)', (job_id, user_id, 'processing'))
    _jdb.commit()
    _jdb.close()

    def _run_job():
        try:
            _t0 = time.time()
            clips = generate_clips(video_path, num_clips=num_clips, clip_duration=clip_duration, broll_enabled=broll_enabled, voiceover_enabled=voiceover_enabled)
            log_usage('compute_seconds', time.time() - _t0, user_id)
            if os.path.exists(video_path):
                os.remove(video_path)
            if not clips:
                _jdb = get_db()
                _jdb.execute('UPDATE jobs SET status=?, error=? WHERE id=?',
                             ('error', 'Could not generate clips. Make sure ffmpeg is installed.', job_id))
                _jdb.commit()
                _jdb.close()
                return

            db = get_db()
            clip_ids = []
            for clip in clips:
                cur = db.execute(
                    'INSERT INTO clips (user_id, filename, caption, duration, start_time, virality_score, virality_reason, thumbnail, title, hashtags, transcript) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (user_id, clip['filename'], clip['caption'], clip['duration'], clip['start'], clip['virality_score'], clip.get('virality_reason'), clip.get('thumbnail'), clip.get('title'), json.dumps(clip.get('hashtags') or []), clip.get('transcript'))
                )
                clip_ids.append(cur.lastrowid)
            db.commit()
            db.close()

            for clip, cid in zip(clips, clip_ids):
                clip['id'] = cid
                clip['download_url'] = f'/download/{cid}'

            _jdb = get_db()
            _jdb.execute('UPDATE jobs SET status=?, clips_json=? WHERE id=?',
                         ('done', json.dumps(clips), job_id))
            _jdb.commit()
            _jdb.close()
        except Exception as e:
            if video_path and os.path.exists(video_path):
                os.remove(video_path)
            _jdb = get_db()
            _jdb.execute('UPDATE jobs SET status=?, error=? WHERE id=?', ('error', str(e), job_id))
            _jdb.commit()
            _jdb.close()

    threading.Thread(target=_run_job, daemon=True).start()
    return jsonify({'job_id': job_id}), 202

@app.route('/api/jobs/<job_id>')
@login_required
def job_status(job_id):
    db = get_db()
    row = db.execute('SELECT status, clips_json, error FROM jobs WHERE id = ? AND user_id = ?',
                      (job_id, current_user_id())).fetchone()
    db.close()
    if not row:
        return jsonify({'status': 'error', 'error': 'Unknown job'}), 404
    if row['status'] == 'done':
        return jsonify({'status': 'done', 'clips': json.loads(row['clips_json'] or '[]')})
    if row['status'] == 'error':
        return jsonify({'status': 'error', 'error': row['error']})
    return jsonify({'status': 'processing'})

@app.route('/api/clips')
@login_required
def api_clips():
    user_id = current_user_id()
    db = get_db()
    rows = db.execute('SELECT * FROM clips WHERE user_id = ? ORDER BY created_at DESC', (user_id,)).fetchall()
    _ensure_reactions_table(db)
    reactions = {r['clip_id']: r['emoji'] for r in db.execute(
        'SELECT clip_id, emoji FROM clip_reactions WHERE user_id = ?', (user_id,)).fetchall()}
    db.close()
    clips = []
    for row in rows:
        r2_key = row['filename']
        thumb_key = row['thumbnail']
        clips.append({
            'id': row['id'],
            'filename': r2_key,
            'title': row['title'],
            'hashtags': json.loads(row['hashtags']) if row['hashtags'] else [],
            'transcript': row['transcript'],
            'caption': row['caption'],
            'duration': row['duration'],
            'start_time': row['start_time'],
            'virality_score': row['virality_score'],
            'virality_reason': row['virality_reason'],
            'reaction': reactions.get(row['id']),
            'created_at': row['created_at'],
            'exists': True,  # R2 objects don't disappear on Railway restarts
            'download_url': f'/download/{row["id"]}',
            'thumbnail_url': r2_public_url(thumb_key) if thumb_key else None
        })
    return jsonify({'clips': clips})

@app.route('/api/clips/<int:clip_id>', methods=['DELETE'])
@login_required
def delete_clip(clip_id):
    user_id = current_user_id()
    db = get_db()
    row = db.execute('SELECT filename, thumbnail FROM clips WHERE id = ? AND user_id = ?', (clip_id, user_id)).fetchone()
    if not row:
        db.close()
        return jsonify({'error': 'Clip not found'}), 404

    delete_from_r2(row['filename'])
    if row['thumbnail']:
        delete_from_r2(row['thumbnail'])

    db.execute('DELETE FROM clips WHERE id = ? AND user_id = ?', (clip_id, user_id))
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/analytics')
@login_required
def api_analytics():
    user_id = current_user_id()
    db = get_db()
    clips = db.execute('SELECT * FROM clips WHERE user_id = ? ORDER BY created_at DESC', (user_id,)).fetchall()
    db.close()

    total = len(clips)
    total_duration = sum(c['duration'] or 0 for c in clips)
    avg_virality = round(sum(c['virality_score'] or 0 for c in clips) / total, 1) if total else 0

    from collections import defaultdict
    daily = defaultdict(int)
    for c in clips:
        day = c['created_at'][:10] if c['created_at'] else 'unknown'
        daily[day] += 1

    top = sorted(
        [{'id': c['id'], 'filename': c['filename'], 'caption': c['caption'],
          'virality_score': c['virality_score'], 'virality_reason': c['virality_reason'],
          'duration': c['duration'], 'created_at': c['created_at']} for c in clips],
        key=lambda x: x['virality_score'] or 0, reverse=True
    )[:5]

    return jsonify({
        'total_clips': total,
        'total_duration': round(total_duration, 1),
        'avg_virality': avg_virality,
        'daily': dict(daily),
        'top_clips': top
    })

@app.route('/api/preferences', methods=['GET'])
@login_required
def get_preferences():
    user_id = current_user_id()
    db = get_db()
    rows = db.execute('SELECT key, value FROM preferences WHERE user_id = ?', (user_id,)).fetchall()
    db.close()
    prefs = {r['key']: r['value'] for r in rows}
    defaults = {'num_clips': '3', 'clip_duration': '30', 'auto_captions': 'true'}
    defaults.update(prefs)
    return jsonify(defaults)

@app.route('/api/preferences', methods=['POST'])
@login_required
def save_preferences():
    user_id = current_user_id()
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400
    db = get_db()
    for key, value in data.items():
        db.execute('INSERT OR REPLACE INTO preferences (user_id, key, value) VALUES (?, ?, ?)',
                   (user_id, key, str(value)))
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/clips/<int:clip_id>/transcript', methods=['POST'])
@login_required
def update_clip_transcript(clip_id):
    user_id = current_user_id()
    data = request.get_json() or {}
    new_transcript = str(data.get('transcript', '')).strip()
    db = get_db()
    row = db.execute('SELECT id FROM clips WHERE id = ? AND user_id = ?', (clip_id, user_id)).fetchone()
    if not row:
        db.close()
        return jsonify({'error': 'Clip not found'}), 404
    db.execute('UPDATE clips SET transcript = ? WHERE id = ? AND user_id = ?', (new_transcript, clip_id, user_id))
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/download/<int:clip_id>')
@login_required
def download(clip_id):
    user_id = current_user_id()
    db = get_db()
    row = db.execute('SELECT filename FROM clips WHERE id = ? AND user_id = ?', (clip_id, user_id)).fetchone()
    db.close()
    if not row:
        return jsonify({'error': 'Clip not found'}), 404

    url = r2_presigned_download_url(row['filename'], os.path.basename(row['filename']))
    if not url:
        return jsonify({'error': 'Storage not configured'}), 500
    return redirect(url)

@app.route('/youtube/connect')
@login_required
def youtube_connect():
    if not YOUTUBE_CLIENT_ID or not YOUTUBE_CLIENT_SECRET:
        return "YouTube publishing isn't set up yet (missing YOUTUBE_CLIENT_ID/YOUTUBE_CLIENT_SECRET).", 500
    from urllib.parse import urlencode
    state = uuid.uuid4().hex
    session['youtube_oauth_state'] = state
    params = {
        'client_id': YOUTUBE_CLIENT_ID,
        'redirect_uri': YOUTUBE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'https://www.googleapis.com/auth/youtube.upload',
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state,
    }
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params))

@app.route('/youtube/callback')
@login_required
def youtube_callback():
    if request.args.get('error'):
        return redirect('/?youtube_error=' + request.args.get('error'))
    state = request.args.get('state')
    if not state or state != session.get('youtube_oauth_state'):
        return redirect('/?youtube_error=bad_state')
    code = request.args.get('code')
    if not code:
        return redirect('/?youtube_error=no_code')
    try:
        resp = requests.post('https://oauth2.googleapis.com/token', data={
            'client_id': YOUTUBE_CLIENT_ID,
            'client_secret': YOUTUBE_CLIENT_SECRET,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': YOUTUBE_REDIRECT_URI,
        }, timeout=20)
        resp.raise_for_status()
        tokens = resp.json()
        refresh_token = tokens.get('refresh_token')
        access_token = tokens.get('access_token')
        if not refresh_token:
            return redirect('/?youtube_error=no_refresh_token')
        db = get_db()
        db.execute('''
            INSERT INTO youtube_tokens (user_id, refresh_token, access_token)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET refresh_token=excluded.refresh_token, access_token=excluded.access_token
        ''', (current_user_id(), refresh_token, access_token))
        db.commit()
        db.close()
        return redirect('/?youtube=connected')
    except Exception as e:
        print('[youtube] OAuth callback failed:', e, flush=True)
        return redirect('/?youtube_error=1')

@app.route('/api/youtube/status')
@login_required
def youtube_status():
    db = get_db()
    row = db.execute('SELECT user_id FROM youtube_tokens WHERE user_id = ?', (current_user_id(),)).fetchone()
    db.close()
    return jsonify({'connected': bool(row), 'configured': bool(YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET)})

@app.route('/api/clips/<int:clip_id>/publish-youtube', methods=['POST'])
@login_required
def publish_to_youtube(clip_id):
    user_id = current_user_id()
    db = get_db()
    tok_row = db.execute('SELECT refresh_token FROM youtube_tokens WHERE user_id = ?', (user_id,)).fetchone()
    clip_row = db.execute('SELECT * FROM clips WHERE id = ? AND user_id = ?', (clip_id, user_id)).fetchone()
    db.close()
    if not tok_row:
        return jsonify({'error': 'Connect your YouTube account first.'}), 400
    if not clip_row:
        return jsonify({'error': 'Clip not found'}), 404

    download_url = r2_presigned_download_url(clip_row['filename'], os.path.basename(clip_row['filename']))
    if not download_url:
        return jsonify({'error': 'Clip storage not available'}), 500

    tmp_path = os.path.join(OUTPUT_FOLDER, f"yt_upload_{clip_id}_{uuid.uuid4().hex[:8]}.mp4")
    try:
        with requests.get(download_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(tmp_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)

        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds = Credentials(
            None,
            refresh_token=tok_row['refresh_token'],
            token_uri='https://oauth2.googleapis.com/token',
            client_id=YOUTUBE_CLIENT_ID,
            client_secret=YOUTUBE_CLIENT_SECRET,
        )
        creds.refresh(GoogleAuthRequest())

        try:
            hashtags = json.loads(clip_row['hashtags'] or '[]')
        except Exception:
            hashtags = []
        hashtag_text = ' '.join(h if h.startswith('#') else f'#{h}' for h in hashtags)

        top_hashtags = ' '.join(hashtag_text.split()[:2])
        title = (clip_row['title'] or clip_row['caption'] or 'New Clip').strip()
        suffix = (' ' + top_hashtags if top_hashtags else '') + ' #Shorts'
        max_title_len = 100 - len(suffix)
        if len(title) > max_title_len:
            title = title[:max_title_len].rstrip()
        if '#shorts' not in title.lower():
            title = f"{title}{suffix}"

        description = ((clip_row['caption'] or '') + '\n\n' + hashtag_text).strip()
        tags = [h.lstrip('#') for h in hashtags][:15]

        youtube = build('youtube', 'v3', credentials=creds)
        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': tags,
                'categoryId': '22',
            },
            'status': {
                'privacyStatus': 'public',
                'selfDeclaredMadeForKids': False,
            },
        }
        media = MediaFileUpload(tmp_path, chunksize=-1, resumable=True, mimetype='video/mp4')
        upload_request = youtube.videos().insert(part='snippet,status', body=body, media_body=media)
        response = None
        while response is None:
            status, response = upload_request.next_chunk()
        video_id = response.get('id')
        return jsonify({'success': True, 'youtube_url': f'https://youtube.com/watch?v={video_id}'})
    except Exception as e:
        print('[youtube] publish failed:', e, flush=True)
        return jsonify({'error': f'Publish failed: {e}'}), 500
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

# ── Cost tracker (private /admin/costs page) ─────────────────────────────────
ADMIN_EMAILS = [e.strip().lower() for e in os.environ.get('ADMIN_EMAILS', 'mamtarani999944@gmail.com').split(',') if e.strip()]

# Estimated prices in USD. Edit these if a service changes its prices.
COST_RATES = {
    'anthropic_in_per_million': 1.00,    # Claude Haiku 4.5 input
    'anthropic_out_per_million': 5.00,   # Claude Haiku 4.5 output
    'railway_per_compute_second': 0.00002,  # rough CPU+RAM while generating
    'railway_base_per_month': 5.00,      # always-on base cost
    'r2_per_gb_month': 0.015,
    'r2_free_gb': 10,
    'resend_free_emails_per_month': 3000,
    'resend_paid_per_month': 20.00,
    'domain_per_year': 12.00,
    'usd_to_inr': 88.0,
}

def _ensure_usage_table(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS usage_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT DEFAULT (datetime('now')),
            service TEXT NOT NULL,
            units   REAL NOT NULL,
            user_id INTEGER
        )
    ''')

def log_usage(service, units, user_id=None):
    try:
        db = get_db()
        _ensure_usage_table(db)
        db.execute('INSERT INTO usage_log (service, units, user_id) VALUES (?, ?, ?)',
                   (service, float(units), user_id))
        db.commit()
        db.close()
    except Exception as e:
        print('[usage] could not log usage:', e, flush=True)

def _log_anthropic_usage(data):
    try:
        u = (data or {}).get('usage') or {}
        log_usage('anthropic_in', u.get('input_tokens', 0))
        log_usage('anthropic_out', u.get('output_tokens', 0))
    except Exception:
        pass

COST_PAGE = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Costs</title>
<style>
  body{font-family:Inter,system-ui,sans-serif;background:#f5f5f7;color:#111;margin:0;padding:16px}
  .wrap{max-width:760px;margin:0 auto}
  h1{font-size:20px;margin:8px 0 4px}
  .sub{color:#666;font-size:13px;margin-bottom:14px}
  .tabs a{display:inline-block;padding:6px 12px;border-radius:8px;background:#fff;border:1px solid #e5e5e7;color:#111;text-decoration:none;font-size:13px;margin-right:6px}
  .tabs a.on{background:#111;color:#fff}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
  .card{background:#fff;border:1px solid #e5e5e7;border-radius:12px;padding:12px}
  .card b{display:block;font-size:20px;margin-top:4px}
  .card span{font-size:12px;color:#666}
  .row{background:#fff;border:1px solid #e5e5e7;border-radius:12px;padding:12px;margin-bottom:10px}
  .top{display:flex;justify-content:space-between;gap:10px;font-weight:600}
  .detail{font-size:12px;color:#666;margin-top:2px}
  .bar{height:8px;background:#eee;border-radius:6px;margin-top:8px;overflow:hidden}
  .bar i{display:block;height:100%;background:#111}
  .first .bar i{background:#e11d48}
  .note{font-size:12px;color:#777;margin-top:14px;line-height:1.5}
</style></head><body><div class="wrap">
<h1>Where your money goes</h1>
<div class="sub">Estimated cost per service, biggest first. Last {{ days }} days.</div>
<div class="tabs">
  {% for d in [7,30,90,365] %}<a href="?days={{ d }}" class="{{ 'on' if d == days else '' }}">{{ d }} days</a>{% endfor %}
</div>
<div class="cards">
  <div class="card"><span>Total cost</span><b>${{ total_usd }}</b><span>Rs {{ total_inr }}</span></div>
  <div class="card"><span>Generations</span><b>{{ gens }}</b><span>{{ users }} active users</span></div>
  <div class="card"><span>Cost per generation</span><b>${{ per_gen }}</b><span>variable costs only</span></div>
  <div class="card"><span>Total users</span><b>{{ total_users }}</b><span>all time</span></div>
</div>
{% for r in rows %}
<div class="row {{ 'first' if loop.first and r.usd_raw > 0 else '' }}">
  <div class="top"><span>{{ loop.index }}. {{ r.name }}</span><span>${{ r.usd }} &middot; Rs {{ r.inr }}</span></div>
  <div class="detail">{{ r.detail }}</div>
  <div class="bar"><i style="width:{{ r.pct }}%"></i></div>
</div>
{% endfor %}
<div class="note">These are estimates from usage tracked since this page was installed. Your real bills are on each site's billing page (Railway, Anthropic, Cloudflare, Resend, your domain seller). Razorpay and PayPal fees are taken from each payment automatically and are not shown here.</div>
</div></body></html>'''

@app.route('/admin/costs')
@login_required
def admin_costs():
    email = (session.get('user', {}).get('email') or '').lower()
    if email not in ADMIN_EMAILS:
        return 'Not found', 404
    try:
        days = int(request.args.get('days', 30))
    except ValueError:
        days = 30
    days = max(1, min(days, 3650))
    window = f'-{days} days'
    R = COST_RATES

    db = get_db()
    _ensure_usage_table(db)
    use = {r['service']: (r['t'] or 0) for r in db.execute(
        'SELECT service, SUM(units) AS t FROM usage_log WHERE ts >= datetime(\'now\', ?) GROUP BY service', (window,)).fetchall()}
    stored_bytes = db.execute("SELECT SUM(units) AS t FROM usage_log WHERE service = 'r2_bytes'").fetchone()['t'] or 0
    gens = db.execute("SELECT COUNT(*) AS n FROM jobs WHERE status != 'error' AND created_at >= datetime('now', ?)", (window,)).fetchone()['n']
    users = db.execute("SELECT COUNT(DISTINCT user_id) AS n FROM jobs WHERE status != 'error' AND created_at >= datetime('now', ?)", (window,)).fetchone()['n']
    total_users = db.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
    db.close()

    tok_in = use.get('anthropic_in', 0)
    tok_out = use.get('anthropic_out', 0)
    ai_cost = tok_in / 1e6 * R['anthropic_in_per_million'] + tok_out / 1e6 * R['anthropic_out_per_million']

    secs = use.get('compute_seconds', 0)
    rail_var = secs * R['railway_per_compute_second']
    rail_base = R['railway_base_per_month'] * days / 30
    rail_cost = rail_var + rail_base

    stored_gb = stored_bytes / 1e9
    r2_cost = max(0.0, stored_gb - R['r2_free_gb']) * R['r2_per_gb_month'] * days / 30

    emails = use.get('emails', 0)
    emails_per_month = emails / days * 30
    resend_cost = (R['resend_paid_per_month'] * days / 30) if emails_per_month > R['resend_free_emails_per_month'] else 0.0

    domain_cost = R['domain_per_year'] * days / 365

    items = [
        ('Railway (server)', rail_cost, f'{secs/3600:.1f} hours of clip-making + base cost ${rail_base:.2f}'),
        ('Anthropic (AI)', ai_cost, f'{int(tok_in):,} input + {int(tok_out):,} output tokens'),
        ('Cloudflare R2 (storage)', r2_cost, f'{stored_gb:.2f} GB uploaded so far ({R["r2_free_gb"]} GB free)'),
        ('Resend (login emails)', resend_cost, f'{int(emails)} emails ({R["resend_free_emails_per_month"]}/month free)'),
        ('Domain (viralcut.xyz)', domain_cost, f'about ${R["domain_per_year"]:.0f} per year'),
    ]
    items.sort(key=lambda x: x[1], reverse=True)
    total = sum(x[1] for x in items)
    top = max([x[1] for x in items] + [0.0001])
    rows = [{
        'name': n, 'usd_raw': c, 'usd': f'{c:.2f}', 'inr': f'{c * R["usd_to_inr"]:.0f}',
        'detail': d, 'pct': round(c / top * 100),
    } for n, c, d in items]
    variable = rail_var + ai_cost
    per_gen = f'{(variable / gens):.3f}' if gens else '0.000'
    return render_template_string(
        COST_PAGE, days=days, rows=rows, gens=gens, users=users, total_users=total_users,
        total_usd=f'{total:.2f}', total_inr=f'{total * R["usd_to_inr"]:.0f}', per_gen=per_gen)

@app.after_request
def _security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
    return resp

# ── Emoji reactions on clips ─────────────────────────────────────────────────
def _emojiish(ch):
    o = ord(ch)
    return ((0x1F000 <= o <= 0x1FAFF) or (0x2190 <= o <= 0x2BFF)
            or o in (0xA9, 0xAE, 0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299))

def _valid_reaction(e):
    """True for a short string made only of emoji characters (any emoji)."""
    if not isinstance(e, str) or not (1 <= len(e) <= 16):
        return False
    glue = (0x200D, 0xFE0F, 0x20E3)
    if not all(_emojiish(c) or ord(c) in glue for c in e):
        return False
    return any(_emojiish(c) for c in e)

def _ensure_reactions_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS clip_reactions (
            clip_id INTEGER,
            user_id INTEGER,
            emoji   TEXT NOT NULL,
            PRIMARY KEY (clip_id, user_id)
        )
    """)

@app.route('/api/clips/<int:clip_id>/reaction', methods=['POST'])
@login_required
def set_clip_reaction(clip_id):
    user_id = current_user_id()
    emoji = (request.get_json(silent=True) or {}).get('emoji')
    if not _valid_reaction(emoji):
        return jsonify({'error': 'Unknown emoji'}), 400
    db = get_db()
    _ensure_reactions_table(db)
    clip = db.execute('SELECT id FROM clips WHERE id = ? AND user_id = ?', (clip_id, user_id)).fetchone()
    if not clip:
        db.close()
        return jsonify({'error': 'Clip not found'}), 404
    current = db.execute('SELECT emoji FROM clip_reactions WHERE clip_id = ? AND user_id = ?', (clip_id, user_id)).fetchone()
    if current and current['emoji'] == emoji:
        db.execute('DELETE FROM clip_reactions WHERE clip_id = ? AND user_id = ?', (clip_id, user_id))
        result = None
    else:
        db.execute("""
            INSERT INTO clip_reactions (clip_id, user_id, emoji) VALUES (?, ?, ?)
            ON CONFLICT(clip_id, user_id) DO UPDATE SET emoji = excluded.emoji
        """, (clip_id, user_id, emoji))
        result = emoji
    db.commit()
    db.close()
    return jsonify({'emoji': result})

# ── Visitor counter (private /admin/visits page) ─────────────────────────────
_visits_ready = False
_BOT_WORDS = ('bot', 'spider', 'crawl', 'slurp', 'curl', 'wget', 'python', 'httpx', 'go-http',
              'headless', 'uptime', 'monitor', 'preview', 'facebookexternalhit', 'whatsapp',
              'telegram', 'discord', 'slack', 'lighthouse', 'pingdom', 'java/')
_SKIP_PREFIXES = ('/admin', '/api', '/static', '/auth', '/download', '/health', '/webhook', '/favicon')

def _ensure_visits_table(db):
    global _visits_ready
    if _visits_ready:
        return
    db.execute('''
        CREATE TABLE IF NOT EXISTS visits (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT DEFAULT (datetime('now')),
            path    TEXT,
            source  TEXT,
            visitor TEXT
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts)')
    db.commit()
    _visits_ready = True

def _visit_source():
    import re as _re
    from urllib.parse import urlparse
    tag = (request.args.get('ref') or request.args.get('utm_source') or '').strip().lower()
    tag = _re.sub(r'[^a-z0-9._-]', '', tag)[:40]
    if tag:
        return tag
    ref = request.headers.get('Referer') or ''
    try:
        host = (urlparse(ref).hostname or '').lower()
    except Exception:
        host = ''
    if host.startswith('www.'):
        host = host[4:]
    if not host or host.endswith('viralcut.xyz') or host.endswith('railway.app'):
        return 'direct'
    return _re.sub(r'[^a-z0-9._-]', '', host)[:60] or 'direct'

@app.after_request
def _track_visit(resp):
    try:
        if request.method != 'GET' or resp.status_code != 200 or resp.mimetype != 'text/html':
            return resp
        path = request.path or '/'
        if path.startswith(_SKIP_PREFIXES):
            return resp
        ua = (request.headers.get('User-Agent') or '')
        low = ua.lower()
        if not ua or any(w in low for w in _BOT_WORDS):
            return resp
        email = ((session.get('user') or {}).get('email') or '').lower()
        if email and email in ADMIN_EMAILS:
            return resp
        import hashlib
        salt = str(app.secret_key or '')
        visitor = hashlib.sha256((salt + '|' + (request.remote_addr or '') + '|' + ua).encode('utf-8')).hexdigest()[:16]
        db = get_db()
        _ensure_visits_table(db)
        db.execute('INSERT INTO visits (path, source, visitor) VALUES (?, ?, ?)',
                   (path[:200], _visit_source(), visitor))
        db.commit()
        db.close()
    except Exception as e:
        print('[visits] could not log visit:', e, flush=True)
    return resp

VISITS_PAGE = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Visitors</title>
<style>
  body{font-family:Inter,system-ui,sans-serif;background:#f5f5f7;color:#111;margin:0;padding:16px}
  .wrap{max-width:760px;margin:0 auto}
  h1{font-size:20px;margin:8px 0 4px}
  h2{font-size:15px;margin:22px 0 8px}
  .sub{color:#666;font-size:13px;margin-bottom:14px}
  .tabs a{display:inline-block;padding:6px 12px;border-radius:8px;background:#fff;border:1px solid #e5e5e7;color:#111;text-decoration:none;font-size:13px;margin-right:6px}
  .tabs a.on{background:#111;color:#fff}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
  .card{background:#fff;border:1px solid #e5e5e7;border-radius:12px;padding:12px}
  .card b{display:block;font-size:22px;margin-top:4px}
  .card span{font-size:12px;color:#666}
  .row{background:#fff;border:1px solid #e5e5e7;border-radius:12px;padding:10px 12px;margin-bottom:8px}
  .top{display:flex;justify-content:space-between;gap:10px;font-weight:600;font-size:14px}
  .top em{font-style:normal;font-weight:400;color:#666}
  .bar{height:7px;background:#eee;border-radius:6px;margin-top:7px;overflow:hidden}
  .bar i{display:block;height:100%;background:#111}
  .first .bar i{background:#ff6154}
  .note{font-size:12px;color:#777;margin-top:16px;line-height:1.5}
  .empty{color:#777;font-size:13px;padding:8px 0}
</style></head><body><div class="wrap">
<h1>Your visitors</h1>
<div class="sub">Real people only (no bots, not you). Last {{ days }} days. Days are in UTC.</div>
<div class="tabs">
  {% for d in [1,7,30,90] %}<a href="?days={{ d }}" class="{{ 'on' if d == days else '' }}">{{ 'Today' if d == 1 else (d ~ ' days') }}</a>{% endfor %}
</div>
<div class="cards">
  <div class="card"><span>On the site now (30 min)</span><b>{{ now_n }}</b></div>
  <div class="card"><span>Unique visitors</span><b>{{ uniq }}</b></div>
  <div class="card"><span>Page views</span><b>{{ views }}</b></div>
  <div class="card"><span>New signups</span><b>{{ signups }}</b><span>{{ total_users }} users in total</span></div>
</div>

<h2>Where they came from</h2>
{% for s in sources %}
<div class="row {{ 'first' if loop.first else '' }}">
  <div class="top"><span>{{ loop.index }}. {{ s.name }}</span><span>{{ s.uniq }} <em>people</em></span></div>
  <div class="bar"><i style="width:{{ s.pct }}%"></i></div>
</div>
{% else %}<div class="empty">No visitors yet.</div>{% endfor %}

<h2>Each day</h2>
{% for d in daily %}
<div class="row">
  <div class="top"><span>{{ d.day }}</span><span>{{ d.uniq }} <em>people &middot; {{ d.views }} views</em></span></div>
  <div class="bar"><i style="width:{{ d.pct }}%"></i></div>
</div>
{% else %}<div class="empty">Nothing yet.</div>{% endfor %}

<h2>Pages they opened</h2>
{% for p in pages %}
<div class="row"><div class="top"><span>{{ p.path }}</span><span>{{ p.views }} <em>views</em></span></div></div>
{% else %}<div class="empty">Nothing yet.</div>{% endfor %}

<div class="note">Tip: use the link <b>https://viralcut.xyz/?ref=producthunt</b> on Product Hunt so the source shows as "producthunt". Use <b>?ref=twitter</b>, <b>?ref=reddit</b> etc. for other places. Counting starts when this is deployed. A private scrambled code is used to count unique people; no IP address is saved.</div>
</div></body></html>'''

@app.route('/admin/visits')
@login_required
def admin_visits():
    email = (session.get('user', {}).get('email') or '').lower()
    if email not in ADMIN_EMAILS:
        return 'Not found', 404
    try:
        days = int(request.args.get('days', 7))
    except ValueError:
        days = 7
    days = max(1, min(days, 365))
    window = 'start of day' if days == 1 else f'-{days} days'

    db = get_db()
    _ensure_visits_table(db)
    cond = "ts >= datetime('now', ?)" if days != 1 else "ts >= datetime('now', 'start of day')"
    args = (window,) if days != 1 else ()
    tot = db.execute(f'SELECT COUNT(*) AS v, COUNT(DISTINCT visitor) AS u FROM visits WHERE {cond}', args).fetchone()
    now_n = db.execute("SELECT COUNT(DISTINCT visitor) AS u FROM visits WHERE ts >= datetime('now', '-30 minutes')").fetchone()['u']
    src_rows = db.execute(f'SELECT source, COUNT(DISTINCT visitor) AS u FROM visits WHERE {cond} GROUP BY source ORDER BY u DESC, source LIMIT 15', args).fetchall()
    day_rows = db.execute(f'SELECT date(ts) AS d, COUNT(*) AS v, COUNT(DISTINCT visitor) AS u FROM visits WHERE {cond} GROUP BY date(ts) ORDER BY d DESC LIMIT 31', args).fetchall()
    page_rows = db.execute(f'SELECT path, COUNT(*) AS v FROM visits WHERE {cond} GROUP BY path ORDER BY v DESC LIMIT 8', args).fetchall()
    try:
        total_users = db.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
    except Exception:
        total_users = 0
    try:
        signups = db.execute(f'SELECT COUNT(*) AS n FROM users WHERE {cond.replace("ts ", "created_at ")}', args).fetchone()['n']
    except Exception:
        signups = 'n/a'
    db.close()

    top_s = max([r['u'] for r in src_rows] + [1])
    top_d = max([r['u'] for r in day_rows] + [1])
    sources = [{'name': r['source'], 'uniq': r['u'], 'pct': round(r['u'] / top_s * 100)} for r in src_rows]
    daily = [{'day': r['d'], 'uniq': r['u'], 'views': r['v'], 'pct': round(r['u'] / top_d * 100)} for r in day_rows]
    pages = [{'path': r['path'], 'views': r['v']} for r in page_rows]
    return render_template_string(
        VISITS_PAGE, days=days, now_n=now_n, uniq=tot['u'], views=tot['v'], signups=signups,
        total_users=total_users, sources=sources, daily=daily, pages=pages)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
