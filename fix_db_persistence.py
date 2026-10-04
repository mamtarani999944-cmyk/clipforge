"""
Fixes user accounts/clips getting wiped on every deploy.

Root cause: DB_PATH = 'users.db' is a relative path, which lands in the
app's own ephemeral container filesystem. Railway containers are rebuilt
from scratch on every deploy -- local files like this SQLite DB don't
survive; a fresh, empty DB file gets created on each new deploy.

Fix: point the DB at a directory controlled by an environment variable
(DATA_DIR). Once you attach a Railway Volume and point DATA_DIR at its
mount path, the DB lives on that persistent disk instead of the ephemeral
container, so it survives every future deploy. Until you set DATA_DIR,
nothing changes -- it falls back to the exact same behavior as before
(current directory), so this is safe to deploy before the volume exists.

Run from your clipforge-final project folder:
    python fix_db_persistence.py
"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

old = """UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
DB_PATH = 'users.db'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)"""

new = """UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
# DATA_DIR should point at a mounted Railway Volume (persistent disk) so the
# database survives redeploys. Falls back to the app's own directory (the
# old, non-persistent behavior) if DATA_DIR isn't set yet.
DATA_DIR = os.environ.get('DATA_DIR', '.')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'users.db')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)"""

if old in src:
    src = src.replace(old, new, 1)
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(src)
    print('OK: DB_PATH now uses DATA_DIR (set DATA_DIR in Railway once your volume is attached)')
elif 'DATA_DIR = os.environ.get' in src:
    print('SKIP: already patched')
else:
    print('FAILED: could not find the exact block to replace. No changes made.')

print('')
print('Verify with:')
print('''python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('app.py SYNTAX OK')"''')
