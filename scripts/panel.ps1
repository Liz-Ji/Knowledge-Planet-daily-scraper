# 启动「知识库驾驶舱」面板（速记 / 待看 / 拖文件 / 搜索）。双击本文件即可。
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
Set-Location $root
& $py "src\panel.py"
