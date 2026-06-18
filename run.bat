@echo off
:: Runs the tray app elevated (one-time UAC prompt).
:: Use add_to_startup.bat for a startup shortcut that never prompts.
powershell -NoProfile -Command "Start-Process pythonw.exe -ArgumentList '\"%~dp0cpu_temp_tray.py\"' -Verb RunAs"
