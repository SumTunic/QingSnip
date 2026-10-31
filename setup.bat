@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
echo Setup complete. Double-click start.bat to launch QingSnip.
pause
exit /b 0
:failed
echo Setup failed. Please check Python installation and network access.
pause
exit /b 1
