import os, sys
P = 'app.py'
if not os.path.exists(P):
    print('FAILED: run inside clipforge-final folder'); sys.exit(1)
s = open(P, encoding='utf-8', newline='').read()
nl = '\r\n' if '\r\n' in s else '\n'
s = s.replace('\r\n', '\n')
if 'YT_PROXY' in s:
    print('SKIP: proxy already applied'); sys.exit(0)

a1 = "    cmd_primary = [\n"
a2 = "    result = subprocess.run(cmd_primary, capture_output=True, text=True, timeout=300)\n"
if s.count(a1) != 1 or s.count(a2) != 1:
    print('FAILED: anchors', s.count(a1), s.count(a2)); sys.exit(1)

proxy_block = (
"    result = None\n"
"    _yt_proxy = os.environ.get('YT_PROXY', '').strip()\n"
"    if _yt_proxy:\n"
"        # residential proxy: keeps YouTube from blocking the Railway server\n"
"        # (720p cap keeps proxy bandwidth low)\n"
"        cmd_proxy = [\n"
"            'yt-dlp', '--no-playlist', '--proxy', _yt_proxy,\n"
"            '-f', 'best[height<=720][ext=mp4]/best[height<=720]/best',\n"
"            '--merge-output-format', 'mp4',\n"
"            '-o', out_path, '--no-warnings', '--socket-timeout', '30',\n"
"            '--', url\n"
"        ]\n"
"        try:\n"
"            result = subprocess.run(cmd_proxy, capture_output=True, text=True, timeout=300)\n"
"        except Exception as _e:\n"
"            print('[download] proxy attempt failed:', _e, flush=True)\n"
"\n")
s = s.replace(a1, proxy_block + a1, 1)
s = s.replace(a2,
"    if not os.path.exists(out_path) and not any(f.startswith(job_id) and f.endswith('.mp4') for f in os.listdir(UPLOAD_FOLDER)):\n"
"        result = subprocess.run(cmd_primary, capture_output=True, text=True, timeout=300)\n", 1)
# safe error line if result is None
old_e = "    error = result.stderr.strip().split('\\n')[-1] if result.stderr else 'Download failed'\n"
if s.count(old_e) == 1:
    s = s.replace(old_e, "    error = result.stderr.strip().split('\\n')[-1] if (result is not None and result.stderr) else 'Download failed'\n")
else:
    print('WARN: error line anchor not found')
open(P + '.bak_proxy', 'w', encoding='utf-8', newline='').write(open(P, encoding='utf-8', newline='').read())
open(P, 'w', encoding='utf-8', newline='').write(s.replace('\n', nl))
import py_compile; py_compile.compile(P, doraise=True); print('OK: proxy support added, syntax OK')
