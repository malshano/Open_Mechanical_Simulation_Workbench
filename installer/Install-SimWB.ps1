<#
  Mechanical Simulation Workbench - one-click installer (Windows 10/11, 64-bit, no admin rights).

  Installs everything the workbench needs into one folder and makes a desktop icon:
    1. a private Python 3.12 (uv, pinned and checksummed)    2. the Python packages (gmsh, SciPy, FastAPI, ...)
    3. OpenFOAM v2606 (flow simulation) + Microsoft MPI      4. CalculiX (structural cross-check solver)
    5. desktop + Start menu shortcuts, a self-test, and the first start.
  Nothing is installed system-wide. The Uninstall shortcut (or Uninstall-SimWB.ps1) removes it all.

  Options:  -InstallDir <folder>   where to install (default C:\SimWB, or the folder of an existing install in
                                   %LOCALAPPDATA%\Programs\SimWB)
            -NoLaunch              do not start the workbench at the end
            -SkipSolvers           skip OpenFOAM and CalculiX (the workbench offers them again when it needs them)
            -DryRun                check the computer and show what would be done; download and change nothing
            -Quiet                 never wait for a key press
            -NoDesktopShortcut     no desktop icon (the Start menu entries are still made)
            -NoShortcuts           do not make desktop / Start menu icons (for tests)
#>
param(
    [string]$InstallDir = '',
    [switch]$NoLaunch, [switch]$SkipSolvers, [switch]$DryRun, [switch]$Quiet, [switch]$NoShortcuts, [switch]$NoDesktopShortcut
)
if ($env:SIMWB_NO_DESKTOP_SHORTCUT) { $NoDesktopShortcut = $true }
# the same switches from the environment (for the one-file setup, which cannot pass arguments, and for tests)
if ($env:SIMWB_QUIET) { $Quiet = $true }
if ($env:SIMWB_NO_LAUNCH) { $NoLaunch = $true }
if ($env:SIMWB_SKIP_SOLVERS) { $SkipSolvers = $true }
if ($env:SIMWB_NO_SHORTCUTS) { $NoShortcuts = $true }
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'    # Invoke-WebRequest is many times faster without its own bar
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$UV_VERSION = '0.11.19'
$UV_URL     = "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-pc-windows-msvc.zip"
$UV_SHA256  = '1665fc8e37b5d70a134820d6d7891747471a2ac8bc940ee7af0b69fd03b28d61'
$PYTHON     = '3.12'
$MIN_FREE_GB = 10

$Source  = Split-Path -Parent $PSScriptRoot          # the folder with simwb\ (the unzipped release or a checkout)

