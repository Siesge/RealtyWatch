// Запускатор RealtyWatch.exe: проверяет компоненты (при необходимости показывает окно установки)
// и открывает программу без консоли. Сборка: launcher\build.bat
using System;
using System.Diagnostics;
using System.IO;

class Launcher
{
    static int Run(string file, string args, string dir, bool hidden)
    {
        var psi = new ProcessStartInfo(file, args) { WorkingDirectory = dir, UseShellExecute = false, CreateNoWindow = hidden };
        using (var p = Process.Start(psi))
        {
            p.WaitForExit();
            return p.ExitCode;
        }
    }

    [STAThread]
    static int Main()
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string setup = "-NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(dir, "setup.ps1") + "\"";

        if (Run("powershell.exe", setup + " -CheckOnly", dir, true) != 0)
        {
            int code = Run("powershell.exe", setup + " -Quick -PauseOnError", dir, false);
            if (code != 0) return code;
        }

        var app = new ProcessStartInfo(Path.Combine(dir, @"runtime\python\pythonw.exe"), "-m app.main")
        {
            WorkingDirectory = dir,
            UseShellExecute = false,
        };
        app.EnvironmentVariables["PLAYWRIGHT_BROWSERS_PATH"] = Path.Combine(dir, "browsers");
        app.EnvironmentVariables["PYTHONUTF8"] = "1";
        Process.Start(app);
        return 0;
    }
}
