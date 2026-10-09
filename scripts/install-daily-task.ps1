param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$At,

    [string]$TaskName = "Daily Todo Printer - Morning",

    [string]$User
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

if ($User) {
    $principal = New-ScheduledTaskPrincipal `
        -UserId $User `
        -LogonType S4U `
        -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null

    Write-Host "Installed scheduled task: $TaskName at $At"
    Write-Host "Principal: $User (S4U, Limited; no stored password or network access)"
}
else {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -User "SYSTEM" `
        -RunLevel Highest `
        -Force | Out-Null

    Write-Host "Installed scheduled task: $TaskName at $At"
    Write-Warning "This task runs as SYSTEM. That is supported for deterministic planning only."
    Write-Warning "Local AI refuses to run as SYSTEM. Reinstall with -User <dedicated non-admin account> before enabling AI."
}
