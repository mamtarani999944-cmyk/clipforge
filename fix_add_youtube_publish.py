"""
Adds "Publish to YouTube": a button on each generated clip that uploads it
straight to your own YouTube channel (as a Short), using the YouTube
Data API with your own Google Cloud OAuth credentials.

How it works:
1. You (the account owner) click "Connect YouTube" once, sign in with
   Google, and grant upload access. We store a refresh token for your
   account so future publishes don't need you to sign in again.
2. Clicking "Publish to YouTube" on a clip downloads it from storage,
   uploads it to your channel with the AI-generated title/caption/hashtags
   already filled in, and gives you back the live YouTube link.

Needs 2 things from your Google Cloud OAuth client (the one you just
created, named "ClipForge YouTube"):
    YOUTUBE_CLIENT_ID
    YOUTUBE_CLIENT_SECRET
Add both as Railway Variables. Optionally also add YOUTUBE_REDIRECT_URI
if your domain isn't https://viralcut.xyz (defaults to that).

Safety: if those variables aren't set, the Connect button just shows an
error message -- nothing else breaks. If a publish fails for any reason
(expired token, YouTube quota, bad file), it shows the real error message
instead of crashing, and the clip itself is untouched either way.

Run from your clipforge-final project folder:
    python fix_add_youtube_publish.py
"""
with open('requirements.txt', 'r', encoding='utf-8') as f:
    req = f.read()
needed_pkgs = ['google-auth', 'google-auth-httplib2', 'google-api-python-client']
missing = [p for p in needed_pkgs if p not in req]
if missing:
    req = req.rstrip('\n') + '\n' + '\n'.join(missing) + '\n'
    with open('requirements.txt', 'w', encoding='utf-8') as f:
        f.write(req)
    print('OK: added to requirements.txt:', ', '.join(missing))
else:
    print('SKIP: YouTube upload packages already in requirements.txt')

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

changed = []

# 1. Add YouTube OAuth config alongside the other API key definitions.
old_key = "ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')"
new_key = (
    "ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')\n"
    "YOUTUBE_CLIENT_ID = os.environ.get('YOUTUBE_CLIENT_ID')\n"
    "YOUTUBE_CLIENT_SECRET = os.environ.get('YOUTUBE_CLIENT_SECRET')\n"
    "YOUTUBE_REDIRECT_URI = os.environ.get('YOUTUBE_REDIRECT_URI', 'https://viralcut.xyz/youtube/callback')"
)
if old_key in src and 'YOUTUBE_CLIENT_ID' not in src:
    src = src.replace(old_key, new_key, 1)
    changed.append('added YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REDIRECT_URI config')
elif 'YOUTUBE_CLIENT_ID' in src:
    print('SKIP: YouTube OAuth config already present')
else:
    print('FAILED: could not find ANTHROPIC_API_KEY line. No changes made.')

# 2. Add the youtube_tokens table to init_db(), right after the
#    `preferences` table block.
old_table_block = """    db.execute('''
        CREATE TABLE IF NOT EXISTS preferences (
            user_id INTEGER,
            key     TEXT,
            value   TEXT,
            PRIMARY KEY (user_id, key)
        )
    ''')
    db.commit()"""
new_table_block = """    db.execute('''
        CREATE TABLE IF NOT EXISTS preferences (
            user_id INTEGER,
            key     TEXT,
            value   TEXT,
            PRIMARY KEY (user_id, key)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS youtube_tokens (
            user_id       INTEGER PRIMARY KEY,
            refresh_token TEXT NOT NULL,
            access_token  TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        )
    ''')
    db.commit()"""
if old_table_block in src:
    src = src.replace(old_table_block, new_table_block, 1)
    changed.append('added the youtube_tokens table')
elif 'CREATE TABLE IF NOT EXISTS youtube_tokens' in src:
    print('SKIP: youtube_tokens table already present')
else:
    print('FAILED: could not find the preferences table block in init_db(). No changes made there.')

