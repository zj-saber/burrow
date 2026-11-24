@echo off
rem Starts Burrow. Creates the virtual environment on first run.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Setting up Burrow for the first time...
    py -3 -m venv .venv || python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0Burrow.pyw" %*
