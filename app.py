import os
import threading
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
from datetime import datetime
from flask import Flask, request, jsonify, send_file, render_template, redirect, url_for, session
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
DB_PATH = 'users.db'
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

def transcribe_video(video_path):
    """Transcribe the full video once. Returns a flat list of word dicts:
    [{'text': 'hello', 'start': 1.2, 'end': 1.4}, ...]. Returns [] on any
    failure so clip generation never breaks because of transcription.

    Audio is extracted with our own ffmpeg call (mono 16kHz PCM) and handed
    to Whisper as a numpy array, instead of letting faster-whisper decode
    the file itself via PyAV -- PyAV can't be reliably built/linked in this
    environment, so this sidesteps it completely.
    """
    try:
        import numpy as np
        cmd = [
            'ffmpeg', '-i', video_path,
            '-f', 's16le', '-acodec', 'pcm_s16le',
            '-ac', '1', '-ar', '16000',
            '-'
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if not result.stdout:
            print('[whisper] ffmpeg produced no audio output:', result.stderr[-500:] if result.stderr else '')
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
        print('Transcription failed:', e)
        return []
def get_clip_words(all_words, clip_start, clip_duration):
    """Slice the full-video word list down to one clip's window, with
    timestamps made relative to the clip's own start."""
    clip_end = clip_start + clip_duration
    out = []
    for w in all_words:
        if w['start'] >= clip_start and w['start'] < clip_end:
            out.append({
                'text': w['text'],
                'start': max(w['start'] - clip_start, 0),
                'end': min(w['end'] - clip_start, clip_duration),
            })
    return out

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

def download_from_url(url, job_id):
    out_path = os.path.join(UPLOAD_FOLDER, f'{job_id}.mp4')
    cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')

    cmd_primary = [
        'yt-dlp', '--no-playlist',
        '--extractor-args', 'youtube:player_client=android',
        '-f', 'best[height<=1080][ext=mp4]/best[ext=mp4]/best',
        '--merge-output-format', 'mp4',
        '-o', out_path, '--no-warnings',
        url
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
            url
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

def extract_clip(video_path, start, duration, output_path, caption="", words=None):
    target_w, target_h = 1080, 1920
    ass_path = None

    if words:
        ass_path = output_path.replace('.mp4', '.ass')
        generate_ass_captions_from_words(words, duration, ass_path)
    elif caption:
        ass_path = output_path.replace('.mp4', '.ass')
        generate_ass_captions(caption, duration, ass_path)

    if ass_path:
        escaped_ass = ass_path.replace('\\', '/').replace(':', '\\:')
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},"
            f"ass='{escaped_ass}'"
        )
    else:
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h}"
        )

    cmd = [
        'ffmpeg', '-y',
        '-ss', str(start), '-i', video_path,
        '-t', str(duration),
        '-vf', vf,
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-threads', '2',
        '-c:a', 'aac', '-b:a', '128k',
        '-movflags', '+faststart',
        output_path
    ]
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
        print("FFMPEG STDERR:", stderr[-3000:])

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

def generate_clips(video_path, num_clips=3, clip_duration=30):
    duration = get_video_duration(video_path)
    clips = []
    job_id = str(uuid.uuid4())[:8]
    scene_times = detect_scenes(video_path)

    print('[transcribe] starting full-video transcription...', flush=True)
    all_words = transcribe_video(video_path)
    print(f'[transcribe] got {len(all_words)} words', flush=True)

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

        clip_words = get_clip_words(all_words, actual_start, actual_dur)
        transcript_text = ' '.join(w['text'] for w in clip_words).strip()

        meta = generate_metadata_with_claude(transcript_text, actual_dur, i)
        title = meta['title']
        caption = meta['caption']
        hashtags = meta['hashtags']
        virality_score = meta['virality_score']
        virality_reason = meta['reasoning']

        out_filename = f"clip_{job_id}_{i+1}.mp4"
        out_path = os.path.join(OUTPUT_FOLDER, out_filename)
        success = extract_clip(video_path, actual_start, actual_dur, out_path, caption, words=clip_words)
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

    session['user'] = {'id': user_id, 'email': email, 'name': name, 'picture': picture}
    try:
        send_login_notification(email, name, request)
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
    from paypal_subscriptions import get_user_plan_limits
    _limits = get_user_plan_limits(current_user_id())
    num_clips = min(max(num_clips, 1), _limits['max_clips'])
    clip_duration = min(max(clip_duration, 15), _limits['max_duration'])
    job_id = uuid.uuid4().hex
    user_id = current_user_id()

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
            clips = generate_clips(video_path, num_clips=num_clips, clip_duration=clip_duration)
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

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
