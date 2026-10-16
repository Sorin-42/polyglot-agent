# Builds dist\PolyglotAgent.exe: one file, no Python needed on the PC that runs it.
# Usage (from the project folder):  powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
# Builds with a Python that PyInstaller and pythonnet support well (3.13, then 3.12,
# 3.14); plain "python" on PATH can be anything (even 3.4).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path .build-venv)) {
    $made = $false
    foreach ($v in "3.13", "3.12", "3.14") {
        & py "-$v" -m venv .build-venv 2>$null
        if ($LASTEXITCODE -eq 0) { $made = $true; break }
    }
    if (-not $made) { python -m venv .build-venv }
}
.\.build-venv\Scripts\python.exe -m pip install --upgrade pip
.\.build-venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.\.build-venv\Scripts\python.exe -m PyInstaller --noconfirm --clean PolyglotAgent.spec
Write-Host "`nBuilt dist\PolyglotAgent.exe (on first start it asks where to keep your data)"
