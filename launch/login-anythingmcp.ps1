# Interactive, local-only admin login for provisioning. Never send credentials in chat.
. "$PSScriptRoot\common.ps1"
try {
    $credential = Get-Credential -Message 'Existing local AnythingMCP administrator (localhost:4000)'
    if (-not $credential) { throw 'Login cancelled.' }
    $body = @{email=$credential.UserName;password=$credential.GetNetworkCredential().Password} | ConvertTo-Json
    $response = Invoke-RestMethod 'http://127.0.0.1:4000/api/auth/login' -Method Post -ContentType application/json -Body $body
    if (-not $response.accessToken) { throw 'Login did not return an access token.' }
    New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null
    [IO.File]::WriteAllText((Join-Path $LocalDir 'anythingmcp-admin-token.txt'), $response.accessToken)
    Write-Host 'Admin session saved locally in gitignored .local/anythingmcp-admin-token.txt. No password saved or token printed.'
    exit 0
} catch { Write-Host 'AnythingMCP login failed or cancelled. No credentials printed.'; exit 1 }
