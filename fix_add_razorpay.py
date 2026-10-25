"""
Adds RAZORPAY back (UPI / cards / net banking, in INR) next to PayPal.

How it works:
  - Pricing page gets an "India" section: Basic / Pro / Premium, paid in INR.
  - Customer pays in the Razorpay popup -> server checks the payment signature
    -> the plan is active for 30 DAYS (one-time pass, no auto-renew; they pay
    again to extend). Paying again before it ends adds 30 more days.
  - Plan limits (clips per run, duration) work for Razorpay AND PayPal users:
    the higher plan wins. The free daily limit still applies to users with no plan.
  - PayPal is not touched.

Needs these Railway Variables (Variables > New Variable):
    RAZORPAY_KEY_ID          (starts with rzp_live_  or rzp_test_ for testing)
    RAZORPAY_KEY_SECRET
  Optional, recommended:
    RAZORPAY_WEBHOOK_SECRET  (a password YOU choose; same value goes in Razorpay
                              Dashboard > Webhooks). It saves a payment when the
                              customer closes the tab right after paying.

Prices (INR) are in RAZORPAY_PRICES in razorpay_payments.py. Edit there if you want.

Run from your clipforge-final folder:
    python fix_add_razorpay.py
"""
import os
import re

failed = []
changed = []

