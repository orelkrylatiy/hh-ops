[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

Set-Location $projectRoot

if (-not (Test-Path $venvPython)) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($null -eq $py) {
        throw "Python launcher 'py' not found. Install Python 3.11 or newer."
    }
    & py -3.11 -m venv .venv
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e ".[playwright,pillow]"
& $venvPython -m pip install -r admin\requirements.txt
& $venvPython -m playwright install chromium

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

Write-Host "Windows setup complete. Check the CLI with: .\hh.bat --help"
