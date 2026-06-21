@echo off
cd %~dp0

REM Lite 版 (JSON/seed/並べ替え機能なし)。venv はルート (..) を共有する
if exist ..\venv\Scripts\activate.bat (
    echo Activating virtual environment [..\venv] and running the Python script...
    call ..\venv\Scripts\activate
    goto run
)
if exist ..\.venv\Scripts\activate.bat (
    echo Activating virtual environment [..\.venv] and running the Python script...
    call ..\.venv\Scripts\activate
    goto run
)
echo No virtual environment found in parent folder, running the script directly...

:run
python image_viewer.py

:end
pause
