. "$PSScriptRoot\common.ps1"
Push-Location $ProjectRoot
try { & $PythonExe -m codebound.kit.demo; exit $LASTEXITCODE }
finally { Pop-Location }
