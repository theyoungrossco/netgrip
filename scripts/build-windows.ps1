<#
.SYNOPSIS
    Build the NetGrip Windows installer (NetGrip-<version>-setup.exe).

.DESCRIPTION
    Freezes the app with PyInstaller, then wraps it with Inno Setup. Run on
    Windows from anywhere in the repo:

        powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1

    Requirements:
      * Python 3.10+ on PATH (python.org build recommended).
      * Inno Setup 6 (https://jrsoftware.org/isdl.php) — the script finds ISCC
        on PATH or in the default Program Files location, or set $env:ISCC.

    The same script runs in CI (.github/workflows/release.yml); CI just installs
    Inno Setup first. Output lands in dist\.
#>
[CmdletBinding()]
param(
    # Skip rebuilding the venv if it already exists (faster local iteration).
    [switch]$ReuseVenv
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$Venv = Join-Path $RepoRoot ".buildvenv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"

Write-Host "==> Preparing build venv" -ForegroundColor Cyan
if (-not ($ReuseVenv -and (Test-Path $VenvPy))) {
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
    python -m venv $Venv
    & $VenvPy -m pip install --upgrade pip wheel | Out-Host
    & $VenvPy -m pip install pyinstaller . | Out-Host
}

# Single source of truth for the version: the installed package.
$Version = (& $VenvPy -c "import netgrip; print(netgrip.__version__)").Trim()
Write-Host "==> Building NetGrip $Version" -ForegroundColor Cyan

Write-Host "==> Freezing app with PyInstaller" -ForegroundColor Cyan
& $VenvPy -m PyInstaller --noconfirm --clean installer\windows\netgrip.spec | Out-Host
if (-not (Test-Path "dist\NetGrip\NetGrip.exe")) {
    throw "PyInstaller did not produce dist\NetGrip\NetGrip.exe"
}

# Locate the Inno Setup compiler.
$Iscc = $env:ISCC
if (-not $Iscc) {
    $Iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
}
foreach ($cand in @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe")) {
    if (-not $Iscc -and (Test-Path $cand)) { $Iscc = $cand }
}
if (-not $Iscc) {
    throw "Inno Setup (ISCC.exe) not found. Install it from https://jrsoftware.org/isdl.php or set `$env:ISCC."
}

Write-Host "==> Compiling installer with Inno Setup" -ForegroundColor Cyan
& $Iscc "/DAppVersion=$Version" "installer\windows\netgrip.iss" | Out-Host

$Setup = Join-Path $RepoRoot "dist\NetGrip-$Version-setup.exe"
if (-not (Test-Path $Setup)) { throw "Installer was not produced: $Setup" }
Write-Host ""
Write-Host "Built $Setup" -ForegroundColor Green
