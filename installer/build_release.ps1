<#
  Builds the release files in dist\ from what git would commit (tracked + untracked-but-not-ignored files):
    SimWB-<version>-windows.zip   the whole workbench; unzip and double-click "Install SimWB.bat"
    SimWB-Setup.exe              one file: double-click, it unpacks and runs the installer (compiled with csc.exe)
    SHA256SUMS.txt               checksums of both
  Usage:  powershell -ExecutionPolicy Bypass -File installer\build_release.ps1
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$version = (Select-String -Path (Join-Path $Root 'simwb\__init__.py') -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$dist = Join-Path $Root 'dist'
if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Path $dist | Out-Null
$stage = Join-Path $env:TEMP ("simwb_stage_" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null

# 1. the files: exactly what git would commit (.gitignore applied), nothing else
$internal = @('HANDOFF.md', 'SECURITY.md', 'CONTRIBUTING.md')   # notes for the developers, not for the users
$files = git ls-files -co --exclude-standard | Where-Object { $_ -and ($internal -notcontains $_) -and -not $_.StartsWith('.github/') -and (Test-Path -LiteralPath $_ -PathType Leaf) }
$app = Join-Path $stage 'app'
foreach ($f in $files) {
    $dest = Join-Path $app $f
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    Copy-Item -LiteralPath $f -Destination $dest
}
Write-Host ("{0} files, {1:N1} MB" -f $files.Count, ((Get-ChildItem $app -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))

# 2. the zip (forward-slash entries so it also unpacks on other systems)
$zip = Join-Path $dist "SimWB-v$version-windows.zip"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [IO.Compression.ZipFile]::Open($zip, 'Create')
try {
    Get-ChildItem $app -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($app.Length + 1).Replace('\', '/')
        [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($z, $_.FullName, $rel, 'Optimal')
    }
} finally { $z.Dispose() }

# 3. the one-file setup: a small exe (compiled with the C# compiler that ships with Windows) that carries app.zip,
#    asks where to install (default C:\SimWB), unpacks it there and runs the installer
$exe = Join-Path $dist 'SimWB-Setup.exe'
$csc = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { throw 'The C# compiler (csc.exe of .NET Framework 4) was not found.' }
$cscArgs = @('/nologo', '/target:exe', "/out:$exe", "/resource:$zip,app.zip", '/reference:System.IO.Compression.dll', '/reference:System.IO.Compression.FileSystem.dll', '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll', (Join-Path $PSScriptRoot 'Setup.cs'))
$ico = Join-Path $PSScriptRoot 'simwb.ico'
if (Test-Path $ico) { $cscArgs += "/win32icon:$ico" }
& $csc @cscArgs
if (-not (Test-Path $exe)) { throw 'The setup exe could not be compiled.' }

# 4. checksums
Get-ChildItem $dist -File | ForEach-Object { "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name } | Set-Content (Join-Path $dist 'SHA256SUMS.txt') -Encoding ASCII
Remove-Item $stage -Recurse -Force
Get-ChildItem $dist -File | ForEach-Object { Write-Host ("  {0}  {1:N1} MB" -f $_.Name, ($_.Length / 1MB)) }
