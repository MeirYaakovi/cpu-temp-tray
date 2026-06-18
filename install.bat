@echo off
echo Installing dependencies...
pip install -r "%~dp0requirements.txt"
echo.
echo Done. Run run.bat to start the tray app.
pause
