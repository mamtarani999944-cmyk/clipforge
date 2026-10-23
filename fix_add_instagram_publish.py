"""
Adds "Publish to Instagram" (as a Reel) to every clip card, on the Dashboard
and on My Clips. Uses the Instagram API with Instagram Login (the Meta app
"ViralCut" you just made).

How it works for the person using it:
1. They tap "Connect Instagram" on any clip card (once), log in to Instagram
   and allow ViralCut to post for them.
2. After that, each clip card shows "Publish to Instagram". One tap sends the
   clip to their Instagram as a Reel with the AI caption + hashtags.

Needs 2 Railway Variables (Railway > your project > Variables > New Variable):
    INSTAGRAM_APP_ID       = the "Instagram app ID" from the Meta page
    INSTAGRAM_APP_SECRET   = the "Instagram app secret" from the Meta page
(Optional: INSTAGRAM_REDIRECT_URI, default
 https://viralcut.xyz/auth/instagram/callback)

IMPORTANT: until Meta approves your app ("App Review"), only Instagram
accounts you added as "Instagram Tester" can connect (your own account).

Safety: if the 2 variables are missing, the button shows a message and
nothing else breaks. Failed publishes show the real error and never touch
your clip.

Run from your clipforge-final project folder:
    python fix_add_instagram_publish.py
"""
import re

changed = []
failed = []

# ------------------------------------------------------------------ app.py
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

anchor = "if __name__ == '__main__':"

