import re, sys, os
P = 'app.py'
if not os.path.exists(P):
    print('FAILED: run this inside the clipforge-final folder'); sys.exit(1)
s = open(P, encoding='utf-8', newline='').read()
nl = '\r\n' if '\r\n' in s else '\n'
s = s.replace('\r\n', '\n')
changed = False

# 1) never let the transcription window start below 0
old1 = "        actual_start = min(max(start, 0), duration - clip_duration)\n"
new1 = "        actual_start = max(0, min(max(start, 0), duration - clip_duration))\n"
if new1 in s:
    print('SKIP 1: start clamp already applied')
elif s.count(old1) == 1:
    s = s.replace(old1, new1); changed = True; print('OK 1: start clamp')
else:
    print('FAILED 1: anchor not found (%d)' % s.count(old1))

# 2) voice-over on clips that have no audio track
pat = re.compile(r"(        filter_complex = \(\n            f\"\[0:a\]volume=0\.25)")
marker = 'no audio track'
if marker in s:
    print('SKIP 2: no-audio voice-over already applied')
else:
    m = pat.findall(s)
    if len(m) != 1:
        print('FAILED 2: anchor found %d times' % len(m))
    else:
        block = (
"        # clip has no audio track -> use the voice-over as the only audio\n"
"        _a = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a',\n"
"                             '-show_entries', 'stream=index', '-of', 'csv=p=0', clip_path],\n"
"                            capture_output=True, text=True, timeout=30).stdout.strip()\n"
"        if not _a:\n"
"            print('[voiceover] source has no audio track, using voice-over only', flush=True)\n"
"            _r = subprocess.run(['ffmpeg', '-y', '-i', clip_path, '-i', voiceover_path,\n"
"                                 '-map', '0:v', '-map', '1:a', '-c:v', 'copy',\n"
"                                 '-c:a', 'aac', '-b:a', '128k', '-shortest', output_path],\n"
"                                capture_output=True, text=True, timeout=60)\n"
"            _ok = os.path.exists(output_path) and os.path.getsize(output_path) > 1000\n"
"            if not _ok:\n"
"                print('[voiceover] STDERR:', (_r.stderr or '')[-1500:], flush=True)\n"
"            return _ok\n\n")
        s = pat.sub(lambda mo: block + mo.group(1), s, count=1); changed = True
        print('OK 2: voice-over without audio track')

if changed:
    open(P + '.bak_vo', 'w', encoding='utf-8', newline='').write(open(P, encoding='utf-8', newline='').read())
    open(P, 'w', encoding='utf-8', newline='').write(s.replace('\n', nl))
    import py_compile; py_compile.compile(P, doraise=True); print('syntax OK')
