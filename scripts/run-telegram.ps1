$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$exe = Join-Path $repoRoot ".venv\Scripts\receipt-todo.exe"
$config = Join-Path $repoRoot "config.local.toml"

& $exe telegram --config $config
exit $LASTEXITCODE
