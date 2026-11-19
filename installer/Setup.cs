// SimWB-Setup.exe: asks where to install and whether to make a desktop icon, unpacks the workbench (embedded app.zip)
// there and runs the installer. Default folder: C:\SimWB (no admin rights needed; see DefaultFolder).
// Built by installer\build_release.ps1 with the C# compiler that ships with Windows (no extra tools needed).
// SIMWB_QUIET=1 skips the window and uses the defaults (for tests); SIMWB_INSTALL_DIR sets the folder then.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Windows.Forms;

static class Setup
{
    // The folder the workbench goes into. The uninstaller removes this whole folder, so a folder that already holds
    // other things (Documents, a drive root) is never used itself: the workbench goes into a "SimWB" folder inside it.
    public static string TargetFolder(string typed)
    {
        string dir = Path.GetFullPath(Environment.ExpandEnvironmentVariables((typed ?? "").Trim().Trim('"')));
        bool exists = Directory.Exists(dir);
        bool isSimwb = File.Exists(Path.Combine(dir, "simwb", "__init__.py")) || File.Exists(Path.Combine(dir, "install.log"));
        bool empty = !exists || Directory.GetFileSystemEntries(dir).Length == 0;
        bool root = Path.GetPathRoot(dir).TrimEnd('\\') == dir.TrimEnd('\\');
        if (isSimwb || (empty && !root) || string.Equals(Path.GetFileName(dir.TrimEnd('\\')), "SimWB", StringComparison.OrdinalIgnoreCase))
            return dir;
        return Path.Combine(dir, "SimWB");
    }

    class Choice { public string Dir; public bool Desktop = true; }

