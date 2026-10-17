param([switch]$Status, [switch]$Stop, [switch]$Test, [switch]$Benchmark)
$ErrorActionPreference = 'Stop'
if (@($Status,$Stop,$Test,$Benchmark | Where-Object { $_ }).Count -gt 1) { throw 'Choose one operation.' }
. "$PSScriptRoot\launch\common.ps1"
function Invoke-Helper([string]$Name, [string[]]$Options = @()) {
    & powershell.exe -NoProfile -File (Join-Path $PSScriptRoot "launch\$Name.ps1") @Options
    if ($LASTEXITCODE -ne 0) { throw "$Name failed (exit $LASTEXITCODE)." }
}
try {
    if ($Stop) { Invoke-Helper 'stop'; exit 0 }
    if ($Test) { Invoke-Helper 'test'; exit 0 }
    if ($Benchmark) { & "$PSScriptRoot\benchmark.ps1"; exit $LASTEXITCODE }
    if ($Status) { Invoke-Helper 'status' }
    else {
        if (-not (Test-Path -LiteralPath $PythonExe)) { throw 'Create .venv and install .[dev]; see README.md.' }
        if (-not (Docker-Ready)) {
            $desktop = Find-Executable 'Docker Desktop.exe' @("$env:ProgramFiles\Docker\Docker\Docker Desktop.exe", "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe")
            if (-not $desktop) { throw 'Install/start Docker Desktop with WSL 2, then retry.' }
            if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) { Start-Process -FilePath $desktop -WindowStyle Hidden }
            $deadline = [DateTime]::UtcNow.AddSeconds(120)
            while (-not (Docker-Ready)) {
                if ([DateTime]::UtcNow -gt $deadline) { throw 'Docker did not become ready within 120 seconds; inspect Docker Desktop.' }
                Start-Sleep -Seconds 2
            }
        }
        try { $null = Read-JsonUrl 'http://127.0.0.1:11434/api/tags' }
        catch {
            $ollama = Find-Executable 'ollama.exe' @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")
            if (-not $ollama) { throw 'Install Ollama, configure a local OLLAMA_MODEL, then retry.' }
            Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
            $deadline = [DateTime]::UtcNow.AddSeconds(30)
            while ($true) {
                try { $null = Read-JsonUrl 'http://127.0.0.1:11434/api/tags'; break } catch {}
                if ([DateTime]::UtcNow -gt $deadline) { throw 'Ollama did not become ready within 30 seconds.' }
                Start-Sleep -Seconds 1
            }
        }
        Push-Location $ProjectRoot
        try { & $PythonExe -m codebound.llm.check; if ($LASTEXITCODE -ne 0) { throw "Pull the configured local model with ollama pull <model>; set OLLAMA_MODEL or .local/ollama.json. No automatic download." } }
        finally { Pop-Location }
        Invoke-Helper 'start' @('-Mcp','-Ollama')
    }
    if ($Status) {
        Assert-AnythingMcp
        Push-Location $ProjectRoot
        try {
            & $PythonExe -m codebound.llm.check
            if ($LASTEXITCODE -ne 0) { throw 'Ollama/model unavailable.' }
            & $PythonExe -m codebound.worker_admin check
            if ($LASTEXITCODE -ne 0) { throw 'Sandbox runtime unavailable.' }
            & $PythonExe -m codebound.check_mcp
            if ($LASTEXITCODE -ne 0) { throw 'Authenticated MCP discovery failed.' }
        } finally { Pop-Location }
    }
    # Read-only checks: never merge config, replace credentials or close Claude.
    $bridgePython = Join-Path $ProjectRoot '.venv-claude\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $bridgePython)) { throw 'Claude bridge environment missing; see README.md.' }
    . "$PSScriptRoot\launch\claude-path.ps1"
    $configPath = Get-ClaudeConfigPath
    & $bridgePython (Join-Path $ProjectRoot 'integrations\claude_desktop\launch_check.py') $configPath
    if ($LASTEXITCODE -ne 0) { throw 'Claude bridge/configuration is not ready; see README.md.' }
    $health = Backend-Health
    if ($health.status -ne 'ok' -or $health.sandbox_mode -ne 'local_docker') { throw 'Codebound local_docker API is not healthy.' }
    Write-Host 'READY: AnythingMCP http://localhost:3000 | Codebound http://127.0.0.1:8000/docs'
    Write-Host 'Claude bridge verified; open Claude Desktop to run the demo. Desktop invocation is a separate manual check.'
    exit 0
} catch { Write-Host "LAUNCH FAILED: $($_.Exception.Message)"; exit 1 }
