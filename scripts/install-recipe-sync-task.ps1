param(
    [string]$TaskName = "Daily Todo Printer - Recipe Inbox",
    [ValidateRange(5, 60)]
    [int]$EveryMinutes = 5
)

$ErrorActionPreference = "Stop"
$runner = Join-Path $PSScriptRoot "run-recipe-sync.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $runner)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
$settings.StopIfGoingOnBatteries = $false
$settings.DisallowStartIfGoingOnBatteries = $false
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -User "SYSTEM" -RunLevel Highest -Force
Write-Host "Installed read-only recipe inbox poller: $TaskName (every $EveryMinutes minutes)"
Write-Host "This task imports jobs but never prints them automatically."