MODULE = r'''# Razorpay one-time 30-day plan payments (INR). Works next to paypal_subscriptions.
import os, hmac, hashlib, json, time
import requests
from flask import Blueprint, request, jsonify

from paypal_subscriptions import PLAN_LIMITS, get_db_conn, current_user, get_user_plan_limits as _paypal_limits

razorpay_bp = Blueprint('razorpay_bp', __name__)

RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET')
RAZORPAY_WEBHOOK_SECRET = os.environ.get('RAZORPAY_WEBHOOK_SECRET')

# Price per 30 days, in rupees. Change these numbers if you want other prices.
RAZORPAY_PRICES = {'basic': 199, 'pro': 449, 'premium': 699}
PASS_DAYS = 30
_RANK = {None: 0, 'basic': 1, 'pro': 2, 'premium': 3}


def _ensure_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS razorpay_payments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            plan_key   TEXT NOT NULL,
            order_id   TEXT UNIQUE,
            payment_id TEXT,
            amount     INTEGER,
            status     TEXT DEFAULT 'created',
            created_at TEXT DEFAULT (datetime('now')),
            expires_at INTEGER
        )
    """)
    db.commit()


def get_user_plan_limits(user_id):
    # The better of: active PayPal plan, active Razorpay pass. Returns objects from PLAN_LIMITS.
    best = _paypal_limits(user_id)
    if not user_id:
        return best
    try:
        db = get_db_conn()
        try:
            _ensure_table(db)
            rows = db.execute(
                "SELECT plan_key FROM razorpay_payments WHERE user_id=? AND status='paid' AND expires_at > ?",
                (user_id, int(time.time()))
            ).fetchall()
        finally:
            db.close()
        top = None
        for r in rows:
            if _RANK.get(r['plan_key'], 0) > _RANK.get(top, 0):
                top = r['plan_key']
        if top and top in PLAN_LIMITS:
            cand = PLAN_LIMITS[top]
            if cand.get('max_clips', 0) >= best.get('max_clips', 0):
                return cand
    except Exception as e:
        print('[razorpay] plan lookup failed:', e, flush=True)
    return best


def _activate(db, order_id, payment_id):
    # Marks an order paid and gives 30 days (extends if a pass is still running). Safe to call twice.
    row = db.execute('SELECT * FROM razorpay_payments WHERE order_id=?', (order_id,)).fetchone()
    if not row:
        return False
    if row['status'] == 'paid':
        return True
    now = int(time.time())
    cur = db.execute(
        "SELECT MAX(expires_at) AS e FROM razorpay_payments WHERE user_id=? AND plan_key=? AND status='paid' AND expires_at > ?",
        (row['user_id'], row['plan_key'], now)
    ).fetchone()
    start = max(now, (cur['e'] or 0) if cur else 0)
    db.execute(
        "UPDATE razorpay_payments SET status='paid', payment_id=?, expires_at=? WHERE order_id=? AND status='created'",
        (payment_id, start + PASS_DAYS * 86400, order_id)
    )
    db.commit()
    return True


@razorpay_bp.route('/api/razorpay-create-order', methods=['POST'])
def razorpay_create_order():
    user = current_user()
    if not user:
        return jsonify({'error': 'login'}), 401
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return jsonify({'error': 'Razorpay is not set up yet.'}), 500
    plan_key = (request.get_json(silent=True) or {}).get('plan_key')
    if plan_key not in RAZORPAY_PRICES:
        return jsonify({'error': 'Unknown plan'}), 400
    amount_paise = RAZORPAY_PRICES[plan_key] * 100
    try:
        r = requests.post('https://api.razorpay.com/v1/orders', auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET), json={
            'amount': amount_paise, 'currency': 'INR',
            'receipt': 'vc_%s_%d' % (user['id'], int(time.time())),
            'notes': {'user_id': str(user['id']), 'plan_key': plan_key},
        }, timeout=20)
        if not r.ok:
            try:
                msg = r.json().get('error', {}).get('description') or 'Razorpay error'
            except Exception:
                msg = 'Razorpay error'
            print('[razorpay] create order failed:', r.status_code, msg, flush=True)
            return jsonify({'error': msg}), 502
        order_id = r.json()['id']
        db = get_db_conn()
        try:
            _ensure_table(db)
            db.execute('INSERT INTO razorpay_payments (user_id, plan_key, order_id, amount) VALUES (?, ?, ?, ?)',
                       (user['id'], plan_key, order_id, amount_paise))
            db.commit()
        finally:
            db.close()
        return jsonify({'order_id': order_id, 'amount': amount_paise, 'key_id': RAZORPAY_KEY_ID,
                        'name': 'ViralCut', 'description': plan_key.capitalize() + ' plan, 30 days',
                        'email': user.get('email') if isinstance(user, dict) else None})
    except Exception as e:
        print('[razorpay] create order error:', e, flush=True)
        return jsonify({'error': 'Could not start payment. Please try again.'}), 500


@razorpay_bp.route('/api/razorpay-verify', methods=['POST'])
def razorpay_verify():
    user = current_user()
    if not user:
        return jsonify({'error': 'login'}), 401
    d = request.get_json(silent=True) or {}
    order_id = str(d.get('razorpay_order_id') or '')
    payment_id = str(d.get('razorpay_payment_id') or '')
    signature = str(d.get('razorpay_signature') or '')
    if not (order_id and payment_id and signature) or not RAZORPAY_KEY_SECRET:
        return jsonify({'error': 'Missing payment details'}), 400
    expected = hmac.new(RAZORPAY_KEY_SECRET.encode(), (order_id + '|' + payment_id).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        print('[razorpay] bad signature for order', order_id, flush=True)
        return jsonify({'error': 'Payment could not be verified. If money was taken, email us.'}), 400
    db = get_db_conn()
    try:
        _ensure_table(db)
        row = db.execute('SELECT user_id FROM razorpay_payments WHERE order_id=?', (order_id,)).fetchone()
        if not row or row['user_id'] != user['id']:
            return jsonify({'error': 'Order not found'}), 404
        ok = _activate(db, order_id, payment_id)
    finally:
        db.close()
    return jsonify({'success': bool(ok)})


@razorpay_bp.route('/api/razorpay-webhook', methods=['POST'])
def razorpay_webhook():
    # Backup: Razorpay tells us a payment was captured even if the customer closed the tab.
    if not RAZORPAY_WEBHOOK_SECRET:
        return jsonify({'ok': False}), 503
    body = request.get_data()
    sig = request.headers.get('X-Razorpay-Signature', '')
    expected = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return jsonify({'ok': False}), 400
    try:
        evt = json.loads(body.decode('utf-8'))
        if evt.get('event') in ('payment.captured', 'order.paid'):
            pay = (((evt.get('payload') or {}).get('payment') or {}).get('entity')) or {}
            order_id, payment_id = pay.get('order_id'), pay.get('id')
            if order_id and payment_id and pay.get('currency') == 'INR':
                db = get_db_conn()
                try:
                    _ensure_table(db)
                    row = db.execute('SELECT amount FROM razorpay_payments WHERE order_id=?', (order_id,)).fetchone()
                    if row and row['amount'] == pay.get('amount'):
                        _activate(db, order_id, payment_id)
                finally:
                    db.close()
    except Exception as e:
        print('[razorpay] webhook error:', e, flush=True)
    return jsonify({'ok': True})
'''

