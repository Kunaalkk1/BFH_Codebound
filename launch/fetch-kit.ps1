param([string]$DestinationRoot = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$dependency = (Get-Content -Raw (Join-Path $PSScriptRoot '..\dependencies.json') | ConvertFrom-Json).'codebound-kit'
$destination = Join-Path $DestinationRoot 'external\codebound-kit'
if (Test-Path -LiteralPath $destination) {
    $actual = & git -C $destination rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $actual -ne $dependency.commit) {
        throw 'Existing external/codebound-kit differs from dependencies.json. Preserving it; use a fresh checkout or review it manually.'
    }
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    & git clone --no-checkout $dependency.url $destination
    if ($LASTEXITCODE -ne 0) { throw 'Official kit download failed. Check GitHub access and retry.' }
    & git -C $destination checkout --detach $dependency.commit
    if ($LASTEXITCODE -ne 0) { throw 'Could not check out the tested official kit commit.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $destination 'harness\setup.mjs'))) {
    throw 'Official kit checkout is incomplete. Preserve local changes and repair the checkout.'
}
Write-Host "Official Codebound kit ready at $($dependency.commit)"
