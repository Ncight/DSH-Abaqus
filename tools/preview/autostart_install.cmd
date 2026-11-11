@echo off
rem Optional: start the Abaqus preview panel at every Windows logon (no window).
rem Adds one shortcut, "abaqus-preview", to your Startup folder; undo with autostart_remove.cmd.
rem The panel only listens on 127.0.0.1:8765 and only reads *.inp files of this workspace.
setlocal
set "TARGET=%~dp0preview_server.py"
if not "%~1"=="" (set "DEST=%~1") else (for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Startup')"`) do set "DEST=%%i")
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $env:DEST 'abaqus-preview.lnk')); $s.TargetPath=(Get-Command pyw.exe).Source; $s.Arguments='\"'+$env:TARGET+'\" --port 8765'; $s.WorkingDirectory=(Split-Path $env:TARGET); $s.Description='Abaqus INP preview panel (127.0.0.1:8765)'; $s.Save()"
if errorlevel 1 (echo failed to create the shortcut & exit /b 1)
echo Shortcut created: %DEST%\abaqus-preview.lnk
echo The panel will start at the next logon. To start it now: start_preview.cmd
