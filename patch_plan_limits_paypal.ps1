$path = "razorpay_subscriptions.py"
$text = [System.IO.File]::ReadAllText((Resolve-Path $path), [System.Text.Encoding]::UTF8)

$old = @'
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
'@

$new = @'
def get_user_plan_limits(user_id):
    if not user_id:
        return PLAN_LIMITS[None]
    db = get_db_conn()
    row = db.execute(
        "SELECT plan_key FROM subscriptions WHERE user_id=? AND status IN ('active','authenticated') ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    plan_key = row['plan_key'] if row else None

    if not plan_key:
        try:
            prow = db.execute(
                "SELECT plan_key FROM paypal_subscriptions WHERE user_id=? AND status IN ('ACTIVE','APPROVED') ORDER BY id DESC LIMIT 1",
                (user_id,)
            ).fetchone()
            if prow:
                plan_key = prow['plan_key']
        except Exception:
            pass

    db.close()
    return PLAN_LIMITS.get(plan_key, PLAN_LIMITS[None])
'@

if ($text.Contains($old)) {
    $text = $text.Replace($old, $new)
    Write-Host "OK: get_user_plan_limits now also checks paypal_subscriptions."
} else {
    Write-Host "FAILED: could not find exact old function block."
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Resolve-Path $path), $text, $utf8NoBom)

Write-Host ""
python -c "import ast; ast.parse(open('$path', encoding='utf-8').read()); print('SYNTAX OK')"
