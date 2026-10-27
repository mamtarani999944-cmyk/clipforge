import os, re, sys
ok = True
# ---- requirements.txt: yt-dlp needs its JS-challenge solver + a JS runtime for YouTube
R = 'requirements.txt'
if os.path.exists(R):
    lines = open(R, encoding='utf-8').read().splitlines()
    out, seen_y, seen_d = [], False, False
    for l in lines:
        st = l.strip().lower()
        if st.startswith('yt-dlp'):
            if not seen_y:
                out.append('yt-dlp[default]'); seen_y = True
            continue
        if st.startswith('deno'):
            seen_d = True
        out.append(l)
    if not seen_y: out.append('yt-dlp[default]')
    if not seen_d: out.append('deno')
    new = '\n'.join(out) + '\n'
    if new != open(R, encoding='utf-8').read():
        open(R + '.bak_yt', 'w', encoding='utf-8').write(open(R, encoding='utf-8').read())
        open(R, 'w', encoding='utf-8').write(new); print('OK requirements.txt: yt-dlp[default] + deno')
    else:
        print('SKIP requirements.txt already fine')
else:
    print('FAILED: requirements.txt not found (run inside clipforge-final)'); ok = False

# ---- app.py
P = 'app.py'
s = open(P, encoding='utf-8', newline='').read()
nl = '\r\n' if '\r\n' in s else '\n'
s = s.replace('\r\n', '\n'); changed = False
android = "        '--extractor-args', 'youtube:player_client=android',\n"
if android in s:
    if s.count(android) != 1: print('FAILED: android anchor count', s.count(android)); ok = False
    else:
        s = s.replace(android, ''); changed = True; print("OK app.py: removed forced 'android' client (now blocked by YouTube)")
else:
    print('SKIP app.py: android client already removed')
if '[download] yt-dlp failed' in s:
    print('SKIP app.py: logging already added')
else:
    m = re.findall(r"^    error = result.*$", s, flags=re.M)
    if len(m) != 1: print('FAILED: error-line anchor count', len(m)); ok = False
    else:
        s = s.replace(m[0], "    print('[download] yt-dlp failed:', (result.stderr or '')[-1200:] if result is not None else '', flush=True)\n" + m[0], 1)
        changed = True; print('OK app.py: yt-dlp error details now logged')
if changed:
    open(P + '.bak_yt', 'w', encoding='utf-8', newline='').write(open(P, encoding='utf-8', newline='').read())
    open(P, 'w', encoding='utf-8', newline='').write(s.replace('\n', nl))
    import py_compile; py_compile.compile(P, doraise=True); print('syntax OK')
sys.exit(0 if ok else 1)