backend = """# ── Instagram publishing (Instagram API with Instagram Login) ────────────────
INSTAGRAM_APP_ID = os.environ.get('INSTAGRAM_APP_ID')
INSTAGRAM_APP_SECRET = os.environ.get('INSTAGRAM_APP_SECRET')
INSTAGRAM_REDIRECT_URI = os.environ.get('INSTAGRAM_REDIRECT_URI', 'https://viralcut.xyz/auth/instagram/callback')
INSTAGRAM_API_VERSION = os.environ.get('INSTAGRAM_API_VERSION', 'v25.0')
INSTAGRAM_GRAPH = 'https://graph.instagram.com/' + INSTAGRAM_API_VERSION

def _ensure_instagram_table(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS instagram_tokens (
            user_id      INTEGER PRIMARY KEY,
            ig_user_id   TEXT NOT NULL,
            username     TEXT,
            access_token TEXT NOT NULL,
            expires_at   INTEGER,
            refreshed_at INTEGER
        )
    ''')
    db.commit()

def _ig_error_text(resp):
    try:
        j = resp.json()
        e = j.get('error') or {}
        if isinstance(e, dict):
            return e.get('error_user_msg') or e.get('message') or str(j)
        return str(e or j)
    except Exception:
        return 'HTTP ' + str(resp.status_code)

def _ig_get_token(user_id):
    \"\"\"Returns (ig_user_id, access_token) for this user, refreshing the 60-day token when it is close to expiring.\"\"\"
    import time as _time
    db = get_db()
    _ensure_instagram_table(db)
    row = db.execute('SELECT * FROM instagram_tokens WHERE user_id = ?', (user_id,)).fetchone()
    if not row:
        db.close()
        return None, None
    token = row['access_token']
    now = int(_time.time())
    exp = row['expires_at'] or 0
    refreshed = row['refreshed_at'] or 0
    if exp and exp - now < 10 * 86400 and now - refreshed > 86400:
        try:
            r = requests.get('https://graph.instagram.com/refresh_access_token', params={
                'grant_type': 'ig_refresh_token', 'access_token': token}, timeout=20)
            if r.ok:
                j = r.json()
                token = j.get('access_token', token)
                exp = now + int(j.get('expires_in', 5184000))
                db.execute('UPDATE instagram_tokens SET access_token = ?, expires_at = ?, refreshed_at = ? WHERE user_id = ?',
                           (token, exp, now, user_id))
                db.commit()
        except Exception as e:
            print('[instagram] token refresh failed:', e, flush=True)
    db.close()
    return row['ig_user_id'], token

@app.route('/instagram/connect')
@login_required
def instagram_connect():
    if not INSTAGRAM_APP_ID or not INSTAGRAM_APP_SECRET:
        return "Instagram publishing isn't set up yet (missing INSTAGRAM_APP_ID/INSTAGRAM_APP_SECRET).", 500
    from urllib.parse import urlencode
    state = uuid.uuid4().hex
    session['instagram_oauth_state'] = state
    params = {
        'client_id': INSTAGRAM_APP_ID,
        'redirect_uri': INSTAGRAM_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'instagram_business_basic,instagram_business_content_publish',
        'state': state,
    }
    return redirect('https://www.instagram.com/oauth/authorize?' + urlencode(params))

@app.route('/auth/instagram/callback')
@login_required
def instagram_callback():
    import time as _time
    if request.args.get('error'):
        return redirect('/?instagram_error=' + (request.args.get('error_reason') or request.args.get('error') or 'denied')[:40].replace(' ', '_'))
    state = request.args.get('state')
    if not state or state != session.get('instagram_oauth_state'):
        return redirect('/?instagram_error=bad_state')
    code = (request.args.get('code') or '').split('#')[0]
    if not code:
        return redirect('/?instagram_error=no_code')
    try:
        r = requests.post('https://api.instagram.com/oauth/access_token', data={
            'client_id': INSTAGRAM_APP_ID,
            'client_secret': INSTAGRAM_APP_SECRET,
            'grant_type': 'authorization_code',
            'redirect_uri': INSTAGRAM_REDIRECT_URI,
            'code': code,
        }, timeout=20)
        if not r.ok:
            print('[instagram] token exchange failed:', _ig_error_text(r), flush=True)
            return redirect('/?instagram_error=token_exchange')
        j = r.json()
        if isinstance(j.get('data'), list) and j['data']:
            j = j['data'][0]
        short_token = j.get('access_token')
        ig_user_id = str(j.get('user_id') or '')
        if not short_token:
            return redirect('/?instagram_error=no_token')
        lr = requests.get('https://graph.instagram.com/access_token', params={
            'grant_type': 'ig_exchange_token',
            'client_secret': INSTAGRAM_APP_SECRET,
            'access_token': short_token,
        }, timeout=20)
        if not lr.ok:
            print('[instagram] long-lived token failed:', _ig_error_text(lr), flush=True)
            return redirect('/?instagram_error=long_token')
        lj = lr.json()
        token = lj.get('access_token')
        now = int(_time.time())
        exp = now + int(lj.get('expires_in', 5184000))
        username = None
        mr = requests.get(INSTAGRAM_GRAPH + '/me', params={'fields': 'user_id,username', 'access_token': token}, timeout=20)
        if mr.ok:
            mj = mr.json()
            username = mj.get('username')
            ig_user_id = str(mj.get('user_id') or ig_user_id)
        if not ig_user_id:
            return redirect('/?instagram_error=no_account')
        db = get_db()
        _ensure_instagram_table(db)
        db.execute('''
            INSERT INTO instagram_tokens (user_id, ig_user_id, username, access_token, expires_at, refreshed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET ig_user_id=excluded.ig_user_id, username=excluded.username,
                access_token=excluded.access_token, expires_at=excluded.expires_at, refreshed_at=excluded.refreshed_at
        ''', (current_user_id(), ig_user_id, username, token, exp, now))
        db.commit()
        db.close()
        return redirect('/?instagram=connected')
    except Exception as e:
        print('[instagram] OAuth callback failed:', e, flush=True)
        return redirect('/?instagram_error=1')

@app.route('/api/instagram/status')
@login_required
def instagram_status():
    db = get_db()
    _ensure_instagram_table(db)
    row = db.execute('SELECT username FROM instagram_tokens WHERE user_id = ?', (current_user_id(),)).fetchone()
    db.close()
    return jsonify({'connected': bool(row), 'username': row['username'] if row else None,
                    'configured': bool(INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET)})

@app.route('/api/clips/<int:clip_id>/publish-instagram', methods=['POST'])
@login_required
def publish_to_instagram(clip_id):
    \"\"\"Step 1: ask Instagram to fetch the clip and prepare a Reel. Returns a container id.\"\"\"
    user_id = current_user_id()
    ig_user_id, token = _ig_get_token(user_id)
    if not token:
        return jsonify({'error': 'Connect your Instagram account first.'}), 400
    db = get_db()
    clip_row = db.execute('SELECT * FROM clips WHERE id = ? AND user_id = ?', (clip_id, user_id)).fetchone()
    db.close()
    if not clip_row:
        return jsonify({'error': 'Clip not found'}), 404
    video_url = r2_presigned_download_url(clip_row['filename'], os.path.basename(clip_row['filename']))
    if not video_url:
        return jsonify({'error': 'Clip storage not available'}), 500
    try:
        hashtags = json.loads(clip_row['hashtags'] or '[]')
    except Exception:
        hashtags = []
    tags = ' '.join(h if str(h).startswith('#') else '#' + str(h) for h in hashtags[:20])
    caption = ((clip_row['caption'] or clip_row['title'] or '').strip() + '\\n\\n' + tags).strip()[:2200]
    try:
        r = requests.post(INSTAGRAM_GRAPH + '/' + str(ig_user_id) + '/media', data={
            'media_type': 'REELS',
            'video_url': video_url,
            'caption': caption,
            'share_to_feed': 'true',
            'access_token': token,
        }, timeout=40)
        if not r.ok:
            msg = _ig_error_text(r)
            print('[instagram] create container failed:', msg, flush=True)
            return jsonify({'error': 'Instagram said: ' + msg}), 502
        cid = str(r.json().get('id') or '')
        if not cid:
            return jsonify({'error': 'Instagram did not return an id'}), 502
        return jsonify({'container_id': cid})
    except Exception as e:
        print('[instagram] publish start failed:', e, flush=True)
        return jsonify({'error': f'Publish failed: {e}'}), 500

@app.route('/api/instagram/container/<container_id>')
@login_required
def instagram_container_status(container_id):
    \"\"\"Step 2: the page asks every few seconds if Instagram finished preparing the Reel.\"\"\"
    if not re.fullmatch(r'\\d{5,40}', container_id or ''):
        return jsonify({'status': 'ERROR', 'error': 'bad id'}), 400
    ig_user_id, token = _ig_get_token(current_user_id())
    if not token:
        return jsonify({'status': 'ERROR', 'error': 'Connect your Instagram account first.'}), 400
    try:
        r = requests.get(INSTAGRAM_GRAPH + '/' + container_id, params={
            'fields': 'status_code,status', 'access_token': token}, timeout=20)
        if not r.ok:
            return jsonify({'status': 'ERROR', 'error': _ig_error_text(r)}), 502
        j = r.json()
        return jsonify({'status': j.get('status_code', 'IN_PROGRESS'),
                        'error': j.get('status') if j.get('status_code') in ('ERROR', 'EXPIRED') else None})
    except Exception as e:
        return jsonify({'status': 'ERROR', 'error': str(e)}), 500

@app.route('/api/instagram/publish', methods=['POST'])
@login_required
def instagram_publish_container():
    \"\"\"Step 3: publish the finished Reel.\"\"\"
    data = request.get_json(silent=True) or {}
    cid = str(data.get('container_id') or '')
    if not re.fullmatch(r'\\d{5,40}', cid):
        return jsonify({'error': 'bad id'}), 400
    ig_user_id, token = _ig_get_token(current_user_id())
    if not token:
        return jsonify({'error': 'Connect your Instagram account first.'}), 400
    try:
        r = requests.post(INSTAGRAM_GRAPH + '/' + str(ig_user_id) + '/media_publish', data={
            'creation_id': cid, 'access_token': token}, timeout=40)
        if not r.ok:
            msg = _ig_error_text(r)
            print('[instagram] media_publish failed:', msg, flush=True)
            return jsonify({'error': 'Instagram said: ' + msg}), 502
        media_id = str(r.json().get('id') or '')
        permalink = None
        try:
            pr = requests.get(INSTAGRAM_GRAPH + '/' + media_id, params={'fields': 'permalink', 'access_token': token}, timeout=20)
            if pr.ok:
                permalink = pr.json().get('permalink')
        except Exception:
            pass
        return jsonify({'success': True, 'permalink': permalink})
    except Exception as e:
        print('[instagram] publish failed:', e, flush=True)
        return jsonify({'error': f'Publish failed: {e}'}), 500

"""

