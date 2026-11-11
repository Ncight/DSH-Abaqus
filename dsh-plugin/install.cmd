@echo off
rem Installs the Abaqus review-gate plugin into your DeepSeek Harness *desktop* profile.
rem What it changes: adds one dependency + one bundle entry to %USERPROFILE%\.dsh\profiles\desktop\package.json
rem (a backup is written next to it first). Restart DeepSeek Harness afterwards.
rem To remove it later:  "D:\DSH\resources\runtime\cli\bin\dsh.cmd" plugin --profile desktop remove @local/abq-gate
setlocal
set "PROFILE=%USERPROFILE%\.dsh\profiles\desktop"
if not exist "%PROFILE%\package.json" (
  echo Desktop profile not found at %PROFILE%
  exit /b 1
)
copy /y "%PROFILE%\package.json" "%PROFILE%\package.json.before-abq-gate" >nul
call "D:\DSH\resources\runtime\cli\bin\dsh.cmd" plugin --profile desktop add "link:%~dp0abq-gate"
if errorlevel 1 (
  echo Install failed. Your profile backup is %PROFILE%\package.json.before-abq-gate
  exit /b 1
)
echo.
echo Installed. Close and reopen DeepSeek Harness to load the plugin.
echo It only acts in workspaces whose .abq\config.json has "gate": "human".
