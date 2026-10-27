import os, sys
P = os.path.join('templates', 'index.html')
if not os.path.exists(P):
    print('FAILED: run inside clipforge-final folder'); sys.exit(1)
s = open(P, encoding='utf-8', newline='').read()
a_old = 'placeholder="Paste YouTube, TikTok, Instagram, or any video URL..."'
a_new = 'placeholder="Or paste a TikTok / Instagram / Vimeo link (YouTube links may fail - upload instead)"'
b_old = 'Supports <b>YouTube</b> &middot; <b>TikTok</b> &middot; <b>Instagram</b> &middot; <b>Facebook</b> &middot; <b>Twitter/X</b> &middot; <b>Vimeo</b> and 1000+ more'
b_new = '<b>Best way:</b> upload your video file above (always works). Links from TikTok, Instagram, Facebook or Vimeo may work. YouTube links are often blocked &mdash; save the video to your device and upload it.'
changed = False
for old, new, n in ((a_old, a_new, 'placeholder'), (b_old, b_new, 'supports line')):
    if new in s: print('SKIP', n); continue
    if s.count(old) != 1: print('FAILED', n, s.count(old)); continue
    s = s.replace(old, new); changed = True; print('OK', n)
if changed:
    open(P + '.bak', 'w', encoding='utf-8', newline='').write(open(P, encoding='utf-8', newline='').read())
    open(P, 'w', encoding='utf-8', newline='').write(s)
