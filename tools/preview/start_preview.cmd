@echo off
rem Abaqus preview panel: watches the workspace for *.inp and shows them at http://127.0.0.1:8765/
start "abaqus-preview" /min py "%~dp0preview_server.py" --port 8765
timeout /t 1 >nul
start "" http://127.0.0.1:8765/
