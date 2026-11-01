@echo off
rem RealtyWatch tests: removal logic, page status, scheduler, run log, captcha.
rem Everything runs in a temp folder with synthetic data; the real database and logs are not touched.
cd /d "%~dp0.."
"runtime\python\python.exe" -m unittest discover -s tests -t . -v
echo.
pause
