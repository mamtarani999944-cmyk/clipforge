$content = Get-Content -Raw -Path "app.py"

$anchor1 = "from login_notification_email import send_login_notification"
$insert1 = "from login_notification_email import send_login_notification`nfrom razorpay_subscriptions import razorpay_bp, init_subscriptions_db"

if ($content -like "*$anchor1*") {
    $content = $content.Replace($anchor1, $insert1)
    Write-Host "Inserted razorpay import."
} else {
    Write-Host "WARNING: anchor1 not found, import not inserted."
}

$anchor2 = "app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')"
$insert2 = "app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')`napp.register_blueprint(razorpay_bp)`ninit_subscriptions_db()"

if ($content -like "*$anchor2*") {
    $content = $content.Replace($anchor2, $insert2)
    Write-Host "Inserted blueprint registration."
} else {
    Write-Host "WARNING: anchor2 not found, blueprint registration not inserted."
}

Set-Content -Path "app.py" -Value $content -Encoding UTF8 -NoNewline
Write-Host "Done. Showing first 25 lines:"
Get-Content "app.py" | Select-Object -First 25
