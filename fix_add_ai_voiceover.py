"""
Adds AI Voice-Over: an optional spoken "hook" narration automatically laid
over the first couple seconds of each clip, reading the AI-generated title
out loud in a natural AI voice -- while the original audio keeps playing
underneath, just quieter during the voice-over.

How it works:
1. After a clip's title/hook is generated (already happens today via
   Claude), its text is synthesized into speech using Microsoft Edge's
   free text-to-speech engine (via the `edge-tts` library) -- no API key,
   no signup, no cost.
2. That narration audio is mixed over the start of the finished clip: the
   clip's own audio is ducked to 25% volume only during the narration, then
   returns to full volume once the narration ends. Video is untouched.

IMPORTANT HONESTY NOTE (so you know the real risk): edge-tts works by
reusing the same free voice service that powers "Read Aloud" in the Edge
browser. It's not an official, supported API -- Microsoft could change or
block it at any time without notice. That's WHY it's built the same
fail-safe way as your other AI features: if the narration service is
unreachable or fails for any reason, the clip is simply produced without a
voice-over, exactly as before. Nothing breaks, nothing errors out to the
user. If this ever stops working, clips keep generating normally -- just
message me and I'll swap in a different voice provider.

Safety: fully opt-in via a new checkbox in the UI (off by default). Fails
safe at every step -- missing title text, a failed synthesis call, or any
ffmpeg mixing error all just skip the voice-over and keep the plain clip.

Run from your clipforge-final project folder:
    python fix_add_ai_voiceover.py
"""
with open('requirements.txt', 'r', encoding='utf-8') as f:
    req = f.read()
if 'edge-tts' not in req:
    req = req.rstrip('\n') + '\nedge-tts\n'
    with open('requirements.txt', 'w', encoding='utf-8') as f:
        f.write(req)
    print('OK: added edge-tts to requirements.txt')
else:
    print('SKIP: edge-tts already in requirements.txt')

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

changed = []

# 1. Add `import asyncio` (needed to run edge-tts, which is async-only).
old_imports = "import threading\nimport uuid"
new_imports = "import threading\nimport asyncio\nimport uuid"
if old_imports in src and 'import asyncio' not in src:
    src = src.replace(old_imports, new_imports, 1)
    changed.append('added import asyncio')
elif 'import asyncio' in src:
    print('SKIP: import asyncio already present')
else:
    print('FAILED: could not find the import block to extend. No changes made.')

# 2. Insert generate_voiceover_tts() and apply_voiceover() right before
#    extract_clip() (works whether or not the AI B-roll patch already ran,
#    since it matches on extract_clip's fixed prefix, not its full signature).
anchor_prefix = 'def extract_clip(video_path, start, duration, output_path'
anchor_idx = src.find(anchor_prefix)
voiceover_funcs = '''def generate_voiceover_tts(text, out_path, voice="en-US-GuyNeural"):
    """Synthesizes `text` into speech using Microsoft Edge's free TTS
    engine (via edge-tts) and saves it as an mp3 at out_path. Returns True
    on success, False on any failure (missing/empty text, network issue,
    the service being unreachable, etc.) so callers can skip the
    voice-over entirely and keep a normal clip.
    """
    text = (text or '').strip()
    if not text:
        return False
    try:
        import edge_tts

        async def _synthesize():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(out_path)

        asyncio.run(_synthesize())
        return os.path.exists(out_path) and os.path.getsize(out_path) > 500
    except Exception as e:
        print('[voiceover] TTS synthesis failed:', e, flush=True)
        return False

def apply_voiceover(clip_path, voiceover_path, output_path):
    """Mixes a narration track (voiceover_path) over the start of an
    already-rendered clip (clip_path): the clip's own audio is ducked to
    25% volume only while the narration plays, then returns to full volume.
    Video is copied through untouched (no re-encode). Writes the result to
    output_path. Returns True on success, False on any failure -- callers
    should keep the original clip_path untouched in that case.
    """
    try:
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', voiceover_path],
            capture_output=True, text=True, timeout=15,
        )
        vo_duration = float(probe.stdout.strip() or 0)
        if vo_duration <= 0:
            return False

        filter_complex = (
            f"[0:a]volume=0.25:enable='between(t,0,{vo_duration})'[a0];"
            f"[1:a]apad[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        cmd = [
            'ffmpeg', '-y',
            '-i', clip_path,
            '-i', voiceover_path,
            '-filter_complex', filter_complex,
            '-map', '0:v', '-map', '[aout]',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k',
            '-shortest',
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        ok = os.path.exists(output_path) and os.path.getsize(output_path) > 1000
        if not ok:
            print('[voiceover] mixing failed. RETURNCODE:', result.returncode, flush=True)
            print('[voiceover] STDERR:', (result.stderr or '')[-2000:], flush=True)
        return ok
    except Exception as e:
        print('[voiceover] mixing failed:', e, flush=True)
        return False

''' + anchor_prefix

