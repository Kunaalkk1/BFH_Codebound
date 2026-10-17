param([ValidateSet('Start','Status','Stop','Reload','Check','Security')][string]$Operation = 'Start',[switch]$Must)
. "$PSScriptRoot\common.ps1"
$KitDir = Join-Path $LocalDir 'kit'
$KitStateFile = Join-Path $KitDir 'backend.json'
function Kit-Compose([string[]]$Options) {
    & $DockerExe compose --env-file (Join-Path $KitDir 'compose.env') -f (Join-Path $ProjectRoot 'docker\compose.kit.yml') -f (Join-Path $ProjectRoot 'external\codebound-kit\compose\anythingmcp.override.yml') -f (Join-Path $ProjectRoot 'docker\compose.kit-network.yml') @Options
    if ($LASTEXITCODE -ne 0) { throw 'Isolated kit Compose operation failed.' }
}
function Kit-Process {
    if (-not (Test-Path -LiteralPath $KitStateFile)) { return $null }
    $state = Get-Content -Raw -LiteralPath $KitStateFile | ConvertFrom-Json
    $p = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    if (-not $p) { return $null }
    $info = Get-CimInstance Win32_Process -Filter "ProcessId = $($state.pid)"
    $runner = Join-Path $PSScriptRoot 'serve_kit.py'
    if ($p.StartTime.ToUniversalTime().Ticks.ToString() -ne $state.started -or -not $info.CommandLine.Contains($runner) -or -not $info.CommandLine.Contains($state.instance)) { throw 'Kit process ownership could not be verified.' }
    return @{ process=$p; state=$state }
}
function Stop-KitApi {
    $owned=Kit-Process
    if ($owned) {
        New-Item -ItemType File -Path (Join-Path $KitDir "stop-$($owned.state.instance)") -Force | Out-Null
        if (-not $owned.process.WaitForExit(20000)) { throw 'Kit API graceful stop timed out.' }
        Remove-Item -LiteralPath $KitStateFile
    }
}
Push-Location $ProjectRoot
try {
    $env:CODEBOUND_STATE_DIR = Join-Path $KitDir 'state'
    $env:CODEBOUND_MOCKS_ADMIN='http://127.0.0.1:14180'
    $env:CODEBOUND_OUTSIDE_ADMIN='http://127.0.0.1:14181'
    if ($Operation -eq 'Reload') { Stop-KitApi; $Operation='Start' }
    if ($Operation -eq 'Start' -and -not (Docker-Ready)) {
        $desktop=Find-Executable 'Docker Desktop.exe' @("$env:ProgramFiles\Docker\Docker\Docker Desktop.exe","$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe")
        if (-not $desktop) { throw 'Install/start Docker Desktop with its Linux engine.' }
        if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) { Start-Process -FilePath $desktop -WindowStyle Hidden }
        $deadline=[DateTime]::UtcNow.AddSeconds(120)
        while (-not (Docker-Ready)) {
            if ([DateTime]::UtcNow -gt $deadline) { throw 'Docker readiness timed out after 120 seconds.' }
            Start-Sleep -Seconds 2
        }
    }
    if (-not (Docker-Ready)) { throw 'Start Docker Desktop; the isolated kit requires its Linux daemon.' }
    if ($Operation -eq 'Stop') {
        Stop-KitApi
        Kit-Compose @('stop','--timeout','20','app','postgres')
        & $DockerExe compose -f external/codebound-kit/compose/mocks.yml -f docker/compose.kit-mocks.yml stop --timeout 20
        if ($LASTEXITCODE -ne 0) { throw 'Isolated mock stop failed.' }
        Write-Host 'Kit stopped; all databases/images/volumes retained. Other mock installations, old amcp, Ollama and Claude left intact.'
        exit 0
    }
    if ($Operation -eq 'Check') {
        $checkOptions=@()
        if ($Must) { $checkOptions+=@('--only','must') }
        $reportFile=if ($Must) {'.local/kit/official-must-report.json'} else {'.local/kit/official-report.json'}
        node external/codebound-kit/harness/cli.mjs check --team .local/kit/team.json --json $reportFile @checkOptions
        exit $LASTEXITCODE
    }
    if ($Operation -eq 'Security') {
        & $PythonExe -m codebound.kit.attacks
        exit $LASTEXITCODE
    }
    if ($Operation -eq 'Start') {
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'external\codebound-kit\package.json'))) { throw 'Run .\setup.ps1 to download the official kit and install dependencies; see README.' }
        $nodeVersion=& node --version
        if ($LASTEXITCODE -ne 0 -or [int]($nodeVersion.TrimStart('v').Split('.')[0]) -lt 20) { throw 'Node 20+ required by the official kit.' }
        Write-Host "Node $nodeVersion"
        try { $null=Read-JsonUrl 'http://127.0.0.1:11434/api/tags' }
        catch {
            $ollama=Find-Executable 'ollama.exe' @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")
            if (-not $ollama) { throw 'Install native Ollama and configure the required local model.' }
            Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
            $deadline=[DateTime]::UtcNow.AddSeconds(30)
            while ($true) {
                try { $null=Read-JsonUrl 'http://127.0.0.1:11434/api/tags';break } catch {}
                if ([DateTime]::UtcNow -gt $deadline) { throw 'Ollama readiness timed out after 30 seconds.' }
                Start-Sleep -Seconds 1
            }
        }
        try { $null = Read-JsonUrl 'http://127.0.0.1:14180/__health'; $null = Read-JsonUrl 'http://127.0.0.1:14181/__health' }
        catch {
            & $DockerExe compose -f external/codebound-kit/compose/mocks.yml -f docker/compose.kit-mocks.yml up -d --build
            if ($LASTEXITCODE -ne 0) { throw 'Official mock startup failed.' }
        }
        $mockDeadline=[DateTime]::UtcNow.AddSeconds(30)
        while ($true) {
            try { $null=Read-JsonUrl 'http://127.0.0.1:14180/__health';$null=Read-JsonUrl 'http://127.0.0.1:14181/__health';break } catch {}
            if ([DateTime]::UtcNow -gt $mockDeadline) { throw 'Isolated official mock readiness timed out.' }
            Start-Sleep -Milliseconds 300
        }
        Kit-Compose @('up','-d','--wait','--wait-timeout','180')
        if (-not (Test-Path -LiteralPath (Join-Path $KitDir 'state\state.json'))) { throw 'Run launch/setup-kit.ps1 against the isolated empty database first; never use old amcp.' }
        $owned = Kit-Process
        if (-not $owned) {
            try { $null = Read-JsonUrl 'http://127.0.0.1:8001/health'; throw 'Port 8001 is managed elsewhere; leaving it untouched.' }
            catch { if ($_.Exception.Message -like 'Port 8001*') { throw } }
            $instance = [guid]::NewGuid().ToString('N')
            $runner = Join-Path $PSScriptRoot 'serve_kit.py'
            $p = Start-Process -FilePath $PythonExe -ArgumentList @('"'+$runner+'"',$instance) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $KitDir 'backend.out.log') -RedirectStandardError (Join-Path $KitDir 'backend.err.log')
            @{pid=$p.Id; started=$p.StartTime.ToUniversalTime().Ticks.ToString(); instance=$instance} | ConvertTo-Json | Set-Content -LiteralPath $KitStateFile -Encoding UTF8
            $deadline = [DateTime]::UtcNow.AddSeconds(40)
            while ($true) {
                if ($p.HasExited) { throw 'Kit API exited; inspect .local/kit/backend.err.log.' }
                try { $health = Read-JsonUrl 'http://127.0.0.1:8001/health'; if ($health.status -eq 'ok') { break } } catch {}
                if ([DateTime]::UtcNow -gt $deadline) { throw 'Kit API readiness timed out.' }
                Start-Sleep -Milliseconds 300
            }
        }
    }
    $health = Read-JsonUrl 'http://127.0.0.1:8001/health'
    if ($health.environment -ne 'official-kit') { throw 'Unexpected service on port 8001.' }
    $gateway = Read-JsonUrl 'http://127.0.0.1:4200/health'
    if ($gateway.status -ne 'ok') { throw 'Kit gateway unhealthy.' }
    foreach ($port in @(14101,14102,14103,14104,14199)) { $null = Read-JsonUrl "http://127.0.0.1:$port/health" }
    & $PythonExe -m codebound.kit.check
    if ($LASTEXITCODE -ne 0) { throw 'Kit authenticated discovery failed.' }
    & $PythonExe -m codebound.llm.check
    if ($LASTEXITCODE -ne 0) { throw 'Start native Ollama and configure its local model; see README.' }
    $bridgePython=Join-Path $ProjectRoot '.venv-claude\Scripts\python.exe'
    if (Test-Path -LiteralPath $bridgePython) {
        & $bridgePython (Join-Path $ProjectRoot 'integrations\claude_desktop\kit_bridge.py') --check
        if ($LASTEXITCODE -ne 0) { throw 'Prepare the separate kit DPAPI credential; see README.' }
        . "$PSScriptRoot\claude-path.ps1"
        $claudeConfig=Get-ClaudeConfigPath
        $configured=$false
        if (Test-Path -LiteralPath $claudeConfig) {
            $entry=(Get-Content -LiteralPath $claudeConfig -Raw | ConvertFrom-Json).mcpServers.'codebound-kit-alpha'
            $configured=$entry -and $entry.command -eq $bridgePython -and $entry.args.Count -eq 2 -and $entry.args[1] -eq (Join-Path $ProjectRoot 'integrations\claude_desktop\kit_bridge.py')
        }
        Write-Host "Claude kit server configured: $([bool]$configured) (application invocation requires reopening Claude)"
    } else { Write-Host 'Claude kit bridge environment missing; see README. Service APIs are available.' }
    Write-Host 'READY: Official mocks (host) 14101-14104; request logs 14180/14181; outside target 14199'
    Write-Host 'AnythingMCP http://localhost:3100 | MCP/API :4200 | Codebound http://127.0.0.1:8001/docs'
    Write-Host 'Existing Claude configuration is preserved; kit bridge setup is explicit (README).'
    exit 0
} catch { Write-Host "KIT FAILED: $($_.Exception.Message)"; exit 1 }
finally { Pop-Location }
