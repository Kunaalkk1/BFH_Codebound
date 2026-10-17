param([switch]$Live, [switch]$Docker, [switch]$Mcp, [switch]$Ollama)
if ($Ollama) {
    . "$PSScriptRoot\common.ps1"
    Push-Location $ProjectRoot
    try {
        $env:CODEBOUND_TEST_OLLAMA = '1'
        & $PythonExe -m pytest tests/test_ollama_live.py -q -s --tb=short --junitxml .local/pytest-ollama-results.xml
        exit $LASTEXITCODE
    } finally { Pop-Location }
}
if ($Mcp) { & "$PSScriptRoot\integrate.ps1" -Demo; exit $LASTEXITCODE }
. "$PSScriptRoot\common.ps1"
try {
    if (-not (Test-Path -LiteralPath $PythonExe)) { throw 'Project virtual environment missing.' }
    New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null
    $resultsFile = Join-Path $LocalDir $(if ($Docker) { 'pytest-docker-results.xml' } else { 'pytest-results.xml' })
    if (Test-Path -LiteralPath $resultsFile) { Remove-Item -LiteralPath $resultsFile }
    Push-Location $ProjectRoot
    try {
        if ($Docker) {
            if (-not (Docker-Ready)) { throw 'Docker daemon unavailable; real worker tests cannot run.' }
            $env:CODEBOUND_TEST_DOCKER = '1'
            & $PythonExe -m pytest -q (Join-Path $ProjectRoot 'tests\test_docker_integration.py') --junitxml $resultsFile @args
        } else {
            & $PythonExe -m pytest -q -m 'not docker' --junitxml $resultsFile @args
        }
        $testExit = $LASTEXITCODE
        if (Test-Path -LiteralPath $resultsFile) {
            [xml]$report = Get-Content -LiteralPath $resultsFile -Raw
            $suite = $report.testsuites.testsuite
            $failed = [int]$suite.failures + [int]$suite.errors
            $skipped = [int]$suite.skipped
            $passed = [int]$suite.tests - $failed - $skipped
            Write-Host "Actual pytest counts: $passed passed, $failed failed/errors, $skipped skipped"
        }
        if ($testExit -ne 0) { exit $testExit }
        if ($Live) {
            & $PythonExe (Join-Path $ProjectRoot 'scripts\verify_http.py')
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
        if ($Docker) { Write-Host 'Real Docker tests passed under tested conditions; shared-kernel and daemon-authority risks remain.' }
        else { Write-Host 'Tests passed. Mock/policy tests do not certify secure execution.' }
    } finally { Pop-Location }
    exit 0
} catch { Write-Host "TEST FAILED: $($_.Exception.Message)"; exit 1 }
