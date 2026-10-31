@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "runtime\python\python.exe" (
    echo Среда не установлена — запускаю setup...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" || (pause & exit /b 1)
)
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0browsers"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
"runtime\python\python.exe" -m app.main %*
pause
