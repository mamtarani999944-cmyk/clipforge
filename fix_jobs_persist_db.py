"""
Fixes 'Unknown job' errors by moving job status out of an in-memory dict
and into SQLite (same DB everything else already uses).

Why: the in-memory `_jobs` dict only exists inside one gunicorn worker
process. If that process restarts for ANY reason between job creation and
your poll -- loading the Whisper model + a full audio buffer is memory
heavy and can get OOM-killed, or Railway does a health-check restart, or a
redeploy rolls the process -- the new process starts with an empty dict and
every poll for jobs created before the restart returns 404 "Unknown job".
A DB row survives process restarts.

Run from your clipforge-final project folder:
    python fix_jobs_persist_db.py
"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

changed = []

# 1. Remove the in-memory job store (threading.Lock stays unused elsewhere is fine,
#    but we no longer need _jobs/_jobs_lock).
old_store = """_jobs = {}
_jobs_lock = threading.Lock()
"""
if old_store in src:
    src = src.replace(old_store, "", 1)
    changed.append('removed in-memory _jobs store')

# 2. Add a `jobs` table in init_db(), right before the `preferences` table.
anchor = """    db.execute('''
        CREATE TABLE IF NOT EXISTS preferences ("""
new_jobs_table = """    db.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            id         TEXT PRIMARY KEY,
            user_id    INTEGER,
            status     TEXT NOT NULL,
            clips_json TEXT,
            error      TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
"""
if 'CREATE TABLE IF NOT EXISTS jobs' not in src:
    src = src.replace(anchor, new_jobs_table + anchor, 1)
    changed.append('added jobs table to init_db()')

# 3. Replace the job-creation line in upload()
old_create = """    with _jobs_lock:
        _jobs[job_id] = {'status': 'processing', 'clips': None, 'error': None}
"""
new_create = """    _jdb = get_db()
    _jdb.execute('INSERT INTO jobs (id, user_id, status) VALUES (?, ?, ?)', (job_id, user_id, 'processing'))
    _jdb.commit()
    _jdb.close()
"""
if old_create in src:
    src = src.replace(old_create, new_create, 1)
    changed.append('job creation now writes a DB row')

# 4. Replace the three places _run_job writes job status (error-no-clips, success, exception)
old_err_noclips = """            if not clips:
                with _jobs_lock:
                    _jobs[job_id] = {'status': 'error', 'clips': None,
                                      'error': 'Could not generate clips. Make sure ffmpeg is installed.'}
                return
"""
new_err_noclips = """            if not clips:
                _jdb = get_db()
                _jdb.execute('UPDATE jobs SET status=?, error=? WHERE id=?',
                             ('error', 'Could not generate clips. Make sure ffmpeg is installed.', job_id))
                _jdb.commit()
                _jdb.close()
                return
"""
if old_err_noclips in src:
    src = src.replace(old_err_noclips, new_err_noclips, 1)
    changed.append('no-clips error now written to DB')

old_success = """            with _jobs_lock:
                _jobs[job_id] = {'status': 'done', 'clips': clips, 'error': None}
"""
new_success = """            _jdb = get_db()
            _jdb.execute('UPDATE jobs SET status=?, clips_json=? WHERE id=?',
                         ('done', json.dumps(clips), job_id))
            _jdb.commit()
            _jdb.close()
"""
if old_success in src:
    src = src.replace(old_success, new_success, 1)
    changed.append('success status now written to DB')

old_exc = """            with _jobs_lock:
                _jobs[job_id] = {'status': 'error', 'clips': None, 'error': str(e)}
"""
new_exc = """            _jdb = get_db()
            _jdb.execute('UPDATE jobs SET status=?, error=? WHERE id=?', ('error', str(e), job_id))
            _jdb.commit()
            _jdb.close()
"""
if old_exc in src:
    src = src.replace(old_exc, new_exc, 1)
    changed.append('exception status now written to DB')

# 5. Replace job_status() route body to read from DB
old_status_route = """@app.route('/api/jobs/<job_id>')
@login_required
def job_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({'status': 'error', 'error': 'Unknown job'}), 404
    if job['status'] == 'done':
        # Free the slot once the client has it; keep a short grace window
        # in case of a duplicate poll in flight.
        resp = {'status': 'done', 'clips': job['clips']}
        return jsonify(resp)
    if job['status'] == 'error':
        return jsonify({'status': 'error', 'error': job['error']})
    return jsonify({'status': 'processing'})
"""
new_status_route = """@app.route('/api/jobs/<job_id>')
@login_required
def job_status(job_id):
    db = get_db()
    row = db.execute('SELECT status, clips_json, error FROM jobs WHERE id = ? AND user_id = ?',
                      (job_id, current_user_id())).fetchone()
    db.close()
    if not row:
        return jsonify({'status': 'error', 'error': 'Unknown job'}), 404
    if row['status'] == 'done':
        return jsonify({'status': 'done', 'clips': json.loads(row['clips_json'] or '[]')})
    if row['status'] == 'error':
        return jsonify({'status': 'error', 'error': row['error']})
    return jsonify({'status': 'processing'})
"""
if old_status_route in src:
    src = src.replace(old_status_route, new_status_route, 1)
    changed.append('job_status route now reads from DB')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

if changed:
    for c in changed:
        print('OK:', c)
else:
    print('FAILED: no matching blocks found -- app.py may already differ from what this script expects.')
    print('No changes made.')

print('')
print('Verify with:')
print('''python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('app.py SYNTAX OK')"''')
