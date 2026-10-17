param([ValidateSet('mock','local_docker')][string]$Mode = 'mock', [switch]$Mcp, [switch]$Ollama)
if ($Ollama) {
    & "$PSScriptRoot\start.ps1" -Mcp -Ollama
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & "$PSScriptRoot\test.ps1" -Ollama
    exit $LASTEXITCODE
}
if ($Mcp) {
    & "$PSScriptRoot\start.ps1" -Mcp
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & "$PSScriptRoot\integrate.ps1" -Demo
    exit $LASTEXITCODE
}
. "$PSScriptRoot\common.ps1"
$scriptId = $null
$failed = $false
try {
    $health = Backend-Health
    if ($health.status -ne 'ok' -or $health.sandbox_mode -ne $Mode) { throw "Healthy $Mode backend required. Run start.ps1 -Development -Mode $Mode." }
    $tokens = Get-DemoTokens
    $headers = @{Authorization="Bearer $($tokens.a)"}
    $base = 'http://127.0.0.1:8000'
    $datasets = Invoke-RestMethod "$base/datasets" -Headers $headers
    if ($datasets.datasets.id -notcontains 'railway-demo') { throw 'Authorized railway-demo dataset unavailable.' }
    if ($Mode -eq 'mock') { Write-Host 'MOCK EXECUTION - no submitted Python runs' }
    else { Write-Host 'REAL PYTHON - disposable restricted Docker worker; shared-kernel limitations apply' }
    Write-Host 'Dataset: railway-demo; tenant A; September 2026 travel dates'
    $sourceCode = '# codebound-mock:september_totals'
    if ($Mode -eq 'local_docker') { $sourceCode = [IO.File]::ReadAllText((Join-Path $ProjectRoot 'scripts\september.py')) }
    $body = @{name='Disposable launch demo';source_code=$sourceCode} | ConvertTo-Json
    $script = Invoke-RestMethod "$base/scripts" -Method Post -Headers $headers -ContentType application/json -Body $body
    $scriptId = $script.script_id
    Write-Host "Script created: $scriptId"
    $request = @{parameters=@{year=2026};reference=@{kind='dataset';id='railway-demo'}} | ConvertTo-Json -Depth 4
    $response = Invoke-WebRequest "$base/scripts/$scriptId/execute" -UseBasicParsing -Method Post -Headers $headers -ContentType application/json -Body $request
    $outcome = $response.Content | ConvertFrom-Json
    if ($outcome.status -ne 'succeeded') { throw 'Mock execution did not succeed.' }
    Write-Host $response.Content
    Write-Host "Tickets: $($outcome.result.ticket_count)"
    Write-Host ('Revenue: {0:F2} EUR ({1} cents)' -f ($outcome.result.total_revenue_cents / 100), $outcome.result.total_revenue_cents)
    Write-Host "Response size: $([Text.Encoding]::UTF8.GetByteCount($response.Content)) bytes"
} catch { Write-Host "DEMO FAILED: $($_.Exception.Message)"; $failed = $true }
finally {
    if ($scriptId) {
        try { Invoke-RestMethod "$base/scripts/$scriptId" -Method Delete -Headers $headers | Out-Null; Write-Host 'Disposable script deleted; audit retained.' }
        catch { Write-Host 'Demo script cleanup failed.'; $failed = $true }
    }
}
if ($failed) { exit 1 }; exit 0
