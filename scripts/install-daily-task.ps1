param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$At,

    [string]$TaskName = "Daily Todo Printer - Morning"
)

$ErrorActionPreference = "Stop"
$runner = Join-Path $PSScriptRoot "run-daily.ps1"
$runAt = [DateTime]::ParseExact(
    $At,
    "HH:mm",
    [System.Globalization.CultureInfo]::InvariantCulture
)

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""

$trigger = New-ScheduledTaskTrigger -Daily -At $runAt

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -User "SYSTEM" `
    -RunLevel Highest `
    -Force

Write-Host "Installed scheduled task: $TaskName at $At"
