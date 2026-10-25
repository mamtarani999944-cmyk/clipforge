# Razorpay one-time 30-day plan payments (INR). Works next to paypal_subscriptions.
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
