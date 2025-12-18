# =====================================================
# test-to-cal-patched one-command launcher (Windows)
# File: generate_progress_report_TEST_TO_CAL_PATCHED.ps1
# =====================================================

$ErrorActionPreference = "Stop"

# Always run from the folder that contains this script
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

Write-Host "Project root: $ROOT" -ForegroundColor Cyan

# --- Helpers ---------------------------------------------------------------

function Get-FileSha256([string]$Path) {
    if (-Not (Test-Path $Path)) { return "" }
    $h = Get-FileHash -Algorithm SHA256 -Path $Path
    return $h.Hash
}

# --- 1) Ensure virtual environment exists ----------------------------------

if (-Not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
}

$PY = ".\.venv\Scripts\python.exe"

# --- 2) Upgrade pip (once per venv, best-effort) ---------------------------

try {
    & $PY -m pip install --upgrade pip | Out-Host
} catch {
    Write-Host "pip upgrade skipped: $($_.Exception.Message)" -ForegroundColor DarkYellow
}

# --- 3) Install deps only when requirements changed ------------------------

$REQ = ".\requirements.txt"
$STAMP_DIR = ".\.venv\.stamps"
$REQ_STAMP = Join-Path $STAMP_DIR "requirements.sha256"

if (-Not (Test-Path $STAMP_DIR)) { New-Item -ItemType Directory -Path $STAMP_DIR | Out-Null }

$reqHash = Get-FileSha256 $REQ
$oldHash = ""
if (Test-Path $REQ_STAMP) { $oldHash = (Get-Content $REQ_STAMP -Raw).Trim() }

if (($reqHash -ne "") -and ($reqHash -ne $oldHash)) {
    Write-Host "Installing dependencies from requirements.txt (changed)..." -ForegroundColor Green
    & $PY -m pip install -r $REQ
    Set-Content -Path $REQ_STAMP -Value $reqHash
} elseif (($reqHash -ne "") -and ($reqHash -eq $oldHash)) {
    Write-Host "Dependencies already installed (requirements unchanged)." -ForegroundColor DarkGreen
} else {
    Write-Host "requirements.txt not found; installing minimal deps..." -ForegroundColor Yellow
    & $PY -m pip install PyQt5 playwright
}

# --- 4) Ensure Playwright browsers are installed (only once per venv) ------

$PW_STAMP = Join-Path $STAMP_DIR "playwright_browsers_installed.txt"
if (-Not (Test-Path $PW_STAMP)) {
    Write-Host "Installing Playwright browsers (first run)..." -ForegroundColor Green
    & $PY -m playwright install
    Set-Content -Path $PW_STAMP -Value "installed"
} else {
    Write-Host "Playwright browsers already installed." -ForegroundColor DarkGreen
}

# --- 5) Run app ------------------------------------------------------------

Write-Host "Launching dietCalculator.py..." -ForegroundColor Cyan
& $PY .\dietCalculator.py
