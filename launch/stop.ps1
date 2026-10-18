param([switch]$Development)
. "$PSScriptRoot\common.ps1"
try {
    Write-Host 'Ollama is independently managed; its processes and models are left intact.'
    $state = Read-BackendState
    $process = Owned-Backend $state
    if ($process) {
        New-Item -ItemType File -Force -Path (Join-Path $LocalDir "stop-$($state.instance)") | Out-Null
        if (-not $process.WaitForExit(15000)) { throw 'Graceful stop timed out; no unrelated process was killed.' }
        Write-Host 'Codebound STOPPED'
    } else { Write-Host 'Codebound STOPPED (no managed process)' }
    if (Test-Path -LiteralPath $StateFile) { Remove-Item -LiteralPath $StateFile }
    if (Docker-Ready) {
        Push-Location $ProjectRoot
        try { & $PythonExe -m codebound.worker_admin cleanup; if ($LASTEXITCODE -ne 0) { throw 'Worker cleanup failed.' } }
        finally { Pop-Location }
    }
    if (-not $Development) {
        if (-not (Docker-Ready)) { throw 'Docker daemon unavailable; containers were not stopped.' }
        Invoke-Compose @('stop','--timeout','20','app','postgres')
        Write-Host 'AnythingMCP STOPPED; volumes, registered MCP tools and connector configuration retained'
    }
    exit 0
} catch { Write-Host "STOP FAILED: $($_.Exception.Message)"; exit 1 }
