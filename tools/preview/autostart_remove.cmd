@echo off
rem Removes the logon shortcut created by autostart_install.cmd (does not stop a panel that is running).
setlocal
if not "%~1"=="" (set "DEST=%~1") else (for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Startup')"`) do set "DEST=%%i")
if exist "%DEST%\abaqus-preview.lnk" (del "%DEST%\abaqus-preview.lnk" & echo Removed %DEST%\abaqus-preview.lnk) else (echo No shortcut found in %DEST%)
