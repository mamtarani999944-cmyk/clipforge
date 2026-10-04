$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$nixPath = "nixpacks.toml"
$newContent = @'
[phases.setup]
aptPkgs = ["ffmpeg", "nodejs", "curl", "unzip"]
cmds = ["curl -fsSL https://deno.land/install.sh | sh -s -- -y", "ln -sf /root/.deno/bin/deno /usr/local/bin/deno"]
'@.Replace("`r`n", "`n")

[System.IO.File]::WriteAllText((Resolve-Path $nixPath), $newContent, $utf8NoBom)
Write-Host "OK: rewrote nixpacks.toml to install Deno via its official installer instead of nixPkgs"
Write-Host ""
Write-Host "nixpacks.toml now:"
Get-Content $nixPath
