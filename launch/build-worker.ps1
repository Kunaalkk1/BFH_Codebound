. "$PSScriptRoot\common.ps1"
try {
    if (-not (Docker-Ready)) { throw 'Docker daemon unavailable.' }
    & $DockerExe build --tag codebound-worker:local (Join-Path $ProjectRoot 'sandbox-worker')
    if ($LASTEXITCODE -ne 0) { throw 'Worker build failed.' }
    Write-Host 'Built codebound-worker:local. No untrusted scripts executed during build.'
    exit 0
} catch { Write-Host "BUILD FAILED: $($_.Exception.Message)"; exit 1 }
