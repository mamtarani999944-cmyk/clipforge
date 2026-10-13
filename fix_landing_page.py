"""
Adds a public HOME PAGE at  viralcut.xyz  for people who are not logged in.

Before: new visitors landed straight on a Google login box with no
explanation, so they left (205 login views, 0 signups).
Now: they see what ViralCut does, the 3 steps, the features, and a
"Try free" button that goes to the same login.

Logged-in users are NOT affected (they still go to the Dashboard).

Run from your clipforge-final project folder:
    python fix_landing_page.py
"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

if '_landing_page' in src:
    print('SKIP: landing page already present')
    raise SystemExit

anchor = "if __name__ == '__main__':"
if src.count(anchor) != 1:
    print(f'FAILED, nothing was changed: main block found {src.count(anchor)} times (expected 1)')
    raise SystemExit

block = """# ── Public home page (shown to logged-out visitors at "/") ───────────────────
LANDING_PAGE = '''<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ViralCut - Turn long videos into viral short clips with AI</title>
<meta name="description" content="Paste a YouTube link or upload a video. AI finds the best moments and cuts vertical clips with captions and hashtags. Add B-roll and voice-over, then publish to YouTube. 3 free clips every day.">
<meta property="og:title" content="ViralCut - Turn long videos into viral short clips with AI">
<meta property="og:description" content="AI finds the best moments in your video and makes ready short clips with captions and hashtags. 3 free clips every day.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://viralcut.xyz/">
<link rel="canonical" href="https://viralcut.xyz/">
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
<link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">
<style>
  :root{--bg:#0d0d0d;--card:#171717;--line:#2a2a2a;--text:#f5f5f5;--mute:#a3a3a3;--o1:#fbb01e;--o2:#f97316}
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.5}
  a{color:inherit}
  .wrap{max-width:1040px;margin:0 auto;padding:0 20px}
  nav{display:flex;align-items:center;justify-content:space-between;padding:18px 0}
  .brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:20px;text-decoration:none}
  .brand img{width:30px;height:30px;border-radius:8px}
  .login{font-size:14px;color:var(--mute);text-decoration:none;border:1px solid var(--line);padding:8px 14px;border-radius:10px}
  .login:hover{color:var(--text);border-color:#444}
  .hero{display:grid;grid-template-columns:1.1fr .9fr;gap:40px;align-items:center;padding:48px 0 56px}
  h1{font-size:clamp(34px,5.4vw,58px);line-height:1.05;margin:0 0 16px;letter-spacing:-.02em}
  h1 span{background:linear-gradient(90deg,var(--o1),var(--o2));-webkit-background-clip:text;background-clip:text;color:transparent}
  .sub{font-size:clamp(16px,2vw,19px);color:var(--mute);margin:0 0 26px;max-width:520px}
  .cta{display:inline-block;background:linear-gradient(90deg,var(--o1),var(--o2));color:#111;font-weight:800;font-size:17px;text-decoration:none;padding:15px 28px;border-radius:14px}
  .cta:hover{filter:brightness(1.07)}
  .fine{font-size:13px;color:var(--mute);margin-top:12px}
  .phones{display:flex;gap:12px;justify-content:center}
  .phone{width:30%;max-width:150px;aspect-ratio:9/16;border-radius:16px;border:1px solid var(--line);background:linear-gradient(160deg,#262626,#141414);position:relative;overflow:hidden;padding:10px;display:flex;flex-direction:column;justify-content:flex-end}
  .phone:nth-child(2){transform:translateY(-14px);border-color:#5a4210}
  .phone .cap{background:rgba(0,0,0,.65);border-radius:8px;padding:6px 7px;font-size:10.5px;font-weight:700;line-height:1.25}
  .phone .tags{font-size:9px;color:var(--o1);margin-top:5px}
  .phone .score{position:absolute;top:8px;right:8px;background:var(--o1);color:#111;font-weight:800;font-size:11px;border-radius:7px;padding:2px 6px}
  .ex{font-size:11px;color:#6b6b6b;text-align:center;margin-top:14px}
  section{padding:44px 0;border-top:1px solid var(--line)}
  h2{font-size:clamp(24px,3.4vw,34px);margin:0 0 8px;letter-spacing:-.01em}
  .lead{color:var(--mute);margin:0 0 26px}
  .grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
  .grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
  .box{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}
  .num{display:inline-flex;width:30px;height:30px;border-radius:50%;background:linear-gradient(90deg,var(--o1),var(--o2));color:#111;font-weight:800;align-items:center;justify-content:center;margin-bottom:10px}
  .box h3{margin:0 0 4px;font-size:17px}
  .box p{margin:0;color:var(--mute);font-size:14.5px}
  .final{text-align:center;padding:56px 0}
  footer{border-top:1px solid var(--line);padding:22px 0 40px;color:var(--mute);font-size:13px;display:flex;flex-wrap:wrap;gap:14px;justify-content:space-between}
  footer a{text-decoration:none;margin-right:14px}
  footer a:hover{color:var(--text)}
  @media(max-width:820px){
    .hero{grid-template-columns:1fr;padding-top:26px}
    .grid3,.grid2{grid-template-columns:1fr}
    .phones{margin-top:8px}
  }
</style></head><body>
<div class="wrap">
  <nav>
    <a class="brand" href="/"><img src="/static/favicon-32x32.png" alt="">ViralCut</a>
    <a class="login" href="/login">Log in</a>
  </nav>

  <div class="hero">
    <div>
      <h1>Turn long videos into <span>viral short clips</span> with AI</h1>
      <p class="sub">Paste a YouTube link or upload a video. AI finds the best moments and cuts ready vertical clips with captions and hashtags. Publish to YouTube in one click.</p>
      <a class="cta" href="/login">Try free with Google</a>
      <div class="fine">3 free clips every day. No credit card needed.</div>
    </div>
    <div>
      <div class="phones">
        <div class="phone"><div class="score">92</div><div class="cap">The one mistake every beginner makes</div><div class="tags">#tips #learn #shorts</div></div>
        <div class="phone"><div class="score">96</div><div class="cap">Wait for the ending...</div><div class="tags">#viral #story #shorts</div></div>
        <div class="phone"><div class="score">89</div><div class="cap">Nobody tells you this</div><div class="tags">#secret #howto #shorts</div></div>
      </div>
      <div class="ex">Example of what you get: clip, caption, hashtags and a virality score</div>
    </div>
  </div>

  <section id="how">
    <h2>How it works</h2>
    <p class="lead">From one long video to ready clips in a few minutes.</p>
    <div class="grid3">
      <div class="box"><div class="num">1</div><h3>Add your video</h3><p>Paste a YouTube link or upload a file from your phone or computer.</p></div>
      <div class="box"><div class="num">2</div><h3>AI picks the best moments</h3><p>It reads the transcript, finds the strongest parts and cuts them into vertical clips.</p></div>
      <div class="box"><div class="num">3</div><h3>Download or publish</h3><p>Get captions and hashtags for every clip. Publish to YouTube in one click.</p></div>
    </div>
  </section>

  <section id="features">
    <h2>Everything for short-form video</h2>
    <p class="lead">Made for creators who want more reach without hours of editing.</p>
    <div class="grid2">
      <div class="box"><h3>Vertical clips with captions</h3><p>9:16 clips for Shorts and Reels, with a ready caption and hashtags for each one.</p></div>
      <div class="box"><h3>Virality score and reason</h3><p>See which clip is likely to work best, and why the AI picked it.</p></div>
      <div class="box"><h3>AI B-roll and voice-over</h3><p>Add matching background footage and an AI voice to make clips more interesting.</p></div>
      <div class="box"><h3>Publish to YouTube</h3><p>Connect your channel once, then publish clips with the title and hashtags already filled in.</p></div>
    </div>
  </section>

  <section class="final">
    <h2>Make your first clips today</h2>
    <p class="lead">Free for 3 clips every day. Upgrade only if you need more.</p>
    <a class="cta" href="/login">Try free with Google</a>
  </section>

  <footer>
    <div>&copy; ViralCut</div>
    <div><a href="/about">About</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/contact">Contact</a></div>
  </footer>
</div>
</body></html>'''

@app.before_request
def _landing_page():
    try:
        if request.method == 'GET' and request.path == '/' and 'user' not in session:
            if 'src' not in session and '_visit_source_raw' in globals():
                _s = _visit_source_raw()
                if _s and _s != 'direct':
                    session['src'] = _s
            from flask import Response
            return Response(LANDING_PAGE, mimetype='text/html')
    except Exception as e:
        print('[landing] could not show landing page:', e, flush=True)

"""

src = src.replace(anchor, block + anchor, 1)
with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)
print('OK: public home page added (logged-out visitors see it at the main address)')
print('Logged-in users still go to the Dashboard as before.')
