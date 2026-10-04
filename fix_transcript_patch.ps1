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

function Insert-Before {
    param(
        [System.Collections.Generic.List[string]]$Lines,
        [string]$AnchorContains,
        [string[]]$NewBlock
    )
    $idx = -1
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i].Contains($AnchorContains)) { $idx = $i; break }
    }
    if ($idx -eq -1) {
        Write-Host "FAILED: anchor not found: '$AnchorContains'"
        return $false
    }
    $Lines.InsertRange($idx, $NewBlock)
    Write-Host "OK: inserted before '$AnchorContains'"
    return $true
}

$appPath = "app.py"
$appLines = [System.Collections.Generic.List[string]]::new()
$appLines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path $appPath), [System.Text.Encoding]::UTF8))

# ============================================================
# FIX 1: the broken /upload INSERT (currently "cur = db.execute(\n)")
# ============================================================
$newUploadBlock = @'
        for clip in clips:
            cur = db.execute(
                'INSERT INTO clips (user_id, filename, caption, duration, start_time, virality_score, virality_reason, thumbnail, title, hashtags, transcript) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (user_id, clip['filename'], clip['caption'], clip['duration'], clip['start'], clip['virality_score'], clip.get('virality_reason'), clip.get('thumbnail'), clip.get('title'), json.dumps(clip.get('hashtags') or []), clip.get('transcript'))
            )
            clip_ids.append(cur.lastrowid)
'@.Replace("`r`n", "`n").Split("`n")

Replace-Block -Lines $appLines -StartContains "for clip in clips:" -EndContains "clip_ids.append(cur.lastrowid)" -NewBlock $newUploadBlock -IncludeEnd

# ============================================================
# FIX 2: insert the missing transcription + Claude metadata functions
# (anchored on ASCII-only text this time, right before "oauth = OAuth(app)")
# ============================================================
$newFuncs = @'
_whisper_model = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        print('[whisper] loading model...', flush=True)
        _whisper_model = WhisperModel('base', device='cpu', compute_type='int8')
        print('[whisper] model loaded', flush=True)
    return _whisper_model

def transcribe_video(video_path):
    """Transcribe the full video once. Returns a flat list of word dicts:
    [{'text': 'hello', 'start': 1.2, 'end': 1.4}, ...]. Returns [] on any
    failure so clip generation never breaks because of transcription."""
    try:
        model = get_whisper_model()
        segments, _info = model.transcribe(video_path, word_timestamps=True, vad_filter=True)
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

def get_clip_words(all_words, clip_start, clip_duration):
    """Slice the full-video word list down to one clip's window, with
    timestamps made relative to the clip's own start."""
    clip_end = clip_start + clip_duration
    out = []
    for w in all_words:
        if w['start'] >= clip_start and w['start'] < clip_end:
            out.append({
                'text': w['text'],
                'start': max(w['start'] - clip_start, 0),
                'end': min(w['end'] - clip_start, clip_duration),
            })
    return out

