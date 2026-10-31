@echo off
setlocal
cd /d "%~dp0"
if exist "dist\QingSnip\QingSnip.exe" (
    start "" "%~dp0dist\QingSnip\QingSnip.exe" --settings
    exit /b 0
)
if not exist ".venv\Scripts\pythonw.exe" (
    echo Please run setup.bat first.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py" --settings
