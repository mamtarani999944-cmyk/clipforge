"""
Changes the YouTube download error so people know what to do:
  "YouTube is blocking links right now. Please download the video and use Upload instead."

Run from your clipforge-final folder:  python fix_download_message.py
"""
OLD = "This video couldn't be downloaded. It may be restricted, age-limited, or protected by YouTube. Please try a different video."
NEW = "We couldn't download this link (YouTube often blocks link downloads). Please save the video to your device and use the Upload button instead."

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()
n = src.count(OLD)
if n == 0:
    if NEW in src:
        print('SKIP: message already changed')
    else:
        print('FAILED, nothing changed: the old message was not found in app.py')
    raise SystemExit
with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src.replace(OLD, NEW))
print(f'OK: changed the download error message ({n} place(s))')