# 3. Add the 4 new routes right before the final `if __name__ == ...` block
#    (after the existing /download/<id> route).
anchor = "if __name__ == '__main__':"
youtube_routes = """@app.route('/youtube/connect')
@login_required
def youtube_connect():
    if not YOUTUBE_CLIENT_ID or not YOUTUBE_CLIENT_SECRET:
        return "YouTube publishing isn't set up yet (missing YOUTUBE_CLIENT_ID/YOUTUBE_CLIENT_SECRET).", 500
    from urllib.parse import urlencode
    state = uuid.uuid4().hex
    session['youtube_oauth_state'] = state
    params = {
        'client_id': YOUTUBE_CLIENT_ID,
        'redirect_uri': YOUTUBE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'https://www.googleapis.com/auth/youtube.upload',
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state,
    }
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params))

@app.route('/youtube/callback')
@login_required
def youtube_callback():
    if request.args.get('error'):
        return redirect('/?youtube_error=' + request.args.get('error'))
    state = request.args.get('state')
    if not state or state != session.get('youtube_oauth_state'):
        return redirect('/?youtube_error=bad_state')
    code = request.args.get('code')
    if not code:
        return redirect('/?youtube_error=no_code')
    try:
        resp = requests.post('https://oauth2.googleapis.com/token', data={
            'client_id': YOUTUBE_CLIENT_ID,
            'client_secret': YOUTUBE_CLIENT_SECRET,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': YOUTUBE_REDIRECT_URI,
        }, timeout=20)
        resp.raise_for_status()
        tokens = resp.json()
        refresh_token = tokens.get('refresh_token')
        access_token = tokens.get('access_token')
        if not refresh_token:
            return redirect('/?youtube_error=no_refresh_token')
        db = get_db()
        db.execute('''
            INSERT INTO youtube_tokens (user_id, refresh_token, access_token)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET refresh_token=excluded.refresh_token, access_token=excluded.access_token
        ''', (current_user_id(), refresh_token, access_token))
        db.commit()
        db.close()
        return redirect('/?youtube=connected')
    except Exception as e:
        print('[youtube] OAuth callback failed:', e, flush=True)
        return redirect('/?youtube_error=1')

@app.route('/api/youtube/status')
@login_required
def youtube_status():
    db = get_db()
    row = db.execute('SELECT user_id FROM youtube_tokens WHERE user_id = ?', (current_user_id(),)).fetchone()
    db.close()
    return jsonify({'connected': bool(row), 'configured': bool(YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET)})

@app.route('/api/clips/<int:clip_id>/publish-youtube', methods=['POST'])
@login_required
def publish_to_youtube(clip_id):
    user_id = current_user_id()
    db = get_db()
    tok_row = db.execute('SELECT refresh_token FROM youtube_tokens WHERE user_id = ?', (user_id,)).fetchone()
    clip_row = db.execute('SELECT * FROM clips WHERE id = ? AND user_id = ?', (clip_id, user_id)).fetchone()
    db.close()
    if not tok_row:
        return jsonify({'error': 'Connect your YouTube account first.'}), 400
    if not clip_row:
        return jsonify({'error': 'Clip not found'}), 404

    download_url = r2_presigned_download_url(clip_row['filename'], os.path.basename(clip_row['filename']))
    if not download_url:
        return jsonify({'error': 'Clip storage not available'}), 500

    tmp_path = os.path.join(OUTPUT_FOLDER, f"yt_upload_{clip_id}_{uuid.uuid4().hex[:8]}.mp4")
    try:
        with requests.get(download_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(tmp_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)

        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds = Credentials(
            None,
            refresh_token=tok_row['refresh_token'],
            token_uri='https://oauth2.googleapis.com/token',
            client_id=YOUTUBE_CLIENT_ID,
            client_secret=YOUTUBE_CLIENT_SECRET,
        )
        creds.refresh(GoogleAuthRequest())

        try:
            hashtags = json.loads(clip_row['hashtags'] or '[]')
        except Exception:
            hashtags = []
        hashtag_text = ' '.join(h if h.startswith('#') else f'#{h}' for h in hashtags)

        title = (clip_row['title'] or clip_row['caption'] or 'New Clip').strip()
        if len(title) > 90:
            title = title[:90].rstrip()
        if '#shorts' not in title.lower():
            title = f"{title} #Shorts"

        description = ((clip_row['caption'] or '') + '\\n\\n' + hashtag_text).strip()
        tags = [h.lstrip('#') for h in hashtags][:15]

        youtube = build('youtube', 'v3', credentials=creds)
        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': tags,
                'categoryId': '22',
            },
            'status': {
                'privacyStatus': 'public',
                'selfDeclaredMadeForKids': False,
            },
        }
        media = MediaFileUpload(tmp_path, chunksize=-1, resumable=True, mimetype='video/mp4')
        upload_request = youtube.videos().insert(part='snippet,status', body=body, media_body=media)
        response = None
        while response is None:
            status, response = upload_request.next_chunk()
        video_id = response.get('id')
        return jsonify({'success': True, 'youtube_url': f'https://youtube.com/watch?v={video_id}'})
    except Exception as e:
        print('[youtube] publish failed:', e, flush=True)
        return jsonify({'error': f'Publish failed: {e}'}), 500
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

""" + anchor

n = src.count(anchor)
if n == 1 and 'def youtube_connect' not in src:
    src = src.replace(anchor, youtube_routes, 1)
    changed.append('added /youtube/connect, /youtube/callback, /api/youtube/status, /api/clips/<id>/publish-youtube')
elif 'def youtube_connect' in src:
    print('SKIP: YouTube routes already present')
else:
    print(f"FAILED: expected exactly 1 match for \"if __name__ == '__main__':\", found {n}. No changes made there.")

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

for c in changed:
    print('OK:', c)

print('')
print('Part 1 of 2 done (backend). Run fix_wire_up_youtube.py next.')
