$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$reqPath = "requirements.txt"
$reqText = [System.IO.File]::ReadAllText((Resolve-Path $reqPath), [System.Text.Encoding]::UTF8)

if (-not $reqText.Contains("av<12")) {
    $reqText = $reqText.TrimEnd() + "`nav<12`n"
    Write-Host "OK: pinned av<12 in requirements.txt (fixes faster-whisper's metadata_errors crash)"
} else {
    Write-Host "SKIP: av<12 already present"
}

[System.IO.File]::WriteAllText((Resolve-Path $reqPath), $reqText, $utf8NoBom)

Write-Host ""
Write-Host "requirements.txt now:"
Get-Content $reqPath
