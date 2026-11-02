$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & python -c "import sys; print(sys.version); sys.exit(0 if sys.version_info >= (3, 10) else 1)"
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 or newer is required.' }
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the project Python environment.' }
    & ./.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    Write-Host 'Setup complete. Run ./run.ps1 to collect NBA odds using installed Microsoft Edge.'
} finally {
    Pop-Location
}
