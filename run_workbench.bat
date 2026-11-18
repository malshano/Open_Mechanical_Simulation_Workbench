@echo off
rem Mechanical Simulation Workbench - one-click start (creates the Python environment on first run)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo First start: creating Python environment and installing dependencies...
    python -m venv .venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\pip install -r requirements.txt || (pause & exit /b 1)
    rem built-in OpenFOAM for the flow studies (one time; retried automatically when the server starts)
    .venv\Scripts\python -m simwb.solvers.openfoam.installer
)
.venv\Scripts\python -m simwb.server
pause
