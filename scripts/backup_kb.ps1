# Knowledge-base Git backup launcher. Double-click to back up now.
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
Set-Location $root
& $py "src\backup_kb.py"
