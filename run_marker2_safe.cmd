@echo off
setlocal

if "%~1"=="" (
    echo Drag a PDF or input folder onto this file.
    echo.
    echo Optional command line:
    echo   run_marker2_safe.cmd "input PDF or folder" "output folder"
    pause
    exit /b 2
)

set "INPUT_PATH=%~f1"
if "%~2"=="" (
    set "OUTPUT_PATH=%~dp1marker_output"
) else (
    set "OUTPUT_PATH=%~f2"
)

echo Marker 2 balanced-quality conversion
echo Input:  %INPUT_PATH%
echo Output: %OUTPUT_PATH%
echo.
echo For maximum stability, close Codex and other memory-heavy applications.
echo Completed pages will be skipped if this command is run again.
echo.

"%~dp0.venv\Scripts\python.exe" "%~dp0marker_process.py" "%INPUT_PATH%" --engine marker2 --output "%OUTPUT_PATH%"
set "MARKER_EXIT=%ERRORLEVEL%"

echo.
if not "%MARKER_EXIT%"=="0" (
    exit /b %MARKER_EXIT%
)

echo SUCCESS: Every page was converted and the final Markdown was written.
exit /b 0
