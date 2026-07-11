# 启动本地速记小应用（写入知识库 00-Inbox）。双击本文件即可。
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
Set-Location $root
& $py "src\capture.py"
