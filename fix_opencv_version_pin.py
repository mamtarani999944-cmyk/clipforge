"""
Fixes 'module cv2 has no attribute CascadeClassifier'.

Railway's build logs show pip resolved opencv-python-headless to version
5.0.0.93 -- there is no real OpenCV 5.0 Python release; that's a broken or
mis-packaged build on PyPI, which is why the real objdetect bindings
(CascadeClassifier) are missing even though `import cv2` succeeds. Pinning
to a known-good, widely used stable 4.x build fixes it.

Run from your clipforge-final project folder:
    python fix_opencv_version_pin.py
"""
with open('requirements.txt', 'r', encoding='utf-8') as f:
    lines = f.read().splitlines()

changed = False
for i, line in enumerate(lines):
    if line.strip().startswith('opencv-python-headless'):
        if line.strip() != 'opencv-python-headless==4.10.0.84':
            lines[i] = 'opencv-python-headless==4.10.0.84'
            changed = True
        break
else:
    lines.append('opencv-python-headless==4.10.0.84')
    changed = True

with open('requirements.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')

if changed:
    print('OK: pinned opencv-python-headless==4.10.0.84 in requirements.txt')
else:
    print('SKIP: already pinned to 4.10.0.84')

print('')
print('requirements.txt now:')
print(open('requirements.txt', encoding='utf-8').read())
