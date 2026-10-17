. "$PSScriptRoot\common.ps1"
Push-Location $ProjectRoot
try {
    if (-not (Docker-Ready)) { throw 'Start Docker Desktop first.' }
    & "$PSScriptRoot\fetch-kit.ps1"
    & $PythonExe -m codebound.kit.prepare
    if ($LASTEXITCODE -ne 0) { throw 'Kit configuration preparation failed.' }
    & $DockerExe compose -f external/codebound-kit/compose/mocks.yml -f docker/compose.kit-mocks.yml up -d --build
    if ($LASTEXITCODE -ne 0) { throw 'Official mock startup failed.' }
    & $DockerExe compose --env-file .local/kit/compose.env -f docker/compose.kit.yml -f external/codebound-kit/compose/anythingmcp.override.yml -f docker/compose.kit-network.yml up -d --wait --wait-timeout 180
    if ($LASTEXITCODE -ne 0) { throw 'Isolated AnythingMCP startup failed.' }
    $env:CODEBOUND_STATE_DIR = Join-Path $LocalDir 'kit\state'
    $env:CODEBOUND_MOCKS_ADMIN='http://127.0.0.1:14180'
    $env:CODEBOUND_OUTSIDE_ADMIN='http://127.0.0.1:14181'
    if (-not (Test-Path .local/kit/state/state.json)) {
        $count = & $DockerExe exec codebound-kit-postgres psql -U amcp -d anythingmcp -At -c 'SELECT count(*) FROM users'
        if ($LASTEXITCODE -ne 0 -or $count.Trim() -ne '0') { throw 'Isolated database is not empty; setup refused. Do not reset or delete a volume.' }
        $tenants = Read-JsonUrl 'http://127.0.0.1:14180/__tenants'
        if (@($tenants.PSObject.Properties).Count -gt 0) { throw 'Official mocks already have credentials for another setup; refusing to replace them.' }
        & node launch/setup-kit.mjs
        if ($LASTEXITCODE -ne 0) { throw 'Official setup failed; preserve partial state for recovery.' }
    }
    & $DockerExe build --tag codebound-worker:kit sandbox-worker
    if ($LASTEXITCODE -ne 0) { throw 'Kit worker image build failed.' }
    & $PythonExe -m codebound.kit.configure
    if ($LASTEXITCODE -ne 0) { throw 'Kit tool registration failed.' }
    Write-Host 'Kit provisioned. Run .\launch.ps1 -Demo; Claude configuration remains untouched.'
    exit 0
} catch {
    Write-Host "KIT SETUP FAILED: $($_.Exception.Message)"
    Write-Host $_.InvocationInfo.PositionMessage
    Write-Host $_.ScriptStackTrace
    exit 1
}
finally { Pop-Location }
