param([switch]$Configure,[switch]$Demo,[switch]$Ollama)
if ($Configure) { & "$PSScriptRoot\setup-kit.ps1"; exit $LASTEXITCODE }
if ($Demo) { & "$PSScriptRoot\kit.ps1" -Operation Check; exit $LASTEXITCODE }
. "$PSScriptRoot\common.ps1"
Push-Location $ProjectRoot
try { & $PythonExe -m codebound.kit.check; exit $LASTEXITCODE }
finally { Pop-Location }
