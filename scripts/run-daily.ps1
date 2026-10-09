param(
    [string]$Config
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not $Config) {
    $dedicated = Join-Path $repoRoot "config.daily.toml"
    $legacy = Join-Path $repoRoot "config.local.toml"
    $Config = if (Test-Path $dedicated) { $dedicated } else { $legacy }
}

$exe = Join-Path $repoRoot ".venv\Scripts\receipt-todo.exe"
& $exe daily --config $Config
exit $LASTEXITCODE
