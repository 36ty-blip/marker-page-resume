@echo off
rem Compatibility shortcut. The improved path is now the normal Marker 1.10 path.
call "%~dp0run_marker1.cmd" %*
exit /b %ERRORLEVEL%
