@echo off
echo Starting ClipForge...
start cmd /k "cd /d C:\Users\Hp\Downloads\clipforge-v2-with-url\clipforge && python app.py"
timeout /t 3
start cmd /k "cd /d C:\Users\Hp\Downloads\clipforge-v2-with-url\clipforge && cloudflared.exe tunnel --url http://localhost:5000"
echo Done! Check the two windows for your public link.