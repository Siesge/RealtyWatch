@echo off
rem ASCII only: cmd misreads multibyte UTF-8 lines in .bat files
cd /d "%~dp0"
if exist "%~dp0data\update_staging\run_update.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0data\update_staging\run_update.ps1"
    if errorlevel 1 pause
    exit /b %errorlevel%
)
if exist "%~dp0app\manual_update.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\manual_update.ps1"
    exit /b %errorlevel%
)
powershell -NoProfile -Command "Write-Host ([regex]::Unescape('\n\u041f\u0430\u043a\u0435\u0442 data\\update_staging\\run_update.ps1 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d.\n\u0421\u043a\u0430\u0447\u0430\u0439\u0442\u0435 RealtyWatch-*-windows-portable.zip \u0441 https://github.com/Siesge/RealtyWatch/releases/latest\n\u0438 \u0440\u0430\u0441\u043f\u0430\u043a\u0443\u0439\u0442\u0435 \u043f\u043e\u0432\u0435\u0440\u0445 \u043f\u0430\u043f\u043a\u0438 \u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u044b (\u0431\u0430\u0437\u0430 database\\ \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u0441\u044f).\n')) -ForegroundColor Yellow"
pause
