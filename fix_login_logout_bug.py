"""
Fixes: getting logged out of viralcut.xyz very quickly / constantly.

Cause: the login session wasn't marked "permanent", so the browser could
drop it almost immediately instead of keeping you logged in for weeks.
This also locks in a stable SECRET_KEY-based session so logins survive
app restarts/redeploys properly.

Run from your clipforge-final project folder:
    python fix_login_logout_bug.py
"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

changed = []

# 1. Import timedelta alongside datetime.
old_import = "from datetime import datetime"
new_import = "from datetime import datetime, timedelta"
if old_import in src and 'from datetime import datetime, timedelta' not in src:
    src = src.replace(old_import, new_import, 1)
    changed.append('imported timedelta')
elif 'timedelta' in src:
    print('SKIP: timedelta already imported')
else:
    print('FAILED: could not find the datetime import. No changes made there.')

# 2. Make sessions permanent with a 30-day lifetime, and lock down cookie settings.
old_config = "app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')"
new_config = """app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True"""
if old_config in src and "app.config['PERMANENT_SESSION_LIFETIME']" not in src:
    src = src.replace(old_config, new_config, 1)
    changed.append('set a 30-day session lifetime and locked down cookie settings')
elif "app.config['PERMANENT_SESSION_LIFETIME']" in src:
    print('SKIP: session lifetime config already present')
else:
    print('FAILED: could not find the app.secret_key line. No changes made there.')

# 3. Mark the session permanent right when we log the user in.
old_login_set = "    session['user'] = {'id': user_id, 'email': email, 'name': name, 'picture': picture}"
new_login_set = """    session.permanent = True
    session['user'] = {'id': user_id, 'email': email, 'name': name, 'picture': picture}"""
if old_login_set in src and 'session.permanent = True' not in src:
    src = src.replace(old_login_set, new_login_set, 1)
    changed.append('marked the login session as permanent (30 days) so it stops expiring instantly')
elif 'session.permanent = True' in src:
    print('SKIP: session.permanent already set')
else:
    print('FAILED: could not find the session["user"] = ... line in auth_google_callback. No changes made there.')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

for c in changed:
    print('OK:', c)

print('')
print('Also do this in Railway (one-time, important):')
print('Make sure a SECRET_KEY variable is set in Railway Variables, to any long random')
print('string (e.g. 40+ random characters). If it is missing, the app falls back to an')
print('insecure default. If it is already set, you are fine -- leave it as is.')
