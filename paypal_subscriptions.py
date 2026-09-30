import os
import json
import requests
from flask import Blueprint, request, jsonify, session, redirect

paypal_bp = Blueprint("paypal_bp", __name__)

PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET")
PAYPAL_API_BASE = "https://api-m.paypal.com"

PAYPAL_PLANS = {
    "basic": {"plan_id": "P-0T2151043JY075461LNK6P7EI", "name": "ClipForge Basic", "amount": 5},
    "pro": {"plan_id": "P-0LM6980711937220BNK6P7NY", "name": "ClipForge Pro", "amount": 12},
    "premium": {"plan_id": "P-30M32449WJ4747317NK6P7VQ", "name": "ClipForge Premium", "amount": 18},
}


def get_paypal_token():
    resp = requests.post(
        f"{PAYPAL_API_BASE}/v1/oauth2/token",
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def init_paypal_db():
    import sqlite3
    db = sqlite3.connect("users.db")
    db.execute("""
        CREATE TABLE IF NOT EXISTS paypal_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            plan_key TEXT,
            paypal_subscription_id TEXT,
            status TEXT DEFAULT 'created',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.commit()
    db.close()


def get_db_conn():
    import sqlite3
    db = sqlite3.connect("users.db")
    db.row_factory = sqlite3.Row
    return db


def current_user():
    return session.get("user")


def get_paypal_plan_limits(user_id):
    if not user_id:
        return None
    db = get_db_conn()
    row = db.execute(
        "SELECT plan_key FROM paypal_subscriptions WHERE user_id=? AND status IN (\'ACTIVE\',\'APPROVED\') ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    db.close()
    return row["plan_key"] if row else None


@paypal_bp.route("/api/paypal-create-subscription", methods=["POST"])
def paypal_create_subscription():
    user = current_user()
    if not user:
        return jsonify({"error": "Not logged in"}), 401
    data = request.get_json() or {}
    plan_key = data.get("plan_key")
    if plan_key not in PAYPAL_PLANS:
        return jsonify({"error": "Invalid plan"}), 400
    plan = PAYPAL_PLANS[plan_key]
    token = get_paypal_token()
    resp = requests.post(
        f"{PAYPAL_API_BASE}/v1/billing/subscriptions",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "plan_id": plan["plan_id"],
            "custom_id": str(user["id"]),
            "subscriber": {"email_address": user.get("email", "")},
            "application_context": {
                "brand_name": "ClipForge",
                "return_url": request.url_root.rstrip("/") + "/paypal/return",
                "cancel_url": request.url_root.rstrip("/") + "/pricing",
                "user_action": "SUBSCRIBE_NOW",
            }
        },
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        return jsonify({"error": "Failed to create subscription", "details": resp.text}), 500
    sub = resp.json()
    approval_url = next((l["href"] for l in sub.get("links", []) if l.get("rel") == "approve"), None)
    db = get_db_conn()
    db.execute(
        "INSERT INTO paypal_subscriptions (user_id, plan_key, paypal_subscription_id, status) VALUES (?, ?, ?, ?)",
        (user["id"], plan_key, sub["id"], sub.get("status", "APPROVAL_PENDING"))
    )
    db.commit()
    db.close()
    return jsonify({"approval_url": approval_url, "subscription_id": sub["id"]})


@paypal_bp.route("/paypal/return")
def paypal_return():
    sub_id = request.args.get("subscription_id")
    if sub_id:
        token = get_paypal_token()
        resp = requests.get(
            f"{PAYPAL_API_BASE}/v1/billing/subscriptions/{sub_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code == 200:
            status = resp.json().get("status")
            db = get_db_conn()
            db.execute("UPDATE paypal_subscriptions SET status=? WHERE paypal_subscription_id=?", (status, sub_id))
            db.commit()
            db.close()
    return redirect("/pricing?paypal=success")


PAYPAL_WEBHOOK_ID = os.environ.get("PAYPAL_WEBHOOK_ID", "")


def verify_paypal_webhook(headers, body_bytes, token):
    if not PAYPAL_WEBHOOK_ID:
        return True
    payload = {
        "transmission_id": headers.get("Paypal-Transmission-Id"),
        "transmission_time": headers.get("Paypal-Transmission-Time"),
        "cert_url": headers.get("Paypal-Cert-Url"),
        "auth_algo": headers.get("Paypal-Auth-Algo"),
        "transmission_sig": headers.get("Paypal-Transmission-Sig"),
        "webhook_id": PAYPAL_WEBHOOK_ID,
        "webhook_event": json.loads(body_bytes.decode("utf-8")),
    }
    resp = requests.post(
        f"{PAYPAL_API_BASE}/v1/notifications/verify-webhook-signature",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    if resp.status_code != 200:
        return False
    return resp.json().get("verification_status") == "SUCCESS"


@paypal_bp.route("/api/paypal-webhook", methods=["POST"])
def paypal_webhook():
    body_bytes = request.get_data()
    token = get_paypal_token()
    if not verify_paypal_webhook(request.headers, body_bytes, token):
        return jsonify({"error": "Invalid signature"}), 400
    event = request.get_json() or {}
    resource = event.get("resource", {})
    sub_id = resource.get("id")
    status = resource.get("status")
    if sub_id and status:
        db = get_db_conn()
        db.execute("UPDATE paypal_subscriptions SET status=? WHERE paypal_subscription_id=?", (status, sub_id))
        db.commit()
        db.close()
    return jsonify({"status": "ok"})