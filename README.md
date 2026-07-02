# ClipForge — Real Video to Shorts

Paste any YouTube or TikTok URL → get real clips of YOUR video.
No demo videos. No stock footage. Your video, your clips.

---

## Run it (3 steps)

### Mac / Linux
```bash
bash run_mac_linux.sh
```

### Windows
Double-click `run_windows.bat`

### Already have Python + FFmpeg + yt-dlp?
```bash
pip install faster-whisper
python clipforge.py
```

---

## What happens when you run it

```
Paste your YouTube or TikTok URL: https://youtube.com/watch?v=YOUR_VIDEO

→ Fetching video info...
  ✓ Title:    YOUR VIDEO TITLE          ← real title
  ✓ Channel:  Your Channel Name
  ✓ Duration: 10:34

→ Downloading YOUR video...
  ✓ Downloaded: source.mp4  (145.2 MB)

→ Transcribing speech...
  ✓ Transcribed 87 segments

→ Finding best moments...
  ✓ Clip 1  [1:24 → 2:18]  score=97  "Most people don't realize..."
  ✓ Clip 2  [4:05 → 5:02]  score=89  "Here's the key framework..."
  ✓ Clip 3  [7:11 → 8:04]  score=84  "This changes everything..."

→ Cutting 3 clips...
  ✓ Saved: clip_1_of_3.mp4  (8,200 KB)
  ✓ Saved: clip_2_of_3.mp4  (7,900 KB)
  ✓ Saved: clip_3_of_3.mp4  (8,100 KB)

Done!
  Clips folder opens automatically ↗
```

---

## Output folder

```
clipforge_output/
├── clips/
│   ├── clip_1_of_3.mp4   ← your real video, 9:16, with captions
│   ├── clip_2_of_3.mp4
│   ├── clip_3_of_3.mp4
│   ├── thumb_1.jpg
│   ├── thumb_2.jpg
│   └── thumb_3.jpg
└── summary.txt            ← video title + timestamps + scores
```

---

## Common issues

| Problem | Fix |
|---------|-----|
| "yt-dlp not found" | `pip install yt-dlp` |
| "FFmpeg not found" | See install steps above |
| "Private video" | Use a public YouTube URL |
| "Age-restricted" | Try a different video |
| No captions in clips | `pip install faster-whisper` |
| Slow transcription | Normal on CPU — 1 min per 10 min video |
