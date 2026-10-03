$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$tags = @'
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png">
<link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">
'@

$files = Get-ChildItem -Path "templates" -Filter "*.html" -Recurse
$patched = 0
$skipped = 0

foreach ($file in $files) {
    $text = [System.IO.File]::ReadAllText($file.FullName, [System.Text.Encoding]::UTF8)

    if ($text.Contains("favicon")) {
        Write-Host "SKIP (already has favicon tags): $($file.Name)"
        $skipped++
        continue
    }

    if (-not $text.Contains("<head>")) {
        Write-Host "SKIP (no <head> tag found): $($file.Name)"
        $skipped++
        continue
    }

    $newText = $text.Replace("<head>", "<head>`n$tags")
    [System.IO.File]::WriteAllText($file.FullName, $newText, $utf8NoBom)
    Write-Host "OK: added favicon tags to $($file.Name)"
    $patched++
}

Write-Host ""
Write-Host "Patched $patched file(s), skipped $skipped file(s)."
