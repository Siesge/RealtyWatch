@echo off
rem ASCII only: cmd misreads multibyte UTF-8 lines in .bat files
setlocal
set "RW_ROOT=%~dp0"
cd /d "%RW_ROOT%"

if not exist "%RW_ROOT%data\update_staging\run_update.ps1" goto :no_staged

powershell -NoProfile -ExecutionPolicy Bypass -Command "$r = [System.IO.Path]::GetFullPath($env:RW_ROOT).TrimEnd('\') + '\'; $lf = Join-Path $r 'data\app.lock'; $warned = $false; while ($true) { $procs = @(Get-Process -Name pythonw, python, RealtyWatch -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $PID -and $_.Path -and $_.Path.StartsWith($r, [System.StringComparison]::OrdinalIgnoreCase) }); $locked = $false; if (Test-Path -LiteralPath $lf) { try { $fs = [System.IO.File]::Open($lf, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None); $fs.Close() } catch [System.IO.FileNotFoundException] { $locked = $false } catch [System.IO.DirectoryNotFoundException] { $locked = $false } catch { $locked = $true } }; if ($procs.Count -eq 0 -and -not $locked) { break }; if (-not $warned) { Write-Host ([regex]::Unescape('\nRealtyWatch \u0441\u0435\u0439\u0447\u0430\u0441 \u0437\u0430\u043f\u0443\u0449\u0435\u043d \u0432 \u044d\u0442\u043e\u0439 \u043f\u0430\u043f\u043a\u0435.\n\u0417\u0430\u043a\u0440\u043e\u0439\u0442\u0435 \u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u0443 (\u0437\u043d\u0430\u0447\u043e\u043a \u0432 \u0442\u0440\u0435\u0435 -> \u043f\u0440\u0430\u0432\u0430\u044f \u043a\u043d\u043e\u043f\u043a\u0430 -> \u0412\u044b\u0445\u043e\u0434).\n\u041e\u0436\u0438\u0434\u0430\u044e \u0437\u0430\u043a\u0440\u044b\u0442\u0438\u044f RealtyWatch \u0434\u043b\u044f \u0437\u0430\u043f\u0443\u0441\u043a\u0430 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0438...\n')) -ForegroundColor Yellow; $warned = $true }; Start-Sleep -Milliseconds 500 }; if ($warned) { Start-Sleep -Milliseconds 400; Write-Host ([regex]::Unescape('\u041f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u0430 \u0437\u0430\u043a\u0440\u044b\u0442\u0430. \u0417\u0430\u043f\u0443\u0441\u043a\u0430\u044e \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0443 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u044f...')) -ForegroundColor Green }"
if errorlevel 1 exit /b %errorlevel%

powershell -NoProfile -ExecutionPolicy Bypass -File "%RW_ROOT%data\update_staging\run_update.ps1"
if errorlevel 1 pause
exit /b %errorlevel%

:no_staged
if exist "%RW_ROOT%app\manual_update.ps1" goto :run_helper
powershell -NoProfile -Command "Write-Host ([regex]::Unescape('\n\u041f\u0430\u043a\u0435\u0442 data\\update_staging\\run_update.ps1 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d.\n\u0421\u043a\u0430\u0447\u0430\u0439\u0442\u0435 RealtyWatch-*-windows-portable.zip \u0441 https://github.com/Siesge/RealtyWatch/releases/latest\n\u0438 \u0440\u0430\u0441\u043f\u0430\u043a\u0443\u0439\u0442\u0435 \u043f\u043e\u0432\u0435\u0440\u0445 \u043f\u0430\u043f\u043a\u0438 \u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u044b (\u0431\u0430\u0437\u0430 database\\ \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u0441\u044f).\n')) -ForegroundColor Yellow"
pause
exit /b 1

:run_helper
powershell -NoProfile -ExecutionPolicy Bypass -File "%RW_ROOT%app\manual_update.ps1"
exit /b %errorlevel%
