@echo off
setlocal
cd /d "%~dp0"
rem All build caches stay inside this folder
set "PIP_CACHE_DIR=%~dp0.cache\pip"
set "PYINSTALLER_CONFIG_DIR=%~dp0.cache\pyinstaller"
set "MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"

if not exist ".venv\Scripts\python.exe" (
    echo Please run install_and_run.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check pyinstaller
if errorlevel 1 ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -i %MIRROR% pyinstaller

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --windowed --name PerfSkin ^
    --icon "%~dp0assets\icon.ico" --hidden-import pystray._win32 --collect-submodules perfmon ^
    --add-data "%~dp0web;web" --add-data "%~dp0assets;assets" --add-data "%~dp0skins;skins" ^
    --workpath "%~dp0.cache\build" --specpath "%~dp0.cache\build" --distpath "%~dp0dist" ^
    main.py
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)
echo.
echo Done: dist\PerfSkin\PerfSkin.exe
echo (One-folder build, so nothing gets unpacked to the temp folder at runtime.)
pause
