@echo off
title ClipForge - Video to Shorts
echo.
echo ==========================================
echo    ClipForge ^| Video to Shorts
echo ==========================================
echo.

:: Check Python
python --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Install from: https://python.org
    echo Make sure to check "Add Python to PATH"
    pause
    exit /b 1
)
echo [OK] Python found

:: Check FFmpeg
ffmpeg -version >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] FFmpeg not found!
    echo.
    echo To install FFmpeg on Windows:
    echo   1. Go to https://www.gyan.dev/ffmpeg/builds/
    echo   2. Download ffmpeg-release-essentials.zip
    echo   3. Extract to C:\ffmpeg
    echo   4. Add C:\ffmpeg\bin to your System PATH
    echo   5. Restart this script
    echo.
    pause
    exit /b 1
)
echo [OK] FFmpeg found

:: Install Python packages
echo.
echo Installing packages...
python -m pip install -q yt-dlp faster-whisper
echo [OK] yt-dlp and faster-whisper installed

echo.
echo ==========================================
echo    Starting ClipForge...
echo ==========================================
echo.

python clipforge.py
pause
