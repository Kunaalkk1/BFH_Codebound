param([ValidateRange(1,10)][int]$Samples = 3, [switch]$Fresh)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\launch\common.ps1"
Push-Location $ProjectRoot
try {
    $options = @('--samples', "$Samples")
    if ($Fresh) { $options += '--fresh' }
    & $PythonExe -m codebound.benchmark @options
    exit $LASTEXITCODE
} finally { Pop-Location }
