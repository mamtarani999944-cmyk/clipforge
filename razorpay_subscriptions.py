import os
import hmac
import hashlib
import requests
from flask import Blueprint, request, jsonify, render_template, session

razorpay_bp = Blueprint('razorpay_bp', __name__)

RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET')
RAZORPAY_WEBHOOK_SECRET = os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')

PLANS = {
    'basic': {'plan_id': 'plan_TgZayBKu3tx9lN', 'name': 'ClipForge Basic', 'amount': 399},
    'pro': {'plan_id': 'plan_TgZcbvxhxGHujp', 'name': 'ClipForge Pro', 'amount': 999},
    'premium': {'plan_id': 'plan_TgZiKdnmMSGcav', 'name': 'ClipForge Premium', 'amount': 1499},
}


def init_subscriptions_db():
    import sqlite3
    db = sqlite3.connect('users.db')
    db.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            plan_key TEXT,
            razorpay_subscription_id TEXT,
            status TEXT DEFAULT 'created',
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    db.commit()
    db.close()


def get_db_conn():
    import sqlite3
    db = sqlite3.connect('users.db')
    db.row_factory = sqlite3.Row
    return db


def current_user():
    return session.get('user')


@razorpay_bp.route('/pricing')
def pricing():
    user = current_user()
    my_status = None
    if user:
        db = get_db_conn()
        row = db.execute(
            "SELECT plan_key, status FROM subscriptions WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user['id'],)
        ).fetchone()
        db.close()
        if row:
            my_status = {'plan_key': row['plan_key'], 'status': row['status']}
    return render_template('pricing.html', user=user, plans=PLANS,
                            razorpay_key_id=RAZORPAY_KEY_ID, my_status=my_status)


@razorpay_bp.route('/api/create-subscription', methods=['POST'])
def create_subscription():
    user = current_user()
    if not user:
        return jsonify({'error': 'Not logged in'}), 401

    data = request.get_json() or {}
    plan_key = data.get('plan_key')
    if plan_key not in PLANS:
        return jsonify({'error': 'Invalid plan'}), 400
    plan = PLANS[plan_key]

    resp = requests.post(
        'https://api.razorpay.com/v1/subscriptions',
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
        json={
            'plan_id': plan['plan_id'],
            'total_count': 120,
            'customer_notify': 1,
            'notes': {'user_id': str(user['id']), 'email': user.get('email', '')}
        },
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        return jsonify({'error': 'Failed to create subscription', 'details': resp.text}), 500

    sub = resp.json()
    db = get_db_conn()
    db.execute(
        'INSERT INTO subscriptions (user_id, plan_key, razorpay_subscription_id, status) VALUES (?, ?, ?, ?)',
        (user['id'], plan_key, sub['id'], sub.get('status', 'created'))
    )
    db.commit()
    db.close()

    return jsonify({'subscription_id': sub['id'], 'key_id': RAZORPAY_KEY_ID, 'plan_name': plan['name']})


@razorpay_bp.route('/api/verify-payment', methods=['POST'])
def verify_payment():
    user = current_user()
    if not user:
        return jsonify({'error': 'Not logged in'}), 401

    data = request.get_json() or {}
    payment_id = data.get('razorpay_payment_id')
    subscription_id = data.get('razorpay_subscription_id')
    signature = data.get('razorpay_signature')

    if not (payment_id and subscription_id and signature):
        return jsonify({'error': 'Missing fields'}), 400

    payload = f"{payment_id}|{subscription_id}"
    expected_signature = hmac.new(
        RAZORPAY_KEY_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        return jsonify({'error': 'Invalid signature'}), 400

    db = get_db_conn()
    db.execute(
        "UPDATE subscriptions SET status='active' WHERE razorpay_subscription_id=?",
        (subscription_id,)
    )
    db.commit()
    db.close()

    return jsonify({'success': True})


@razorpay_bp.route('/api/razorpay-webhook', methods=['POST'])
def razorpay_webhook():
    payload = request.get_data()
    sig = request.headers.get('X-Razorpay-Signature', '')

    if RAZORPAY_WEBHOOK_SECRET:
        expected = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return jsonify({'error': 'Invalid signature'}), 400

    event = request.get_json() or {}
    sub_entity = event.get('payload', {}).get('subscription', {}).get('entity', {})
    sub_id = sub_entity.get('id')
    status = sub_entity.get('status')

    if sub_id and status:
        db = get_db_conn()
        db.execute(
            "UPDATE subscriptions SET status=? WHERE razorpay_subscription_id=?",
            (status, sub_id)
        )
        db.commit()
        db.close()

    return jsonify({'status': 'ok'})


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