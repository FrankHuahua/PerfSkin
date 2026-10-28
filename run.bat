@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Please run install_and_run.bat first.
    pause
    exit /b 1
)
rem --replace: if it is already running, restart it (picks up new code/config)
start "" ".venv\Scripts\pythonw.exe" main.py --replace
