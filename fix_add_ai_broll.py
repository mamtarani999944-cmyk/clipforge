"""
Adds AI B-Roll: automatically splices short relevant stock-footage cutaways
into clips based on what's actually being said, while keeping the original
audio and burned-in captions continuous underneath.

How it works:
1. Claude reads the clip's transcript and picks at most 2 short moments
   (1-4s each) where generic stock footage would enhance the content, each
   with a short visual search query (e.g. "city traffic", "ocean waves").
2. Each query is searched on Pexels (free stock video API) and a short
   portrait-friendly clip is downloaded.
3. A multi-input ffmpeg filter graph splices those clips into the main
   footage at the chosen timestamps (video only), then burns captions on
   top of the whole result, with the ORIGINAL audio kept continuous
   underneath throughout (the B-roll never affects audio).

This was tested end-to-end with real ffmpeg filter graphs (splice timing
verified frame-by-frame) before being written into app.py.

Safety: it's fully opt-in via a new checkbox in the UI (off by default) and
fails safe at every step -- missing PEXELS_API_KEY, no transcript, a failed
Claude call, a failed download, or any ffmpeg error all fall back to a
normal clip with no B-roll, exactly like the existing reframe feature does.

Requires a free Pexels API key: https://www.pexels.com/api/ -- after
running this script and deploying, add PEXELS_API_KEY in Railway's
Variables tab.

Run from your clipforge-final project folder:
    python fix_add_ai_broll.py
"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

changed = []

# 1. Add PEXELS_API_KEY alongside the other API key definitions.
old_key = "ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')"
new_key = (
    "ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')\n"
    "PEXELS_API_KEY = os.environ.get('PEXELS_API_KEY')  # https://www.pexels.com/api/ (free)"
)
if old_key in src and 'PEXELS_API_KEY' not in src:
    src = src.replace(old_key, new_key, 1)
    changed.append('added PEXELS_API_KEY config')
elif 'PEXELS_API_KEY' in src:
    print('SKIP: PEXELS_API_KEY already present')
else:
    print('FAILED: could not find ANTHROPIC_API_KEY line. No changes made.')

# 2. Insert the three new B-roll functions right before extract_clip().
anchor = 'def extract_clip(video_path, start, duration, output_path, caption="", words=None):'
broll_funcs = '''def get_broll_segments(transcript_text, clip_duration):
    """Asks Claude to pick at most 2 short moments in the clip where generic
    stock B-roll footage would enhance the content, each with a short
    visual search query. Returns a list of {'query','start','end'} dicts,
    non-overlapping, sorted by start, clamped inside [0, clip_duration],
    capped to ~35% of the clip's total time.

    Returns [] if B-roll isn't configured (missing API keys), there's no
    transcript, the clip is too short, or anything about the request fails
    -- extract_clip() falls back to a plain clip with no B-roll in that case.
    """
    if not ANTHROPIC_API_KEY or not PEXELS_API_KEY or not transcript_text.strip() or clip_duration < 8:
        return []
    try:
        prompt = (
            "You're editing a short vertical video. Here is its spoken transcript "
            f"({clip_duration:.0f} seconds total):\\n\\n"
            f'"{transcript_text}"\\n\\n'
            "Pick at most 2 short moments (2-3.5 seconds each) where generic stock "
            "B-roll footage (not showing the speaker) would visually enhance what's "
            "being said -- e.g. if they mention money, a city, nature, technology, etc. "
            "Only pick moments that have an obvious, concrete visual. If nothing fits, "
            "return an empty array. Respond with ONLY a JSON array (no markdown, no "
            "preamble) shaped like: "
            '[{"query": "2-4 word stock footage search term", "start": <seconds into '
            'the clip>, "end": <seconds into the clip>}]'
        )
        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={'model': CLAUDE_MODEL, 'max_tokens': 300,
                  'messages': [{'role': 'user', 'content': prompt}]},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text = ''.join(
            b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text'
        ).strip()
        text = re.sub(r'^```(json)?|```$', '', text.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return []

        segments = []
        for item in parsed:
            try:
                q = str(item.get('query', '')).strip()
                s = float(item.get('start'))
                e = float(item.get('end'))
            except (TypeError, ValueError, AttributeError):
                continue
            s = max(0.0, min(s, clip_duration))
            e = max(0.0, min(e, clip_duration))
            if not q or e - s < 1.0 or e - s > 4.0:
                continue
            segments.append({'query': q, 'start': round(s, 2), 'end': round(e, 2)})

        segments.sort(key=lambda seg: seg['start'])
        kept = []
        last_end = 0.0
        total = 0.0
        for seg in segments:
            if seg['start'] < last_end + 0.5:
                continue
            if total + (seg['end'] - seg['start']) > clip_duration * 0.35:
                continue
            kept.append(seg)
            last_end = seg['end']
            total += seg['end'] - seg['start']
            if len(kept) >= 2:
                break
        return kept
    except Exception as e:
        print('[broll] segment selection failed:', e, flush=True)
        return []

def fetch_broll_clip(query, out_path, min_duration):
    """Searches Pexels for a short portrait-friendly stock video matching
    `query` and downloads it to out_path. Returns True only if the
    download succeeded AND the clip is long enough to cover min_duration,
    so the caller can safely trim it in the filter graph."""
    try:
        resp = requests.get(
            'https://api.pexels.com/videos/search',
            headers={'Authorization': PEXELS_API_KEY},
            params={'query': query, 'orientation': 'portrait', 'per_page': 5},
            timeout=20,
        )
        resp.raise_for_status()
        videos = resp.json().get('videos', [])
        if not videos:
            return False

        best_url = None
        for v in videos:
            if (v.get('duration') or 0) < min_duration + 0.5:
                continue
            files = sorted(v.get('video_files', []) or [], key=lambda f: f.get('width', 0) or 0)
            portrait = [f for f in files if (f.get('height') or 0) > (f.get('width') or 0)]
            pick = portrait or files
            if pick:
                best_url = pick[len(pick) // 2].get('link')
                if best_url:
                    break
        if not best_url:
            return False

        with requests.get(best_url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(out_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=262144):
                    f.write(chunk)

        if not (os.path.exists(out_path) and os.path.getsize(out_path) > 1000):
            return False

        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', out_path],
            capture_output=True, text=True, timeout=15,
        )
        dl_duration = float(probe.stdout.strip() or 0)
        if dl_duration < min_duration:
            return False
        return True
    except Exception as e:
        print('[broll] fetch failed for query', repr(query), ':', e, flush=True)
        return False

def build_broll_filter_complex(segments, clip_duration, target_w, target_h, main_crop_filter, ass_path=None):
    """Builds an ffmpeg filter_complex string that: applies main_crop_filter
    to the main clip (input 0), scales/center-crops each B-roll clip
    (inputs 1..N) to the same target size, splices the B-roll clips into
    the main clip's timeline at `segments`' windows via concat, and (if
    ass_path is given) burns captions on top of the whole result. Returns
    (filter_complex_str, output_video_label). Audio is left untouched --
    the caller maps input 0's audio directly, so it stays continuous
    underneath the B-roll cutaways."""
    broll_crop = f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h}"
    lines = [f"[0:v]{main_crop_filter}[mainbase]"]

    n_main_segments = len(segments) + 1
    split_outs = ''.join(f"[m{i}]" for i in range(n_main_segments))
    lines.append(f"[mainbase]split={n_main_segments}{split_outs}")

    concat_inputs = []
    cursor = 0.0
    for i, seg in enumerate(segments):
        lines.append(f"[m{i}]trim=start={cursor}:end={seg['start']},setpts=PTS-STARTPTS[v{i}]")
        concat_inputs.append(f"[v{i}]")
        broll_idx = i + 1
        broll_dur = seg['end'] - seg['start']
        lines.append(f"[{broll_idx}:v]{broll_crop},trim=start=0:end={broll_dur},setpts=PTS-STARTPTS[b{i}]")
        concat_inputs.append(f"[b{i}]")
        cursor = seg['end']

    last_i = len(segments)
    lines.append(f"[m{last_i}]trim=start={cursor}:end={clip_duration},setpts=PTS-STARTPTS[v{last_i}]")
    concat_inputs.append(f"[v{last_i}]")

    concat_str = ''.join(concat_inputs)
    lines.append(f"{concat_str}concat=n={len(concat_inputs)}:v=1:a=0[vconcat]")

    out_label = 'vconcat'
    if ass_path:
        escaped_ass = ass_path.replace('\\\\', '/').replace(':', '\\\\:')
        lines.append(f"[vconcat]ass='{escaped_ass}'[vout]")
        out_label = 'vout'

    return ';'.join(lines), out_label

''' + anchor

n = src.count(anchor)
if n == 1 and 'def get_broll_segments' not in src:
    src = src.replace(anchor, broll_funcs, 1)
    changed.append('inserted get_broll_segments() / fetch_broll_clip() / build_broll_filter_complex()')
elif 'def get_broll_segments' in src:
    print('SKIP: B-roll helper functions already present')
else:
    print(f'FAILED: expected exactly 1 match for extract_clip() def, found {n}. No changes made there.')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

for c in changed:
    print('OK:', c)

print('')
print('Part 1 of 2 done (helper functions). Run fix_wire_up_broll.py next.')