# ---- safety of the install folder (same rules as SimWB-Setup.exe). The workbench runs programs from its folder, so
# nobody else may be able to write there or to have made it first. Folders in the user's profile are private already;
# any other is created with permissions for the user, SYSTEM and Administrators only, and an existing one is used only
# when the user (or Administrators) own it.
function Test-InstallDir([string]$Dir) {      # $null when usable, otherwise the reason
    if (($Dir.TrimEnd('\') + '\').StartsWith($env:USERPROFILE.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return $null }
    $chain = Test-ChainProblem $Dir; if ($chain) { return $chain }
    if (-not (Test-Path -LiteralPath $Dir)) { return $null }
    if ((Get-Item -LiteralPath $Dir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { return "$Dir is a link, not a folder." }
    $acl = Get-Acl -LiteralPath $Dir
    if (-not (Test-TrustedSid $acl.GetOwner([Security.Principal.SecurityIdentifier]))) { return "$Dir already exists and belongs to another user." }
    # files in a folder that others could write to may have been planted: only an empty folder or a private one
    if (-not (Test-OnlyTrusted $acl) -and @(Get-ChildItem -LiteralPath $Dir -Force).Count -gt 0) { return "$Dir already holds files and is not private to you: choose an empty folder." }
    $null
}
# The existing parent folders (up to, not including, the drive root) must be private, or someone else could rename one
# and put their own folder in its place (a folder made on C: gives every user that right). Parents that do not exist
# yet are made private by New-LockedDirectory. $null when all are fine.
function Test-ChainProblem([string]$Dir) {
    $d = $Dir.TrimEnd('\')
    if ($d.StartsWith('\\')) { return 'Network folders cannot be used.' }
    $root = [IO.Path]::GetPathRoot($d).TrimEnd('\')
    for ($p = Split-Path $d -Parent; $p -and $p.TrimEnd('\') -ne $root; $p = Split-Path $p -Parent) {
        if (-not (Test-Path -LiteralPath $p)) { continue }
        if ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { return "$p is a link." }
        $a = Get-Acl -LiteralPath $p
        if (-not (Test-TrustedSid $a.GetOwner([Security.Principal.SecurityIdentifier]))) { return "$p belongs to another user." }
        if (-not (Test-OnlyTrusted $a)) { return "$p can be changed by other users of this computer." }
    }
    $null
}
# The folder to really use for $Dir, adjusted when it is not safe: a SimWB folder directly on the same drive, else the
# per-user folder. Says why when it adjusts.
function Resolve-InstallDir([string]$Dir) {
    $why = Test-InstallDir $Dir
    if (-not $why) { return $Dir }
    $root = [IO.Path]::GetPathRoot($Dir)
    $alt = if ($root -and -not $root.StartsWith('\\')) { Join-Path $root 'SimWB' } else { $null }
    $res = if ($alt -and ($alt -ne $Dir.TrimEnd('\')) -and -not (Test-InstallDir $alt)) { $alt } else { Join-Path $env:LOCALAPPDATA 'Programs\SimWB' }
    Write-Host "  Note: $why" -ForegroundColor Yellow
    Write-Host "  Installing into $res instead." -ForegroundColor Yellow
    $res
}
function Test-TrustedSid($sid) {
    $sid.Equals([Security.Principal.WindowsIdentity]::GetCurrent().User) -or
        $sid.IsWellKnown([Security.Principal.WellKnownSidType]::LocalSystemSid) -or
        $sid.IsWellKnown([Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid)
}
function Test-OnlyTrusted($acl) {      # only the user, SYSTEM and Administrators are given any access
    -not @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]) |
        Where-Object { $_.AccessControlType -eq 'Allow' -and -not (Test-TrustedSid $_.IdentityReference) })
}
function Test-TreeTrusted([string]$Dir) {   # every file and folder inside: not a link, trusted owner, open to nobody else
    foreach ($e in [IO.Directory]::EnumerateFileSystemEntries($Dir, '*', [IO.SearchOption]::AllDirectories)) {
        $a = [IO.File]::GetAttributes($e)
        if ($a -band [IO.FileAttributes]::ReparsePoint) { return "$e is a link." }
        $fs = if ($a -band [IO.FileAttributes]::Directory) { (New-Object IO.DirectoryInfo $e).GetAccessControl() } else { (New-Object IO.FileInfo $e).GetAccessControl() }
        if (-not (Test-TrustedSid $fs.GetOwner([Security.Principal.SecurityIdentifier])) -or -not (Test-OnlyTrusted $fs)) { return "$e belongs to, or can be changed by, another user." }
    }
    $null
}
function New-LockedDirectory([string]$Dir) {
    $inside = ($Dir.TrimEnd('\') + '\').StartsWith($env:USERPROFILE.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)
    if ($inside) { New-Item -ItemType Directory -Force -Path $Dir | Out-Null; return }
    $why = Test-InstallDir $Dir; if ($why) { throw $why }
    $sec = New-Object Security.AccessControl.DirectorySecurity
    $sec.SetAccessRuleProtection($true, $false)         # no inherited permissions (C:\ gives every user write access)
    foreach ($sid in @([Security.Principal.WindowsIdentity]::GetCurrent().User,
                       (New-Object Security.Principal.SecurityIdentifier([Security.Principal.WellKnownSidType]::LocalSystemSid, $null)),
                       (New-Object Security.Principal.SecurityIdentifier([Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null)))) {
        $sec.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
    }
    # parents that do not exist yet are made private first, from the top (D:\Engineering, then D:\Engineering\Tools)
    $root = [IO.Path]::GetPathRoot($Dir).TrimEnd('\')
    $missing = @(); for ($p = Split-Path $Dir.TrimEnd('\') -Parent; $p -and $p.TrimEnd('\') -ne $root -and -not (Test-Path -LiteralPath $p); $p = Split-Path $p -Parent) { $missing = @($p) + $missing }
    foreach ($m in $missing) { [IO.Directory]::CreateDirectory($m, $sec) | Out-Null }
    $existed = Test-Path -LiteralPath $Dir
    # decided once, before anything is changed: a folder that is not private now must still be empty after it is locked
    $wasPrivate = $existed -and (Test-OnlyTrusted (Get-Acl -LiteralPath $Dir))
    if ($existed -and $wasPrivate -and @(Get-ChildItem -LiteralPath $Dir -Force).Count -gt 0) {
        $why = Test-TreeTrusted $Dir    # an existing install: nothing inside may come from, or be open to, another user
        if ($why) { throw "$why Uninstall it first, or choose another folder." }
    }
    if ($existed) { (Get-Item -LiteralPath $Dir -Force).SetAccessControl($sec) }
    else { [IO.Directory]::CreateDirectory($Dir, $sec) | Out-Null }   # created with the permissions, never open
    $why = Test-InstallDir $Dir; if ($why) { throw $why }
    if ($existed -and -not $wasPrivate -and @(Get-ChildItem -LiteralPath $Dir -Force).Count -gt 0) { throw "$Dir changed while it was being made private: nothing was installed." }
}

if (-not $InstallDir) {
    # an installed copy in the older per-user folder is updated in place; otherwise C:\SimWB, unless that is not safe
    $perUser = Join-Path $env:LOCALAPPDATA 'Programs\SimWB'
    $short = Join-Path $(if ($env:SystemDrive) { $env:SystemDrive } else { 'C:' }) 'SimWB'
    if ((Test-Path (Join-Path $perUser 'install.log')) -or (Test-Path (Join-Path $perUser 'simwb\__init__.py'))) { $InstallDir = $perUser }
    else { $InstallDir = $short }
}
$InstallDir = Resolve-InstallDir ([IO.Path]::GetFullPath($InstallDir))   # (adjusted, with a note, when it is not safe)
$Log     = Join-Path $InstallDir 'install.log'
$T0      = Get-Date
$TotalSteps = 7
$script:Step = 0

# ------------------------------------------------------------------------------------------------ look and feel
function C($text, $color) { Write-Host $text -ForegroundColor $color -NoNewline }
function Line($text = '', $color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Logf($m) { if (Test-Path (Split-Path $Log)) { Add-Content -Path $Log -Value ("{0:HH:mm:ss} {1}" -f (Get-Date), $m) } }
function Banner {
    try { Clear-Host } catch { }
    Line ''
    Line '   ╔══════════════════════════════════════════════════════════╗' Cyan
    Line '   ║                                                          ║' Cyan
    C    '   ║   ' Cyan; C '⚙  Mechanical Simulation Workbench' White; Line '                     ║' Cyan
    C    '   ║   ' Cyan; C 'CAD in, answers out: stress, vibration, heat, flow' DarkGray; Line '    ║' Cyan
    Line '   ║                                                          ║' Cyan
    Line '   ╚══════════════════════════════════════════════════════════╝' Cyan
    Line ''
}
function StepStart($title, $detail) {
    $script:Step++
    Line ''
    C ("  [{0}/{1}] " -f $script:Step, $TotalSteps) Cyan; C $title White
    if ($detail) { C ("  " + $detail) DarkGray }
    Line ''
    Logf "STEP $($script:Step): $title"
}
function Ok($msg)   { C '        ✓ ' Green; Line $msg }
function Skip($msg) { C '        ↷ ' DarkYellow; Line $msg DarkGray }
function Warn($msg) { C '        ! ' Yellow; Line $msg Yellow }
function Bar($pct, $text) {
    $w = 30; $f = [Math]::Max(0, [Math]::Min($w, [int]($pct / 100 * $w)))
    $bar = ('█' * $f) + ('░' * ($w - $f))
    $t = if ($text.Length -gt 46) { $text.Substring(0, 45) + '…' } else { $text }
    Write-Host ("`r        {0} {1,3}%  {2}".PadRight(100) -f $bar, [int]$pct, $t) -NoNewline -ForegroundColor Cyan
}
function BarDone { Write-Host ("`r" + (' ' * 100) + "`r") -NoNewline }
function Fail($what, $hint) {
    Line ''
    C '  ✗ ' Red; Line $what Red
    if ($hint) { Line ("    " + $hint) Gray }
    Line ("    Details are in " + $Log) DarkGray
    Logf "FAILED: $what"
    if (-not $Quiet) { Line ''; Read-Host '  Press Enter to close' | Out-Null }
    exit 1
}

# ------------------------------------------------------------------------------------------------ helpers
function Download($url, $dest, $sha256, $label) {
    $req = [Net.HttpWebRequest]::Create($url); $req.UserAgent = 'SimWB-Installer'; $req.AllowAutoRedirect = $true
    $resp = $req.GetResponse(); $total = $resp.ContentLength
    $in = $resp.GetResponseStream(); $out = [IO.File]::Create($dest)
    $buf = New-Object byte[] 262144; $done = 0; $sw = [Diagnostics.Stopwatch]::StartNew(); $last = 0
    try {
        while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
            $out.Write($buf, 0, $n); $done += $n
            if ($sw.ElapsedMilliseconds - $last -gt 150) {
                $last = $sw.ElapsedMilliseconds
                $speed = $done / 1MB / [Math]::Max(0.1, $sw.Elapsed.TotalSeconds)
                $pct = if ($total -gt 0) { 100.0 * $done / $total } else { 0 }
                Bar $pct ("{0}  {1:N0}/{2:N0} MB  {3:N1} MB/s" -f $label, ($done / 1MB), ($total / 1MB), $speed)
            }
        }
    } finally { $out.Close(); $in.Close(); $resp.Close() }
    BarDone
    if ($sha256) {
        $h = (Get-FileHash -Algorithm SHA256 -Path $dest).Hash.ToLower()
        if ($h -ne $sha256.ToLower()) { Remove-Item $dest -Force; throw "The download of $label is damaged (checksum differs). Please run the setup again." }
    }
}

# Runs a program, shows its last line (or a progress bar for lines like "[ 35 %] message") and logs everything.
function RunLogged($exe, [string[]]$argv, $label, $env = @{}) {
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = 'cmd.exe'
    $q = { param($s) '"' + $s + '"' }
    $psi.Arguments = '/d /c "' + ((@($exe) + $argv | ForEach-Object { & $q $_ }) -join ' ') + ' 2>&1"'
    $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; $psi.CreateNoWindow = $true
    $psi.WorkingDirectory = $InstallDir
    foreach ($k in $env.Keys) { $psi.EnvironmentVariables[$k] = $env[$k] }
    $p = [Diagnostics.Process]::Start($psi); $sw = [Diagnostics.Stopwatch]::StartNew(); $pct = 0; $spin = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'; $i = 0
    while (($line = $p.StandardOutput.ReadLine()) -ne $null) {
        Logf $line
        $t = $line.Trim()
        if ($t -match '^\[\s*(\d+)\s*%\]\s*(.*)$') { $pct = [int]$Matches[1]; $t = $Matches[2] }
        if ($t.Length -gt 0) {
            $i++
            Bar $pct ("{0} {1}  {2:mm\:ss}" -f $spin[$i % $spin.Length], $t, $sw.Elapsed)
        }
    }
    $p.WaitForExit(); BarDone
    return $p.ExitCode
}

# ------------------------------------------------------------------------------------------------ the install
Banner
Line ("  Installing into  " + $InstallDir) White
if ($DryRun) { Line '  (dry run: nothing is downloaded or changed)' Yellow }
Line '  About 3 GB of downloads and 8 GB of disk; 15-30 minutes on a typical connection.' DarkGray
Line '  No admin rights needed. You can keep using the computer.' DarkGray

# 1 ---------------------------------------------------------------------------------------- the computer
StepStart 'Checking your computer' ''
if (-not [Environment]::Is64BitOperatingSystem) { Fail 'This needs 64-bit Windows 10 or 11.' '' }
$os = [Environment]::OSVersion.Version
if ($os.Major -lt 10) { Fail 'This needs Windows 10 or 11.' '' }
Ok ("Windows {0}.{1} (build {2}), 64-bit" -f $os.Major, $os.Minor, $os.Build)
$drive = (Get-Item ([IO.Path]::GetPathRoot($InstallDir))).FullName
$free = (Get-PSDrive -Name $drive.Substring(0, 1)).Free / 1GB
if ($free -lt $MIN_FREE_GB) { Fail ("Only {0:N1} GB free on {1}; {2} GB are needed." -f $free, $drive, $MIN_FREE_GB) 'Free some space or pass -InstallDir to use another drive.' }
Ok ("{0:N0} GB free on {1}" -f $free, $drive)
try {
    $r = Invoke-WebRequest -Uri 'https://github.com' -Method Head -UseBasicParsing -TimeoutSec 15
    Ok 'Internet connection works'
} catch { Fail 'Cannot reach the internet (github.com).' 'The installer downloads Python and the solvers. Check the connection or any proxy and run it again.' }
$ramGB = [Math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
if ($ramGB -lt 8) { Warn "$ramGB GB of memory: fine for small studies, flow simulations of bigger meshes want 16 GB or more." } else { Ok "$ramGB GB memory, $([Environment]::ProcessorCount) logical cores" }
if ($DryRun) { Line ''; C '  Dry run finished: this computer is ready for the setup.' Green; Line ''; exit 0 }

try { if (-not ($env:SIMWB_DIR_PREPARED -and (Test-Path -LiteralPath $InstallDir))) { New-LockedDirectory $InstallDir } } catch { Line ''; C '  ✗ ' Red; Line "$_" Red; Line '    Choose another folder with -InstallDir.' Gray; if (-not $Quiet) { Read-Host '  Press Enter to close' | Out-Null }; exit 1 }
Add-Content -Path $Log -Value ("=== SimWB setup {0} ===" -f (Get-Date))

# 2 ---------------------------------------------------------------------------------------- the workbench files
StepStart 'Putting the workbench in place' ''
$same = ($Source.TrimEnd('\') -ieq $InstallDir.TrimEnd('\'))
if ($same) { Skip 'Already running from the install folder' }
else {
    # only the TOP-level data folders are skipped (a bare name would also skip the source packages simwb\solvers\openfoam and calculix)
    $skip = @('.git', '.venv', 'projects', 'openfoam', 'mpi', 'calculix', 'tools', 'dist', '.pytest_cache') | ForEach-Object { Join-Path $Source $_ }
    $null = robocopy $Source $InstallDir /E /NFL /NDL /NJH /NJS /NP /XD @($skip + '__pycache__') /XF install.log
    if ($LASTEXITCODE -ge 8) { Fail 'Could not copy the workbench files.' 'Is the target folder writable?' }
    Ok 'Workbench files copied'
}
$py = Join-Path $InstallDir '.venv\Scripts\python.exe'

# 3 ---------------------------------------------------------------------------------------- uv
StepStart 'Getting the Python installer (uv)' 'one small program, checked against a known checksum'
$tools = Join-Path $InstallDir 'tools'; $uvDir = Join-Path $tools 'uv'; $uv = Join-Path $uvDir 'uv.exe'
New-Item -ItemType Directory -Force -Path $uvDir | Out-Null
if (Test-Path $uv) { Skip 'Already there' }
else {
    $zip = Join-Path $tools 'uv.zip'
    Download $UV_URL $zip $UV_SHA256 'uv'
    Expand-Archive -Path $zip -DestinationPath $uvDir -Force; Remove-Item $zip -Force
    if (-not (Test-Path $uv)) { $found = Get-ChildItem $uvDir -Recurse -Filter uv.exe | Select-Object -First 1; if ($found) { Move-Item $found.FullName $uv -Force } }
    Ok 'uv ready'
}
$uvEnv = @{ UV_PYTHON_INSTALL_DIR = (Join-Path $tools 'python'); UV_CACHE_DIR = (Join-Path $tools 'uv-cache'); UV_PYTHON_PREFERENCE = 'only-managed' }

# 4 ---------------------------------------------------------------------------------------- python + packages
StepStart 'Python and the simulation libraries' 'gmsh, NumPy, SciPy, FastAPI, Qt, VTK ... (about 1.1 GB)'
if (-not (Test-Path $py)) {
    $code = RunLogged $uv @('venv', '--python', $PYTHON, (Join-Path $InstallDir '.venv')) 'python' $uvEnv
    if ($code -ne 0) { Fail 'Could not set up Python.' 'See the log; a proxy or antivirus may be blocking the download.' }
    Ok "Python $PYTHON ready (a private copy, your own Python is untouched)"
} else { Skip 'Python environment already there' }
$reqs = @('-r', (Join-Path $InstallDir 'requirements.txt'))
if (Test-Path (Join-Path $InstallDir 'requirements-qt.txt')) { $reqs += @('-r', (Join-Path $InstallDir 'requirements-qt.txt')) }   # (the desktop application's libraries, ON TOP of the others)
$code = RunLogged $uv (@('pip', 'install', '--python', $py) + $reqs) 'packages' $uvEnv
if ($code -ne 0) { Fail 'Could not install the Python libraries.' 'Run the setup again: it continues where it stopped.' }
Ok 'Libraries installed'

# 5 ---------------------------------------------------------------------------------------- openfoam + mpi
StepStart 'OpenFOAM and Microsoft MPI' 'flow simulation: drones, propellers, aircraft, electronics cooling (about 250 MB download)'
if ($SkipSolvers) { Skip 'Skipped (-SkipSolvers): the workbench offers it again when a flow study needs it' }
else {
    $code = RunLogged $py @('-u', '-m', 'simwb.solvers.openfoam.installer') 'openfoam'
    if ($code -ne 0) { Warn 'OpenFOAM could not be set up now; the workbench will retry the first time you run a flow study.' }
    else { Ok 'OpenFOAM v2606 and MPI ready' }
}

# 6 ---------------------------------------------------------------------------------------- calculix
StepStart 'CalculiX' 'a second structural solver to cross-check the built-in one (about 150 MB download)'
if ($SkipSolvers) { Skip 'Skipped (-SkipSolvers)' }
else {
    $code = RunLogged $py @('-u', '-m', 'simwb.solvers.calculix.installer') 'calculix'
    if ($code -ne 0) { Warn 'CalculiX could not be set up now; the Solve step offers it later.' }
    else { Ok 'CalculiX ready' }
}

# 7 ---------------------------------------------------------------------------------------- shortcuts + self-test
StepStart 'Icons and a quick self-test' ''
$icon = Join-Path $InstallDir 'installer\simwb.ico'
try {
    Add-Type -AssemblyName System.Drawing
    $bmp = New-Object Drawing.Bitmap 256, 256; $g = [Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'; $g.TextRenderingHint = 'AntiAliasGridFit'
    $rect = New-Object Drawing.Rectangle 0, 0, 256, 256
    $br = New-Object Drawing.Drawing2D.LinearGradientBrush $rect, ([Drawing.Color]::FromArgb(255, 20, 116, 214)), ([Drawing.Color]::FromArgb(255, 6, 214, 160)), 45
    $path = New-Object Drawing.Drawing2D.GraphicsPath; $r = 52
    $path.AddArc(0, 0, $r, $r, 180, 90); $path.AddArc(256 - $r, 0, $r, $r, 270, 90); $path.AddArc(256 - $r, 256 - $r, $r, $r, 0, 90); $path.AddArc(0, 256 - $r, $r, $r, 90, 90); $path.CloseFigure()
    $g.FillPath($br, $path)
    $font = New-Object Drawing.Font 'Segoe UI', 120, ([Drawing.FontStyle]::Bold), ([Drawing.GraphicsUnit]::Pixel)
    $sf = New-Object Drawing.StringFormat; $sf.Alignment = 'Center'; $sf.LineAlignment = 'Center'
    $g.DrawString('S', $font, [Drawing.Brushes]::White, (New-Object Drawing.RectangleF 0, 8, 256, 256), $sf)
    $ms = New-Object IO.MemoryStream; $bmp.Save($ms, [Drawing.Imaging.ImageFormat]::Png); $png = $ms.ToArray()
    $fs = [IO.File]::Create($icon); $bw = New-Object IO.BinaryWriter $fs
    $bw.Write([uint16]0); $bw.Write([uint16]1); $bw.Write([uint16]1)                    # ICO header, one image
    $bw.Write([byte]0); $bw.Write([byte]0); $bw.Write([byte]0); $bw.Write([byte]0)       # 256 x 256, no palette
    $bw.Write([uint16]1); $bw.Write([uint16]32); $bw.Write([uint32]$png.Length); $bw.Write([uint32]22)
    $bw.Write($png); $bw.Close(); $fs.Close(); $g.Dispose(); $bmp.Dispose()
} catch { $icon = $null; Logf "icon: $_" }
$launcher = Join-Path $InstallDir 'installer\Launch-SimWB.ps1'
$launcherDesktop = Join-Path $InstallDir 'installer\Launch-SimWB-Desktop.ps1'
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$wsh = New-Object -ComObject WScript.Shell
$links = @(
    @{ Path = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mechanical Simulation Workbench.lnk'); Args = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""; Desc = 'Start the workbench' },
    @{ Path = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mechanical Simulation Workbench (beta desktop).lnk'); Args = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcherDesktop`""; Desc = 'Start the desktop application (beta)' },
    @{ Path = (Join-Path ([Environment]::GetFolderPath('Programs')) 'Mechanical Simulation Workbench.lnk'); Args = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""; Desc = 'Start the workbench' },
    @{ Path = (Join-Path ([Environment]::GetFolderPath('Programs')) 'Mechanical Simulation Workbench (beta desktop).lnk'); Args = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcherDesktop`""; Desc = 'Start the desktop application (beta)' },
    @{ Path = (Join-Path ([Environment]::GetFolderPath('Programs')) 'Uninstall Mechanical Simulation Workbench.lnk'); Args = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $InstallDir 'installer\Uninstall-SimWB.ps1')`""; Desc = 'Remove the workbench and its solvers' }
)
if ($NoDesktopShortcut) { $links = @($links | Where-Object { $_.Path -notlike (Join-Path ([Environment]::GetFolderPath('Desktop')) '*') }) }
foreach ($l in $(if ($NoShortcuts) { @() } else { $links })) {
    $s = $wsh.CreateShortcut($l.Path); $s.TargetPath = $ps; $s.Arguments = $l.Args; $s.WorkingDirectory = $InstallDir; $s.Description = $l.Desc
    if ($icon -and (Test-Path $icon)) { $s.IconLocation = $icon }
    $s.Save()
}
if ($NoShortcuts) { Skip 'No icons (-NoShortcuts)' }
elseif ($NoDesktopShortcut) { Ok 'Start menu icons made (no desktop icon, as chosen)' }
else { Ok 'Desktop and Start menu icons made' }
Push-Location $InstallDir   # (the packages must be found in the install folder, not in the folder the setup was started from)
$env:PYTHONPATH = ''
$test = & $py -c "import gmsh, numpy, scipy, fastapi, PySide6.QtWidgets, vtk, simwb.server, simwb.qt.app; from simwb.solvers.openfoam import runner; from simwb.solvers.calculix import installer as c; print('openfoam', runner.detect(True)['available'], '| calculix', c.detect(True)['available'])" 2>&1
$testExit = $LASTEXITCODE; Pop-Location
Logf ($test -join ' ')
if ($testExit -eq 0) { Ok ("Self-test passed (" + ($test | Select-Object -Last 1) + ")") } else { Warn 'The self-test reported a problem; see install.log. The workbench may still start.' }

# ------------------------------------------------------------------------------------------------ done
$mins = [Math]::Round(((Get-Date) - $T0).TotalMinutes, 1)
Line ''
Line '   ╔══════════════════════════════════════════════════════════╗' Green
C    '   ║  ' Green; C ("✓  All set in $mins minutes".PadRight(56)) White; Line '║' Green
C    '   ║  ' Green; C 'Start it from a desktop icon (the beta desktop is new).'.PadRight(56) Gray; Line '║' Green
C    '   ║  ' Green; C 'Your studies are saved where you choose.'.PadRight(56) Gray; Line '║' Green
Line '   ╚══════════════════════════════════════════════════════════╝' Green
Line ''
Logf "DONE in $mins min"
if (-not $NoLaunch) {
    if (-not $Quiet) { Read-Host '  Press Enter to open the workbench now' | Out-Null }
    Start-Process -FilePath $ps -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $launcher)
}
