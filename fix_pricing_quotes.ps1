$path = "templates/pricing.html"
$text = [System.IO.File]::ReadAllText((Resolve-Path $path), [System.Text.Encoding]::UTF8)

$count = ([regex]::Matches($text, "''")).Count
Write-Host "Found $count occurrences of doubled single-quotes."

$fixed = $text -replace "''", "'"

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Resolve-Path $path), $fixed, $utf8NoBom)
Write-Host "OK: fixed quotes in pricing.html"

Write-Host ""
Write-Host "Check:"
Select-String -Path $path -Pattern "paypalSubscribe" | Select-Object -First 3
