$path = "templates/index.html"
$lines = [System.Collections.Generic.List[string]]::new()
$lines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path $path)))

$targetIndex = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i].Contains('href="/analytics"')) {
        $targetIndex = $i
        break
    }
}

if ($targetIndex -eq -1) {
    Write-Host "FAILED: could not find Analytics nav-item line."
} else {
    [string[]]$newLine = '    <a class="nav-item" href="/pricing">&#128176; &nbsp;Pricing</a>'
    $lines.InsertRange($targetIndex + 1, $newLine)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines((Resolve-Path $path), $lines, $utf8NoBom)
    Write-Host "OK: inserted Pricing nav link after line $($targetIndex + 1)."
}

Write-Host ""
Write-Host "Context:"
Select-String -Path $path -Pattern "nav-item" -Context 0,1
