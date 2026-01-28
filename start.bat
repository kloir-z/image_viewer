@echo off
cd %~dp0

REM Check conditions and act accordingly (supports both venv and .venv)
if exist venv\Scripts\activate.bat (
    echo Activating virtual environment [venv] and running the Python script...
    call venv\Scripts\activate
    goto run
)
if exist .venv\Scripts\activate.bat (
    echo Activating virtual environment [.venv] and running the Python script...
    call .venv\Scripts\activate
    goto run
)
if exist requirements.txt (
    echo ERROR: requirements.txt found but no virtual environment.
    echo Please create a virtual environment using:
    echo     python -m venv venv
    echo Then, activate it and install the requirements:
    echo     venv\Scripts\activate
    echo     pip install -r requirements.txt
    goto end
)
echo No virtual environment or requirements.txt found, running the script directly...

:run

for %%f in (*.py) do (
    python "%%f"
    goto :end
)

:end
pause
