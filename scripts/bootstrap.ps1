# scripts/bootstrap.ps1 — Windows bootstrap
# All real logic lives in Python. This script only creates a venv and calls the CLI,
# deliberately: every line of shell here is a line that behaves differently on Windows.
$ErrorActionPreference = "Stop"

Write-Host "=== CWT Video Ads Agent — bootstrap ===" -ForegroundColor Cyan

# 1. Python version gate
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) {
    # Check py launcher
    $pyLauncher = (Get-Command py -ErrorAction SilentlyContinue).Source
    if ($pyLauncher) {
        $py = "py"
    } else {
        throw "python not found on PATH. Install Python 3.11+ from python.org."
    }
}
$ver = (& $py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if ([version]$ver -lt [version]"3.11") { throw "Python $ver found; 3.11+ required." }
Write-Host "  python $ver  $py" -ForegroundColor Green

# 2. venv
if (-not (Test-Path ".venv")) { & $py -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt --quiet
& .\.venv\Scripts\python.exe -m pip install -e . --quiet
Write-Host "  venv ready" -ForegroundColor Green

# 3. .env
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "  .env created from .env.example — FILL IN YOUR KEYS" -ForegroundColor Yellow
}

# 4. Delegate everything else to the CLI, which is cross-platform.
& .\.venv\Scripts\python.exe -m cwt doctor
