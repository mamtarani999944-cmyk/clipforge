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

function Insert-After {
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
    $Lines.InsertRange($idx + 1, $NewBlock)
    Write-Host "OK: inserted after '$AnchorContains'"
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

# ============================================================
# 1. requirements.txt
# ============================================================
$reqPath = "requirements.txt"
$reqLines = [System.Collections.Generic.List[string]]::new()
$reqLines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path $reqPath)))
if (-not ($reqLines -join "`n").Contains("faster-whisper")) {
    $reqLines.Add("faster-whisper")
    Write-Host "OK: added faster-whisper to requirements.txt"
} else {
    Write-Host "SKIP: faster-whisper already in requirements.txt"
}
[System.IO.File]::WriteAllLines((Resolve-Path $reqPath), $reqLines, $utf8NoBom)

# ============================================================
# 2. app.py
# ============================================================
$appPath = "app.py"
$appLines = [System.Collections.Generic.List[string]]::new()
$appLines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path $appPath)))

# --- 2a. Insert transcription + Claude metadata functions before Google OAuth section ---
$newFuncs = @'
# ── Transcription (faster-whisper) ────────────────────────────────────────────

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

Insert-Before -Lines $appLines -AnchorContains "# ── Google OAuth" -NewBlock $newFuncs

# --- 2b. Replace extract_clip to accept a `words` list for accurate captions ---
$newExtractClip = @'
def extract_clip(video_path, start, duration, output_path, caption="", words=None):
    target_w, target_h = 1080, 1920
    ass_path = None

    if words:
        ass_path = output_path.replace('.mp4', '.ass')
        generate_ass_captions_from_words(words, duration, ass_path)
    elif caption:
        ass_path = output_path.replace('.mp4', '.ass')
        generate_ass_captions(caption, duration, ass_path)

    if ass_path:
        escaped_ass = ass_path.replace('\\', '/').replace(':', '\\:')
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},"
            f"ass='{escaped_ass}'"
        )
    else:
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h}"
        )

    cmd = [
        'ffmpeg', '-y',
        '-ss', str(start), '-i', video_path,
        '-t', str(duration),
        '-vf', vf,
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-threads', '2',
        '-c:a', 'aac', '-b:a', '128k',
        '-movflags', '+faststart',
        output_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        returncode = result.returncode
        stderr = result.stderr
    except subprocess.TimeoutExpired as e:
        returncode = "TIMEOUT"
        stderr = (e.stderr or b"").decode(errors="ignore") if isinstance(e.stderr, bytes) else (e.stderr or "")

    ok = os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    if not ok:
        print("FFMPEG FAILED. RETURNCODE:", returncode)
        print("FFMPEG STDERR:", stderr[-3000:])

    if ass_path and os.path.exists(ass_path):
        os.remove(ass_path)

    return ok
'@.Replace("`r`n", "`n").Split("`n")

Replace-Block -Lines $appLines -StartContains "def extract_clip(video_path, start, duration, output_path, caption=" -EndContains "def generate_thumbnail(clip_path, thumb_path):" -NewBlock $newExtractClip

# --- 2c. Replace generate_clips to transcribe once and use transcript-based metadata ---
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

            # Upload to R2, then remove local copies so Railway's ephemeral
            # disk never has to hold onto them.
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

Replace-Block -Lines $appLines -StartContains "def generate_clips(video_path, num_clips=3, clip_duration=30):" -EndContains "# ── Auth routes" -NewBlock $newGenerateClips

# --- 2d. DB migration: add title, hashtags, transcript columns ---
$dbMigration = @'
    try:
        db.execute('ALTER TABLE clips ADD COLUMN title TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        db.execute('ALTER TABLE clips ADD COLUMN hashtags TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        db.execute('ALTER TABLE clips ADD COLUMN transcript TEXT')
    except sqlite3.OperationalError:
        pass
'@.Replace("`r`n", "`n").Split("`n")

Insert-After -Lines $appLines -AnchorContains "pass  # column already exists" -NewBlock $dbMigration

# --- 2e. /upload INSERT statement: add title, hashtags, transcript ---
$oldInsertAnchor = "INSERT INTO clips (user_id, filename, caption, duration, start_time, virality_score, virality_reason, thumbnail) VALUES"
$idx = -1
for ($i = 0; $i -lt $appLines.Count; $i++) {
    if ($appLines[$i].Contains($oldInsertAnchor)) { $idx = $i; break }
}
if ($idx -eq -1) {
    Write-Host "FAILED: could not find /upload INSERT statement"
} else {
    $newInsert = @(
        "                'INSERT INTO clips (user_id, filename, caption, duration, start_time, virality_score, virality_reason, thumbnail, title, hashtags, transcript) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',",
        "                (user_id, clip['filename'], clip['caption'], clip['duration'], clip['start'], clip['virality_score'], clip.get('virality_reason'), clip.get('thumbnail'), clip.get('title'), json.dumps(clip.get('hashtags') or []), clip.get('transcript'))"
    )
    $appLines.RemoveRange($idx, 2)
    $appLines.InsertRange($idx, $newInsert)
    Write-Host "OK: updated /upload INSERT statement"
}

# --- 2f. /api/clips GET: include title, hashtags, transcript in response ---
$apiClipsNew = @(
            "            'title': row['title'],",
            "            'hashtags': json.loads(row['hashtags']) if row['hashtags'] else [],",
            "            'transcript': row['transcript'],"
)
Insert-After -Lines $appLines -AnchorContains "'filename': r2_key," -NewBlock $apiClipsNew

# --- 2g. New route: update a clip's transcript ---
$newRoute = @'
@app.route('/api/clips/<int:clip_id>/transcript', methods=['POST'])
@login_required
def update_clip_transcript(clip_id):
    user_id = current_user_id()
    data = request.get_json() or {}
    new_transcript = str(data.get('transcript', '')).strip()
    db = get_db()
    row = db.execute('SELECT id FROM clips WHERE id = ? AND user_id = ?', (clip_id, user_id)).fetchone()
    if not row:
        db.close()
        return jsonify({'error': 'Clip not found'}), 404
    db.execute('UPDATE clips SET transcript = ? WHERE id = ? AND user_id = ?', (new_transcript, clip_id, user_id))
    db.commit()
    db.close()
    return jsonify({'success': True})

'@.Replace("`r`n", "`n").Split("`n")

Insert-Before -Lines $appLines -AnchorContains "@app.route('/download/<int:clip_id>')" -NewBlock $newRoute

[System.IO.File]::WriteAllLines((Resolve-Path $appPath), $appLines, $utf8NoBom)

Write-Host ""
python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('app.py SYNTAX OK')"

# ============================================================
# 3. templates/my_clips.html
# ============================================================
$htmlPath = "templates\my_clips.html"
$htmlLines = [System.Collections.Generic.List[string]]::new()
$htmlLines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path $htmlPath)))

