param([string]$DestinationRoot = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$dependency = (Get-Content -Raw (Join-Path $PSScriptRoot '..\dependencies.json') | ConvertFrom-Json).'codebound-kit'
$destination = Join-Path $DestinationRoot 'external\codebound-kit'
if ((Test-Path -LiteralPath $destination) -and @(Get-ChildItem -LiteralPath $destination -Force).Count -gt 0) {
    $actual = & git -C $destination rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $actual -ne $dependency.commit) {
        throw "Existing kit at $destination differs from dependencies.json. Preserving it; use a fresh checkout or review it manually."
    }
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    & git clone --no-checkout $dependency.url $destination
    if ($LASTEXITCODE -ne 0) { throw 'Official kit download failed. Check GitHub access and retry.' }
    & git -C $destination checkout --detach $dependency.commit
    if ($LASTEXITCODE -ne 0) { throw 'Could not check out the tested official kit commit.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $destination 'harness\setup.mjs'))) {
    throw "Official kit file is missing: $destination\harness\setup.mjs. Preserve local changes and repair the checkout."
}
Write-Host "Official Codebound kit ready at $($dependency.commit)"