def generate_metadata_with_claude(transcript_text, duration, fallback_index):
    """Ask Claude for a title, hook caption, hashtags, and virality score
    based on what's actually said in the clip. Falls back to generic values
    if the API key is missing, there's no transcript, or the call fails."""
    fallback = {
        'title': 'Untitled clip',
        'caption': FALLBACK_CAPTIONS[fallback_index % len(FALLBACK_CAPTIONS)],
        'hashtags': ['#shorts', '#viral', '#fyp'],
        'virality_score': random.randint(62, 90),
        'reasoning': None,
    }

    if not ANTHROPIC_API_KEY or not transcript_text.strip():
        return fallback

    try:
        prompt = (
            "You're a short-form video strategist. Here is the spoken transcript of a "
            f"{duration:.0f}-second clip intended for TikTok/Reels/Shorts:\n\n"
            f'"{transcript_text}"\n\n'
            "Respond with ONLY a JSON object (no markdown, no preamble) in this exact shape:\n"
            '{"title": "a short 3-6 word title for this clip", '
            '"caption": "a punchy 5-10 word hook/caption", '
            '"hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"], '
            '"virality_score": <integer 1-100>, '
            '"reasoning": "one short sentence explaining the score"}'
        )

        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': CLAUDE_MODEL,
                'max_tokens': 400,
                'messages': [{'role': 'user', 'content': prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text = ''.join(
            block.get('text', '') for block in data.get('content', [])
            if block.get('type') == 'text'
        ).strip()
        text = re.sub(r'^```(json)?|```$', '', text.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(text)

        title = str(parsed.get('title', '')).strip() or fallback['title']
        caption = str(parsed.get('caption', '')).strip() or fallback['caption']
        hashtags = parsed.get('hashtags') or fallback['hashtags']
        if not isinstance(hashtags, list):
            hashtags = fallback['hashtags']
        hashtags = [str(h).strip() for h in hashtags if str(h).strip()][:8]
        score = int(parsed.get('virality_score', fallback['virality_score']))
        score = min(max(score, 1), 100)
        reasoning = str(parsed.get('reasoning', '')).strip() or None

        return {
            'title': title, 'caption': caption, 'hashtags': hashtags,
            'virality_score': score, 'reasoning': reasoning,
        }
    except Exception as e:
        print('Claude metadata generation failed, using fallback:', e)
        return fallback

def generate_ass_captions_from_words(words, duration, ass_path):
    """Build karaoke-style burned captions from real transcribed words with
    their real timestamps, instead of evenly splitting a caption string."""
    def ts(t):
        t = max(t, 0)
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h:01d}:{m:02d}:{s:05.2f}"

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,Arial Black,90,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,6,0,2,60,60,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for w in words:
        start = ts(w['start'])
        end = ts(max(w['end'], w['start'] + 0.05))
        text = (
            r"{\fscx80\fscy80\t(0,80,\fscx105\fscy105)\t(80,150,\fscx100\fscy100)}"
            + w['text']
        )
        lines.append(f"Dialogue: 0,{start},{end},Word,,0,0,0,,{text}\n")

    with open(ass_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)

'@.Replace("`r`n", "`n").Split("`n")

Insert-Before -Lines $appLines -AnchorContains "oauth = OAuth(app)" -NewBlock $newFuncs

# ============================================================
# FIX 3: replace generate_clips with the transcript-aware version
# (end anchor is now ASCII-only: "@app.route('/login')")
# ============================================================
$newGenerateClips = @'
def generate_clips(video_path, num_clips=3, clip_duration=30):
    duration = get_video_duration(video_path)
    clips = []
    job_id = str(uuid.uuid4())[:8]
    scene_times = detect_scenes(video_path)

    print('[transcribe] starting full-video transcription...', flush=True)
    all_words = transcribe_video(video_path)
    print(f'[transcribe] got {len(all_words)} words', flush=True)

    if len(scene_times) >= num_clips:
        step = len(scene_times) // num_clips
        selected = [scene_times[i * step] for i in range(num_clips)]
    else:
        margin = clip_duration
        usable = max(duration - margin, clip_duration)
        step = usable / num_clips
        selected = [margin / 2 + i * step for i in range(num_clips)]

    for i, start in enumerate(selected[:num_clips]):
        actual_start = min(max(start, 0), duration - clip_duration)
        actual_dur = min(clip_duration, duration - actual_start)

        clip_words = get_clip_words(all_words, actual_start, actual_dur)
        transcript_text = ' '.join(w['text'] for w in clip_words).strip()

        meta = generate_metadata_with_claude(transcript_text, actual_dur, i)
        title = meta['title']
        caption = meta['caption']
        hashtags = meta['hashtags']
        virality_score = meta['virality_score']
        virality_reason = meta['reasoning']

        out_filename = f"clip_{job_id}_{i+1}.mp4"
        out_path = os.path.join(OUTPUT_FOLDER, out_filename)
        success = extract_clip(video_path, actual_start, actual_dur, out_path, caption, words=clip_words)
        if success:
            thumb_filename = f"thumb_{job_id}_{i+1}.jpg"
            thumb_path = os.path.join(OUTPUT_FOLDER, thumb_filename)
            thumb_ok = generate_thumbnail(out_path, thumb_path)

            clip_r2_key = f"clips/{out_filename}"
            clip_uploaded = upload_to_r2(out_path, clip_r2_key, 'video/mp4')
            if clip_uploaded and os.path.exists(out_path):
                os.remove(out_path)

            thumb_r2_key = None
            if thumb_ok:
                thumb_r2_key = f"thumbnails/{thumb_filename}"
                thumb_uploaded = upload_to_r2(thumb_path, thumb_r2_key, 'image/jpeg')
                if thumb_uploaded and os.path.exists(thumb_path):
                    os.remove(thumb_path)
                if not thumb_uploaded:
                    thumb_r2_key = None

            clips.append({
                'id': i + 1,
                'filename': clip_r2_key if clip_uploaded else out_filename,
                'title': title,
                'caption': caption,
                'hashtags': hashtags,
                'transcript': transcript_text,
                'start': round(actual_start, 1),
                'duration': round(actual_dur, 1),
                'virality_score': virality_score,
                'virality_reason': virality_reason,
                'thumbnail': thumb_r2_key,
                'download_url': f'/download/{job_id}_{i+1}',
                'thumbnail_url': r2_public_url(thumb_r2_key) if thumb_r2_key else None
            })
    return clips

'@.Replace("`r`n", "`n").Split("`n")

Replace-Block -Lines $appLines -StartContains "def generate_clips(video_path, num_clips=3, clip_duration=30):" -EndContains "@app.route('/login')" -NewBlock $newGenerateClips

[System.IO.File]::WriteAllLines((Resolve-Path $appPath), $appLines, $utf8NoBom)

Write-Host ""
python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('app.py SYNTAX OK')"
Write-Host ""
Write-Host "Sanity check - these should ALL show up:"
Select-String -Path $appPath -Pattern "def transcribe_video|def get_clip_words|def generate_metadata_with_claude|def generate_ass_captions_from_words|all_words = transcribe_video|INSERT INTO clips \(user_id, filename, caption, duration, start_time, virality_score, virality_reason, thumbnail, title, hashtags, transcript\)"
