param([switch]$Status,[switch]$Stop,[switch]$Test,[switch]$Benchmark)
$ErrorActionPreference='Stop'
if (@($Status,$Stop,$Test,$Benchmark | Where-Object { $_ }).Count -gt 1) { throw 'Choose one operation.' }
if ($Benchmark) { & "$PSScriptRoot\benchmark.ps1"; exit $LASTEXITCODE }
if ($Test) { & powershell.exe -NoProfile -File "$PSScriptRoot\launch\test.ps1"; exit $LASTEXITCODE }
$operation = if ($Status) {'Status'} elseif ($Stop) {'Stop'} else {'Start'}
& powershell.exe -NoProfile -File "$PSScriptRoot\launch\kit.ps1" -Operation $operation
exit $LASTEXITCODE
