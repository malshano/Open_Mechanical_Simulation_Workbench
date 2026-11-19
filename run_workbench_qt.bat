@echo off
rem Mechanical Simulation Workbench - the desktop (Qt) application. Needs the environment of run_workbench.bat first.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run run_workbench.bat once first: it creates the Python environment.
    pause & exit /b 1
)
.venv\Scripts\python -c "import PySide6, vtk" 2>nul || (
    echo Installing the desktop application's libraries (Qt and VTK, about 400 MB, one time)...
    .venv\Scripts\pip install -r requirements-qt.txt || (pause & exit /b 1)
)
.venv\Scripts\python -m simwb.qt %*