if 'def instagram_connect' in src:
    print('SKIP: Instagram backend already present')
else:
    if src.count(anchor) != 1:
        failed.append(f"app.py: main block found {src.count(anchor)} times (expected 1)")
    for need in ('def r2_presigned_download_url', 'def login_required', 'def current_user_id'):
        if need not in src:
            failed.append(f'app.py: could not find "{need}"')
    if not failed:
        if not re.search(r'^import re\b|^import .*\bre\b|^from re import', src, flags=re.MULTILINE):
            src = 'import re\n' + src
            changed.append('added "import re"')
        if not re.search(r'^import requests|^import .*\brequests\b|^from requests import', src, flags=re.MULTILINE):
            src = 'import requests\n' + src
            changed.append('added "import requests"')
        src = src.replace(anchor, backend + anchor, 1)
        with open('app.py', 'w', encoding='utf-8') as f:
            f.write(src)
        changed.append('app.py: added Instagram connect/callback/status/publish routes')

# --------------------------------------------------------------- templates
IG_JS = """/* INSTAGRAM_V1 */
var _igStatusPromise = null;
function igStatus() {
  if (!_igStatusPromise) {
    _igStatusPromise = fetch('/api/instagram/status').then(function (r) { return r.json(); }).catch(function () { return {}; });
  }
  return _igStatusPromise;
}
async function igClick(clipId, btn) {
  var st = await igStatus();
  if (!st.configured) { alert('Instagram publishing is not set up yet.'); return; }
  if (!st.connected) { window.location.href = '/instagram/connect'; return; }
  var old = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Sending to Instagram...';
  try {
    var r = await fetch('/api/clips/' + clipId + '/publish-instagram', { method: 'POST' });
    var d = await r.json();
    if (!r.ok || !d.container_id) { throw new Error(d.error || 'Could not start publishing'); }
    btn.textContent = 'Instagram is preparing it...';
    var cid = d.container_id, status = 'IN_PROGRESS', tries = 0;
    while (status === 'IN_PROGRESS' && tries < 60) {
      await new Promise(function (res) { setTimeout(res, 3000); });
      tries++;
      var sr = await fetch('/api/instagram/container/' + cid);
      var sd = await sr.json();
      status = sd.status || 'ERROR';
      if (status === 'ERROR' || status === 'EXPIRED') { throw new Error(sd.error || 'Instagram could not process this video'); }
    }
    if (status !== 'FINISHED') { throw new Error('Instagram is taking too long. Please try again in a few minutes.'); }
    btn.textContent = 'Publishing...';
    var pr = await fetch('/api/instagram/publish', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ container_id: cid }) });
    var pd = await pr.json();
    if (!pr.ok || !pd.success) { throw new Error(pd.error || 'Publish failed'); }
    btn.textContent = 'Published! Open \\u2192';
    btn.onclick = function () { window.open(pd.permalink || 'https://www.instagram.com/', '_blank'); };
    btn.disabled = false;
  } catch (e) {
    alert('Instagram: ' + e.message);
    btn.textContent = old;
    btn.disabled = false;
  }
}
(function () {
  igStatus().then(function (st) {
    if (st && st.configured && !st.connected) {
      document.querySelectorAll('.ig-publish-btn').forEach(function (b) { if (b.textContent === 'Publish to Instagram') { b.textContent = 'Connect Instagram'; } });
    }
  });
})();
(function () {
  try {
    var p = new URLSearchParams(window.location.search);
    if (p.get('instagram') === 'connected') { alert('Instagram connected! Now tap "Publish to Instagram" on a clip.'); }
    else if (p.get('instagram_error')) { alert('Instagram connection failed: ' + p.get('instagram_error')); }
  } catch (e) { /* ignore */ }
})();
"""

