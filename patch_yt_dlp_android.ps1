$path = "app.py"
$text = [System.IO.File]::ReadAllText((Resolve-Path $path), [System.Text.Encoding]::UTF8)

$old = @'
def download_from_url(url, job_id):
    out_path = os.path.join(UPLOAD_FOLDER, f'{job_id}.mp4')
    cmd = [
        'yt-dlp', '--no-playlist',
        '-f', 'best[height<=1080][ext=mp4]/best[ext=mp4]/best',
        '--merge-output-format', 'mp4',
        '-o', out_path, '--no-warnings',
        '--cookies', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt'),
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if os.path.exists(out_path):
        return out_path, None
    for f in os.listdir(UPLOAD_FOLDER):
        if f.startswith(job_id) and f.endswith('.mp4'):
            return os.path.join(UPLOAD_FOLDER, f), None
    error = result.stderr.strip().split('\n')[-1] if result.stderr else 'Download failed'
    return None, error
'@

$new = @'
def download_from_url(url, job_id):
    out_path = os.path.join(UPLOAD_FOLDER, f'{job_id}.mp4')
    cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')

    cmd_primary = [
        'yt-dlp', '--no-playlist',
        '--extractor-args', 'youtube:player_client=android',
        '-f', 'best[height<=1080][ext=mp4]/best[ext=mp4]/best',
        '--merge-output-format', 'mp4',
        '-o', out_path, '--no-warnings',
        url
    ]
    result = subprocess.run(cmd_primary, capture_output=True, text=True, timeout=300)

    if not os.path.exists(out_path):
        for f in os.listdir(UPLOAD_FOLDER):
            if f.startswith(job_id) and f.endswith('.mp4'):
                return os.path.join(UPLOAD_FOLDER, f), None

    if not os.path.exists(out_path):
        cmd_fallback = [
            'yt-dlp', '--no-playlist',
            '-f', 'best[height<=1080][ext=mp4]/best[ext=mp4]/best',
            '--merge-output-format', 'mp4',
            '-o', out_path, '--no-warnings',
            '--cookies', cookies_path,
            url
        ]
        result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=300)

    if os.path.exists(out_path):
        return out_path, None
    for f in os.listdir(UPLOAD_FOLDER):
        if f.startswith(job_id) and f.endswith('.mp4'):
            return os.path.join(UPLOAD_FOLDER, f), None
    error = result.stderr.strip().split('\n')[-1] if result.stderr else 'Download failed'
    return None, error
'@

if ($text.Contains($old)) {
    $text = $text.Replace($old, $new)
    Write-Host "OK: replaced download_from_url with android-client-first version."
} else {
    Write-Host "FAILED: could not find exact old function block."
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Resolve-Path $path), $text, $utf8NoBom)

Write-Host ""
python -c "import ast; ast.parse(open('$path', encoding='utf-8').read()); print('SYNTAX OK')"
