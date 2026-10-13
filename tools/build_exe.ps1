# Builds dist\PolyglotAgent.exe: one file, no Python needed on the PC that runs it.
# Usage (from the project folder):  powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path .build-venv)) { python -m venv .build-venv }
.\.build-venv\Scripts\python.exe -m pip install --upgrade pip
.\.build-venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.\.build-venv\Scripts\python.exe -m PyInstaller --noconfirm --clean PolyglotAgent.spec
Write-Host "`nBuilt dist\PolyglotAgent.exe (data is stored in %LOCALAPPDATA%\PolyglotAgent)"
