"""
Fixes OOM crashes on long source videos.

Root cause: transcribe_video() loaded the ENTIRE source video's audio into
memory and ran Whisper over all of it, even though only a few short clips
(e.g. 3 x 30s = 90s) actually get used. For a ~2-hour video, that's roughly
78x more audio data and Whisper processing than necessary, and it's what
triggered the "Worker was sent SIGKILL! Perhaps out of memory?" crash on a
1h57m test video.

Fix: transcribe only each selected clip's own audio window (via a tight
ffmpeg -ss/-t seek), not the whole video. This bounds memory/CPU to roughly
clip_duration regardless of source video length -- fixes OOM for any video
length AND makes normal-length videos faster too, since no time is spent
transcribing footage that never becomes a clip.

Run from your clipforge-final project folder:
    python fix_per_clip_transcription.py
"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

changed = []

# 1. Replace transcribe_video() + get_clip_words() with a single
#    transcribe_clip_window() that only looks at one clip's time range.
old_funcs = '''def transcribe_video(video_path):
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
    return out'''

new_func = '''def transcribe_clip_window(video_path, start, duration):
    """Transcribes just ONE clip's audio window (via a tight ffmpeg -ss/-t
    seek), instead of the whole source video. This keeps peak memory/CPU
    bounded by clip_duration no matter how long the original video is --
    a 2-hour source video costs the same as a 2-minute one here. Word
    timestamps come back already relative to the clip's own start (no
    separate full-video-to-clip slicing step needed).

    Returns [] on any failure so clip generation never breaks because of
    transcription.
    """
    try:
        import numpy as np
        cmd = [
            'ffmpeg', '-y', '-ss', str(start), '-i', video_path, '-t', str(duration),
            '-f', 's16le', '-acodec', 'pcm_s16le',
            '-ac', '1', '-ar', '16000',
            '-'
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if not result.stdout:
            print('[whisper] ffmpeg produced no audio output for clip window:', result.stderr[-500:] if result.stderr else '')
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
        print('[whisper] clip transcription failed:', e, flush=True)
        return []'''

if old_funcs in src:
    src = src.replace(old_funcs, new_func, 1)
    changed.append('replaced transcribe_video()/get_clip_words() with transcribe_clip_window()')
elif 'def transcribe_clip_window' in src:
    print('SKIP: already patched')
else:
    print('FAILED: could not find the exact old functions to replace. No changes made.')

# 2. Update generate_clips(): drop the whole-video transcription pass,
#    transcribe each clip's own window inside the loop instead.
old_call_site = '''    scene_times = detect_scenes(video_path)

    print('[transcribe] starting full-video transcription...', flush=True)
    all_words = transcribe_video(video_path)
    print(f'[transcribe] got {len(all_words)} words', flush=True)

    if len(scene_times) >= num_clips:'''
new_call_site = '''    scene_times = detect_scenes(video_path)

    if len(scene_times) >= num_clips:'''
if old_call_site in src:
    src = src.replace(old_call_site, new_call_site, 1)
    changed.append('removed the whole-video transcription pass from generate_clips()')
elif 'transcribe_clip_window(video_path, actual_start' in src:
    print('SKIP: call site already patched')
else:
    print('FAILED: could not find the exact generate_clips() transcription call site. No changes made.')

old_per_clip = '''        clip_words = get_clip_words(all_words, actual_start, actual_dur)
        transcript_text = ' '.join(w['text'] for w in clip_words).strip()'''
new_per_clip = '''        print(f'[transcribe] clip {i+1}: transcribing {actual_dur:.0f}s window starting at {actual_start:.0f}s...', flush=True)
        clip_words = transcribe_clip_window(video_path, actual_start, actual_dur)
        print(f'[transcribe] clip {i+1}: got {len(clip_words)} words', flush=True)
        transcript_text = ' '.join(w['text'] for w in clip_words).strip()'''
if old_per_clip in src:
    src = src.replace(old_per_clip, new_per_clip, 1)
    changed.append('generate_clips() now transcribes each clip window individually')
elif 'clip_words = transcribe_clip_window' in src:
    pass  # already reported above
else:
    print('FAILED: could not find the exact per-clip transcription call to replace. No changes made.')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

for c in changed:
    print('OK:', c)

print('')
print('Verify with:')
print('''python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('app.py SYNTAX OK')"''')
