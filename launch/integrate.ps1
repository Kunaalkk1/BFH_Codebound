param([switch]$Configure, [switch]$Demo, [switch]$Ollama)
. "$PSScriptRoot\common.ps1"
try {
    Push-Location $ProjectRoot
    try {
        if ($Configure) {
            if ($Ollama) { & $PythonExe -m codebound.configure_mcp --ollama }
            else { & $PythonExe -m codebound.configure_mcp }
            if ($LASTEXITCODE -ne 0) { throw 'REST connector registration failed.' }
            & $PythonExe -m codebound.configure_connector
            if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL connector registration failed.' }
            Write-Host 'Restart managed Codebound once to load broker configuration: stop.ps1 -Development; start.ps1 -Mode local_docker'
        }
        & $PythonExe -m codebound.check_mcp
        if ($LASTEXITCODE -ne 0) { throw 'Authenticated MCP discovery failed.' }
        if ($Demo) {
            $env:CODEBOUND_TEST_MCP = '1'
            & $PythonExe -m pytest tests/test_mcp_live.py -q -s --tb=short --junitxml .local/pytest-mcp-results.xml
            if ($LASTEXITCODE -ne 0) { throw 'Live MCP execution verification failed.' }
        }
    } finally { Pop-Location }
    exit 0
} catch { Write-Host "INTEGRATION FAILED: $($_.Exception.Message)"; exit 1 }
