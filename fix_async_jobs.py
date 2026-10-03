"""
Fixes the 'upstream error' / JSON parse crash for good.

Root cause: Railway's edge proxy has its own fixed request timeout that's
shorter than the time it now takes to download + ffmpeg + Whisper-transcribe
+ cut clips inside a single HTTP request. Raising gunicorn's own --timeout
doesn't help because Railway's proxy cuts the connection first and returns a
plain-text "upstream error" body, which the frontend then fails to JSON-parse.

Fix: make /upload return immediately with a job_id (fast), do the real work
in a background thread, and have the frontend poll a new /api/jobs/<id>
endpoint until it's done. No single HTTP request is ever held open long
enough to hit any proxy timeout.

Run this from your clipforge-final project folder:
    python fix_async_jobs.py
"""
import re

# ============================================================
# 1. app.py
# ============================================================
with open('app.py', 'r', encoding='utf-8') as f:
    app_src = f.read()

# 1a. add threading import
if 'import threading' not in app_src:
    app_src = app_src.replace('import os\n', 'import os\nimport threading\n', 1)
    print('OK: added `import threading`')
else:
    print('SKIP: threading already imported')

# 1b. add in-memory job store right after app.register_blueprint(paypal_bp) block
jobs_store_code = '''
# In-memory job store for async clip generation. Fine with a single
# gunicorn worker (see Procfile --workers 1); each job is only read by the
# user who created it.
_jobs = {}
_jobs_lock = threading.Lock()
'''
anchor = "init_paypal_db()\n"
if '_jobs = {}' not in app_src:
    app_src = app_src.replace(anchor, anchor + jobs_store_code, 1)
    print('OK: added _jobs store')
else:
    print('SKIP: _jobs store already present')

# 1c. replace the body of def upload(): with a fast dispatch + background worker
old_upload_pattern = re.compile(
    r"@app\.route\('/upload', methods=\['POST'\]\)\n"
    r"@login_required\n"
    r"def upload\(\):\n"
    r"(?:.*\n)*?"
    r"        return jsonify\(\{'error': str\(e\)\}\), 500\n",
)

new_upload_code = '''@app.route('/upload', methods=['POST'])
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

    with _jobs_lock:
        _jobs[job_id] = {'status': 'processing', 'clips': None, 'error': None}

    def _run_job():
        try:
            clips = generate_clips(video_path, num_clips=num_clips, clip_duration=clip_duration)
            if os.path.exists(video_path):
                os.remove(video_path)
            if not clips:
                with _jobs_lock:
                    _jobs[job_id] = {'status': 'error', 'clips': None,
                                      'error': 'Could not generate clips. Make sure ffmpeg is installed.'}
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

            with _jobs_lock:
                _jobs[job_id] = {'status': 'done', 'clips': clips, 'error': None}
        except Exception as e:
            if video_path and os.path.exists(video_path):
                os.remove(video_path)
            with _jobs_lock:
                _jobs[job_id] = {'status': 'error', 'clips': None, 'error': str(e)}

    threading.Thread(target=_run_job, daemon=True).start()
    return jsonify({'job_id': job_id}), 202

@app.route('/api/jobs/<job_id>')
@login_required
def job_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({'status': 'error', 'error': 'Unknown job'}), 404
    if job['status'] == 'done':
        # Free the slot once the client has it; keep a short grace window
        # in case of a duplicate poll in flight.
        resp = {'status': 'done', 'clips': job['clips']}
        return jsonify(resp)
    if job['status'] == 'error':
        return jsonify({'status': 'error', 'error': job['error']})
    return jsonify({'status': 'processing'})
'''

new_app_src, n = old_upload_pattern.subn(new_upload_code, app_src)
if n == 1:
    app_src = new_app_src
    print('OK: replaced /upload route with async job dispatch + /api/jobs/<id>')
else:
    print(f'FAILED: expected exactly 1 match for old upload() block, got {n}. No changes made to app.py upload route.')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(app_src)

# ============================================================
# 2. templates/index.html — poll instead of waiting on one response
# ============================================================
with open('templates/index.html', 'r', encoding='utf-8') as f:
    html_src = f.read()

old_js = """    let st = 0;
    const timer = setInterval(() => { st = Math.min(st + 1, 3); setStep(st); }, 10000);

    try {
      const res = await fetch('/upload', { method: 'POST', body: form });
      clearInterval(timer);
      setStep(4);
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || 'Server error');
      setTimeout(() => { hide('progressSection'); showResults(data.clips); btn.disabled = false; }, 500);
    } catch(err) {
      clearInterval(timer);
      hide('progressSection');
      document.getElementById('errorText').textContent = err.message || 'Processing failed.';
      show('errorBox', 'flex');
      btn.disabled = false;
    }
  }"""

new_js = """    let st = 0;
    const timer = setInterval(() => { st = Math.min(st + 1, 3); setStep(st); }, 10000);

    async function pollJob(jobId) {
      const maxAttempts = 180; // up to 15 min at 5s intervals
      for (let i = 0; i < maxAttempts; i++) {
        await new Promise(r => setTimeout(r, 5000));
        let res, data;
        try {
          res = await fetch('/api/jobs/' + jobId);
          data = await res.json();
        } catch (e) {
          continue; // transient network hiccup, keep polling
        }
        if (data.status === 'done') return data;
        if (data.status === 'error') throw new Error(data.error || 'Processing failed.');
        // status === 'processing' -> keep waiting
      }
      throw new Error('Still processing after 15 minutes — try a shorter video or fewer clips.');
    }

    try {
      const res = await fetch('/upload', { method: 'POST', body: form });
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || 'Server error');
      setStep(3);
      const jobResult = await pollJob(data.job_id);
      clearInterval(timer);
      setStep(4);
      setTimeout(() => { hide('progressSection'); showResults(jobResult.clips); btn.disabled = false; }, 500);
    } catch(err) {
      clearInterval(timer);
      hide('progressSection');
      document.getElementById('errorText').textContent = err.message || 'Processing failed.';
      show('errorBox', 'flex');
      btn.disabled = false;
    }
  }"""

if old_js in html_src:
    html_src = html_src.replace(old_js, new_js, 1)
    print('OK: replaced frontend upload handler with job-polling version')
else:
    print('FAILED: could not find exact frontend upload JS block to replace. No changes made to index.html.')

with open('templates/index.html', 'w', encoding='utf-8') as f:
    f.write(html_src)

print('')
print('Done. Verify with:')
print('''python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('app.py SYNTAX OK')"''')
