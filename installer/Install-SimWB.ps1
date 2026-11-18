<#
  Mechanical Simulation Workbench - one-click installer (Windows 10/11, 64-bit, no admin rights).

  Installs everything the workbench needs into one folder and makes a desktop icon:
    1. a private Python 3.12 (uv, pinned and checksummed)    2. the Python packages (gmsh, SciPy, FastAPI, ...)
    3. OpenFOAM v2606 (flow simulation) + Microsoft MPI      4. CalculiX (structural cross-check solver)
    5. desktop + Start menu shortcuts, a self-test, and the first start.
  Nothing is installed system-wide. The Uninstall shortcut (or Uninstall-SimWB.ps1) removes it all.

  Options:  -InstallDir <folder>   where to install (default %LOCALAPPDATA%\Programs\SimWB)
            -NoLaunch              do not start the workbench at the end
            -SkipSolvers           skip OpenFOAM and CalculiX (the workbench offers them again when it needs them)
            -DryRun                check the computer and show what would be done; download and change nothing
            -Quiet                 never wait for a key press
            -NoShortcuts           do not make desktop / Start menu icons (for tests)
#>
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\SimWB'),
    [switch]$NoLaunch, [switch]$SkipSolvers, [switch]$DryRun, [switch]$Quiet, [switch]$NoShortcuts
)
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
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
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

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
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
StepStart 'Python and the simulation libraries' 'gmsh, NumPy, SciPy, FastAPI, ... (about 700 MB)'
if (-not (Test-Path $py)) {
    $code = RunLogged $uv @('venv', '--python', $PYTHON, (Join-Path $InstallDir '.venv')) 'python' $uvEnv
    if ($code -ne 0) { Fail 'Could not set up Python.' 'See the log; a proxy or antivirus may be blocking the download.' }
    Ok "Python $PYTHON ready (a private copy, your own Python is untouched)"
} else { Skip 'Python environment already there' }
$code = RunLogged $uv @('pip', 'install', '--python', $py, '-r', (Join-Path $InstallDir 'requirements.txt')) 'packages' $uvEnv
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
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$wsh = New-Object -ComObject WScript.Shell
$links = @(
    @{ Path = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mechanical Simulation Workbench.lnk'); Args = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""; Desc = 'Start the workbench' },
    @{ Path = (Join-Path ([Environment]::GetFolderPath('Programs')) 'Mechanical Simulation Workbench.lnk'); Args = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""; Desc = 'Start the workbench' },
    @{ Path = (Join-Path ([Environment]::GetFolderPath('Programs')) 'Uninstall Mechanical Simulation Workbench.lnk'); Args = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $InstallDir 'installer\Uninstall-SimWB.ps1')`""; Desc = 'Remove the workbench and its solvers' }
)
foreach ($l in $(if ($NoShortcuts) { @() } else { $links })) {
    $s = $wsh.CreateShortcut($l.Path); $s.TargetPath = $ps; $s.Arguments = $l.Args; $s.WorkingDirectory = $InstallDir; $s.Description = $l.Desc
    if ($icon -and (Test-Path $icon)) { $s.IconLocation = $icon }
    $s.Save()
}
if ($NoShortcuts) { Skip 'No icons (-NoShortcuts)' } else { Ok 'Desktop and Start menu icons made' }
Push-Location $InstallDir   # (the packages must be found in the install folder, not in the folder the setup was started from)
$env:PYTHONPATH = ''
$test = & $py -c "import gmsh, numpy, scipy, fastapi, simwb.server; from simwb.solvers.openfoam import runner; from simwb.solvers.calculix import installer as c; print('openfoam', runner.detect(True)['available'], '| calculix', c.detect(True)['available'])" 2>&1
$testExit = $LASTEXITCODE; Pop-Location
Logf ($test -join ' ')
if ($testExit -eq 0) { Ok ("Self-test passed (" + ($test | Select-Object -Last 1) + ")") } else { Warn 'The self-test reported a problem; see install.log. The workbench may still start.' }

# ------------------------------------------------------------------------------------------------ done
$mins = [Math]::Round(((Get-Date) - $T0).TotalMinutes, 1)
Line ''
Line '   ╔══════════════════════════════════════════════════════════╗' Green
C    '   ║  ' Green; C ("✓  All set in $mins minutes".PadRight(56)) White; Line '║' Green
C    '   ║  ' Green; C 'Start it from the desktop icon.'.PadRight(56) Gray; Line '║' Green
C    '   ║  ' Green; C 'Your studies are saved where you choose.'.PadRight(56) Gray; Line '║' Green
Line '   ╚══════════════════════════════════════════════════════════╝' Green
Line ''
Logf "DONE in $mins min"
if (-not $NoLaunch) {
    if (-not $Quiet) { Read-Host '  Press Enter to open the workbench now' | Out-Null }
    Start-Process -FilePath $ps -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $launcher)
}
