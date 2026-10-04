$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# ── 1. nixpacks.toml: add Deno as a system JS runtime ──────────────────────
$nixPath = "nixpacks.toml"
$nixText = [System.IO.File]::ReadAllText((Resolve-Path $nixPath), [System.Text.Encoding]::UTF8)

$oldNix = 'aptPkgs = ["ffmpeg", "nodejs"]'
$newNix = @'
aptPkgs = ["ffmpeg", "nodejs"]
nixPkgs = ["deno"]
'@.Replace("`r`n", "`n")

if ($nixText.Contains($oldNix) -and -not $nixText.Contains('nixPkgs')) {
    $nixText = $nixText.Replace($oldNix, $newNix)
    Write-Host "OK: added nixPkgs = [`"deno`"] to nixpacks.toml"
} else {
    Write-Host "WARN: nixpacks.toml not changed (either pattern not found or deno already present)"
}
[System.IO.File]::WriteAllText((Resolve-Path $nixPath), $nixText, $utf8NoBom)

# ── 2. requirements.txt: install yt-dlp with the [default] extra (bundles the EJS challenge solver) ──
$reqPath = "requirements.txt"
$reqText = [System.IO.File]::ReadAllText((Resolve-Path $reqPath), [System.Text.Encoding]::UTF8)

$oldReq = "yt-dlp @ git+https://github.com/yt-dlp/yt-dlp.git"
$newReq = "yt-dlp[default] @ git+https://github.com/yt-dlp/yt-dlp.git"
if ($reqText.Contains($oldReq)) {
    $reqText = $reqText.Replace($oldReq, $newReq)
    Write-Host "OK: updated requirements.txt to install yt-dlp[default] (includes EJS challenge solver)"
} else {
    Write-Host "WARN: could not find the yt-dlp line in requirements.txt to update"
}
[System.IO.File]::WriteAllText((Resolve-Path $reqPath), $reqText, $utf8NoBom)

Write-Host ""
Write-Host "nixpacks.toml now:"
Get-Content $nixPath
Write-Host ""
Write-Host "requirements.txt now:"
Get-Content $reqPath
