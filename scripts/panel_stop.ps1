# Stop the running panel now. It comes back on next login (autostart).
# To disable autostart permanently, run panel_autostart_off.ps1 instead.
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -like "*panel.py*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "Panel stopped (will restart on next login)."
