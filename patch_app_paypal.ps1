$path = "app.py"
$lines = [System.Collections.Generic.List[string]]::new()
$lines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path $path)))

$idx1 = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i].Contains("from razorpay_subscriptions import razorpay_bp, init_subscriptions_db")) {
        $idx1 = $i
        break
    }
}

if ($idx1 -eq -1) {
    Write-Host "FAILED: could not find razorpay import line."
} else {
    [string[]]$importLine = "from paypal_subscriptions import paypal_bp, init_paypal_db"
    $lines.InsertRange($idx1 + 1, $importLine)
    Write-Host "OK: inserted paypal import after line $($idx1 + 1)."
}

$idx2 = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i].Contains("init_subscriptions_db()")) {
        $idx2 = $i
        break
    }
}

if ($idx2 -eq -1) {
    Write-Host "FAILED: could not find init_subscriptions_db() line."
} else {
    [string[]]$regLines = "app.register_blueprint(paypal_bp)", "init_paypal_db()"
    $lines.InsertRange($idx2 + 1, $regLines)
    Write-Host "OK: inserted paypal registration after line $($idx2 + 1)."
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines((Resolve-Path $path), $lines, $utf8NoBom)

Write-Host ""
Write-Host "Context:"
Select-String -Path $path -Pattern "paypal" -Context 0,1
Write-Host ""
python -c "import ast; ast.parse(open('$path', encoding='utf-8').read()); print('SYNTAX OK')"
