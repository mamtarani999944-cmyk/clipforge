"""
Security hardening for ClipForge. Fixes:

  1. A logged-in user could paste a special "video link" like  --version
     and make the video downloader (yt-dlp) treat it as a command option.
     Now only normal http/https links are accepted, and the link is passed
     safely (after "--").
  2. The downloader could be pointed at internal/private addresses
     (localhost, your server's private network). These are now blocked.
  3. Text made by the AI (titles, captions) was put into the page without
     escaping, so a crafted video could inject code into the page. It is
     now escaped on the Dashboard, My Clips and Analytics pages.
  4. Adds standard browser security headers (anti-clickjacking, etc).

Run from your clipforge-final project folder:
    python fix_security_hardening.py
"""
import re

problems = []
changed = []

# ---------------------------------------------------------------- app.py
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

if '_validate_video_url' in src:
    print('SKIP: app.py hardening already present')
else:
    # 1 + 2: validate URL and separate it from options
    old_def = 'def download_from_url(url, job_id):\n'
    new_def = '''def _validate_video_url(url):
    """Returns None if the URL is safe to hand to yt-dlp, else a reason string."""
    from urllib.parse import urlparse
    import socket, ipaddress
    if not isinstance(url, str) or url.startswith('-') or any(c.isspace() for c in url) or len(url) > 2000:
        return 'invalid url'
    try:
        u = urlparse(url)
    except Exception:
        return 'invalid url'
    if u.scheme not in ('http', 'https') or not u.hostname:
        return 'invalid url'
    try:
        for info in socket.getaddrinfo(u.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                return 'blocked address'
    except Exception:
        return 'could not resolve host'
    return None

def download_from_url(url, job_id):
    _bad = _validate_video_url(url)
    if _bad:
        return None, _bad
'''
    if src.count(old_def) == 1:
        src = src.replace(old_def, new_def, 1)
    else:
        problems.append(f'app.py: download_from_url definition found {src.count(old_def)} times (expected 1)')

    pattern = re.compile(r'^([ \t]+)url\n([ \t]+)\]', flags=re.MULTILINE)
    if len(pattern.findall(src)) == 2:
        src = pattern.sub(lambda m: f"{m.group(1)}'--', url\n{m.group(2)}]", src)
    else:
        problems.append(f'app.py: yt-dlp url lines found {len(pattern.findall(src))} times (expected 2)')

    # 4: security headers
    anchor = "if __name__ == '__main__':"
    headers = '''@app.after_request
def _security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
    return resp

'''
    if src.count(anchor) == 1:
        src = src.replace(anchor, headers + anchor, 1)
    else:
        problems.append(f'app.py: main block found {src.count(anchor)} times (expected 1)')

    if not any(p.startswith('app.py') for p in problems):
        with open('app.py', 'w', encoding='utf-8') as f:
            f.write(src)
        changed.append('app.py: links are validated and passed safely, private addresses blocked, security headers added')

# ------------------------------------------------------------- templates
ESC_JS = ("\nfunction esc(s){return String(s==null?'':s).replace(/[&<>\"']/g,function(c){"
          "return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c];});}\n")

def patch_template(path, edits):
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()
    if 'function esc(' in html:
        print(f'SKIP: {path} already escapes text')
        return
    for old, new in edits:
        if html.count(old) != 1:
            problems.append(f'{path}: expected 1 match, found {html.count(old)} for: {old[:60]}...')
            return
    if '<script>' not in html:
        problems.append(f'{path}: no <script> tag found')
        return
    for old, new in edits:
        html = html.replace(old, new, 1)
    html = html.replace('<script>', '<script>' + ESC_JS, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    changed.append(f'{path}: AI text is now escaped')

patch_template('templates/index.html', [
    ('<div class="clip-caption-text">${clip.caption}</div>',
     '<div class="clip-caption-text">${esc(clip.caption)}</div>'),
    ('''onclick="shareClip('${clip.download_url}', '${clip.caption}')"''',
     '''data-caption="${esc(clip.caption)}" onclick="shareClip('${clip.download_url}', this.dataset.caption)"'''),
])
patch_template('templates/my_clips.html', [
    ('<div class="clip-title-text">${clip.title}</div>',
     '<div class="clip-title-text">${esc(clip.title)}</div>'),
    ('<div class="clip-caption-text" title="${clip.caption}">${clip.caption}</div>',
     '<div class="clip-caption-text" title="${esc(clip.caption)}">${esc(clip.caption)}</div>'),
])
patch_template('templates/analytics.html', [
    ('<div class="caption-cell" title="${clip.caption}">${clip.caption}</div>',
     '<div class="caption-cell" title="${esc(clip.caption)}">${esc(clip.caption)}</div>'),
])

for c in changed:
    print('OK:', c)
for p in problems:
    print('FAILED:', p)
if problems:
    print('Some parts failed. Tell me and I will fix them.')