# --- 3a. CSS additions ---
$newCss = @'
.clip-title-text{font-size:0.82rem;font-weight:700;color:#111;margin-bottom:0.2rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.clip-hashtags{font-size:0.68rem;color:#6366f1;margin-bottom:0.4rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.transcript-panel{margin-top:0.6rem;padding-top:0.6rem;border-top:1px solid #f0f0f2}
.transcript-edit{width:100%;min-height:70px;font-size:0.72rem;font-family:inherit;color:#333;background:#f5f5f7;border:1px solid #e5e5e7;border-radius:6px;padding:0.4rem;resize:vertical;outline:none}
.transcript-actions{display:flex;align-items:center;gap:8px;margin-top:0.4rem}
.transcript-saved{font-size:0.7rem;color:#22c55e;font-weight:600;opacity:0;transition:opacity 0.3s}
.transcript-saved.show{opacity:1}
'@.Replace("`r`n", "`n").Split("`n")

Insert-After -Lines $htmlLines -AnchorContains ".missing-badge{" -NewBlock $newCss

# --- 3b. card.innerHTML block: add title, hashtags, transcript toggle/panel ---
$newCardBlock = @'
      card.innerHTML = `
        ${!clip.exists ? '<div style="padding:0.4rem 0.7rem 0;"><span class="missing-badge">File deleted</span></div>' : ''}
        <div class="clip-thumb" onclick="previewClip('${clip.download_url}')">
          ${thumbHtml}
          <span class="score-badge">${score}</span>
          <span class="dur-badge">${clip.duration}s</span>
        </div>
        <div class="clip-body">
          ${clip.title ? `<div class="clip-title-text">${clip.title}</div>` : ''}
          <div class="clip-caption-text" title="${clip.caption}">${clip.caption}</div>
          ${clip.hashtags && clip.hashtags.length ? `<div class="clip-hashtags" id="hashtags-${clip.id}"></div>` : ''}
          <div class="clip-date">${date}</div>
          <div class="virality-row">
            <span class="virality-label">Virality</span>
            <span class="virality-score">${score}/100</span>
          </div>
          <div class="virality-bar"><div class="virality-fill" style="width:${score}%"></div></div>
          <div class="clip-actions">
            ${clip.exists
              ? `<a class="clip-btn primary" href="${clip.download_url}" download>&#11015;</a>`
              : `<span class="clip-btn primary" style="opacity:0.4;cursor:not-allowed">&#11015;</span>`}
            <button class="clip-btn" onclick="previewClip('${clip.download_url}')" ${!clip.exists ? 'disabled style="opacity:0.4"' : ''}>&#9654;</button>
            <button class="clip-btn" onclick="toggleTranscript(${clip.id})">&#128221;</button>
            <button class="clip-btn danger" onclick="deleteClip(${clip.id}, this)">&#128465;</button>
          </div>
          <div class="transcript-panel" id="transcript-${clip.id}" style="display:none">
            <textarea class="transcript-edit" id="transcript-text-${clip.id}"></textarea>
            <div class="transcript-actions">
              <button class="clip-btn primary" onclick="saveTranscript(${clip.id})">Save</button>
              <span class="transcript-saved" id="transcript-saved-${clip.id}">Saved!</span>
            </div>
          </div>
        </div>`;
      grid.appendChild(card);

      // Set via DOM (not string interpolation) so special characters in
      // hashtags/transcript can never break the HTML.
      if (clip.hashtags && clip.hashtags.length) {
        const htEl = card.querySelector('#hashtags-' + clip.id);
        if (htEl) {
          htEl.textContent = clip.hashtags.join(' ');
          htEl.title = 'Click to copy';
          htEl.style.cursor = 'pointer';
          htEl.onclick = () => {
            navigator.clipboard.writeText(clip.hashtags.join(' ')).catch(() => {});
            htEl.title = 'Copied!';
          };
        }
      }
      const taEl = card.querySelector('#transcript-text-' + clip.id);
      if (taEl) taEl.value = clip.transcript || '';
'@.Replace("`r`n", "`n").Split("`n")

Replace-Block -Lines $htmlLines -StartContains "card.innerHTML = ``" -EndContains "grid.appendChild(card);" -NewBlock $newCardBlock -IncludeEnd

# --- 3c. New JS functions: toggleTranscript, saveTranscript ---
$newJsFuncs = @'
  function toggleTranscript(id) {
    const panel = document.getElementById('transcript-' + id);
    if (panel) panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
  }

  async function saveTranscript(id) {
    const ta = document.getElementById('transcript-text-' + id);
    const msg = document.getElementById('transcript-saved-' + id);
    try {
      await fetch('/api/clips/' + id + '/transcript', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcript: ta.value })
      });
      const clip = allClips.find(c => c.id === id);
      if (clip) clip.transcript = ta.value;
      msg.classList.add('show');
      setTimeout(() => msg.classList.remove('show'), 2000);
    } catch(e) {
      alert('Failed to save transcript.');
    }
  }

'@.Replace("`r`n", "`n").Split("`n")

Insert-Before -Lines $htmlLines -AnchorContains "function confirmDeleteAll() {" -NewBlock $newJsFuncs

[System.IO.File]::WriteAllLines((Resolve-Path $htmlPath), $htmlLines, $utf8NoBom)

Write-Host ""
Write-Host "All done. Review the OK/FAILED lines above before committing."
