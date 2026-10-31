@echo off
rem Builds ..\RealtyWatch.exe with the app icon (uses the C# compiler bundled with Windows .NET Framework)
cd /d "%~dp0"
"%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /optimize+ /win32icon:..\assets\icon.ico /out:..\RealtyWatch.exe RealtyWatch.cs
