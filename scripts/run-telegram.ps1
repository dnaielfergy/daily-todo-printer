$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$exe = Join-Path $repoRoot ".venv\Scripts\receipt-todo.exe"
$dedicated = Join-Path $repoRoot "config.telegram.toml"
$legacy = Join-Path $repoRoot "config.local.toml"
$config = if (Test-Path $dedicated) { $dedicated } else { $legacy }

& $exe telegram --config $config
exit $LASTEXITCODE
