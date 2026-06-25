#!/bin/bash
# ClipForge Server Setup Script
# Run this on your DigitalOcean Ubuntu server

echo "=== ClipForge Server Setup ==="

# Update system
apt update && apt upgrade -y

# Install Python, pip, git, ffmpeg
apt install python3 python3-pip python3-venv git ffmpeg -y

# Verify ffmpeg
echo "ffmpeg version:"
ffmpeg -version | head -1

# Clone your repo (replace with your GitHub URL)
# git clone https://github.com/mamtarani999944-cmyk/clipforge.git
# cd clipforge

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

echo "=== Setup complete! ==="
echo "Run: gunicorn --bind 0.0.0.0:5000 app:app"
