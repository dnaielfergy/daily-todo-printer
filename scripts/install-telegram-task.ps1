param(
    [string]$TaskName = "Daily Todo Printer - Telegram"
)

$ErrorActionPreference = "Stop"
$runner = Join-Path $PSScriptRoot "run-telegram.ps1"
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtStartup
# The Telegram listener is continuous; the default 72-hour task limit would terminate it.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1)

# Allow the laptop to keep polling Telegram when running on battery power.
$settings.StopIfGoingOnBatteries = $false
$settings.DisallowStartIfOnBatteries = $false

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -User "SYSTEM" `
    -RunLevel Highest `
    -Force

Write-Host "Installed scheduled task: $TaskName"
