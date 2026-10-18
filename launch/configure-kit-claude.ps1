param([switch]$Preview,[string]$ConfigPath)
$ErrorActionPreference='Stop'
. "$PSScriptRoot\claude-path.ps1"
$ProjectRoot=Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) { $ConfigPath=Get-ClaudeConfigPath }
$python=Join-Path $ProjectRoot '.venv-claude\Scripts\python.exe'
$options=@('--config',$ConfigPath)
if ($Preview) { $options+='--preview' }
& $python (Join-Path $ProjectRoot 'integrations\claude_desktop\configure_kit.py') @options
exit $LASTEXITCODE
