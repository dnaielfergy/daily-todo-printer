param(
    [ValidateSet("auto", "cpu", "cuda")]
    [string]$Device = "auto",

    [string]$Model = "jaredpalmer/kev-0.8b@v1.0",

    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$aiRoot = Join-Path $repoRoot ".ai\kev"
$venv = Join-Path $aiRoot ".venv"
$python = Join-Path $venv "Scripts\python.exe"
$bridge = Join-Path $repoRoot "scripts\kev_rank.py"
$cache = Join-Path $repoRoot ".ai\cache\huggingface"
$kevRef = "61b041bf04ede9d0c4649631025105ef90647e20"

function Find-Uv {
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\uv.exe"),
        (Join-Path $HOME ".local\bin\uv.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return $null
}

$uv = Find-Uv
if (-not $uv) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "uv is required. Install it from https://docs.astral.sh/uv/ and rerun this script."
    }

    Write-Host "Installing uv with WinGet..."
    & winget install --id=astral-sh.uv -e --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "WinGet could not install uv."
    }

    $uv = Find-Uv
    if (-not $uv) {
        throw "uv was installed but is not visible in this shell. Open a new PowerShell window and rerun this script."
    }
}

New-Item -ItemType Directory -Force -Path $aiRoot | Out-Null
New-Item -ItemType Directory -Force -Path $cache | Out-Null

Write-Host "Installing Python 3.13 for the isolated Kev runtime..."
& $uv python install 3.13
if ($LASTEXITCODE -ne 0) {
    throw "uv could not install Python 3.13."
}

Write-Host "Creating isolated Kev environment..."
& $uv venv --python 3.13 $venv
if ($LASTEXITCODE -ne 0) {
    throw "uv could not create the Kev virtual environment."
}

Write-Host "Installing pinned Kev 1.0 code..."
$kevPackage = "kev @ git+https://github.com/jaredpalmer/kev.git@$kevRef"
& $uv pip install --python $python $kevPackage
if ($LASTEXITCODE -ne 0) {
    throw "Kev installation failed."
}

function Invoke-KevSmoke {
    param([string]$SelectedDevice)

    $smoke = @{
        model = $Model
        device = $SelectedDevice
        requests = @(
            @{
                id = "smoke"
                state = @{
                    today = "2026-10-09"
                    goal = "Choose the task that should be worked on first today."
                }
                question = @{
                    type = "choice"
                    instructions = "Which task should be prioritized highest for today?"
                    criteria = @{
                        "1" = @{
                            text = "Submit tax payment due today"
                            priority = "high"
                            due_at = "2026-10-09"
                        }
                        "2" = @{
                            text = "Reorganize desk drawer"
                            priority = "low"
                            due_at = $null
                        }
                    }
                }
            }
        )
    } | ConvertTo-Json -Depth 10 -Compress

    $output = $smoke | & $python $bridge
    if ($LASTEXITCODE -ne 0) {
        return $null
    }

    try {
        $parsed = $output | ConvertFrom-Json
        if (-not $parsed.results[0].probabilities) {
            return $null
        }
        return $parsed
    }
    catch {
        return $null
    }
}

if (-not $SkipSmoke) {
    Write-Host "Downloading/caching the pinned model and running a local smoke test..."
    $env:HF_HOME = $cache
    $env:PYTHONNOUSERSITE = "1"

    $parsed = Invoke-KevSmoke -SelectedDevice $Device
    if (-not $parsed -and $Device -eq "auto") {
        Write-Warning "Automatic device selection failed. Retrying the smoke test on CPU."
        $parsed = Invoke-KevSmoke -SelectedDevice "cpu"
        if ($parsed) {
            Write-Warning "CPU succeeded. Use 'device = \"cpu\"' in [ai] on this machine."
        }
    }
    if (-not $parsed) {
        throw "Kev smoke test failed. Retry with -Device cpu to distinguish CUDA compatibility from model/runtime setup."
    }

    Write-Host "Kev smoke test passed."
    Write-Host ("Model: {0}" -f $parsed.model)
    Write-Host ("Device: {0}" -f $parsed.device)
    Write-Host ("Load: {0:N0} ms" -f [double]$parsed.load_ms)
    Write-Host ("Inference: {0:N0} ms" -f [double]$parsed.results[0].inference_ms)
}

Write-Host ""
Write-Host "Local AI runtime is installed but production AI remains opt-in."
Write-Host "Use data\tasks.test.db for evaluation before enabling [ai].enabled."
