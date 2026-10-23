"""
Owner accounts (ADMIN_EMAILS) are not blocked by the free daily limit,
so you can test ViralCut without waiting 24 hours. Everyone else is unchanged.

Run from your clipforge-final folder:
    python fix_admin_no_limit.py
"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()
if '_is_owner_test' in src:
    print('SKIP: already present'); raise SystemExit
old = "    if _limits is _PLAN_LIMITS[None]:\n        _cdb = get_db()\n"
new = ("    _is_owner_test = ((session.get('user') or {}).get('email') or '').lower() in ADMIN_EMAILS\n"
       "    if _limits is _PLAN_LIMITS[None] and not _is_owner_test:\n        _cdb = get_db()\n")
if src.count(old) != 1 or 'ADMIN_EMAILS' not in src:
    print(f'FAILED, nothing changed: anchor found {src.count(old)} times, ADMIN_EMAILS present: {"ADMIN_EMAILS" in src}'); raise SystemExit
with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src.replace(old, new, 1))
print('OK: your owner account has no daily limit now (others still limited to 3/day)')
