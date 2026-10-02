$path = "app.py"
$lines = [System.Collections.Generic.List[string]]::new()
$lines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path $path)))

$keep = [System.Collections.Generic.List[string]]::new()
$removed = 0
foreach ($line in $lines) {
    if ($line.Contains("razorpay_bp") -or $line.Contains("init_subscriptions_db") -or $line.Contains("from razorpay_subscriptions")) {
        Write-Host "Removing line: $line"
        $removed++
        continue
    }
    $keep.Add($line)
}

Write-Host "Removed $removed line(s)."

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines((Resolve-Path $path), $keep, $utf8NoBom)

Write-Host ""
Write-Host "Remaining references to razorpay (should be none):"
Select-String -Path $path -Pattern "razorpay" -CaseSensitive:$false

Write-Host ""
python -c "import ast; ast.parse(open('$path', encoding='utf-8').read()); print('SYNTAX OK')"