PRICING_BLOCK = r'''<div class="inr-section" style="text-align:center;margin-top:24px">
  <h2 style="color:#fff;margin:0 0 4px">India: pay with UPI / card (INR)</h2>
  <p style="color:#999;margin:0">One payment = 30 days. Pay again to add 30 more days.</p>
</div>
<div class="plans">
  <div class="plan"><h3>Basic</h3><div class="price">&#8377;199 <span>/ 30 days</span></div><button style="background:#2563eb" onclick="razorpayPay('basic')">Pay with Razorpay</button></div>
  <div class="plan"><h3>Pro</h3><div class="price">&#8377;449 <span>/ 30 days</span></div><button style="background:#2563eb" onclick="razorpayPay('pro')">Pay with Razorpay</button></div>
  <div class="plan"><h3>Premium</h3><div class="price">&#8377;699 <span>/ 30 days</span></div><button style="background:#2563eb" onclick="razorpayPay('premium')">Pay with Razorpay</button></div>
</div>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<script>
async function razorpayPay(planKey) {
  try {
    const resp = await fetch('/api/razorpay-create-order', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({plan_key: planKey})});
    if (resp.status === 401) { window.location.href = '/login'; return; }
    const o = await resp.json();
    if (!o.order_id) { alert('Could not start payment: ' + (o.error || 'unknown error')); return; }
    const rz = new Razorpay({
      key: o.key_id, amount: o.amount, currency: 'INR', name: o.name, description: o.description, order_id: o.order_id,
      prefill: {email: o.email || ''},
      theme: {color: '#f97316'},
      handler: async function (r) {
        const vr = await fetch('/api/razorpay-verify', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(r)});
        const vd = await vr.json();
        if (vd.success) { alert('Payment successful! Your plan is active for 30 days.'); window.location.href = '/'; }
        else { alert(vd.error || 'Payment could not be verified. Please contact us.'); }
      }
    });
    rz.on('payment.failed', function (e) { alert('Payment failed: ' + ((e.error && e.error.description) || 'please try again')); });
    rz.open();
  } catch (e) { alert('Error: ' + e.message); }
}
</script>
<p class="subtitle" style="margin-top:36px">International customers: pay with PayPal (USD)</p>'''

# ---- read everything first; change nothing unless all anchors are found ----
def read(p):
    with open(p, 'r', encoding='utf-8') as f:
        return f.read()

if os.path.exists('razorpay_payments.py') and 'razorpay_bp' in read('app.py'):
    print('SKIP: Razorpay already added')
    raise SystemExit

for need in ('app.py', 'paypal_subscriptions.py', 'templates/pricing.html'):
    if not os.path.exists(need):
        failed.append(f'{need} not found (are you in the clipforge-final folder?)')

if not failed:
    app = read('app.py')
    pp = read('paypal_subscriptions.py')
    html = read('templates/pricing.html')

    for name in ('PLAN_LIMITS', 'def get_db_conn', 'def current_user', 'def get_user_plan_limits'):
        if name not in pp:
            failed.append(f'paypal_subscriptions.py: "{name}" not found')

    imp = 'from paypal_subscriptions import paypal_bp, init_paypal_db'
    reg = 'app.register_blueprint(paypal_bp)'
    if app.count(imp) != 1:
        failed.append(f'app.py: PayPal import line found {app.count(imp)} times (expected 1)')
    if app.count(reg) != 1:
        failed.append(f'app.py: PayPal blueprint line found {app.count(reg)} times (expected 1)')
    lim = 'from paypal_subscriptions import get_user_plan_limits'
    if app.count(lim) < 1:
        failed.append('app.py: "from paypal_subscriptions import get_user_plan_limits" not found')

    sub = '<p class="subtitle">Pay with PayPal (USD)</p>'
    if html.count(sub) != 1:
        failed.append(f'pricing.html: PayPal subtitle found {html.count(sub)} times (expected 1)')

if failed:
    print('FAILED, nothing was changed:')
    for x in failed:
        print(' -', x)
    raise SystemExit

with open('razorpay_payments.py', 'w', encoding='utf-8') as f:
    f.write(MODULE)
changed.append('created razorpay_payments.py')

app = app.replace(imp, imp + '\nfrom razorpay_payments import razorpay_bp', 1)
app = app.replace(reg, reg + '\napp.register_blueprint(razorpay_bp)', 1)
app = app.replace(lim, 'from razorpay_payments import get_user_plan_limits')
with open('app.py', 'w', encoding='utf-8') as f:
    f.write(app)
changed.append('app.py: registered Razorpay and made plan limits count Razorpay payments')

html = html.replace(sub, PRICING_BLOCK, 1)
with open('templates/pricing.html', 'w', encoding='utf-8') as f:
    f.write(html)
changed.append('pricing.html: added the India (INR) section')

for c in changed:
    print('OK:', c)
print('')
print('NEXT: Railway > Variables: add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET (and optionally RAZORPAY_WEBHOOK_SECRET).')
print('Then: git add . / git commit / git push')
