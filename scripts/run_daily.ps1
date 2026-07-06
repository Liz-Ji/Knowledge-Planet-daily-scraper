# 每日定时任务入口，由 Windows 任务计划程序调用。
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Main = Join-Path $ProjectRoot "src\main.py"

Set-Location $ProjectRoot
& $Python $Main
