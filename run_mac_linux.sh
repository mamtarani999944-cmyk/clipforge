#!/bin/bash
set -e

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "   🎬  ClipForge — Install & Run"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Python ─────────────────────────────────────
if command -v python3 &>/dev/null; then
    echo "✓ Python found: $(python3 --version)"
else
    echo "✗ Python 3 not found. Install from https://python.org"
    exit 1
fi

# ── pip ────────────────────────────────────────
PIP="python3 -m pip"

# ── FFmpeg ─────────────────────────────────────
if command -v ffmpeg &>/dev/null; then
    echo "✓ FFmpeg found"
else
    echo "→ FFmpeg not found — installing..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v brew &>/dev/null; then
            brew install ffmpeg
        else
            echo "✗ Homebrew not found. Install from https://brew.sh then run: brew install ffmpeg"
            exit 1
        fi
    elif [[ "$OSTYPE" == "linux"* ]]; then
        sudo apt-get update -qq && sudo apt-get install -y ffmpeg
    else
        echo "✗ Please install FFmpeg from https://ffmpeg.org/download.html"
        exit 1
    fi
fi

# ── yt-dlp ─────────────────────────────────────
if command -v yt-dlp &>/dev/null; then
    echo "✓ yt-dlp found"
else
    echo "→ Installing yt-dlp..."
    $PIP install -q yt-dlp
fi

# ── faster-whisper (for captions) ──────────────
if python3 -c "import faster_whisper" 2>/dev/null; then
    echo "✓ faster-whisper found"
else
    echo "→ Installing faster-whisper (AI captions)..."
    $PIP install -q faster-whisper
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "   All set! Starting ClipForge..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

python3 clipforge.py
