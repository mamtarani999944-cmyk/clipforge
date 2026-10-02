$lines = [System.Collections.Generic.List[string]]::new()
$lines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path "app.py")))

$targetIndex = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -like "*session['user'] = {'id': user_id*") {
        $targetIndex = $i
        break
    }
}

if ($targetIndex -eq -1) {
    Write-Host "FAILED: could not find session['user'] line."
} else {
    $newLines = @(
        "    try:",
        "        send_login_notification(email, name, request)",
        "    except Exception:",
        "        pass"
    )
    $lines.InsertRange($targetIndex + 1, $newLines)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines((Resolve-Path "app.py"), $lines, $utf8NoBom)
    Write-Host "OK: inserted send_login_notification call after line $($targetIndex + 1)."
}

Write-Host ""
Write-Host "Context around the change:"
Select-String -Path "app.py" -Pattern "session\['user'\] = \{'id': user_id" -Context 0,7
