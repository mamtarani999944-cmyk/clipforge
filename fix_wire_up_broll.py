"""
Part 2 of 2 for AI B-Roll. Run fix_add_ai_broll.py FIRST.

This part wires the helper functions added by fix_add_ai_broll.py into the
actual clip-generation pipeline:
  - extract_clip(): branches into the multi-input B-roll ffmpeg pipeline
    when broll_enabled=True and B-roll was successfully fetched, and falls
    back automatically (re-running the exact old single-input pipeline) if
    anything about the B-roll pipeline fails.
  - generate_clips(): accepts and threads through a broll_enabled flag.
  - /upload route: reads a new 'broll' form field and passes it through.
  - templates/index.html: adds the "AI B-Roll" checkbox and wires it into
    the upload request.

Run from your clipforge-final project folder, in this order:
    python fix_add_ai_broll.py
    python fix_wire_up_broll.py
"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

changed = []

# 1. Replace extract_clip() entirely with a version that can branch into the
#    multi-input B-roll pipeline, and falls back to the old plain pipeline
#    automatically if B-roll fetch or render fails for any reason.
old_extract_clip = '''def extract_clip(video_path, start, duration, output_path, caption="", words=None):
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

    if ass_path:
        escaped_ass = ass_path.replace('\\\\', '/').replace(':', '\\\\:')
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"{crop_part},"
            f"ass='{escaped_ass}'"
        )
    else:
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"{crop_part}"
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

    return ok'''

new_extract_clip = '''def extract_clip(video_path, start, duration, output_path, caption="", words=None, transcript_text="", broll_enabled=False):
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
            escaped_ass = ass_path.replace('\\\\', '/').replace(':', '\\\\:')
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
        escaped_ass = ass_path.replace('\\\\', '/').replace(':', '\\\\:') if ass_path else None
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

    return ok'''

if old_extract_clip in src:
    src = src.replace(old_extract_clip, new_extract_clip, 1)
    changed.append('extract_clip() now supports the AI B-roll pipeline with automatic fallback')
elif 'def extract_clip(video_path, start, duration, output_path, caption="", words=None, transcript_text="", broll_enabled=False):' in src:
    print('SKIP: extract_clip() already patched')
else:
    print('FAILED: could not find the exact extract_clip() to replace. No changes made there. Did fix_add_ai_broll.py run first?')

# 2. generate_clips(): accept broll_enabled and thread it through.
old_sig = "def generate_clips(video_path, num_clips=3, clip_duration=30):"
new_sig = "def generate_clips(video_path, num_clips=3, clip_duration=30, broll_enabled=False):"
if old_sig in src:
    src = src.replace(old_sig, new_sig, 1)
    changed.append('generate_clips() accepts broll_enabled')
elif new_sig in src:
    print('SKIP: generate_clips() signature already patched')
else:
    print('FAILED: could not find generate_clips() signature. No changes made there.')

old_call = "success = extract_clip(video_path, actual_start, actual_dur, out_path, caption, words=clip_words)"
new_call = "success = extract_clip(video_path, actual_start, actual_dur, out_path, caption, words=clip_words, transcript_text=transcript_text, broll_enabled=broll_enabled)"
if old_call in src:
    src = src.replace(old_call, new_call, 1)
    changed.append('generate_clips() passes transcript_text/broll_enabled into extract_clip()')
elif new_call in src:
    print('SKIP: extract_clip() call site already patched')
else:
    print('FAILED: could not find the extract_clip() call inside generate_clips(). No changes made there.')

# 3. /upload route: read the new 'broll' checkbox field and pass it through.
old_upload_read = """    num_clips = int(request.form.get('num_clips', 3))
    clip_duration = int(request.form.get('clip_duration', 30))"""
new_upload_read = """    num_clips = int(request.form.get('num_clips', 3))
    clip_duration = int(request.form.get('clip_duration', 30))
    broll_enabled = request.form.get('broll', 'false').lower() in ('1', 'true', 'on', 'yes')"""
if old_upload_read in src:
    src = src.replace(old_upload_read, new_upload_read, 1)
    changed.append("added broll_enabled = request.form.get('broll', ...) in /upload")
elif 'broll_enabled = request.form.get(' in src:
    print('SKIP: /upload already reads the broll field')
else:
    print('FAILED: could not find the num_clips/clip_duration read in /upload. No changes made there.')

old_run_job_call = "clips = generate_clips(video_path, num_clips=num_clips, clip_duration=clip_duration)"
new_run_job_call = "clips = generate_clips(video_path, num_clips=num_clips, clip_duration=clip_duration, broll_enabled=broll_enabled)"
if old_run_job_call in src:
    src = src.replace(old_run_job_call, new_run_job_call, 1)
    changed.append('/upload passes broll_enabled into generate_clips()')
elif new_run_job_call in src:
    print('SKIP: generate_clips() call in /upload already patched')
else:
    print('FAILED: could not find the generate_clips() call inside /upload. No changes made there.')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

for c in changed:
    print('OK:', c)

# 4. templates/index.html: add the checkbox UI and wire it into the upload request.
html_path = 'templates/index.html'
with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()

html_changed = []

old_settings_close = '''        <div class="setting-box">
          <label class="setting-label">Duration &mdash; <span id="durVal">30s</span></label>
          <div class="range-row">
            <input type="range" id="clipDur" min="15" max="60" step="5" value="30"
              oninput="document.getElementById('durVal').textContent=this.value+'s'" />
          </div>
        </div>
      </div>
'''
new_settings_close = '''        <div class="setting-box">
          <label class="setting-label">Duration &mdash; <span id="durVal">30s</span></label>
          <div class="range-row">
            <input type="range" id="clipDur" min="15" max="60" step="5" value="30"
              oninput="document.getElementById('durVal').textContent=this.value+'s'" />
          </div>
        </div>
      </div>

      <div class="setting-box" style="margin-top:10px">
        <label class="setting-label" style="display:flex;align-items:center;gap:8px;cursor:pointer">
          <input type="checkbox" id="brollToggle" style="width:auto" />
          AI B-Roll &mdash; auto-insert short stock footage over key moments
        </label>
      </div>
'''
if old_settings_close in html:
    html = html.replace(old_settings_close, new_settings_close, 1)
    html_changed.append('added the AI B-Roll checkbox to the settings panel')
elif 'id="brollToggle"' in html:
    print('SKIP: brollToggle checkbox already present')
else:
    print('FAILED: could not find the settings-row closing block in index.html. No changes made there.')

old_form_build = """    form.append('num_clips', document.getElementById('numClips').value);
    form.append('clip_duration', document.getElementById('clipDur').value);"""
new_form_build = """    form.append('num_clips', document.getElementById('numClips').value);
    form.append('clip_duration', document.getElementById('clipDur').value);
    form.append('broll', document.getElementById('brollToggle') && document.getElementById('brollToggle').checked ? 'true' : 'false');"""
if old_form_build in html:
    html = html.replace(old_form_build, new_form_build, 1)
    html_changed.append('generateClips() now sends the broll flag with the upload request')
elif "form.append('broll'" in html:
    print('SKIP: upload JS already sends the broll flag')
else:
    print('FAILED: could not find the FormData build block in index.html. No changes made there.')

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)

for c in html_changed:
    print('OK:', c)

print('')
print('Verify with:')
print('''python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('app.py SYNTAX OK')"''')
print('')
print('IMPORTANT: AI B-Roll also needs a free Pexels API key.')
print('1. Sign up at https://www.pexels.com/api/ (free, instant)')
print("2. In Railway, add a Variable: PEXELS_API_KEY = <your key>")
print('Without it, the AI B-Roll checkbox simply does nothing (clips generate normally, no B-roll) -- nothing breaks.')
