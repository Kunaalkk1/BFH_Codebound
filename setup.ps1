param([switch]$DependenciesOnly, [switch]$Demo, [string]$Model = 'qwen2.5-coder:3b')
$ErrorActionPreference = 'Stop'
if (-not $Demo) {
    $operation=if ($DependenciesOnly) {'--dependencies-only'} else {'--setup'}
    & python "$PSScriptRoot\launch.py" $operation
    exit $LASTEXITCODE
}
Push-Location $PSScriptRoot
$setupLog = $null
try {
    New-Item -ItemType Directory -Force -Path .local | Out-Null
    $setupLog = Join-Path $PSScriptRoot ('.local\setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
    Start-Transcript -Path $setupLog | Out-Null
    foreach ($relative in @('dependencies.json','pyproject.toml','launch\fetch-kit.ps1','launch\setup-kit.ps1','integrations\claude_desktop\requirements-dev.txt','sandbox-worker\Dockerfile','sandbox-worker\runner.py','sandbox-worker\brokered.py','docker\compose.kit.yml','docker\compose.kit-mocks.yml','docker\compose.kit-network.yml')) {
        $required = Join-Path $PSScriptRoot $relative
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required repository file is missing: $required. Pull dev_sanchit or clone it into a new folder." }
    }
    foreach ($command in @('git','python','node')) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Install $command and reopen PowerShell first; see README.md." }
    }
    & python -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ required.' }
    $nodeVersion = & node --version
    if ($LASTEXITCODE -ne 0 -or [int]($nodeVersion.TrimStart('v').Split('.')[0]) -lt 20) { throw 'Node 20+ required.' }
    & "$PSScriptRoot\launch\fetch-kit.ps1"
    foreach ($environment in @('.venv','.venv-claude')) {
        $interpreter = Join-Path $PSScriptRoot "$environment\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $interpreter)) {
            & python -m venv $environment
            if ($LASTEXITCODE -ne 0) { throw "Could not create $environment." }
        }
    }
    & .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
    & .\.venv-claude\Scripts\python.exe -m pip install -r integrations/claude_desktop/requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Claude bridge dependency installation failed.' }
    if ($DependenciesOnly) { Write-Host 'Source and Python dependencies ready. Run .\setup.ps1 -Demo for provisioning.'; exit 0 }
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { throw 'Install native Ollama and reopen PowerShell; see README.md.' }
    & ollama pull $Model
    if ($LASTEXITCODE -ne 0) { throw 'Model download failed. Start Ollama and retry.' }
    New-Item -ItemType Directory -Force -Path .local | Out-Null
    if (-not (Test-Path .local/ollama.json)) {
        @{model=$Model} | ConvertTo-Json | Set-Content .local/ollama.json -Encoding UTF8
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\launch\setup-kit.ps1"
    if ($LASTEXITCODE -ne 0) { throw 'Kit provisioning failed; existing state was preserved.' }
    if (-not (Test-Path .local/kit/claude-alice.dpapi)) {
        & .\.venv-claude\Scripts\python.exe integrations/claude_desktop/kit_bridge.py --prepare
        if ($LASTEXITCODE -ne 0) { throw 'Local bridge credential preparation failed.' }
    }
    Write-Host 'Setup complete. Run .\launch.ps1 -Demo. To add the Claude server, run .\launch\configure-kit-claude.ps1 explicitly.'
    exit 0
} catch {
    Write-Host "SETUP FAILED: $($_.Exception.Message)"
    Write-Host $_.InvocationInfo.PositionMessage
    Write-Host $_.ScriptStackTrace
    if ($setupLog) { Write-Host "Setup log: $setupLog" }
    exit 1
}
finally { if ($setupLog) { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null }; Pop-Location }
