@echo off
:: Creates a Windows Task Scheduler entry that runs at logon as admin — no UAC popup.
powershell -NoProfile -Command ^
  "$script = 'C:\meir\Projects\cpu-temp-tray\cpu_temp_tray.py'; ^
   $action  = New-ScheduledTaskAction -Execute 'pythonw.exe' -Argument \"`\"`\"$script`\"`\"\"; ^
   $trigger = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME; ^
   $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive; ^
   $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; ^
   Register-ScheduledTask -TaskName 'CpuTempTray' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null; ^
   Write-Host 'Done: CpuTempTray will start automatically at logon (admin, no UAC).'"
pause
