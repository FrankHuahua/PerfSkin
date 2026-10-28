@echo off
setlocal
cd /d "%~dp0"
rem Everything (venv, pip cache, browser profile, logs, your skins) stays inside this folder.
set "PIP_CACHE_DIR=%~dp0.cache\pip"
set "MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"

rem Python: %PYTHON% if you set it, otherwise the py launcher, otherwise python on PATH
if defined PYTHON (set "PY="%PYTHON%"") else (
    where py >nul 2>nul
    if not errorlevel 1 (set "PY=py -3") else (set "PY=python")
)

if not exist ".venv\Scripts\pythonw.exe" (
    echo [1/3] Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 goto nopython
)

echo [2/3] Installing dependencies...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo Default index failed, retrying with mirror...
    ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -i %MIRROR% -r requirements.txt
    if errorlevel 1 goto pipfail
)

echo [3/3] Checking hardware...
".venv\Scripts\python.exe" main.py --check
echo.
start "" ".venv\Scripts\pythonw.exe" main.py --replace
echo Started. The window opens in a moment; the star icon stays in the system tray.
echo Next time just double-click run.bat
pause
exit /b 0

:nopython
echo.
echo Python 3.9+ was not found.
echo Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"),
echo or set PYTHON=C:\path\to\python.exe and run this file again.
pause
exit /b 1

:pipfail
echo.
echo Failed to install dependencies. Please check your network connection.
pause
exit /b 1
