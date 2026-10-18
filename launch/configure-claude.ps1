param([string]$ConfigPath, [switch]$Preview)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\claude-path.ps1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Bridge = Join-Path $ProjectRoot 'integrations\claude_desktop\bridge.py'
$Merger = Join-Path $ProjectRoot 'integrations\claude_desktop\configure.py'
$PythonCandidate = Join-Path $ProjectRoot '.venv-claude\Scripts\python.exe'
try {
    if (-not $ConfigPath) { $ConfigPath = Get-ClaudeConfigPath }
    Write-Host "Claude configuration target: $ConfigPath"
    if (-not (Test-Path -LiteralPath $PythonCandidate -PathType Leaf)) { throw 'Bridge virtual environment missing.' }
    $Python = (& $PythonCandidate -c 'import sys; print(sys.executable)')
    if ($LASTEXITCODE -ne 0 -or -not [IO.Path]::IsPathRooted($Python)) { throw 'Cannot resolve bridge Python.' }
    $Python = [IO.Path]::GetFullPath($Python.Trim())
    & $Python -c 'import win32crypt, httpx2; from mcp import Client; from mcp.server import Server'
    if ($LASTEXITCODE -ne 0) { throw 'Pinned bridge dependencies missing.' }
    $ConfigPath = [IO.Path]::GetFullPath($ConfigPath)
    & $Python $Merger --config $ConfigPath --python $Python --preview
    if ($LASTEXITCODE -ne 0) { throw 'Existing JSON cannot safely be merged.' }
    if ($Preview) { exit 0 }
    & $Python $Bridge --prepare-credentials
    if ($LASTEXITCODE -ne 0) { throw 'Credential protection failed; Claude config unchanged.' }
    & $Python $Bridge --check
    if ($LASTEXITCODE -ne 0) { throw 'Bridge preflight failed; Claude config unchanged.' }
    & $Python $Merger --config $ConfigPath --python $Python
    if ($LASTEXITCODE -ne 0) { throw 'Claude JSON merge failed.' }
    Write-Host 'No credential is in Claude config, arguments or environment entries.'
    Write-Host 'Quit Claude Desktop completely, reopen it, and verify a real tool invocation in the app.'
} catch {
    Write-Host 'Claude configuration failed. See README.md; no credentials displayed.'
    exit 1
}

