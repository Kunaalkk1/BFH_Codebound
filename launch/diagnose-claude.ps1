param([string]$ConfigPath)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\claude-path.ps1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) { $ConfigPath = Get-ClaudeConfigPath }
$packages = @(Get-AppxPackage -Name Claude -ErrorAction SilentlyContinue)
$native = @(Get-Process claude -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -like '*\Claude.exe' })
Write-Output "Claude Desktop installed: $($packages.Count -gt 0 -or $native.Count -gt 0)"
Write-Output "Claude Desktop version: $(if ($packages.Count) {$packages[0].Version} elseif ($native.Count) {$native[0].FileVersion} else {'unknown'})"
Write-Output "Native application running: $($native.Count -gt 0)"
Write-Output "Configuration path: $ConfigPath"
$valid = $false; $entry = $null
if (Test-Path -LiteralPath $ConfigPath) {
    try {
        $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        $valid = $config -is [pscustomobject]
        $entry = $config.mcpServers.'codebound-tenant-a'
    } catch { }
}
Write-Output "Configuration file found: $(Test-Path -LiteralPath $ConfigPath)"
Write-Output "Configuration valid: $valid"
Write-Output "Codebound server entry present: $($null -ne $entry)"
$python = Join-Path $ProjectRoot '.venv-claude\Scripts\python.exe'
$bridge = Join-Path $ProjectRoot 'integrations\claude_desktop\bridge.py'
if ($entry) {
    $python = $entry.command
    if ($entry.args.Count -ge 2) { $bridge = $entry.args[1] }
}
Write-Output "Python executable exists: $(Test-Path -LiteralPath $python -PathType Leaf)"
Write-Output "Bridge script exists: $(Test-Path -LiteralPath $bridge -PathType Leaf)"
try {
    $health = (& $python (Join-Path $ProjectRoot 'integrations\claude_desktop\diagnose.py') 2>$null) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Health command failed' }
    Write-Output "Bridge dependencies available: $($health.dependencies)"
    Write-Output "DPAPI credential loading: $($health.dpapi)"
    Write-Output "AnythingMCP reachable: $($health.reachable)"
    Write-Output "Authenticated MCP discovery (10 tools): $($health.discovery)"
} catch { Write-Output 'Bridge health: unavailable; interpreter/import failure (details suppressed)' }
try {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $claudeIds = @($native | ForEach-Object { $_.Id })
    $observed = @($processes | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($bridge) -and $_.ParentProcessId -in $claudeIds })
    Write-Output "Claude bridge process observed: $($observed.Count -gt 0)"
} catch { Write-Output 'Claude bridge process observed: unknown (process metadata access denied)' }
$logDir = Join-Path (Split-Path -Parent $ConfigPath) 'logs'
Write-Output "Application log directory: $logDir"
$logs = @(Get-ChildItem -LiteralPath $logDir -File -Filter '*mcp*.log' -ErrorAction SilentlyContinue)
Write-Output "MCP log files found: $($logs.Count)"
# Never echo raw log lines: app logs may include headers, tool inputs or outputs.
foreach ($log in $logs) {
    $tail = Get-Content -LiteralPath $log.FullName -Tail 200 -ErrorAction SilentlyContinue
    foreach ($pattern in @('ENOENT','ModuleNotFoundError','startup failed','authentication failed','timed out','connection refused')) {
        if ($tail -match [regex]::Escape($pattern)) { Write-Output "Sanitized recent diagnostic category: $pattern" }
    }
}
Write-Output 'Independent bridge health does not verify Claude connectivity or a real Claude tool invocation.'
Write-Output 'Application verification: PENDING until Developer settings/tools and a real call with matching audit are observed.'
