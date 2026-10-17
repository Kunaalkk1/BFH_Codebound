$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LocalDir = Join-Path $ProjectRoot '.local'
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$ComposeFile = Join-Path $ProjectRoot 'docker\compose.local.yml'
$StateFile = Join-Path $LocalDir 'backend.json'
$TokenFile = Join-Path $LocalDir 'demo-tokens.json'
$OllamaConfig = Join-Path $LocalDir 'ollama.json'
if (-not $env:OLLAMA_MODEL -and (Test-Path -LiteralPath $OllamaConfig)) {
    $env:OLLAMA_MODEL = (Get-Content -LiteralPath $OllamaConfig -Raw | ConvertFrom-Json).model
}

function Find-Executable([string]$Name, [string[]]$Candidates = @()) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($candidate in $Candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Get-OllamaExecutable {
    if ($env:CODEBOUND_OLLAMA_EXE) {
        if (-not (Test-Path -LiteralPath $env:CODEBOUND_OLLAMA_EXE -PathType Leaf)) {
            throw 'CODEBOUND_OLLAMA_EXE does not point to an existing ollama.exe.'
        }
        return $env:CODEBOUND_OLLAMA_EXE
    }
    $executable = Find-Executable 'ollama.exe' @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        "$env:ProgramFiles\Ollama\ollama.exe")
    if (-not $executable) {
        throw 'Ollama CLI not found on PATH or in its standard installation folders. Set CODEBOUND_OLLAMA_EXE to the full path of ollama.exe for a custom installation.'
    }
    return $executable
}

function Start-OllamaIfNeeded([string]$Executable) {
    try { $null = Read-JsonUrl 'http://127.0.0.1:11434/api/version'; return }
    catch {}
    Start-Process -FilePath $Executable -ArgumentList 'serve' -WindowStyle Hidden
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ($true) {
        try { $null = Read-JsonUrl 'http://127.0.0.1:11434/api/version'; return }
        catch {}
        if ([DateTime]::UtcNow -gt $deadline) {
            throw 'Ollama was found, but its server did not become available at http://127.0.0.1:11434 within 30 seconds. Open Ollama and retry.'
        }
        Start-Sleep -Milliseconds 500
    }
}

$DockerExe = Find-Executable 'docker.exe' @(
    "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
    "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe")
if ($DockerExe) { $env:PATH = "$(Split-Path -Parent $DockerExe);$env:PATH" }

function Docker-Ready {
    if (-not $DockerExe) { return $false }
    & $DockerExe info --format '{{.ServerVersion}}' *> $null
    return ($LASTEXITCODE -eq 0)
}

function Invoke-Compose([string[]]$Arguments) {
    if (-not (Test-Path (Join-Path $ProjectRoot 'anythingmcp\docker-compose.quickstart.yml'))) {
        throw 'AnythingMCP NOT INSTALLED. See README.md; no automatic clone.'
    }
    & $DockerExe compose -f $ComposeFile @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed ($LASTEXITCODE)" }
}

function Read-JsonUrl([string]$Url) {
    # Bypass user proxy settings only for these explicit loopback probes.
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.Proxy = $null
    $request.Timeout = 3000
    $request.ReadWriteTimeout = 3000
    $response = $request.GetResponse()
    $reader = New-Object IO.StreamReader($response.GetResponseStream())
    try { return ($reader.ReadToEnd() | ConvertFrom-Json) }
    finally { $reader.Dispose(); $response.Dispose() }
}

function Backend-Health {
    try { return Read-JsonUrl 'http://127.0.0.1:8000/health' } catch { return $null }
}

function Get-LocalHttpStatus([string]$Url) {
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.Proxy = $null
    $request.Timeout = 5000
    $request.ReadWriteTimeout = 5000
    try { $response = $request.GetResponse() }
    catch [Net.WebException] {
        if (-not $_.Exception.Response) { throw }
        $response = $_.Exception.Response
    }
    try { return [int]$response.StatusCode }
    finally { $response.Dispose() }
}

function Assert-AnythingMcp {
    $health = Read-JsonUrl 'http://127.0.0.1:4000/health'
    if ($health.status -ne 'ok' -or $health.details.database.status -ne 'up') {
        throw 'AnythingMCP backend/database health check failed.'
    }
    if ((Get-LocalHttpStatus 'http://127.0.0.1:3000/login') -ne 200) {
        throw 'AnythingMCP dashboard/login is not reachable.'
    }
    if ((Get-LocalHttpStatus 'http://127.0.0.1:4000/mcp') -ne 401) {
        throw 'Expected the local MCP endpoint to reject unauthenticated requests with 401.'
    }
    $info = Read-JsonUrl 'http://127.0.0.1:4000/health/server-info'
    if ($info.hasUsers) { Write-Host 'Administrator setup: account exists (interactive login not tested)' }
    else { Write-Host 'Administrator setup: REQUIRED at http://localhost:3000/login' }
}

function Read-BackendState {
    if (Test-Path -LiteralPath $StateFile) { return Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json }
    return $null
}

function Owned-Backend($State) {
    if (-not $State) { return $null }
    $process = Get-Process -Id $State.pid -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    if ($process.StartTime.ToUniversalTime().Ticks.ToString() -ne $State.started) {
        throw 'PID was reused; refusing to touch an unrelated process.'
    }
    $info = Get-CimInstance Win32_Process -Filter "ProcessId = $($State.pid)"
    $runner = Join-Path $ProjectRoot 'launch\serve.py'
    if (-not $info -or -not $info.CommandLine.Contains($runner) -or -not $info.CommandLine.Contains($State.instance)) {
        throw 'Process ownership cannot be verified; refusing to touch it.'
    }
    return $process
}

function Get-DemoTokens {
    if ($env:CODEBOUND_DEMO_TOKEN_A -and $env:CODEBOUND_DEMO_TOKEN_B) {
        return @{ a=$env:CODEBOUND_DEMO_TOKEN_A; b=$env:CODEBOUND_DEMO_TOKEN_B }
    }
    if ($env:CODEBOUND_DEMO_TOKEN_A -or $env:CODEBOUND_DEMO_TOKEN_B) { throw 'Configure both demo tokens.' }
    if (Test-Path -LiteralPath $TokenFile) { return Get-Content -LiteralPath $TokenFile -Raw | ConvertFrom-Json }
    throw 'No demo credentials. Run start.ps1 -Development or configure both token environment variables.'
}

function New-DemoTokens {
    New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null
    if ((Test-Path -LiteralPath $TokenFile) -or $env:CODEBOUND_DEMO_TOKEN_A -or $env:CODEBOUND_DEMO_TOKEN_B) { return }
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $tokens = @{}
        foreach ($key in @('a','b')) {
            $bytes = New-Object byte[] 32
            $rng.GetBytes($bytes)
            $tokens[$key] = -join ($bytes | ForEach-Object { $_.ToString('x2') })
        }
        $tokens | ConvertTo-Json | Set-Content -LiteralPath $TokenFile -Encoding UTF8
    } finally { $rng.Dispose() }
}

