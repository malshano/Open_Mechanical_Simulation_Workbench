<#
  Starts the Mechanical Simulation Workbench as a desktop application (the Qt window; beta) without a console window.
  The web version keeps its own icon (Launch-SimWB.ps1).
#>
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $py)) {
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show("The workbench is not installed completely (no Python environment in $Root). Please run the setup again.", 'Mechanical Simulation Workbench') | Out-Null
    exit 1
}
$check = & (Join-Path $Root '.venv\Scripts\python.exe') -c "import PySide6, vtk" 2>&1
if ($LASTEXITCODE -ne 0) {
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show("The desktop application's libraries (Qt and VTK) are missing. Run the setup again, or use the web icon.", 'Mechanical Simulation Workbench') | Out-Null
    exit 1
}
Start-Process -FilePath $py -ArgumentList @('-m', 'simwb.qt') -WorkingDirectory $Root -WindowStyle Normal   # (not the hidden style of this script)
