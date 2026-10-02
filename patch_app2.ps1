$content = [System.IO.File]::ReadAllText((Resolve-Path "app.py"), [System.Text.Encoding]::UTF8)

# 1. Add imports
$anchorImport = "from authlib.integrations.flask_client import OAuth"
$insertImport = "from authlib.integrations.flask_client import OAuth`nfrom login_notification_email import send_login_notification`nfrom razorpay_subscriptions import razorpay_bp, init_subscriptions_db"

if ($content.Contains($anchorImport)) {
    $content = $content.Replace($anchorImport, $insertImport)
    Write-Host "Step 1 OK: imports added."
} else {
    Write-Host "Step 1 FAILED: import anchor not found."
}

# 2. Register blueprint + init subscriptions db
$anchorSecret = "app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')"
$insertSecret = "app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')`napp.register_blueprint(razorpay_bp)`ninit_subscriptions_db()"

if ($content.Contains($anchorSecret)) {
    $content = $content.Replace($anchorSecret, $insertSecret)
    Write-Host "Step 2 OK: blueprint registered."
} else {
    Write-Host "Step 2 FAILED: secret_key anchor not found."
}

# 3. Add the actual call to send_login_notification inside the callback
$anchorSession = "session['user'] = {'id': user_id, 'email': email, 'name': name, 'picture': picture}`n    return redirect(url_for('index'))"
$insertSession = "session['user'] = {'id': user_id, 'email': email, 'name': name, 'picture': picture}`n    try:`n        send_login_notification(email, name, request)`n    except Exception:`n        pass`n    return redirect(url_for('index'))"

if ($content.Contains($anchorSession)) {
    $content = $content.Replace($anchorSession, $insertSession)
    Write-Host "Step 3 OK: send_login_notification call added."
} else {
    Write-Host "Step 3 FAILED: session anchor not found."
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Resolve-Path "app.py"), $content, $utf8NoBom)

Write-Host ""
Write-Host "Verifying (should show 3 matches for imports/call, plus blueprint line):"
Select-String -Path "app.py" -Pattern "login_notification|razorpay_bp|init_subscriptions_db"
