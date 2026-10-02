$path = "app.py"
$lines = [System.Collections.Generic.List[string]]::new()
$lines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path $path)))

$oldLine1 = "    num_clips = min(max(num_clips, 1), 6)"
$oldLine2 = "    clip_duration = min(max(clip_duration, 15), 60)"

$idx1 = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -eq $oldLine1 -and $lines[$i+1] -eq $oldLine2) {
        $idx1 = $i
        break
    }
}

if ($idx1 -eq -1) {
    Write-Host "FAILED: could not find the exact clamp lines."
} else {
    [string[]]$replacement = @(
        "    from razorpay_subscriptions import get_user_plan_limits",
        "    _limits = get_user_plan_limits(current_user_id())",
        "    num_clips = min(max(num_clips, 1), _limits['max_clips'])",
        "    clip_duration = min(max(clip_duration, 15), _limits['max_duration'])"
    )
    $lines.RemoveRange($idx1, 2)
    $lines.InsertRange($idx1, $replacement)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines((Resolve-Path $path), $lines, $utf8NoBom)
    Write-Host "OK: replaced clamp lines with plan-aware limits at line $($idx1 + 1)."
}

Write-Host ""
Write-Host "Context:"
Select-String -Path $path -Pattern "get_user_plan_limits" -Context 1,2
Write-Host ""
python -c "import ast; ast.parse(open('$path', encoding='utf-8').read()); print('SYNTAX OK')"
