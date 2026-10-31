@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean QingSnip.spec
if errorlevel 1 goto :failed
echo Build complete: dist\QingSnip\QingSnip.exe
pause
exit /b 0
:failed
echo Build failed. See the messages above.
pause
exit /b 1
