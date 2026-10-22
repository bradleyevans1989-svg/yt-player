@echo off
cd /d "%~dp0"
echo Installing/updating dependencies...
py -m pip install -q -U -r requirements.txt
start "" http://localhost:8080
py app.py
pause
