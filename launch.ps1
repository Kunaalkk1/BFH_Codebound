param([switch]$Status,[switch]$Stop,[switch]$Test,[switch]$Benchmark,[switch]$Demo)
$ErrorActionPreference='Stop'
if (@($Status,$Stop,$Test,$Benchmark | Where-Object { $_ }).Count -gt 1) { throw 'Choose one operation.' }
if (-not $Demo) {
    $options=@()
    if ($Status) {$options+='--status'} elseif ($Stop) {$options+='--stop'} elseif ($Test) {$options+='--test'} elseif ($Benchmark) {$options+='--benchmark'}
    & python "$PSScriptRoot\launch.py" @options
    exit $LASTEXITCODE
}
if ($Benchmark) { & "$PSScriptRoot\benchmark.ps1"; exit $LASTEXITCODE }
if ($Test) { & powershell.exe -NoProfile -File "$PSScriptRoot\launch\test.ps1"; exit $LASTEXITCODE }
$operation = if ($Status) {'Status'} elseif ($Stop) {'Stop'} else {'Start'}
& powershell.exe -NoProfile -File "$PSScriptRoot\launch\kit.ps1" -Operation $operation
exit $LASTEXITCODE
