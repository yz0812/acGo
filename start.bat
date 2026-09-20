@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1

set "VENV_DIR=venv"
if not exist "%VENV_DIR%\Scripts\activate.bat" set "VENV_DIR=.venv"
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [ERROR] No virtual environment found in venv or .venv.
    echo Run these commands in the project directory first:
    echo   python -m venv venv
    echo   venv\Scripts\python.exe -m pip install -r requirements.txt
    goto :failure
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 goto :failure

echo Starting ACGO at http://localhost:5000
echo Press Ctrl+C to stop the server.
"%VENV_DIR%\Scripts\python.exe" run.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] Server exited with code %EXIT_CODE%.
    pause
)
popd
exit /b %EXIT_CODE%

:failure
pause
popd
exit /b 1
