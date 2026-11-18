// SimWB-Setup.exe: unpacks the workbench (embedded app.zip) into %LOCALAPPDATA%\Programs\SimWB and runs the installer.
// Built by installer\build_release.ps1 with the C# compiler that ships with Windows (no extra tools needed).
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;

static class Setup
{
    static int Main()
    {
        Console.Title = "Mechanical Simulation Workbench - Setup";
        string local = Environment.GetEnvironmentVariable("LOCALAPPDATA");
        if (string.IsNullOrEmpty(local)) local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string app = Path.GetFullPath(Path.Combine(local, "Programs", "SimWB"));
        Console.WriteLine();
        Console.WriteLine("  Unpacking the workbench...");
        try
        {
            Directory.CreateDirectory(app);
            using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("app.zip"))
            using (ZipArchive z = new ZipArchive(s))
            {
                foreach (ZipArchiveEntry e in z.Entries)
                {
                    if (e.FullName.EndsWith("/")) continue;
                    string dest = Path.GetFullPath(Path.Combine(app, e.FullName));
                    if (!dest.StartsWith(app + Path.DirectorySeparatorChar)) continue;   // never write outside the install folder
                    Directory.CreateDirectory(Path.GetDirectoryName(dest));
                    e.ExtractToFile(dest, true);
                }
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine("  Could not unpack the files: " + ex.Message);
            Console.WriteLine("  Press Enter to close.");
            Console.ReadLine();
            return 1;
        }
        string script = Path.Combine(app, "installer", "Install-SimWB.ps1");
        ProcessStartInfo psi = new ProcessStartInfo("powershell.exe",
            "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\" -InstallDir \"" + app + "\"");
        psi.UseShellExecute = false;
        using (Process p = Process.Start(psi))
        {
            p.WaitForExit();
            return p.ExitCode;
        }
    }
}
