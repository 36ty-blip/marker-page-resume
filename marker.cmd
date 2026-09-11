@echo off
"%~dp0.venv\Scripts\python.exe" "%~dp0marker_process.py" %*
exit /b %ERRORLEVEL%
