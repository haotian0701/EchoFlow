param(
    [ValidateSet('smoke', 'recovery', 'all')]
    [string]$Suite = 'smoke',
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $Python = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $Python)) { $Python = 'python' }
}
$composeArgs = @('compose', '-p', 'echoflow-e2e', '-f', 'compose.yaml', '-f', 'backend/tests/e2e/compose.yaml')
$previousE2E = $env:ECHOFLOW_E2E
$previousRecovery = $env:ECHOFLOW_E2E_RECOVERY
$previousBrowsers = $env:PLAYWRIGHT_BROWSERS_PATH
$env:ECHOFLOW_E2E = '1'
$env:ECHOFLOW_E2E_RECOVERY = '0'
if ($Suite -ne 'smoke') {
    $composeArgs += @('-f', 'backend/tests/e2e/compose.recovery.yaml')
    $env:ECHOFLOW_E2E_RECOVERY = '1'
}
$browserCache = Join-Path $projectRoot '.tools/playwright-browsers'
if (-not $env:PLAYWRIGHT_BROWSERS_PATH -and (Test-Path -LiteralPath $browserCache)) {
    $env:PLAYWRIGHT_BROWSERS_PATH = $browserCache
}
Push-Location $projectRoot
try {
    docker compose build api worker migrate dispatcher maintenance minio-init frontend
    if ($LASTEXITCODE -ne 0) { throw 'Image build failed' }
    docker @composeArgs up -d --no-build --wait
    if ($LASTEXITCODE -ne 0) { throw 'Isolated Mock environment did not become ready' }
    $testResults = Join-Path $projectRoot ('.tools/e2e-results-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path (Split-Path -Parent $testResults) -Force | Out-Null
    $testArgs = @('-m', 'pytest', 'backend/tests/e2e', '-m', 'e2e', '-q', '--basetemp', $testResults, '-o', "cache_dir=$testResults/pytest-cache")
    if ($Suite -eq 'smoke') { $testArgs += @('-k', 'not recovery') }
    if ($Suite -eq 'recovery') { $testArgs += @('-k', 'recovery') }
    & $Python @testArgs
    if ($LASTEXITCODE -ne 0) { throw 'E2E tests failed' }
} finally {
    # This fixed test project is the only deletion target; main volumes are untouched.
    docker @composeArgs down -v
    Pop-Location
    $env:ECHOFLOW_E2E = $previousE2E
    $env:ECHOFLOW_E2E_RECOVERY = $previousRecovery
    $env:PLAYWRIGHT_BROWSERS_PATH = $previousBrowsers
}
