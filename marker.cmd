@echo off
setlocal

set "MARKER_SCRIPT=%~dp0marker_process.py"
set "LOCAL_PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%MARKER_SCRIPT%" (
    echo ERROR: Marker Controller is incomplete.
    echo.
    echo The controller script was not found:
    echo   %MARKER_SCRIPT%
    echo.
    echo Restore marker_process.py or download the repository again:
    echo   https://github.com/36ty-blip/marker-page-resume
    exit /b 2
)

if defined MARKER_CONTROLLER_PYTHON (
    set "PYTHON_EXE=%MARKER_CONTROLLER_PYTHON%"
) else (
    set "PYTHON_EXE=%LOCAL_PYTHON%"
)

if not exist "%PYTHON_EXE%" (
    echo ERROR: Marker Controller could not find its Python environment.
    echo.
    if defined MARKER_CONTROLLER_PYTHON (
        echo MARKER_CONTROLLER_PYTHON points to a missing file:
        echo   %PYTHON_EXE%
        echo.
        echo Correct that variable, unset it, or create the local environment below.
    ) else (
        echo Expected:
        echo   %LOCAL_PYTHON%
    )
    echo.
    where py >nul 2>nul
    if not errorlevel 1 (
        echo Create the local environment with:
        echo   cd /d "%~dp0"
        echo   py -3.12 -m venv .venv
        echo   ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    ) else (
        where python >nul 2>nul
        if not errorlevel 1 (
            echo Create the local environment with:
            echo   cd /d "%~dp0"
            echo   python -m venv .venv
            echo   ".venv\Scripts\python.exe" -m pip install -r requirements.txt
        ) else (
            echo Python was not found. Install Python 3.12, then create the environment.
            echo   https://www.python.org/downloads/windows/
        )
    )
    echo.
    echo After installation, configure and verify the runtime:
    echo   marker.cmd --setup
    echo   marker.cmd --doctor --engine marker2
    exit /b 2
)

"%PYTHON_EXE%" "%MARKER_SCRIPT%" %*
set "MARKER_EXIT=%ERRORLEVEL%"
exit /b %MARKER_EXIT%
