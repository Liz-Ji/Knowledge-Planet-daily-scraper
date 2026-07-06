# 注册/更新 Windows 任务计划：每次登录(开机后首次登录)时触发抓取。
# 配合 src/main.py 的「当天只成功一次」守卫，实现「每天第一次开机抓一次」。
# 失败/不完整时 main.py 返回非 0 退出码，任务计划程序按下方设置自动重试。
#
# 用法：右键“使用 PowerShell 运行”，或在管理员/普通 PowerShell 中执行：
#   powershell -ExecutionPolicy Bypass -File scripts\setup_task.ps1

$TaskName = "星球内容助手-每日抓取"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $ProjectRoot "scripts\run_daily.ps1"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""

# 触发器：当前用户每次登录时（开机后第一次登录即触发）
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# 设置：
# - StartWhenAvailable：错过的触发点在可用时补跑
# - RestartCount/RestartInterval：本次运行失败(非0退出)后，每30分钟重试，最多6次
# - MultipleInstances IgnoreNew：避免重复触发时多实例叠加
# - ExecutionTimeLimit：单次最多跑1小时，防止卡死
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 6 `
    -RestartInterval (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $TaskName `
    -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "开机首次登录时抓取知识星球姜胡说/珍大户的经济圈的星主+精华内容写入飞书；当天成功一次后不再重复，失败自动重试并飞书提醒" `
    -Force | Out-Null

Write-Host "已注册任务计划：$TaskName（触发方式：每次登录）"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State

# ---- 每周精华周报：每周日 20:00 推送到飞书群 ----
$WeeklyName = "星球内容助手-每周周报"
$WeeklyRunner = Join-Path $ProjectRoot "scripts\run_weekly.ps1"
$WeeklyAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$WeeklyRunner`""
$WeeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 8:00PM
$WeeklySettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $WeeklyName `
    -Action $WeeklyAction -Trigger $WeeklyTrigger -Settings $WeeklySettings `
    -Description "每周日20:00汇总过去一周星球精华，用大模型生成周报推送到飞书群" `
    -Force | Out-Null

Write-Host "已注册任务计划：$WeeklyName（触发方式：每周日 20:00）"
Get-ScheduledTask -TaskName $WeeklyName | Select-Object TaskName, State
