# Check-only: no installation, configuration writes, model pulls or service starts.
. "$PSScriptRoot\common.ps1"
$failures = 0
function Check([string]$Label, [bool]$Passed, [string]$HelpText) {
    if ($Passed) { Write-Host "$Label : OK" }
    else { Write-Host "$Label : MISSING/UNAVAILABLE - $HelpText"; $script:failures++ }
}
try {
    Check 'Windows' ($env:OS -eq 'Windows_NT') 'Windows PowerShell is required.'
    $wsl = Find-Executable 'wsl.exe'
    $wslOutput = ''
    if ($wsl) { $wslOutput = ((& $wsl --list --verbose 2>&1) -join "`n") -replace "`0",'' }
    Check 'WSL 2' ([bool]($wslOutput -match '(?m)\s2\s*$')) 'https://learn.microsoft.com/windows/wsl/install'
    Check 'Docker Desktop CLI' ([bool]$DockerExe) 'https://docs.docker.com/desktop/setup/install/windows-install/'
    Check 'Docker daemon' (Docker-Ready) 'Open Docker Desktop; enable its WSL 2 backend.'
    $compose = $false
    if ($DockerExe) { & $DockerExe compose version; $compose = $LASTEXITCODE -eq 0 }
    Check 'Docker Compose' $compose 'Install/update Docker Desktop; include requires Compose 2.20.3+.'
    Check 'Git' ([bool](Find-Executable 'git.exe')) 'https://git-scm.com/download/win'
    Check 'Python' ([bool](Find-Executable 'python.exe')) 'https://www.python.org/downloads/windows/'
    Check 'Project virtual environment' (Test-Path -LiteralPath $PythonExe) 'From project root: python -m venv .venv'
    if (Test-Path -LiteralPath $PythonExe) {
        & $PythonExe -c 'import sys, fastapi, uvicorn, pytest, httpx; assert sys.version_info >= (3,11); print(sys.version.split()[0])'
        Check 'Python 3.11+ and dependencies' ($LASTEXITCODE -eq 0) 'From project root: .\.venv\Scripts\python.exe -m pip install -e ".[dev]"'
        & $PythonExe -m pip check
        Check 'Dependency compatibility' ($LASTEXITCODE -eq 0) 'Review pip check output.'
    }
    $ollama = Find-Executable 'ollama.exe' @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")
    if ($ollama) {
        Write-Host 'Ollama: INSTALLED (independently managed)'
        Push-Location $ProjectRoot
        try { & $PythonExe -m codebound.llm.check; if ($LASTEXITCODE -ne 0) { Write-Host 'AI features unavailable; see README.md. Existing MCP commands remain usable.' } }
        finally { Pop-Location }
    }
    else { Write-Host 'Ollama: NOT INSTALLED (optional). https://ollama.com/download/windows' }
    if ((Docker-Ready) -and (Test-Path -LiteralPath $PythonExe)) {
        Push-Location $ProjectRoot
        try { & $PythonExe -m codebound.worker_admin check 2>$null; if ($LASTEXITCODE -ne 0) { Write-Host 'Local Docker worker: NOT CONFIGURED; run launch/build-worker.ps1. Mock mode remains available.' } }
        finally { Pop-Location }
    } else { Write-Host 'Local Docker worker: UNAVAILABLE; mock mode remains available.' }
    Write-Host 'MCP setup (explicit): launch/login-anythingmcp.ps1 then launch/integrate.ps1 -Configure. See README.md'
    Write-Host "Check-only completed. Required prerequisite failures: $failures"
    if ($failures) { exit 1 }; exit 0
} catch { Write-Host "SETUP CHECK FAILED: $($_.Exception.Message)"; exit 1 }

