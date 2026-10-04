$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$nixPath = "nixpacks.toml"
$newContent = @'
[phases.setup]
aptPkgs = ["ffmpeg", "nodejs", "curl", "unzip", "pkg-config", "libavformat-dev", "libavcodec-dev", "libavdevice-dev", "libavutil-dev", "libavfilter-dev", "libswscale-dev", "libswresample-dev"]
cmds = ["curl -fsSL https://deno.land/install.sh | sh -s -- -y", "ln -sf /root/.deno/bin/deno /usr/local/bin/deno"]
'@.Replace("`r`n", "`n")

[System.IO.File]::WriteAllText((Resolve-Path $nixPath), $newContent, $utf8NoBom)
Write-Host "OK: added FFmpeg dev headers so av<12 can build from source"
Write-Host ""
Write-Host "nixpacks.toml now:"
Get-Content $nixPath
