"""
Adds a private "where does my money go" page at:  viralcut.xyz/admin/costs

It records usage as customers generate clips (AI tokens, server time,
storage, emails) and shows ESTIMATED cost per service, biggest first.
Only your Google account can open it (default: mamtarani999944@gmail.com;
to change, set ADMIN_EMAILS in Railway Variables, comma separated).

Numbers are estimates from tracked usage. Your real bills are always on
each site's own billing page. Tracking starts the moment this is deployed.

Run from your clipforge-final project folder:
    python fix_add_cost_tracker.py
"""
import re

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

if 'def log_usage' in src:
    print('SKIP: cost tracker already present')
    raise SystemExit

changed = []
failed = []

# 1. import time
if 'import time\n' not in src:
    if src.startswith('import os\n'):
        src = src.replace('import os\n', 'import os\nimport time\n', 1)
        changed.append('imported time')
    else:
        failed.append('could not find "import os" at the top of app.py')

# 2. Log AI token usage after every Anthropic call (3 places).
marker = "'https://api.anthropic.com/v1/messages'"
pos = 0
n_logged = 0
while True:
    i = src.find(marker, pos)
    if i == -1:
        break
    j = src.find('data = resp.json()', i)
    if j == -1:
        break
    line_start = src.rfind('\n', 0, j) + 1
    indent = src[line_start:j]
    line_end = src.find('\n', j)
    insert = '\n' + indent + '_log_anthropic_usage(data)'
    if '_log_anthropic_usage(data)' not in src[line_end:line_end + 80]:
        src = src[:line_end] + insert + src[line_end:]
        n_logged += 1
    pos = line_end + len(insert) + 1
if n_logged:
    changed.append(f'AI token usage is now tracked ({n_logged} places)')
else:
    failed.append('could not find the Anthropic calls')

# 3. Log storage bytes on every R2 upload.
old_r2 = """            ExtraArgs={'ContentType': content_type}
        )
        return True"""
new_r2 = """            ExtraArgs={'ContentType': content_type}
        )
        try:
            log_usage('r2_bytes', os.path.getsize(local_path))
        except Exception:
            pass
        return True"""
if src.count(old_r2) == 1:
    src = src.replace(old_r2, new_r2, 1)
    changed.append('storage uploads are now tracked')
else:
    failed.append(f'R2 upload block found {src.count(old_r2)} times (expected 1)')

# 4. Log server time spent generating clips.
m = re.search(r'^([ \t]*)(clips = generate_clips\(video_path, num_clips=.*\))[ \t]*$', src, flags=re.MULTILINE)
if m:
    ind = m.group(1)
    block = (f"{ind}_t0 = time.time()\n{ind}{m.group(2)}\n"
             f"{ind}log_usage('compute_seconds', time.time() - _t0, user_id)")
    src = src[:m.start()] + block + src[m.end():]
    changed.append('server time per generation is now tracked')
else:
    failed.append('could not find the generate_clips() call')

# 5. Log login emails.
old_mail = """        send_login_notification(email, name, request)
    except Exception:
        pass"""
new_mail = """        send_login_notification(email, name, request)
        log_usage('emails', 1, user_id)
    except Exception:
        pass"""
if src.count(old_mail) == 1:
    src = src.replace(old_mail, new_mail, 1)
    changed.append('login emails are now tracked')
else:
    failed.append(f'login email block found {src.count(old_mail)} times (expected 1)')

