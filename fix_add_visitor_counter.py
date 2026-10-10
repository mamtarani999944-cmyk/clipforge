"""
Adds a private visitor counter page at:  viralcut.xyz/admin/visits

It counts real people (not bots, not you) who open your website pages and
shows: visitors right now, visitors per day, and WHERE they came from
(Product Hunt, Google, Twitter, direct...), biggest first.

Only your Google account can open it (same as /admin/costs).
No raw IP address is saved (only a private scrambled code).

Run from your clipforge-final project folder:
    python fix_add_visitor_counter.py
"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

if 'def admin_visits' in src:
    print('SKIP: visitor counter already present')
    raise SystemExit

failed = []
anchor = "if __name__ == '__main__':"
if src.count(anchor) != 1:
    failed.append(f'main block found {src.count(anchor)} times (expected 1)')
if 'ADMIN_EMAILS' not in src:
    failed.append('ADMIN_EMAILS not found (the cost tracker must be installed first)')
if 'render_template_string' not in src:
    failed.append('render_template_string not found (the cost tracker must be installed first)')

block = """# ── Visitor counter (private /admin/visits page) ─────────────────────────────
_visits_ready = False
_BOT_WORDS = ('bot', 'spider', 'crawl', 'slurp', 'curl', 'wget', 'python', 'httpx', 'go-http',
              'headless', 'uptime', 'monitor', 'preview', 'facebookexternalhit', 'whatsapp',
              'telegram', 'discord', 'slack', 'lighthouse', 'pingdom', 'java/')
_SKIP_PREFIXES = ('/admin', '/api', '/static', '/auth', '/download', '/health', '/webhook', '/favicon')

