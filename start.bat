@echo off
cd %~dp0

REM Check conditions and act accordingly
if exist venv\Scripts\activate.bat (
    if exist requirements.txt (
        echo Activating virtual environment and running the Python script...
        call venv\Scripts\activate
    ) else (
        echo Activating virtual environment and running the Python script...
        echo         call venv\Scripts\activate
    )
) else (
    if exist requirements.txt (
        echo ERROR: requirements.txt found but no virtual environment. 
        echo Please create a virtual environment using:
        echo     python -m venv venv
        echo Then, activate it and install the requirements:
        echo     venv\Scripts\activate
        echo     pip install -r requirements.txt
        goto end
    ) else (
        echo No virtual environment or requirements.txt found, running the script directly...
    )
)

for %%f in (*.py) do (
    python "%%f"
    goto :end
)

:end
pause
