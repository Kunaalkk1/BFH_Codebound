. "$PSScriptRoot\common.ps1"
try {
    $ready = Docker-Ready
    if (-not $DockerExe) { Write-Host 'Docker: NOT INSTALLED' }
    elseif ($ready) { Write-Host 'Docker: RUNNING' }
    elseif (Get-Process -Name 'Docker Desktop','com.docker.backend' -ErrorAction SilentlyContinue) { Write-Host 'Docker: UNHEALTHY (Desktop running, daemon unreachable)' }
    else { Write-Host 'Docker: STOPPED' }
    if (-not (Test-Path (Join-Path $ProjectRoot 'anythingmcp\docker-compose.quickstart.yml'))) { Write-Host 'AnythingMCP: NOT INSTALLED' }
    elseif (-not (Test-Path (Join-Path $ProjectRoot 'anythingmcp\.env'))) { Write-Host 'AnythingMCP: NOT CONFIGURED' }
    elseif ($ready) {
        $rows = @(Invoke-Compose @('ps','--all','--format','json'))
        if (-not $rows) { Write-Host 'AnythingMCP: STOPPED' }
        else {
            $containers = @($rows | ForEach-Object { $_ | ConvertFrom-Json })
            foreach ($item in $containers) {
                $label = 'STOPPED'
                if ($item.State -eq 'running') { $label = if ($item.Health -eq 'healthy') {'RUNNING'} else {'UNHEALTHY'} }
                Write-Host "AnythingMCP $($item.Service): $label"
            }
            if (@($containers | Where-Object { $_.Service -eq 'app' -and $_.Health -eq 'healthy' }).Count) {
                try { Assert-AnythingMcp; Write-Host 'AnythingMCP HTTP: dashboard 200, backend/database healthy, unauthenticated MCP 401' }
                catch { Write-Host "AnythingMCP HTTP: UNHEALTHY - $($_.Exception.Message)" }
            }
        }
    } else { Write-Host 'AnythingMCP: UNHEALTHY (Docker unavailable)' }
    $health = Backend-Health
    if ($health.status -eq 'ok' -and $health.PSObject.Properties.Name -contains 'sandbox_mode') { Write-Host "Codebound: RUNNING ($($health.sandbox_mode))" }
    elseif ($health) { Write-Host 'Codebound: UNHEALTHY (unexpected health response)' }
    elseif (Owned-Backend (Read-BackendState)) { Write-Host 'Codebound: UNHEALTHY' }
    else { Write-Host 'Codebound: STOPPED' }
    $ollama = Find-Executable 'ollama.exe' @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")
    if (-not $ollama) { Write-Host 'Ollama: NOT INSTALLED' }
    else {
        try {
            $null = Read-JsonUrl 'http://127.0.0.1:11434/api/tags'
            Push-Location $ProjectRoot
            try { & $PythonExe -m codebound.llm.check; if ($LASTEXITCODE -ne 0) { Write-Host 'Ollama: model NOT READY' } }
            finally { Pop-Location }
        }
        catch { Write-Host 'Ollama: STOPPED (independently managed; start the native app)' }
    }
    if ($ready -and (Test-Path -LiteralPath $PythonExe)) {
        Push-Location $ProjectRoot
        try { & $PythonExe -m codebound.worker_admin check 2>$null; if ($LASTEXITCODE -ne 0) { Write-Host 'Sandbox runtime: UNAVAILABLE (no mock fallback in local_docker mode)' } }
        finally { Pop-Location }
    } else { Write-Host 'Sandbox runtime: UNAVAILABLE' }
    Write-Host 'URLs: http://localhost:3000 | http://localhost:4000/mcp | http://127.0.0.1:8000/docs'
    if (Test-Path (Join-Path $LocalDir 'anythingmcp-integration.json')) {
        Push-Location $ProjectRoot
        try { & $PythonExe -m codebound.check_mcp; if ($LASTEXITCODE -ne 0) { Write-Host 'MCP integration: UNHEALTHY' } }
        finally { Pop-Location }
    } else { Write-Host 'MCP integration: NOT CONFIGURED' }
    exit 0
} catch { Write-Host "STATUS FAILED: $($_.Exception.Message)"; exit 1 }
