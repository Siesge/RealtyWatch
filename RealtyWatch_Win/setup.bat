@echo off
rem ASCII only: cmd misreads multibyte UTF-8 lines in .bat files
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
