# 每周精华周报入口，由 Windows 任务计划程序调用。
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script = Join-Path $ProjectRoot "src\weekly_report.py"

Set-Location $ProjectRoot
& $Python $Script
