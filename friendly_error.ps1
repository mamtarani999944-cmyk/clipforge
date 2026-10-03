$path = "app.py"
$text = [System.IO.File]::ReadAllText((Resolve-Path $path), [System.Text.Encoding]::UTF8)

$old = "            return jsonify({'error': f'Could not download video: {error}'}), 400"
$new = @'
            friendly_error = "This video couldn't be downloaded. It may be restricted, age-limited, or protected by YouTube. Please try a different video."
            print(f'[download] raw error for {source_url}: {error}', flush=True)
            return jsonify({'error': friendly_error}), 400
'@

if ($text.Contains($old)) {
    $text = $text.Replace($old, $new)
    Write-Host "OK: replaced raw error message with a friendly one (raw error still logged server-side)."
} else {
    Write-Host "FAILED: could not find exact old line."
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Resolve-Path $path), $text, $utf8NoBom)

Write-Host ""
python -c "import ast; ast.parse(open('$path', encoding='utf-8').read()); print('SYNTAX OK')"
