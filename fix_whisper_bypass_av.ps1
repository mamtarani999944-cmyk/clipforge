$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Replace-Block {
    param(
        [System.Collections.Generic.List[string]]$Lines,
        [string]$StartContains,
        [string]$EndContains,
        [string[]]$NewBlock,
        [switch]$IncludeEnd
    )
    $startIdx = -1
    $endIdx = -1
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($startIdx -eq -1 -and $Lines[$i].Contains($StartContains)) {
            $startIdx = $i
            continue
        }
        if ($startIdx -ne -1 -and $Lines[$i].Contains($EndContains)) {
            $endIdx = $i
            break
        }
    }
    if ($startIdx -eq -1 -or $endIdx -eq -1) {
        Write-Host "FAILED: could not find block for start='$StartContains' end='$EndContains'"
        return $false
    }
    $removeCount = $endIdx - $startIdx
    if ($IncludeEnd) { $removeCount++ }
    $Lines.RemoveRange($startIdx, $removeCount)
    $Lines.InsertRange($startIdx, $NewBlock)
    Write-Host "OK: replaced block start='$StartContains'"
    return $true
}

# ============================================================
# 1. requirements.txt: remove the av<12 pin, no longer needed
# ============================================================
$reqPath = "requirements.txt"
$reqLines = [System.Collections.Generic.List[string]]::new()
$reqLines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path $reqPath), [System.Text.Encoding]::UTF8))
$removed = $reqLines.RemoveAll({ param($l) $l.Trim() -eq 'av<12' })
Write-Host "OK: removed $removed line(s) matching 'av<12' from requirements.txt"
[System.IO.File]::WriteAllLines((Resolve-Path $reqPath), $reqLines, $utf8NoBom)

# ============================================================
# 2. nixpacks.toml: drop the ffmpeg dev headers, no longer needed
# ============================================================
$nixPath = "nixpacks.toml"
$newNix = @'
[phases.setup]
aptPkgs = ["ffmpeg", "nodejs", "curl", "unzip"]
cmds = ["curl -fsSL https://deno.land/install.sh | sh -s -- -y", "ln -sf /root/.deno/bin/deno /usr/local/bin/deno"]
'@.Replace("`r`n", "`n")
[System.IO.File]::WriteAllText((Resolve-Path $nixPath), $newNix, $utf8NoBom)
Write-Host "OK: simplified nixpacks.toml back down (dev headers no longer needed)"

# ============================================================
# 3. app.py: make transcribe_video extract audio via ffmpeg directly,
#    bypassing PyAV (faster-whisper's decode_audio) entirely.
# ============================================================
$appPath = "app.py"
$appLines = [System.Collections.Generic.List[string]]::new()
$appLines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path $appPath), [System.Text.Encoding]::UTF8))

$newTranscribeFunc = @'
def transcribe_video(video_path):
    """Transcribe the full video once. Returns a flat list of word dicts:
    [{'text': 'hello', 'start': 1.2, 'end': 1.4}, ...]. Returns [] on any
    failure so clip generation never breaks because of transcription.

    Audio is extracted with our own ffmpeg call (mono 16kHz PCM) and handed
    to Whisper as a numpy array, instead of letting faster-whisper decode
    the file itself via PyAV -- PyAV can't be reliably built/linked in this
    environment, so this sidesteps it completely.
    """
    try:
        import numpy as np
        cmd = [
            'ffmpeg', '-i', video_path,
            '-f', 's16le', '-acodec', 'pcm_s16le',
            '-ac', '1', '-ar', '16000',
            '-'
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if not result.stdout:
            print('[whisper] ffmpeg produced no audio output:', result.stderr[-500:] if result.stderr else '')
            return []
        audio = np.frombuffer(result.stdout, np.int16).astype(np.float32) / 32768.0

        model = get_whisper_model()
        segments, _info = model.transcribe(audio, word_timestamps=True, vad_filter=True)
        words = []
        for seg in segments:
            if not seg.words:
                continue
            for w in seg.words:
                words.append({'text': w.word.strip(), 'start': w.start, 'end': w.end})
        return words
    except Exception as e:
        print('Transcription failed:', e)
        return []
'@.Replace("`r`n", "`n").Split("`n")

Replace-Block -Lines $appLines -StartContains "def transcribe_video(video_path):" -EndContains "def get_clip_words(all_words, clip_start, clip_duration):" -NewBlock $newTranscribeFunc

[System.IO.File]::WriteAllLines((Resolve-Path $appPath), $appLines, $utf8NoBom)

Write-Host ""
python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('app.py SYNTAX OK')"
Write-Host ""
Write-Host "requirements.txt now:"
Get-Content $reqPath
Write-Host ""
Write-Host "nixpacks.toml now:"
Get-Content $nixPath
