@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "runtime\python\pythonw.exe" (
    echo Среда не установлена — запускаю установку...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" || (pause & exit /b 1)
)
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0browsers"
set "PYTHONUTF8=1"
start "" "runtime\python\pythonw.exe" -m app.main