YT_BTN = re.compile(r'(<button[^>]*id="yt-publish-\$\{clip\.id\}"[^>]*>[^<]*Publish to YouTube</button>)')
IG_BTN = ('\n  <button class="clip-btn ig-publish-btn" onclick="igClick(${clip.id}, this)" '
          'style="background:linear-gradient(45deg,#f09433,#dc2743,#bc1888);color:#fff">Publish to Instagram</button>')

def patch_template(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            html = f.read()
    except FileNotFoundError:
        failed.append(f'{path}: file not found')
        return
    if 'INSTAGRAM_V1' in html:
        print(f'SKIP: {path} already has Instagram publishing')
        return
    if len(YT_BTN.findall(html)) != 1:
        failed.append(f'{path}: YouTube button found {len(YT_BTN.findall(html))} times (expected 1) - run the YouTube patches first')
        return
    if '<script>' not in html:
        failed.append(f'{path}: no <script> tag found')
        return
    html = YT_BTN.sub(lambda m: m.group(1) + IG_BTN, html, count=1)
    html = html.replace('<script>', '<script>\n' + IG_JS, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    changed.append(f'{path}: added Publish to Instagram to every clip card')

if not failed:
    patch_template('templates/index.html')
    patch_template('templates/my_clips.html')

if failed:
    print('FAILED (app.py is unchanged unless listed as OK below):')
    for x in failed:
        print(' -', x)
for c in changed:
    print('OK:', c)
if not failed:
    print('')
    print('NEXT: in Railway > Variables, add INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET (from the Meta page).')
