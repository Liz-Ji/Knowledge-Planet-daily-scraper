# Turn OFF autostart permanently: remove the Startup-folder shortcut and stop the running panel.
# Run:  powershell -ExecutionPolicy Bypass -File scripts\panel_autostart_off.ps1
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) "KB-Cockpit.lnk"
if (Test-Path $lnk) { Remove-Item $lnk -Force }
try { Unregister-ScheduledTask -TaskName "KB-Cockpit-Autostart" -Confirm:$false -ErrorAction SilentlyContinue } catch {}
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*panel.py*' -and $_.Name -like 'python*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "Autostart OFF and panel stopped."
