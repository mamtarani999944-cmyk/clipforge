$path = "app.py"
$lines = [System.Collections.Generic.List[string]]::new()
$lines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path $path)))

$startIdx = -1
$endIdx = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i].Contains("def download_from_url(url, job_id):")) {
        $startIdx = $i
    }
    if ($startIdx -ne -1 -and $i -gt $startIdx -and $lines[$i].Contains("def detect_scenes")) {
        $endIdx = $i
        break
    }
}

if ($startIdx -eq -1 -or $endIdx -eq -1) {
    Write-Host "FAILED: could not locate function boundaries. startIdx=$startIdx endIdx=$endIdx"
} else {
    [string[]]$newBlock = @(
        "def download_from_url(url, job_id):",
        "    out_path = os.path.join(UPLOAD_FOLDER, f'{job_id}.mp4')",
        "    cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')",
        "",
        "    cmd_primary = [",
        "        'yt-dlp', '--no-playlist',",
        "        '--extractor-args', 'youtube:player_client=android',",
        "        '-f', 'best[height<=1080][ext=mp4]/best[ext=mp4]/best',",
        "        '--merge-output-format', 'mp4',",
        "        '-o', out_path, '--no-warnings',",
        "        url",
        "    ]",
        "    result = subprocess.run(cmd_primary, capture_output=True, text=True, timeout=300)",
        "",
        "    if not os.path.exists(out_path):",
        "        for f in os.listdir(UPLOAD_FOLDER):",
        "            if f.startswith(job_id) and f.endswith('.mp4'):",
        "                return os.path.join(UPLOAD_FOLDER, f), None",
        "",
        "    if not os.path.exists(out_path):",
        "        cmd_fallback = [",
        "            'yt-dlp', '--no-playlist',",
        "            '-f', 'best[height<=1080][ext=mp4]/best[ext=mp4]/best',",
        "            '--merge-output-format', 'mp4',",
        "            '-o', out_path, '--no-warnings',",
        "            '--cookies', cookies_path,",
        "            url",
        "        ]",
        "        result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=300)",
        "",
        "    if os.path.exists(out_path):",
        "        return out_path, None",
        "    for f in os.listdir(UPLOAD_FOLDER):",
        "        if f.startswith(job_id) and f.endswith('.mp4'):",
        "            return os.path.join(UPLOAD_FOLDER, f), None",
        "    error = result.stderr.strip().split('\n')[-1] if result.stderr else 'Download failed'",
        "    return None, error",
        ""
    )

    $removeCount = $endIdx - $startIdx
    $lines.RemoveRange($startIdx, $removeCount)
    $lines.InsertRange($startIdx, $newBlock)
    Write-Host "OK: replaced download_from_url (lines $($startIdx+1) to $($endIdx)) with android-client-first version."
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines((Resolve-Path $path), $lines, $utf8NoBom)

Write-Host ""
python -c "import ast; ast.parse(open('$path', encoding='utf-8').read()); print('SYNTAX OK')"
Write-Host ""
Select-String -Path $path -Pattern "player_client=android"
