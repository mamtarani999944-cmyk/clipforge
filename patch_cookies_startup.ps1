$path = "app.py"
$lines = [System.Collections.Generic.List[string]]::new()
$lines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path $path)))

$idx = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i].Contains("init_paypal_db()")) {
        $idx = $i
        break
    }
}

if ($idx -eq -1) {
    Write-Host "FAILED: could not find init_paypal_db() line."
} else {
    [string[]]$block = @(
        "",
        "_cookies_b64 = os.environ.get('YOUTUBE_COOKIES_B64')",
        "if _cookies_b64:",
        "    try:",
        "        import base64",
        "        _cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')",
        "        with open(_cookies_path, 'wb') as _f:",
        "            _f.write(base64.b64decode(_cookies_b64))",
        "        print('[startup] cookies.txt written from YOUTUBE_COOKIES_B64', flush=True)",
        "    except Exception as _e:",
        "        print(f'[startup] FAILED to write cookies.txt: {_e}', flush=True)"
    )
    $lines.InsertRange($idx + 1, $block)
    Write-Host "OK: inserted cookies.txt startup writer after line $($idx + 1)."
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines((Resolve-Path $path), $lines, $utf8NoBom)

Write-Host ""
Write-Host "Context:"
Select-String -Path $path -Pattern "YOUTUBE_COOKIES_B64" -Context 0,2
Write-Host ""
python -c "import ast; ast.parse(open('$path', encoding='utf-8').read()); print('SYNTAX OK')"
