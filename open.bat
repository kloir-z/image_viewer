@echo off
cd /d "%~dp0"

if exist .venv\Scripts\pythonw.exe (
    start "" ".venv\Scripts\pythonw.exe" image_viewer.py %*
    goto :eof
)
if exist venv\Scripts\pythonw.exe (
    start "" "venv\Scripts\pythonw.exe" image_viewer.py %*
    goto :eof
)
start "" pythonw.exe image_viewer.py %*
