<#
  Starts the Mechanical Simulation Workbench (hidden) and opens it in the browser.
  If it is already running only the browser is opened.
#>
$Root = Split-Path -Parent $PSScriptRoot
$port = if ($env:SIMWB_PORT) { [int]$env:SIMWB_PORT } else { 8765 }
$url = "http://127.0.0.1:$port/"
$py = Join-Path $Root '.venv\Scripts\python.exe'

function Up {
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $port); $c.Close(); return $true } catch { return $false }
}
if (Up) { Start-Process $url; exit 0 }
if (-not (Test-Path $py)) {
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show("The workbench is not installed completely (no Python environment in $Root). Please run the setup again.", 'Mechanical Simulation Workbench') | Out-Null
    exit 1
}
$env:SIMWB_PORT = "$port"
Start-Process -FilePath $py -ArgumentList @('-m', 'simwb.server') -WorkingDirectory $Root -WindowStyle Hidden   # opens the browser itself