    // ---- safety of the install folder. The workbench runs programs from its folder (Python, OpenFOAM, scripts), so
    // nobody else may be able to write there or to have made it first. Folders inside the user's profile are private
    // to the user already; any other (C:\SimWB, D:\Tools\SimWB) is created readable/writable only by the user, SYSTEM
    // and Administrators, and an existing one is used only when the user (or Administrators) own it.
    static bool UnderProfile(string dir)
    {
        string p = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile).TrimEnd('\\') + "\\";
        return (dir.TrimEnd('\\') + "\\").StartsWith(p, StringComparison.OrdinalIgnoreCase);
    }

    static DirectorySecurity Locked()
    {
        DirectorySecurity ds = new DirectorySecurity();
        ds.SetAccessRuleProtection(true, false);   // no inherited permissions (C:\ gives every user write access)
        SecurityIdentifier[] who = { WindowsIdentity.GetCurrent().User,
            new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
            new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null) };
        foreach (SecurityIdentifier sid in who)
            ds.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl,
                InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
        return ds;
    }

    // The existing parent folders of dir (up to, not including, the drive root): each must be private, or someone else
    // could rename it and put their own folder in its place (a folder made on C: gives every user that right).
    // Parents that do not exist yet are not a problem: Prepare creates them private. null when all are fine.
    static string ChainProblem(string dir)
    {
        if (dir.StartsWith("\\\\")) return "Network folders cannot be used.";
        string root = Path.GetPathRoot(dir).TrimEnd('\\');
        for (string p = Path.GetDirectoryName(dir.TrimEnd('\\')); p != null && p.TrimEnd('\\') != root; p = Path.GetDirectoryName(p))
        {
            if (!Directory.Exists(p)) continue;
            try
            {
                DirectoryInfo di = new DirectoryInfo(p);
                if ((di.Attributes & FileAttributes.ReparsePoint) != 0) return p + " is a link.";
                DirectorySecurity a = di.GetAccessControl();
                if (!Trusted((SecurityIdentifier)a.GetOwner(typeof(SecurityIdentifier)))) return p + " belongs to another user.";
                if (!OnlyTrusted(a)) return p + " can be changed by other users of this computer.";
            }
            catch (Exception e) { return "Cannot check " + p + ": " + e.Message; }
        }
        return null;
    }

    static string PerUserFolder()
    {
        string local = Environment.GetEnvironmentVariable("LOCALAPPDATA");
        if (string.IsNullOrEmpty(local)) local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        return Path.GetFullPath(Path.Combine(local, "Programs", "SimWB"));
    }

    // The folder that will really be used for what the user typed, adjusting it when it is not safe: the folder itself
    // when it is fine; else a SimWB folder directly on the same drive; else the per-user folder. note says why.
    public static string Resolve(string typed, out string note)
    {
        note = null;
        string t = TargetFolder(typed), why = Problem(t);
        if (why == null) return t;
        note = why;
        string drive = Path.GetPathRoot(t);
        if (drive != null && !drive.StartsWith("\\\\"))
        {
            string alt = Path.Combine(drive, "SimWB");
            if (!string.Equals(alt, t, StringComparison.OrdinalIgnoreCase) && Problem(alt) == null) return alt;
        }
        return PerUserFolder();
    }

    static bool Trusted(SecurityIdentifier s)
    {
        return s.Equals(WindowsIdentity.GetCurrent().User) || s.IsWellKnown(WellKnownSidType.LocalSystemSid) ||
               s.IsWellKnown(WellKnownSidType.BuiltinAdministratorsSid);
    }

    // true when only the user, SYSTEM and Administrators are given any access (the file or folder is private)
    static bool OnlyTrusted(FileSystemSecurity fs)
    {
        foreach (FileSystemAccessRule r in fs.GetAccessRules(true, true, typeof(SecurityIdentifier)))
            if (r.AccessControlType == AccessControlType.Allow && !Trusted((SecurityIdentifier)r.IdentityReference)) return false;
        return true;
    }

    // Every file and folder inside an existing install folder: not a link, owned by the user / SYSTEM / Administrators,
    // and open to nobody else. null when all are fine, otherwise the first that is not.
    static string TreeProblem(string dir)
    {
        Stack<string> todo = new Stack<string>();
        todo.Push(dir);
        while (todo.Count > 0)
        {
            foreach (string e in Directory.EnumerateFileSystemEntries(todo.Pop()))
            {
                FileAttributes a = File.GetAttributes(e);
                if ((a & FileAttributes.ReparsePoint) != 0) return e + " is a link.";
                bool isDir = (a & FileAttributes.Directory) != 0;
                FileSystemSecurity fs = isDir ? (FileSystemSecurity)new DirectoryInfo(e).GetAccessControl() : new FileInfo(e).GetAccessControl();
                if (!Trusted((SecurityIdentifier)fs.GetOwner(typeof(SecurityIdentifier))) || !OnlyTrusted(fs))
                    return e + " belongs to, or can be changed by, another user.";
                if (isDir) todo.Push(e);
            }
        }
        return null;
    }

    // null when the folder can be used, otherwise the reason
    public static string Problem(string dir)
    {
        if (UnderProfile(dir)) return null;
        string chain = ChainProblem(dir);
        if (chain != null) return chain;
        if (!Directory.Exists(dir)) return null;
        try
        {
            DirectoryInfo di = new DirectoryInfo(dir);
            if ((di.Attributes & FileAttributes.ReparsePoint) != 0) return dir + " is a link, not a folder.";
            DirectorySecurity acl = di.GetAccessControl();
            SecurityIdentifier owner = (SecurityIdentifier)acl.GetOwner(typeof(SecurityIdentifier));
            if (!owner.Equals(WindowsIdentity.GetCurrent().User) && !owner.IsWellKnown(WellKnownSidType.BuiltinAdministratorsSid))
                return dir + " already exists and belongs to another user.";
            // files in a folder that others could write to may have been planted: only an empty folder or a private one
            if (!OnlyTrusted(acl) && Directory.GetFileSystemEntries(dir).Length > 0)
                return dir + " already holds files and is not private to you: choose an empty folder.";
            return null;
        }
        catch (Exception e) { return "Cannot check " + dir + ": " + e.Message; }
    }

    // creates the folder (or takes over one the user owns) with the protected permissions; throws if it is not safe
    public static void Prepare(string dir)
    {
        if (UnderProfile(dir)) { Directory.CreateDirectory(dir); return; }
        string why = Problem(dir);
        if (why != null) throw new InvalidOperationException(why);
        // parents that do not exist yet are made private first, from the top (D:\Engineering, then D:\Engineering\Tools)
        string root = Path.GetPathRoot(dir).TrimEnd('\\');
        List<string> missing = new List<string>();
        for (string p = Path.GetDirectoryName(dir.TrimEnd('\\')); p != null && p.TrimEnd('\\') != root && !Directory.Exists(p); p = Path.GetDirectoryName(p))
            missing.Insert(0, p);
        foreach (string m in missing) Directory.CreateDirectory(m, Locked());
        bool existed = Directory.Exists(dir);
        // decided once, before anything is changed: a folder that is not private now must still be empty after it is locked
        bool wasPrivate = existed && OnlyTrusted(new DirectoryInfo(dir).GetAccessControl());
        if (existed && wasPrivate && Directory.GetFileSystemEntries(dir).Length > 0)
        {
            why = TreeProblem(dir);   // (an existing install: nothing inside may come from, or be open to, another user)
            if (why != null) throw new InvalidOperationException(why + " Uninstall it first, or choose another folder.");
        }
        if (!existed) Directory.CreateDirectory(dir, Locked());   // created with the permissions, never open
        else new DirectoryInfo(dir).SetAccessControl(Locked());
        why = Problem(dir);   // (another user cannot have won a race: the owner is checked again)
        if (why != null) throw new InvalidOperationException(why);
        if (existed && !wasPrivate && Directory.GetFileSystemEntries(dir).Length > 0)
            throw new InvalidOperationException(dir + " changed while it was being made private: nothing was installed.");
    }

    static bool IsInstall(string dir)
    {
        return File.Exists(Path.Combine(dir, "install.log")) || File.Exists(Path.Combine(dir, "simwb", "__init__.py"));
    }

    // The folder offered first: an installed workbench in the older per-user folder is updated in place; otherwise
    // C:\SimWB (short, easy to find, no admin rights needed for a new folder on the system drive); if that cannot be
    // written (locked-down computer) the per-user folder.
    public static string DefaultFolder()
    {
        string perUser = PerUserFolder();
        if (IsInstall(perUser)) return perUser;
        string drive = Environment.GetEnvironmentVariable("SystemDrive");
        if (string.IsNullOrEmpty(drive)) drive = "C:";
        string shortDir = drive + "\\SimWB";
        try
        {
            if (Problem(shortDir) != null) return perUser;       // someone else's folder (or a link): never used
            if (IsInstall(shortDir)) return shortDir;
            bool made = !Directory.Exists(shortDir);
            if (made) Directory.CreateDirectory(shortDir, Locked());   // (can it be made? private from the first moment)
            string probe = Path.Combine(shortDir, ".write-test");
            File.WriteAllText(probe, "x"); File.Delete(probe);
            if (made && Directory.GetFileSystemEntries(shortDir).Length == 0) Directory.Delete(shortDir);   // (made again at install)
            return shortDir;
        }
        catch { return perUser; }
    }

    static Choice Ask(string defaultDir)
    {
        Application.EnableVisualStyles();
        Form f = new Form { Text = "Mechanical Simulation Workbench - Setup", ClientSize = new Size(560, 250),
                            FormBorderStyle = FormBorderStyle.FixedDialog, MaximizeBox = false, MinimizeBox = false,
                            StartPosition = FormStartPosition.CenterScreen, Font = new Font("Segoe UI", 9f) };
        Label l1 = new Label { Text = "Install the workbench into this folder:", Left = 16, Top = 16, Width = 520 };
        TextBox box = new TextBox { Text = defaultDir, Left = 16, Top = 40, Width = 440 };
        Button browse = new Button { Text = "Browse...", Left = 464, Top = 38, Width = 80 };
        Label will = new Label { Left = 16, Top = 70, Width = 528, Height = 40, ForeColor = Color.DimGray };
        CheckBox desk = new CheckBox { Text = "Put an icon on the desktop", Left = 16, Top = 118, Width = 400, Checked = true };
        Label note = new Label { Left = 16, Top = 146, Width = 528, Height = 50, ForeColor = Color.DimGray,
            Text = "A Start menu entry (and an Uninstall entry) is always made. About 10 GB of free space and 15-30 minutes " +
                   "are needed: the workbench downloads Python, OpenFOAM and CalculiX once. No administrator rights." };
        Button ok = new Button { Text = "Install", Left = 380, Top = 208, Width = 80, DialogResult = DialogResult.OK };
        Button cancel = new Button { Text = "Cancel", Left = 468, Top = 208, Width = 76, DialogResult = DialogResult.Cancel };
        Action refresh = delegate
        {
            try
            {
                string why, t = Resolve(box.Text, out why);
                will.Text = "Will install into: " + t + (why == null ? "" : "\r\nAdjusted, because " + why);
                will.ForeColor = why == null ? Color.DimGray : Color.DarkGoldenrod;
                ok.Enabled = box.Text.Trim().Length > 0;
            }
            catch { will.Text = "This is not a valid folder."; ok.Enabled = false; }
        };
        box.TextChanged += delegate { refresh(); };
        browse.Click += delegate
        {
            using (FolderBrowserDialog d = new FolderBrowserDialog { Description = "Choose where to install the workbench" })
                if (d.ShowDialog(f) == DialogResult.OK) box.Text = d.SelectedPath;
        };
        f.Controls.AddRange(new Control[] { l1, box, browse, will, desk, note, ok, cancel });
        f.AcceptButton = ok; f.CancelButton = cancel;
        refresh();
        if (f.ShowDialog() != DialogResult.OK) return null;
        string unused;
        return new Choice { Dir = Resolve(box.Text, out unused), Desktop = desk.Checked };
    }

    [STAThread]
    static int Main()
    {
        Console.Title = "Mechanical Simulation Workbench - Setup";
        string defaultDir = DefaultFolder();
        Choice c;
        if (!string.IsNullOrEmpty(Environment.GetEnvironmentVariable("SIMWB_QUIET")))
        {
            string d = Environment.GetEnvironmentVariable("SIMWB_INSTALL_DIR");
            string unused;
            c = new Choice { Dir = Resolve(string.IsNullOrEmpty(d) ? defaultDir : d, out unused), Desktop = string.IsNullOrEmpty(Environment.GetEnvironmentVariable("SIMWB_NO_DESKTOP_SHORTCUT")) };
        }
        else
        {
            try { c = Ask(defaultDir); }
            catch { c = new Choice { Dir = defaultDir }; }   // (no window possible: the default folder)
            if (c == null) return 0;                         // cancelled
        }
        string app = c.Dir;
        Console.WriteLine();
        Console.WriteLine("  Installing into " + app);
        Console.WriteLine("  Unpacking the workbench...");
        try
        {
            Prepare(app);
            using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("app.zip"))
            using (ZipArchive z = new ZipArchive(s))
            {
                foreach (ZipArchiveEntry e in z.Entries)
                {
                    if (e.FullName.EndsWith("/")) continue;
                    string dest = Path.GetFullPath(Path.Combine(app, e.FullName));
                    if (!dest.StartsWith(app.TrimEnd('\\') + Path.DirectorySeparatorChar)) continue;   // never write outside the install folder
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
        if (!c.Desktop) psi.EnvironmentVariables["SIMWB_NO_DESKTOP_SHORTCUT"] = "1";
        psi.EnvironmentVariables["SIMWB_DIR_PREPARED"] = "1";   // (the folder was just checked and locked: not walked again)
        using (Process p = Process.Start(psi))
        {
            p.WaitForExit();
            return p.ExitCode;
        }
    }
}
