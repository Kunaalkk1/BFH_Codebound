param([switch]$Development, [ValidateSet('mock','local_docker')][string]$Mode = 'mock', [switch]$Mcp, [switch]$Ollama)
if ($Ollama) { $Mcp = $true }
if ($Mcp) { $Mode = 'local_docker' }
. "$PSScriptRoot\common.ps1"
try {
    if ($Ollama) {
        Push-Location $ProjectRoot
        try { & $PythonExe -m codebound.llm.check --warm; if ($LASTEXITCODE -ne 0) { throw 'Local Ollama/model unavailable. See README.md.' } }
        finally { Pop-Location }
    }
    if ($Mode -eq 'local_docker') {
        if (-not (Docker-Ready)) { throw 'Docker daemon unavailable; no mock fallback.' }
        Push-Location $ProjectRoot
        try { & $PythonExe -m codebound.worker_admin check; if ($LASTEXITCODE -ne 0) { throw 'Worker unavailable. Run launch/build-worker.ps1.' } }
        finally { Pop-Location }
    }
    if (-not $Development) {
        if (-not (Docker-Ready)) { throw 'Docker daemon unavailable; start Docker Desktop.' }
        Invoke-Compose @('config','--quiet')
        Invoke-Compose @('up','-d','--wait','--wait-timeout','120','app','postgres')
        Assert-AnythingMcp
        Write-Host 'AnythingMCP RUNNING: http://localhost:3000'
    }
    $state = Read-BackendState
    if (Owned-Backend $state) {
        $health = Backend-Health
        if ($health.status -ne 'ok' -or $health.sandbox_mode -ne $Mode) { throw 'Backend mode differs or is unhealthy. Stop with -Development, then start the selected mode.' }
        Write-Host "Codebound RUNNING: http://127.0.0.1:8000/docs ($Mode)"
        if ($Mcp) { & "$PSScriptRoot\integrate.ps1"; if ($LASTEXITCODE -ne 0) { throw 'MCP discovery failed.' } }
        if ($Ollama) { Invoke-RestMethod 'http://127.0.0.1:8000/ai/health' -Headers @{Authorization="Bearer $((Get-DemoTokens).a)"} | Out-Null }
        exit 0
    }
    if (Backend-Health) { throw 'Port 8000 already serves an unmanaged API; leaving it unchanged.' }
    if (-not (Test-Path -LiteralPath $PythonExe)) { throw 'Virtual environment missing; see setup.ps1.' }
    if (-not (Test-Path (Join-Path $ProjectRoot 'data\railway.csv'))) { throw 'Dataset missing; run python -m codebound.legacy.generate_data using the project venv.' }
    New-DemoTokens
    $tokens = Get-DemoTokens
    if ($tokens.a.Length -lt 32 -or $tokens.b.Length -lt 32 -or $tokens.a -eq $tokens.b) { throw 'Invalid demo token configuration.' }
    $instance = [guid]::NewGuid().ToString('N')
    $runner = Join-Path $PSScriptRoot 'serve.py'
    $process = Start-Process -FilePath $PythonExe -ArgumentList @('"' + $runner + '"', $instance, $Mode) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $LocalDir 'backend.out.log') -RedirectStandardError (Join-Path $LocalDir 'backend.err.log')
    @{ pid=$process.Id; started=$process.StartTime.ToUniversalTime().Ticks.ToString(); instance=$instance } | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
    for ($attempt=0; $attempt -lt 40; $attempt++) {
        if ($process.HasExited) { throw 'Codebound exited. See .local/backend.err.log.' }
        $health = Backend-Health
        if ($health.status -eq 'ok' -and $health.sandbox_mode -eq $Mode) {
            # Verify this server actually recognizes our configured demo identity.
            Invoke-RestMethod 'http://127.0.0.1:8000/datasets' -Headers @{Authorization="Bearer $($tokens.a)"} | Out-Null
            Write-Host "Codebound RUNNING: http://127.0.0.1:8000/docs ($Mode)"
            if ($Mcp) { & "$PSScriptRoot\integrate.ps1"; if ($LASTEXITCODE -ne 0) { throw 'MCP discovery failed.' } }
            if ($Ollama) { Invoke-RestMethod 'http://127.0.0.1:8000/ai/health' -Headers @{Authorization="Bearer $((Get-DemoTokens).a)"} | Out-Null }
            exit 0
        }
        Start-Sleep -Milliseconds 250
        $process.Refresh()
    }
    throw 'Codebound health check timed out. See .local/backend.err.log.'
} catch { Write-Host "START FAILED: $($_.Exception.Message)"; exit 1 }

