$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
$previousBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH
try {
    if (-not (Test-Path -LiteralPath './.venv/Scripts/python.exe')) {
        throw 'Project environment missing. Run ./setup.ps1 first.'
    }
    & ./.venv/Scripts/python.exe -c "import sys, bs4, playwright; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
    if ($LASTEXITCODE -ne 0) { throw 'Python environment or dependencies unavailable. Run ./setup.ps1 first.' }
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PSScriptRoot '.browsers'
    & ./.venv/Scripts/python.exe scraper.py capture --method browser --channel msedge --headed --via-home
    $scraperExit = $LASTEXITCODE
} finally {
    $env:PLAYWRIGHT_BROWSERS_PATH = $previousBrowserPath
    Pop-Location
}
exit $scraperExit