def _ensure_visits_table(db):
    global _visits_ready
    if _visits_ready:
        return
    db.execute('''
        CREATE TABLE IF NOT EXISTS visits (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT DEFAULT (datetime('now')),
            path    TEXT,
            source  TEXT,
            visitor TEXT
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts)')
    db.commit()
    _visits_ready = True

def _visit_source():
    import re as _re
    from urllib.parse import urlparse
    tag = (request.args.get('ref') or request.args.get('utm_source') or '').strip().lower()
    tag = _re.sub(r'[^a-z0-9._-]', '', tag)[:40]
    if tag:
        return tag
    ref = request.headers.get('Referer') or ''
    try:
        host = (urlparse(ref).hostname or '').lower()
    except Exception:
        host = ''
    if host.startswith('www.'):
        host = host[4:]
    if not host or host.endswith('viralcut.xyz') or host.endswith('railway.app'):
        return 'direct'
    return _re.sub(r'[^a-z0-9._-]', '', host)[:60] or 'direct'

@app.after_request
def _track_visit(resp):
    try:
        if request.method != 'GET' or resp.status_code != 200 or resp.mimetype != 'text/html':
            return resp
        path = request.path or '/'
        if path.startswith(_SKIP_PREFIXES):
            return resp
        ua = (request.headers.get('User-Agent') or '')
        low = ua.lower()
        if not ua or any(w in low for w in _BOT_WORDS):
            return resp
        email = ((session.get('user') or {}).get('email') or '').lower()
        if email and email in ADMIN_EMAILS:
            return resp
        import hashlib
        salt = str(app.secret_key or '')
        visitor = hashlib.sha256((salt + '|' + (request.remote_addr or '') + '|' + ua).encode('utf-8')).hexdigest()[:16]
        db = get_db()
        _ensure_visits_table(db)
        db.execute('INSERT INTO visits (path, source, visitor) VALUES (?, ?, ?)',
                   (path[:200], _visit_source(), visitor))
        db.commit()
        db.close()
    except Exception as e:
        print('[visits] could not log visit:', e, flush=True)
    return resp

VISITS_PAGE = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Visitors</title>
<style>
  body{font-family:Inter,system-ui,sans-serif;background:#f5f5f7;color:#111;margin:0;padding:16px}
  .wrap{max-width:760px;margin:0 auto}
  h1{font-size:20px;margin:8px 0 4px}
  h2{font-size:15px;margin:22px 0 8px}
  .sub{color:#666;font-size:13px;margin-bottom:14px}
  .tabs a{display:inline-block;padding:6px 12px;border-radius:8px;background:#fff;border:1px solid #e5e5e7;color:#111;text-decoration:none;font-size:13px;margin-right:6px}
  .tabs a.on{background:#111;color:#fff}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
  .card{background:#fff;border:1px solid #e5e5e7;border-radius:12px;padding:12px}
  .card b{display:block;font-size:22px;margin-top:4px}
  .card span{font-size:12px;color:#666}
  .row{background:#fff;border:1px solid #e5e5e7;border-radius:12px;padding:10px 12px;margin-bottom:8px}
  .top{display:flex;justify-content:space-between;gap:10px;font-weight:600;font-size:14px}
  .top em{font-style:normal;font-weight:400;color:#666}
  .bar{height:7px;background:#eee;border-radius:6px;margin-top:7px;overflow:hidden}
  .bar i{display:block;height:100%;background:#111}
  .first .bar i{background:#ff6154}
  .note{font-size:12px;color:#777;margin-top:16px;line-height:1.5}
  .empty{color:#777;font-size:13px;padding:8px 0}
</style></head><body><div class="wrap">
<h1>Your visitors</h1>
<div class="sub">Real people only (no bots, not you). Last {{ days }} days. Days are in UTC.</div>
<div class="tabs">
  {% for d in [1,7,30,90] %}<a href="?days={{ d }}" class="{{ 'on' if d == days else '' }}">{{ 'Today' if d == 1 else (d ~ ' days') }}</a>{% endfor %}
</div>
<div class="cards">
  <div class="card"><span>On the site now (30 min)</span><b>{{ now_n }}</b></div>
  <div class="card"><span>Unique visitors</span><b>{{ uniq }}</b></div>
  <div class="card"><span>Page views</span><b>{{ views }}</b></div>
  <div class="card"><span>New signups</span><b>{{ signups }}</b><span>{{ total_users }} users in total</span></div>
</div>

<h2>Where they came from</h2>
{% for s in sources %}
<div class="row {{ 'first' if loop.first else '' }}">
  <div class="top"><span>{{ loop.index }}. {{ s.name }}</span><span>{{ s.uniq }} <em>people</em></span></div>
  <div class="bar"><i style="width:{{ s.pct }}%"></i></div>
</div>
{% else %}<div class="empty">No visitors yet.</div>{% endfor %}

<h2>Each day</h2>
{% for d in daily %}
<div class="row">
  <div class="top"><span>{{ d.day }}</span><span>{{ d.uniq }} <em>people &middot; {{ d.views }} views</em></span></div>
  <div class="bar"><i style="width:{{ d.pct }}%"></i></div>
</div>
{% else %}<div class="empty">Nothing yet.</div>{% endfor %}

<h2>Pages they opened</h2>
{% for p in pages %}
<div class="row"><div class="top"><span>{{ p.path }}</span><span>{{ p.views }} <em>views</em></span></div></div>
{% else %}<div class="empty">Nothing yet.</div>{% endfor %}

<div class="note">Tip: use the link <b>https://viralcut.xyz/?ref=producthunt</b> on Product Hunt so the source shows as "producthunt". Use <b>?ref=twitter</b>, <b>?ref=reddit</b> etc. for other places. Counting starts when this is deployed. A private scrambled code is used to count unique people; no IP address is saved.</div>
</div></body></html>'''

@app.route('/admin/visits')
@login_required
def admin_visits():
    email = (session.get('user', {}).get('email') or '').lower()
    if email not in ADMIN_EMAILS:
        return 'Not found', 404
    try:
        days = int(request.args.get('days', 7))
    except ValueError:
        days = 7
    days = max(1, min(days, 365))
    window = 'start of day' if days == 1 else f'-{days} days'

    db = get_db()
    _ensure_visits_table(db)
    cond = "ts >= datetime('now', ?)" if days != 1 else "ts >= datetime('now', 'start of day')"
    args = (window,) if days != 1 else ()
    tot = db.execute(f'SELECT COUNT(*) AS v, COUNT(DISTINCT visitor) AS u FROM visits WHERE {cond}', args).fetchone()
    now_n = db.execute("SELECT COUNT(DISTINCT visitor) AS u FROM visits WHERE ts >= datetime('now', '-30 minutes')").fetchone()['u']
    src_rows = db.execute(f'SELECT source, COUNT(DISTINCT visitor) AS u FROM visits WHERE {cond} GROUP BY source ORDER BY u DESC, source LIMIT 15', args).fetchall()
    day_rows = db.execute(f'SELECT date(ts) AS d, COUNT(*) AS v, COUNT(DISTINCT visitor) AS u FROM visits WHERE {cond} GROUP BY date(ts) ORDER BY d DESC LIMIT 31', args).fetchall()
    page_rows = db.execute(f'SELECT path, COUNT(*) AS v FROM visits WHERE {cond} GROUP BY path ORDER BY v DESC LIMIT 8', args).fetchall()
    try:
        total_users = db.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
    except Exception:
        total_users = 0
    try:
        signups = db.execute(f'SELECT COUNT(*) AS n FROM users WHERE {cond.replace("ts ", "created_at ")}', args).fetchone()['n']
    except Exception:
        signups = 'n/a'
    db.close()

    top_s = max([r['u'] for r in src_rows] + [1])
    top_d = max([r['u'] for r in day_rows] + [1])
    sources = [{'name': r['source'], 'uniq': r['u'], 'pct': round(r['u'] / top_s * 100)} for r in src_rows]
    daily = [{'day': r['d'], 'uniq': r['u'], 'views': r['v'], 'pct': round(r['u'] / top_d * 100)} for r in day_rows]
    pages = [{'path': r['path'], 'views': r['v']} for r in page_rows]
    return render_template_string(
        VISITS_PAGE, days=days, now_n=now_n, uniq=tot['u'], views=tot['v'], signups=signups,
        total_users=total_users, sources=sources, daily=daily, pages=pages)

"""

if failed:
    print('FAILED, nothing was changed:')
    for x in failed:
        print(' -', x)
else:
    src = src.replace(anchor, block + anchor, 1)
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(src)
    print('OK: visitor counter added')
    print('After deploy, open  viralcut.xyz/admin/visits  while logged in.')
    print('For Product Hunt use this link:  https://viralcut.xyz/?ref=producthunt')
