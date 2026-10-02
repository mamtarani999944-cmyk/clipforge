$path = "paypal_subscriptions.py"
$text = [System.IO.File]::ReadAllText((Resolve-Path $path), [System.Text.Encoding]::UTF8)

$old = 'status TEXT DEFAULT "created",'
$new = "status TEXT DEFAULT 'created',"
if ($text.Contains($old)) {
    $text = $text.Replace($old, $new)
    Write-Host "OK: fixed status default quoting."
} else {
    Write-Host "SKIPPED (status line): not found, may already be fixed."
}

$old2 = 'created_at TEXT DEFAULT (datetime("now"))'
$new2 = "created_at TEXT DEFAULT (datetime('now'))"
if ($text.Contains($old2)) {
    $text = $text.Replace($old2, $new2)
    Write-Host "OK: fixed created_at datetime quoting."
} else {
    Write-Host "SKIPPED (created_at line): not found, may already be fixed."
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Resolve-Path $path), $text, $utf8NoBom)

Write-Host ""
Write-Host "Check:"
Select-String -Path $path -Pattern "status TEXT DEFAULT|created_at TEXT DEFAULT"
Write-Host ""
python -c "import ast; ast.parse(open('$path', encoding='utf-8').read()); print('SYNTAX OK')"
