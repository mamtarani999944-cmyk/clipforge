$path = "razorpay_subscriptions.py"
$addition = @"


PLAN_LIMITS = {
    None: {'max_clips': 3, 'max_duration': 30},
    'basic': {'max_clips': 4, 'max_duration': 45},
    'pro': {'max_clips': 5, 'max_duration': 60},
    'premium': {'max_clips': 6, 'max_duration': 60},
}


def get_user_plan_limits(user_id):
    if not user_id:
        return PLAN_LIMITS[None]
    db = get_db_conn()
    row = db.execute(
        "SELECT plan_key FROM subscriptions WHERE user_id=? AND status IN ('active','authenticated') ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    db.close()
    plan_key = row['plan_key'] if row else None
    return PLAN_LIMITS.get(plan_key, PLAN_LIMITS[None])
"@

$existing = [System.IO.File]::ReadAllText((Resolve-Path $path), [System.Text.Encoding]::UTF8)
if ($existing.Contains("PLAN_LIMITS")) {
    Write-Host "SKIPPED: PLAN_LIMITS already present."
} else {
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::AppendAllText((Resolve-Path $path), $addition, $utf8NoBom)
    Write-Host "OK: appended PLAN_LIMITS and get_user_plan_limits."
}

Write-Host ""
python -c "import ast; ast.parse(open('$path', encoding='utf-8').read()); print('SYNTAX OK')"
