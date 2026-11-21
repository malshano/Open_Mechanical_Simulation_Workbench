<#
  Removes the Mechanical Simulation Workbench: its folder (Python, libraries, OpenFOAM, CalculiX) and the shortcuts.
  Your studies are in the folders you chose when you made them: they are NOT touched.
#>
param([switch]$Quiet)
$Root = Split-Path -Parent $PSScriptRoot
# only an installed copy: never a git checkout (this would delete the whole repository)
if ((Test-Path (Join-Path $Root '.git')) -or -not ((Test-Path (Join-Path $Root 'install.log')) -or (Test-Path (Join-Path $Root 'tools\uv')))) {
    Write-Host '  This folder is not an installed copy of the workbench (no install.log, or it is a git checkout): nothing removed.' -ForegroundColor Yellow
    exit 1
}
Write-Host ''
Write-Host '  Mechanical Simulation Workbench - uninstall' -ForegroundColor Cyan
Write-Host "  This removes $Root (the program, Python, OpenFOAM and CalculiX)." -ForegroundColor Gray
Write-Host '  Your studies are saved in the folders you chose and stay where they are.' -ForegroundColor Gray
if (-not $Quiet) { $a = Read-Host '  Type YES to continue'; if ($a -ne 'YES') { Write-Host '  Nothing removed.'; exit 0 } }
# stop a running workbench first
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.ExecutablePath -like "$Root*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
foreach ($p in @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mechanical Simulation Workbench.lnk'),
                 (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mechanical Simulation Workbench (beta desktop).lnk'),
                 (Join-Path ([Environment]::GetFolderPath('Programs')) 'Mechanical Simulation Workbench (beta desktop).lnk'),
                 (Join-Path ([Environment]::GetFolderPath('Programs')) 'Mechanical Simulation Workbench.lnk'),
                 (Join-Path ([Environment]::GetFolderPath('Programs')) 'Uninstall Mechanical Simulation Workbench.lnk'))) {
    Remove-Item $p -Force -ErrorAction SilentlyContinue
}
# this script lives inside the folder: remove the rest from a copy that runs from outside
$tmp = Join-Path $env:TEMP 'simwb_uninstall.cmd'
Set-Content -Path $tmp -Value "@echo off`r`ntimeout /t 2 /nobreak >nul`r`nrmdir /s /q `"$Root`"`r`necho Removed.`r`ntimeout /t 3 >nul`r`ndel `"%~f0`"" -Encoding ASCII
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $tmp -WindowStyle Hidden
Write-Host '  Done: the folder is removed in a moment.' -ForegroundColor Green