if anchor_idx != -1 and 'def generate_voiceover_tts' not in src:
    src = src[:anchor_idx] + voiceover_funcs + src[anchor_idx + len(anchor_prefix):]
    changed.append('inserted generate_voiceover_tts() and apply_voiceover()')
elif 'def generate_voiceover_tts' in src:
    print('SKIP: voice-over helper functions already present')
else:
    print('FAILED: could not find extract_clip() to anchor on. No changes made there.')

# 3. generate_clips(): accept voiceover_enabled (tolerant of the AI B-roll
#    patch having already added broll_enabled -- matches on the fixed
#    prefix of the signature, not the whole line).
sig_prefix = 'def generate_clips(video_path, num_clips=3, clip_duration=30'
sig_idx = src.find(sig_prefix)
if sig_idx != -1 and 'voiceover_enabled' not in src[sig_idx:sig_idx + 200]:
    line_end = src.find('\n', sig_idx)
    line = src[sig_idx:line_end]
    new_line = line.replace('):', ', voiceover_enabled=False):', 1)
    src = src[:sig_idx] + new_line + src[line_end:]
    changed.append('generate_clips() accepts voiceover_enabled')
elif sig_idx != -1:
    print('SKIP: generate_clips() signature already has voiceover_enabled')
else:
    print('FAILED: could not find generate_clips() signature. No changes made there.')

# 4. Right after a clip's extract_clip() succeeds (before thumbnailing),
#    add the voice-over step. Anchored on the fixed text around `if success:`
#    plus the thumbnail filename line, which exists regardless of whether
#    the AI B-roll patch changed the extract_clip() call above it.
old_success_block = '''        if success:
            thumb_filename = f"thumb_{job_id}_{i+1}.jpg"'''
new_success_block = '''        if success and voiceover_enabled:
            vo_path = out_path.replace('.mp4', '_vo.mp3')
            if generate_voiceover_tts(title, vo_path):
                mixed_path = out_path.replace('.mp4', '_mixed.mp4')
                if apply_voiceover(out_path, vo_path, mixed_path):
                    os.replace(mixed_path, out_path)
                elif os.path.exists(mixed_path):
                    os.remove(mixed_path)
            if os.path.exists(vo_path):
                os.remove(vo_path)

        if success:
            thumb_filename = f"thumb_{job_id}_{i+1}.jpg"'''
if old_success_block in src:
    src = src.replace(old_success_block, new_success_block, 1)
    changed.append('generate_clips() applies the voice-over after a successful extract_clip()')
elif 'generate_voiceover_tts(title, vo_path)' in src:
    print('SKIP: voice-over call site already patched')
else:
    print('FAILED: could not find the success block in generate_clips(). No changes made there.')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

for c in changed:
    print('OK:', c)

print('')
print('Part 1 of 2 done. Run fix_wire_up_voiceover.py next.')
