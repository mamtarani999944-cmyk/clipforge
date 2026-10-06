"""
Adds a daily limit for FREE users: 3 generations per 24 hours.
Paid users (Basic/Pro/Premium) are not limited.
Failed generations don't count.

Run from your clipforge-final project folder:
    python fix_free_daily_limit.py
"""
FREE_DAILY_LIMIT = 3

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

old = """    clip_duration = min(max(clip_duration, 15), _limits['max_duration'])
    job_id = uuid.uuid4().hex
    user_id = current_user_id()
"""
new = """    clip_duration = min(max(clip_duration, 15), _limits['max_duration'])
    job_id = uuid.uuid4().hex
    user_id = current_user_id()

    # Free users: limited number of generations per rolling 24 hours.
    from paypal_subscriptions import PLAN_LIMITS as _PLAN_LIMITS
    if _limits is _PLAN_LIMITS[None]:
        _cdb = get_db()
        _used = _cdb.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE user_id = ? AND status != 'error' AND created_at >= datetime('now', '-1 day')",
            (user_id,)
        ).fetchone()['n']
        _cdb.close()
        if _used >= FREE_DAILY_LIMIT:
            return jsonify({'error': f'Free limit reached ({FREE_DAILY_LIMIT} generations per day). Upgrade your plan on the Pricing page for more, or try again tomorrow.'}), 429
"""
if 'FREE_DAILY_LIMIT' in src:
    print('SKIP: free daily limit already present')
elif old in src:
    src = src.replace(old, new, 1)
    anchor = "ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')"
    if anchor in src:
        src = src.replace(anchor, anchor + "\nFREE_DAILY_LIMIT = " + str(FREE_DAILY_LIMIT) + "  # free-plan generations per rolling 24h", 1)
        with open('app.py', 'w', encoding='utf-8') as f:
            f.write(src)
        print('OK: free users are now limited to', FREE_DAILY_LIMIT, 'generations per day')
        print('(To change the number, edit FREE_DAILY_LIMIT near the top of app.py)')
    else:
        print('FAILED: could not find the ANTHROPIC_API_KEY line. No changes made.')
else:
    print('FAILED: could not find the /upload clip_duration block. No changes made.')
