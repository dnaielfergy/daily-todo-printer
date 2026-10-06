param(
    [string]$Config
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not $Config) {
    $Config = Join-Path $repoRoot "config.local.toml"
}

$exe = Join-Path $repoRoot ".venv\Scripts\receipt-todo.exe"
& $exe daily --config $Config
exit $LASTEXITCODE
