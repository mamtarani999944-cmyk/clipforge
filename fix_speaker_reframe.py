"""
Adds AI speaker tracking / auto-reframe to vertical 9:16.

How it works: samples ~15 frames across each clip's time window directly
from the source video, runs a fast OpenCV face detector on each sample to
find the speaker, and builds an ffmpeg crop-filter expression (eval=frame)
that pans the 1080x1920 crop window horizontally to follow the detected
face position over time -- instead of the old behaviour, which always
center-cropped and chopped off anyone who wasn't dead-center in frame.

This is face-detection-based tracking (fast, works on CPU, no GPU needed),
not full audio-visual speaker diarization -- that would need a much
heavier model this box can't run. For a single clear talking head it gives
a real "follow the speaker" effect.

Safety: if OpenCV can't open the video, finds no faces, or throws for any
reason, it falls back to the exact old center-crop behavior. Nothing about
caption burn-in, transcription, or clip cutting changes.

Run from your clipforge-final project folder:
    python fix_speaker_reframe.py
"""
with open('requirements.txt', 'r', encoding='utf-8') as f:
    req = f.read()
if 'opencv-python-headless' not in req:
    req = req.rstrip('\n') + '\nopencv-python-headless\n'
    with open('requirements.txt', 'w', encoding='utf-8') as f:
        f.write(req)
    print('OK: added opencv-python-headless to requirements.txt')
else:
    print('SKIP: opencv-python-headless already in requirements.txt')

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

changed = []

# 1. Insert the detection + expression-building helpers right before extract_clip()
anchor = "def extract_clip(video_path, start, duration, output_path, caption=\"\", words=None):"
helpers = '''def detect_speaker_crop_keyframes(video_path, clip_start, clip_duration, max_samples=15):
    """Samples frames across a clip's time window from the SOURCE video,
    finds the largest detected face in each sample, and returns a list of
    (t_relative_to_clip, frac_x) keyframes describing where to horizontally
    center the 9:16 crop over time, so the reframe follows the speaker
    instead of doing a dumb center-crop.

    Returns None if OpenCV/the video can't be read or no face is ever
    found, so the caller falls back to a plain center crop.
    """
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

        interval = max(clip_duration / max_samples, 1.0)
        t = 0.0
        keyframes = []
        while t <= clip_duration:
            cap.set(cv2.CAP_PROP_POS_MSEC, (clip_start + t) * 1000)
            ok, frame = cap.read()
            if ok and frame is not None:
                h, w = frame.shape[:2]
                scale = 320.0 / w if w > 320 else 1.0
                small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1.0 else frame
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(30, 30))
                if len(faces):
                    fx, fy, fw, fh = max(faces, key=lambda fc: fc[2] * fc[3])
                    center_x = (fx + fw / 2) / small.shape[1]
                    keyframes.append((round(t, 2), round(center_x, 4)))
            t += interval
        cap.release()
        return keyframes if len(keyframes) >= 1 else None
    except Exception as e:
        print('[reframe] speaker detection failed, falling back to center crop:', e, flush=True)
        return None

def build_crop_x_expr(keyframes):
    """Builds an ffmpeg crop-filter x expression (eval=frame) that pans the
    crop window horizontally between detected speaker positions over time,
    clamped to stay inside the scaled frame. With no keyframes this reduces
    to the same plain center crop as before."""
    if not keyframes:
        raw = "in_w/2-out_w/2"
    elif len(keyframes) == 1:
        raw = f"in_w*{keyframes[0][1]}-out_w/2"
    else:
        raw = f"in_w*{keyframes[-1][1]}-out_w/2"
        for i in range(len(keyframes) - 2, -1, -1):
            t0, x0 = keyframes[i]
            t1, x1 = keyframes[i + 1]
            if t1 <= t0:
                continue
            slope = (x1 - x0) / (t1 - t0)
            seg = f"(in_w*({x0}+({slope})*(t-{t0}))-out_w/2)"
            raw = f"if(lt(t,{t1}),{seg},{raw})"
        raw = f"if(lt(t,{keyframes[0][0]}),in_w*{keyframes[0][1]}-out_w/2,{raw})"
    return f"clip({raw},0,in_w-out_w)"

''' + anchor

n = src.count(anchor)
if n == 1 and 'def detect_speaker_crop_keyframes' not in src:
    src = src.replace(anchor, helpers, 1)
    changed.append('inserted detect_speaker_crop_keyframes() and build_crop_x_expr() helpers')
elif 'def detect_speaker_crop_keyframes' in src:
    print('SKIP: helpers already present')
else:
    print(f'FAILED: expected to find extract_clip() def exactly once, found {n}. No changes made.')

# 2. Swap the two fixed `crop={tw}:{th}` usages inside extract_clip for the
#    dynamic, speaker-tracked crop.
old_crop_block = '''    if ass_path:
        escaped_ass = ass_path.replace('\\\\', '/').replace(':', '\\\\:')
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
'''
new_crop_block = '''    keyframes = detect_speaker_crop_keyframes(video_path, start, duration)
    crop_x_expr = build_crop_x_expr(keyframes)
    crop_part = (
        f"crop=w={target_w}:h={target_h}:"
        f"x='{crop_x_expr}':y='in_h/2-out_h/2'"
    )

    if ass_path:
        escaped_ass = ass_path.replace('\\\\', '/').replace(':', '\\\\:')
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"{crop_part},"
            f"ass='{escaped_ass}'"
        )
    else:
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"{crop_part}"
        )
'''
if old_crop_block in src:
    src = src.replace(old_crop_block, new_crop_block, 1)
    changed.append('extract_clip() now uses the speaker-tracked dynamic crop')
elif 'crop_x_expr = build_crop_x_expr' in src:
    print('SKIP: extract_clip() crop block already patched')
else:
    print('FAILED: could not find the exact crop block inside extract_clip() to replace. No changes made there.')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

for c in changed:
    print('OK:', c)

print('')
print('Verify with:')
print('''python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('app.py SYNTAX OK')"''')
