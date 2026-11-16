@echo off
rem Preview panel: watches the workspace for model files (*.inp, *.cdb) and shows them at http://127.0.0.1:8765/
rem More workspaces (for example the ANSYS one): one folder per line in extra_roots.local.txt next to this file.
rem git ignores that file; when it is missing the panel watches this repository only, as before.
start "abaqus-preview" /min py "%~dp0preview_server.py" --port 8765 --extra-roots-file "%~dp0extra_roots.local.txt"
timeout /t 1 >nul
start "" http://127.0.0.1:8765/
