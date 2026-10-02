$path = "paypal_subscriptions.py"
$text = [System.IO.File]::ReadAllText((Resolve-Path $path), [System.Text.Encoding]::UTF8)

$oldRoute = @'
@paypal_bp.route("/api/paypal-webhook", methods=["POST"])
def paypal_webhook():
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
'@

$newRoute = @'
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
'@

if ($text.Contains($oldRoute)) {
    $text = $text.Replace($oldRoute, $newRoute)
    Write-Host "OK: replaced webhook route with verified version."
} else {
    Write-Host "FAILED: could not find exact old webhook route block."
}

if (-not $text.Contains("`nimport json`n")) {
    $text = $text.Replace("import os`n", "import os`nimport json`n")
    Write-Host "OK: added json import."
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Resolve-Path $path), $text, $utf8NoBom)

Write-Host ""
python -c "import ast; ast.parse(open('$path', encoding='utf-8').read()); print('SYNTAX OK')"
