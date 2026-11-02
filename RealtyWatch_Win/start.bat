@echo off
rem ASCII only: cmd misreads multibyte UTF-8 lines in .bat files
cd /d "%~dp0"
rem Component check: instant when installed, otherwise installs what is missing (messages come from setup.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -Quick || (pause & exit /b 1)
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0browsers"
set "PYTHONUTF8=1"
start "" "runtime\python\pythonw.exe" -m app.main
