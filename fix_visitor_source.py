"""
Fixes the visitor counter so Product Hunt (and other) visitors are not all
shown as "direct".

Problem: your home page sends people to /login, and that jump lost the
"came from Product Hunt" information. Now the first source is remembered
for the visit and used on every page they open.

Run from your clipforge-final project folder:
    python fix_visitor_source.py
"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

if '_remember_source' in src:
    print('SKIP: source fix already present')
    raise SystemExit

old_def = 'def _visit_source():\n'
anchor = '@app.after_request\ndef _track_visit(resp):'
problems = []
if '_track_visit' not in src:
    problems.append('visitor counter not found (run fix_add_visitor_counter.py first)')
else:
    if src.count(old_def) != 1:
        problems.append(f'_visit_source found {src.count(old_def)} times (expected 1)')
    if src.count(anchor) != 1:
        problems.append(f'_track_visit found {src.count(anchor)} times (expected 1)')

if problems:
    print('FAILED, nothing was changed:')
    for p in problems:
        print(' -', p)
    raise SystemExit

new_block = '''@app.before_request
def _remember_source():
    try:
        if request.method == 'GET' and not request.path.startswith(_SKIP_PREFIXES) and 'src' not in session:
            s = _visit_source_raw()
            if s and s != 'direct':
                session['src'] = s
    except Exception:
        pass

def _visit_source():
    return session.get('src') or _visit_source_raw()

'''
src = src.replace(old_def, 'def _visit_source_raw():\n', 1)
src = src.replace(anchor, new_block + anchor, 1)
with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)
print('OK: visitor source is now remembered across pages')
