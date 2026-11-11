@echo off
rem abq - Abaqus toolbox entry point. Usage: E:\DSH-Abaqus\abq.cmd <command> [args]   (abq.cmd -h for the list)
setlocal
set "PYTHONUTF8=1"
py "%~dp0tools\abq\abq.py" %*
exit /b %errorlevel%
