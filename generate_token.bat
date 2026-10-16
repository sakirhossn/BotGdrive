@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ======================================================================
echo   BotGdrive: Launching Google Drive Token Generator
echo ======================================================================
echo.

if exist ".venv\Scripts\python.exe" (
    echo [OK] Using virtual environment Python: .venv\Scripts\python.exe
    ".venv\Scripts\python.exe" generate_token.py
) else if exist "..\BotgDriveBig\.venv\Scripts\python.exe" (
    echo [OK] Using virtual environment Python: ..\BotgDriveBig\.venv\Scripts\python.exe
    "..\BotgDriveBig\.venv\Scripts\python.exe" generate_token.py
) else (
    echo [!] Running with system python...
    python generate_token.py
)

echo.
pause
