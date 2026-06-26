import os
import uuid
import json
import subprocess
import re
import sqlite3
import hashlib
import secrets
from flask import Flask, request, jsonify, send_file, render_template, session, redirect, url_for
from werkzeug.utils import secure_filename
import requests as http_requests

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
DB_PATH = 'users.db'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', 'YOUR_GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', 'YOUR_GOOGLE_CLIENT_SECRET')
GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI', 'https://clipforge-production-5682.up.railway.app/auth/google/callback')

ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm'}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT,
        google_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

init_db()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_by_email(email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE email = ?', (email,))
    user = c.fetchone()
    conn.close()
    return user

def get_user_by_google_id(google_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE google_id = ?', (google_id,))
    user = c.fetchone()
    conn.close()
    return user

def create_user(name, email, password=None, google_id=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    pw_hash = hash_password(password) if password else None
    c.execute('INSERT INTO users (name, email, password_hash, google_id) VALUES (?, ?, ?, ?)',
              (name, email, pw_hash, google_id))
    conn.commit()
    user_id = c.lastrowid
    conn.close()
    return user_id

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

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
    cmd = [
        'yt-dlp', '--no-playlist',
        '--merge-output-format', 'mp4',
        '-o', out_path,
        '--no-warnings',
        '--cookies', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt'),
        '--extractor-args', 'youtube:player_client=web',
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
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

def extract_clip(video_path, start, duration, output_path, caption=""):
    target_w, target_h = 1080, 1920
    if caption:
        safe_caption = caption.replace("'", "\\'").replace(":", "\\:")
        words = safe_caption.split()
        lines, line = [], []
        for word in words:
            line.append(word)
            if len(' '.join(line)) > 25:
                lines.append(' '.join(line[:-1]))
                line = [word]
        if line:
            lines.append(' '.join(line))
        caption_text = '\\n'.join(lines[:3])
        drawtext = (
            f"drawtext=text='{caption_text}'"
            f":fontsize=54:fontcolor=white"
            f":bordercolor=black:borderw=3"
            f":x=(w-text_w)/2:y=h-text_h-90"
            f":line_spacing=8"
        )
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},{drawtext}"
        )
    else:
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h}"
        )
    cmd = [
        'ffmpeg', '-y',
        '-ss', str(start),
        '-i', video_path,
        '-t', str(duration),
        '-vf', vf,
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-c:a', 'aac', '-b:a', '128k',
        '-movflags', '+faststart',
        output_path
    ]
    subprocess.run(cmd, capture_output=True)
    return os.path.exists(output_path)

def generate_clips(video_path, num_clips=3, clip_duration=30):
    duration = get_video_duration(video_path)
    clips = []
    job_id = str(uuid.uuid4())[:8]
    scene_times = detect_scenes(video_path)
    captions = [
        "This part will blow your mind",
        "You won't believe what happens next",
        "The most viral moment",
        "Watch this until the end",
        "Everyone is talking about this",
        "This changed everything",
    ]
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
        caption = captions[i % len(captions)]
        out_filename = f"clip_{job_id}_{i+1}.mp4"
        out_path = os.path.join(OUTPUT_FOLDER, out_filename)
        success = extract_clip(video_path, actual_start, actual_dur, out_path, caption)
        if success:
            clips.append({
                'id': i + 1,
                'filename': out_filename,
                'caption': caption,
                'start': round(actual_start, 1),
                'duration': round(actual_dur, 1),
                'download_url': f'/download/{out_filename}'
            })
    return clips

@app.route('/')
@login_required
def index():
    return render_template('index.html', user_name=session.get('user_name', ''))

@app.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect('/')
    error = request.args.get('error', '')
    return render_template('login.html', error=error)

@app.route('/login', methods=['POST'])
def login_post():
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    user = get_user_by_email(email)
    if not user or user[3] != hash_password(password):
        return redirect('/login?error=Invalid email or password')
    session['user_id'] = user[0]
    session['user_name'] = user[1] or email.split('@')[0]
    session['user_email'] = user[2]
    return redirect('/')

