$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# ---------- 1. paypal_subscriptions.py ----------
$ppPath = "paypal_subscriptions.py"
$ppText = [System.IO.File]::ReadAllText((Resolve-Path $ppPath), [System.Text.Encoding]::UTF8)

$oldImport = 'from flask import Blueprint, request, jsonify, session, redirect'
$newImport = 'from flask import Blueprint, request, jsonify, session, redirect, render_template'
if ($ppText.Contains($oldImport)) {
    $ppText = $ppText.Replace($oldImport, $newImport)
    Write-Host "OK: added render_template import to paypal_subscriptions.py"
} else {
    Write-Host "WARN: import line not found as expected (may already be updated)"
}

$addition = @'


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
        "SELECT plan_key FROM paypal_subscriptions WHERE user_id=? AND status IN ('ACTIVE','APPROVED') ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    db.close()
    plan_key = row['plan_key'] if row else None
    return PLAN_LIMITS.get(plan_key, PLAN_LIMITS[None])


@paypal_bp.route('/pricing')
def pricing():
    user = current_user()
    my_status = None
    if user:
        db = get_db_conn()
        row = db.execute(
            "SELECT plan_key, status FROM paypal_subscriptions WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user['id'],)
        ).fetchone()
        db.close()
        if row:
            my_status = {'plan_key': row['plan_key'], 'status': row['status']}
    return render_template('pricing.html', user=user, plans=PAYPAL_PLANS, my_status=my_status)
'@

if (-not $ppText.Contains('def get_user_plan_limits')) {
    $ppText = $ppText.TrimEnd() + "`n" + $addition + "`n"
    Write-Host "OK: appended PLAN_LIMITS, get_user_plan_limits, and /pricing route to paypal_subscriptions.py"
} else {
    Write-Host "WARN: get_user_plan_limits already present in paypal_subscriptions.py, skipped"
}

[System.IO.File]::WriteAllText((Resolve-Path $ppPath), $ppText, $utf8NoBom)

# ---------- 2. app.py ----------
$appPath = "app.py"
$appText = [System.IO.File]::ReadAllText((Resolve-Path $appPath), [System.Text.Encoding]::UTF8)

$old1 = "from razorpay_subscriptions import razorpay_bp, init_subscriptions_db`nfrom paypal_subscriptions import paypal_bp, init_paypal_db"
$new1 = "from paypal_subscriptions import paypal_bp, init_paypal_db"
if ($appText.Contains($old1)) {
    $appText = $appText.Replace($old1, $new1)
    Write-Host "OK: removed razorpay import line from app.py"
} else {
    Write-Host "FAILED: import block not found exactly, app.py NOT changed for imports"
}

$old2 = "app.register_blueprint(razorpay_bp)`ninit_subscriptions_db()`napp.register_blueprint(paypal_bp)"
$new2 = "app.register_blueprint(paypal_bp)"
if ($appText.Contains($old2)) {
    $appText = $appText.Replace($old2, $new2)
    Write-Host "OK: removed razorpay blueprint registration from app.py"
} else {
    Write-Host "FAILED: blueprint registration block not found exactly, app.py NOT changed for registration"
}

$old3 = "from razorpay_subscriptions import get_user_plan_limits"
$new3 = "from paypal_subscriptions import get_user_plan_limits"
if ($appText.Contains($old3)) {
    $appText = $appText.Replace($old3, $new3)
    Write-Host "OK: repointed get_user_plan_limits import to paypal_subscriptions in app.py"
} else {
    Write-Host "FAILED: get_user_plan_limits import line not found, app.py NOT changed for that line"
}

[System.IO.File]::WriteAllText((Resolve-Path $appPath), $appText, $utf8NoBom)

# ---------- 3. templates/pricing.html ----------
$htmlPath = "templates\pricing.html"
$newHtml = @'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Pricing - ClipForge</title>
<style>
  body { font-family: Arial, sans-serif; background:#0d0d0d; margin:0; padding:40px 20px; }
  h1 { text-align:center; color:#fff; }
  p.subtitle { text-align:center; color:#999; }
  .plans { display:flex; gap:16px; justify-content:center; flex-wrap:wrap; margin-top:32px; }
  .plan { background:#1a1a1a; border-radius:12px; padding:24px; width:220px; text-align:center; }
  .plan h3 { color:#fff; margin:0 0 8px; }
  .plan .price { font-size:28px; color:#fff; font-weight:bold; margin:12px 0; }
  .plan .price span { font-size:14px; color:#999; font-weight:normal; }
  .plan button { background:#0070ba; color:#fff; border:none; border-radius:6px; padding:12px 24px; cursor:pointer; width:100%; font-weight:bold; }
  .plan button:hover { background:#005a94; }
  .current-badge { background:#1a3a2a; color:#4ade80; font-size:12px; padding:4px 10px; border-radius:999px; display:inline-block; margin-bottom:8px; }
  .back-link { display:block; text-align:center; margin-top:32px; color:#999; text-decoration:none; }
</style>
</head>
<body>
<h1>Choose your ClipForge plan</h1>
<p class="subtitle">Pay with PayPal (USD)</p>
<div class="plans">
  {% for key, plan in plans.items() %}
  <div class="plan">
    {% if my_status and my_status.plan_key == key and my_status.status in ['ACTIVE', 'APPROVED'] %}
      <div class="current-badge">Current plan</div>
    {% endif %}
    <h3>{{ plan.name }}</h3>
    <div class="price">${{ plan.amount }} <span>/ month</span></div>
    <button onclick="paypalSubscribe('{{ key }}')">Subscribe</button>
  </div>
  {% endfor %}
</div>
<a class="back-link" href="/">&larr; Back to dashboard</a>
<script>
async function paypalSubscribe(planKey) {
  try {
    const resp = await fetch('/api/paypal-create-subscription', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({plan_key: planKey})
    });
    const data = await resp.json();
    if (data.approval_url) {
      window.location.href = data.approval_url;
    } else {
      alert('Failed to start PayPal subscription: ' + (data.error || 'unknown error'));
    }
  } catch (e) {
    alert('Error: ' + e.message);
  }
}
</script>
</body>
</html>
'@
[System.IO.File]::WriteAllText((Resolve-Path $htmlPath), $newHtml, $utf8NoBom)
Write-Host "OK: rewrote templates/pricing.html with PayPal-only content"

# ---------- 4. delete razorpay_subscriptions.py ----------
if (Test-Path "razorpay_subscriptions.py") {
    Remove-Item "razorpay_subscriptions.py" -Force
    Write-Host "OK: deleted razorpay_subscriptions.py"
}

# ---------- syntax check ----------
Write-Host ""
python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('app.py SYNTAX OK')"
python -c "import ast; ast.parse(open('paypal_subscriptions.py', encoding='utf-8').read()); print('paypal_subscriptions.py SYNTAX OK')"
