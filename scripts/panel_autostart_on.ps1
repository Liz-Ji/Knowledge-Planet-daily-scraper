# Turn ON autostart for the KB Cockpit panel via the Windows Startup folder:
# a windowless pythonw launcher runs at every logon. Also starts it right now.
# ASCII-only on purpose (Chinese path is derived at runtime, never a literal).
# Run:  powershell -ExecutionPolicy Bypass -File scripts\panel_autostart_on.ps1
$ErrorActionPreference = "Stop"
$root   = Split-Path -Parent $PSScriptRoot
$pyw    = Join-Path $root ".venv\Scripts\pythonw.exe"
$script = Join-Path $root "src\panel.py"
$args   = '"' + $script + '" --no-browser'

# Remove any old scheduled-task version to avoid duplicates
try { Unregister-ScheduledTask -TaskName "KB-Cockpit-Autostart" -Confirm:$false -ErrorAction SilentlyContinue } catch {}

# Stop any panel already running so the fresh one owns port 8825
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*panel.py*' -and $_.Name -like 'python*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Create the Startup-folder shortcut (runs at every logon, windowless)
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup "KB-Cockpit.lnk"
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = $pyw
$sc.Arguments = $args
$sc.WorkingDirectory = $root
$sc.WindowStyle = 7
$sc.Description = "KB Cockpit panel (localhost:8825)"
$sc.Save()

# Start it now too
Start-Process -FilePath $pyw -ArgumentList $args -WorkingDirectory $root
Write-Host "Autostart ON (Startup folder). Panel at http://localhost:8825"
