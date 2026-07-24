<#
.SYNOPSIS
    Build L2ArbBot.exe — the single-file Windows installer/launcher.

.DESCRIPTION
    Creates an isolated build venv, installs PyInstaller, stages a clean copy of
    the four component sources, and produces launcher\dist\L2ArbBot.exe.

    The resulting .exe is self-bootstrapping: on first run it installs the app
    and all dependencies; once installed it just launches and opens the
    dashboard. See docs\INSTALL.md.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_windows_exe.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot   # scripts\ -> repo root
Write-Host "==> Repo root: $Root" -ForegroundColor Cyan

# 1. Locate a Python interpreter (>=3.9; 3.11/3.12 recommended).
$pyExe = $null
foreach ($cand in @("python", "py")) {
    $c = Get-Command $cand -ErrorAction SilentlyContinue
    if ($c) { $pyExe = $c.Source; break }
}
if (-not $pyExe) {
    Write-Host "Python not found. Attempting winget install…" -ForegroundColor Yellow
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    $pyExe = (Get-Command python -ErrorAction Stop).Source
}
Write-Host "==> Using Python: $pyExe" -ForegroundColor Cyan

# 2. Isolated build venv with PyInstaller.
$venv = Join-Path $Root "launcher\build\venv-build"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    & $pyExe -m venv $venv
}
$vpy = Join-Path $venv "Scripts\python.exe"
& $vpy -m pip install --upgrade pip wheel | Out-Host
& $vpy -m pip install "pyinstaller>=6.6" | Out-Host

# 3. Build (stages payload + runs PyInstaller against the spec).
& $vpy (Join-Path $Root "scripts\build_exe.py") --clean | Out-Host

$exe = Join-Path $Root "launcher\dist\L2ArbBot.exe"
if (Test-Path $exe) {
    Write-Host "`n==> Built: $exe" -ForegroundColor Green
    Get-Item $exe | Select-Object FullName, Length | Format-List
} else {
    throw "Build failed: $exe not found"
}
