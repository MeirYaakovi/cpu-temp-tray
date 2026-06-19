@echo off
:: Start LHM as admin (needed for hardware access)
powershell -NoProfile -Command "Start-Process '%~dp0lhm_bundle\LibreHardwareMonitor.exe' -Verb RunAs"
:: Wait for LHM to initialize its web server
timeout /t 4 /nobreak >nul
:: Start our tray app (no admin needed)
start "" pythonw.exe "%~dp0cpu_temp_tray.py"
