@echo off
cd /d "%~dp0"
rem Demo data (a made-up gaming PC) for trying skins and the UI. Ctrl+C to stop.
start "" http://127.0.0.1:47899/
".venv\Scripts\python.exe" main.py --demo --no-tray --no-window --port 47899
