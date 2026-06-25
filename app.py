import os
import uuid
import json
import subprocess
import re
from flask import Flask, request, jsonify, send_file, render_template
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm'}

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
    """Download video from YouTube, TikTok, Instagram, etc using yt-dlp"""
    out_path = os.path.join(UPLOAD_FOLDER, f'{job_id}.mp4')
    cmd = [
        'yt-dlp',
        '--no-playlist',
        
        '--merge-output-format', 'mp4',
      '-o', out_path,
        '--no-warnings',
        '--cookies', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt'),
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if os.path.exists(out_path):
        return out_path, None
    # Try alternate filename (yt-dlp sometimes adds title)
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
        "This part will blow your mind 🤯",
        "You won't believe what happens next 👀",
        "The most viral moment 🔥",
        "Watch this until the end ✨",
        "Everyone is talking about this 💬",
        "This changed everything 🚀",
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
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    num_clips = int(request.form.get('num_clips', 3))
    clip_duration = int(request.form.get('clip_duration', 30))
    num_clips = min(max(num_clips, 1), 6)
    clip_duration = min(max(clip_duration, 15), 60)
    job_id = uuid.uuid4().hex

    video_path = None
    source_url = request.form.get('video_url', '').strip()

    # URL mode
    if source_url:
        video_path, error = download_from_url(source_url, job_id)
        if not video_path:
            return jsonify({'error': f'Could not download video: {error}'}), 400

    # File upload mode
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
def download(filename):
    safe = secure_filename(filename)
    path = os.path.join(OUTPUT_FOLDER, safe)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(path, as_attachment=True, download_name=safe)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
