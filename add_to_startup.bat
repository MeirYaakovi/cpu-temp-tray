@echo off
:: Task 1: LHM as admin at logon (no UAC popup)
powershell -NoProfile -Command ^
  "$a = New-ScheduledTaskAction -Execute 'C:\meir\Projects\cpu-temp-tray\lhm_bundle\LibreHardwareMonitor.exe'; ^
   $t = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME; ^
   $p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive; ^
   $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; ^
   Register-ScheduledTask -TaskName 'LHM_Backend' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null; ^
   Write-Host 'LHM task registered.'"

:: Task 2: our tray app at logon, 5 seconds after (gives LHM time to start)
powershell -NoProfile -Command ^
  "$a = New-ScheduledTaskAction -Execute 'pythonw.exe' -Argument 'C:\meir\Projects\cpu-temp-tray\cpu_temp_tray.py'; ^
   $t = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME; ^
   $t.Delay = 'PT5S'; ^
   $p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive; ^
   $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; ^
   Register-ScheduledTask -TaskName 'CpuTempTray' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null; ^
   Write-Host 'Tray task registered.'"

echo.
echo Done. Both will auto-start at next logon.
pause