@app.route('/signup')
def signup_page():
    if 'user_id' in session:
        return redirect('/')
    error = request.args.get('error', '')
    return render_template('signup.html', error=error)

@app.route('/signup', methods=['POST'])
def signup_post():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    if len(password) < 6:
        return redirect('/signup?error=Password must be at least 6 characters')
    if get_user_by_email(email):
        return redirect('/signup?error=An account with this email already exists')
    try:
        user_id = create_user(name, email, password=password)
        session['user_id'] = user_id
        session['user_name'] = name or email.split('@')[0]
        session['user_email'] = email
        return redirect('/')
    except Exception as e:
        return redirect(f'/signup?error=Something went wrong. Try again.')

@app.route('/auth/google')
def google_auth():
    state = secrets.token_hex(16)
    session['oauth_state'] = state
    params = {
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'online'
    }
    from urllib.parse import urlencode
    url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params)
    return redirect(url)

@app.route('/auth/google/callback')
def google_callback():
    error = request.args.get('error')
    if error:
        return redirect('/login?error=Google sign-in was cancelled')
    code = request.args.get('code')
    state = request.args.get('state')
    if not code or state != session.get('oauth_state'):
        return redirect('/login?error=Invalid OAuth state')
    token_resp = http_requests.post('https://oauth2.googleapis.com/token', data={
        'code': code,
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'grant_type': 'authorization_code'
    })
    token_data = token_resp.json()
    access_token = token_data.get('access_token')
    if not access_token:
        return redirect('/login?error=Could not get Google access token')
    userinfo_resp = http_requests.get('https://www.googleapis.com/oauth2/v2/userinfo',
                                      headers={'Authorization': f'Bearer {access_token}'})
    userinfo = userinfo_resp.json()
    google_id = userinfo.get('id')
    email = userinfo.get('email', '').lower()
    name = userinfo.get('name', '')
    if not email:
        return redirect('/login?error=Could not get email from Google')
    user = get_user_by_google_id(google_id)
    if not user:
        user = get_user_by_email(email)
        if user:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('UPDATE users SET google_id = ? WHERE email = ?', (google_id, email))
            conn.commit()
            conn.close()
        else:
            user_id = create_user(name, email, google_id=google_id)
            session['user_id'] = user_id
            session['user_name'] = name
            session['user_email'] = email
            return redirect('/')
    session['user_id'] = user[0]
    session['user_name'] = user[1] or name
    session['user_email'] = user[2]
    return redirect('/')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/upload', methods=['POST'])
@login_required
def upload():
    num_clips = int(request.form.get('num_clips', 3))
    clip_duration = int(request.form.get('clip_duration', 30))
    num_clips = min(max(num_clips, 1), 6)
    clip_duration = min(max(clip_duration, 15), 60)
    job_id = uuid.uuid4().hex
    video_path = None
    source_url = request.form.get('video_url', '').strip()

    if source_url:
        video_path, error = download_from_url(source_url, job_id)
        if not video_path:
            return jsonify({'error': f'Could not download video: {error}'}), 400
    elif 'video' in request.files and request.files['video'].filename:
        file = request.files['video']
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type'}), 400
        filename = f"{job_id}_{secure_filename(file.filename)}"
        video_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(video_path)
    else:
        return jsonify({'error': 'Please upload a video file or paste a URL'}), 400

    try:
        clips = generate_clips(video_path, num_clips=num_clips, clip_duration=clip_duration)
        os.remove(video_path)
        if not clips:
            return jsonify({'error': 'Could not generate clips. Make sure ffmpeg is installed.'}), 500
        return jsonify({'clips': clips})
    except Exception as e:
        if video_path and os.path.exists(video_path):
            os.remove(video_path)
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
@login_required
def download(filename):
    safe = secure_filename(filename)
    path = os.path.join(OUTPUT_FOLDER, safe)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(path, as_attachment=True, download_name=safe)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