# 6. Helper functions + the /admin/costs page.
anchor = "if __name__ == '__main__':"
block = """# ── Cost tracker (private /admin/costs page) ─────────────────────────────────
ADMIN_EMAILS = [e.strip().lower() for e in os.environ.get('ADMIN_EMAILS', 'mamtarani999944@gmail.com').split(',') if e.strip()]

# Estimated prices in USD. Edit these if a service changes its prices.
COST_RATES = {
    'anthropic_in_per_million': 1.00,    # Claude Haiku 4.5 input
    'anthropic_out_per_million': 5.00,   # Claude Haiku 4.5 output
    'railway_per_compute_second': 0.00002,  # rough CPU+RAM while generating
    'railway_base_per_month': 5.00,      # always-on base cost
    'r2_per_gb_month': 0.015,
    'r2_free_gb': 10,
    'resend_free_emails_per_month': 3000,
    'resend_paid_per_month': 20.00,
    'domain_per_year': 12.00,
    'usd_to_inr': 88.0,
}

def _ensure_usage_table(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS usage_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT DEFAULT (datetime('now')),
            service TEXT NOT NULL,
            units   REAL NOT NULL,
            user_id INTEGER
        )
    ''')

def log_usage(service, units, user_id=None):
    try:
        db = get_db()
        _ensure_usage_table(db)
        db.execute('INSERT INTO usage_log (service, units, user_id) VALUES (?, ?, ?)',
                   (service, float(units), user_id))
        db.commit()
        db.close()
    except Exception as e:
        print('[usage] could not log usage:', e, flush=True)

def _log_anthropic_usage(data):
    try:
        u = (data or {}).get('usage') or {}
        log_usage('anthropic_in', u.get('input_tokens', 0))
        log_usage('anthropic_out', u.get('output_tokens', 0))
    except Exception:
        pass

COST_PAGE = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Costs</title>
<style>
  body{font-family:Inter,system-ui,sans-serif;background:#f5f5f7;color:#111;margin:0;padding:16px}
  .wrap{max-width:760px;margin:0 auto}
  h1{font-size:20px;margin:8px 0 4px}
  .sub{color:#666;font-size:13px;margin-bottom:14px}
  .tabs a{display:inline-block;padding:6px 12px;border-radius:8px;background:#fff;border:1px solid #e5e5e7;color:#111;text-decoration:none;font-size:13px;margin-right:6px}
  .tabs a.on{background:#111;color:#fff}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
  .card{background:#fff;border:1px solid #e5e5e7;border-radius:12px;padding:12px}
  .card b{display:block;font-size:20px;margin-top:4px}
  .card span{font-size:12px;color:#666}
  .row{background:#fff;border:1px solid #e5e5e7;border-radius:12px;padding:12px;margin-bottom:10px}
  .top{display:flex;justify-content:space-between;gap:10px;font-weight:600}
  .detail{font-size:12px;color:#666;margin-top:2px}
  .bar{height:8px;background:#eee;border-radius:6px;margin-top:8px;overflow:hidden}
  .bar i{display:block;height:100%;background:#111}
  .first .bar i{background:#e11d48}
  .note{font-size:12px;color:#777;margin-top:14px;line-height:1.5}
</style></head><body><div class="wrap">
<h1>Where your money goes</h1>
<div class="sub">Estimated cost per service, biggest first. Last {{ days }} days.</div>
<div class="tabs">
  {% for d in [7,30,90,365] %}<a href="?days={{ d }}" class="{{ 'on' if d == days else '' }}">{{ d }} days</a>{% endfor %}
</div>
<div class="cards">
  <div class="card"><span>Total cost</span><b>${{ total_usd }}</b><span>Rs {{ total_inr }}</span></div>
  <div class="card"><span>Generations</span><b>{{ gens }}</b><span>{{ users }} active users</span></div>
  <div class="card"><span>Cost per generation</span><b>${{ per_gen }}</b><span>variable costs only</span></div>
  <div class="card"><span>Total users</span><b>{{ total_users }}</b><span>all time</span></div>
</div>
{% for r in rows %}
<div class="row {{ 'first' if loop.first and r.usd_raw > 0 else '' }}">
  <div class="top"><span>{{ loop.index }}. {{ r.name }}</span><span>${{ r.usd }} &middot; Rs {{ r.inr }}</span></div>
  <div class="detail">{{ r.detail }}</div>
  <div class="bar"><i style="width:{{ r.pct }}%"></i></div>
</div>
{% endfor %}
<div class="note">These are estimates from usage tracked since this page was installed. Your real bills are on each site's billing page (Railway, Anthropic, Cloudflare, Resend, your domain seller). Razorpay and PayPal fees are taken from each payment automatically and are not shown here.</div>
</div></body></html>'''

@app.route('/admin/costs')
@login_required
def admin_costs():
    email = (session.get('user', {}).get('email') or '').lower()
    if email not in ADMIN_EMAILS:
        return 'Not found', 404
    try:
        days = int(request.args.get('days', 30))
    except ValueError:
        days = 30
    days = max(1, min(days, 3650))
    window = f'-{days} days'
    R = COST_RATES

    db = get_db()
    _ensure_usage_table(db)
    use = {r['service']: (r['t'] or 0) for r in db.execute(
        'SELECT service, SUM(units) AS t FROM usage_log WHERE ts >= datetime(\\'now\\', ?) GROUP BY service', (window,)).fetchall()}
    stored_bytes = db.execute("SELECT SUM(units) AS t FROM usage_log WHERE service = 'r2_bytes'").fetchone()['t'] or 0
    gens = db.execute("SELECT COUNT(*) AS n FROM jobs WHERE status != 'error' AND created_at >= datetime('now', ?)", (window,)).fetchone()['n']
    users = db.execute("SELECT COUNT(DISTINCT user_id) AS n FROM jobs WHERE status != 'error' AND created_at >= datetime('now', ?)", (window,)).fetchone()['n']
    total_users = db.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
    db.close()

    tok_in = use.get('anthropic_in', 0)
    tok_out = use.get('anthropic_out', 0)
    ai_cost = tok_in / 1e6 * R['anthropic_in_per_million'] + tok_out / 1e6 * R['anthropic_out_per_million']

    secs = use.get('compute_seconds', 0)
    rail_var = secs * R['railway_per_compute_second']
    rail_base = R['railway_base_per_month'] * days / 30
    rail_cost = rail_var + rail_base

    stored_gb = stored_bytes / 1e9
    r2_cost = max(0.0, stored_gb - R['r2_free_gb']) * R['r2_per_gb_month'] * days / 30

    emails = use.get('emails', 0)
    emails_per_month = emails / days * 30
    resend_cost = (R['resend_paid_per_month'] * days / 30) if emails_per_month > R['resend_free_emails_per_month'] else 0.0

    domain_cost = R['domain_per_year'] * days / 365

    items = [
        ('Railway (server)', rail_cost, f'{secs/3600:.1f} hours of clip-making + base cost ${rail_base:.2f}'),
        ('Anthropic (AI)', ai_cost, f'{int(tok_in):,} input + {int(tok_out):,} output tokens'),
        ('Cloudflare R2 (storage)', r2_cost, f'{stored_gb:.2f} GB uploaded so far ({R["r2_free_gb"]} GB free)'),
        ('Resend (login emails)', resend_cost, f'{int(emails)} emails ({R["resend_free_emails_per_month"]}/month free)'),
        ('Domain (viralcut.xyz)', domain_cost, f'about ${R["domain_per_year"]:.0f} per year'),
    ]
    items.sort(key=lambda x: x[1], reverse=True)
    total = sum(x[1] for x in items)
    top = max([x[1] for x in items] + [0.0001])
    rows = [{
        'name': n, 'usd_raw': c, 'usd': f'{c:.2f}', 'inr': f'{c * R["usd_to_inr"]:.0f}',
        'detail': d, 'pct': round(c / top * 100),
    } for n, c, d in items]
    variable = rail_var + ai_cost
    per_gen = f'{(variable / gens):.3f}' if gens else '0.000'
    return render_template_string(
        COST_PAGE, days=days, rows=rows, gens=gens, users=users, total_users=total_users,
        total_usd=f'{total:.2f}', total_inr=f'{total * R["usd_to_inr"]:.0f}', per_gen=per_gen)

"""
if src.count(anchor) == 1:
    src = src.replace(anchor, block + anchor, 1)
    changed.append('added the /admin/costs page')
else:
    failed.append(f"expected 1 match for \"{anchor}\", found {src.count(anchor)}")

# 7. render_template_string import
imp = 'from flask import Flask, request, jsonify, send_file, render_template, redirect, url_for, session'
if 'render_template_string' not in src.split('app = Flask')[0]:
    if imp in src:
        src = src.replace(imp, imp + ', render_template_string', 1)
        changed.append('imported render_template_string')
    else:
        failed.append('could not find the flask import line')

if failed:
    print('FAILED, nothing was changed:')
    for x in failed:
        print(' -', x)
else:
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(src)
    for c in changed:
        print('OK:', c)
    print('')
    print('Done. After deploy, open  viralcut.xyz/admin/costs  while logged in.')
    print('It starts empty and fills up as customers generate clips.')
