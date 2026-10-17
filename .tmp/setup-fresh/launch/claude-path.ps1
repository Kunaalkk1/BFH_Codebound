function Get-ClaudeConfigPath {
    # MSIX redirects the application's Roaming AppData into LocalCache.
    $packages = @(Get-AppxPackage -Name Claude -ErrorAction SilentlyContinue)
    if ($packages.Count -gt 1) { throw 'Multiple Claude packages; specify -ConfigPath explicitly.' }
    if ($packages.Count -eq 1) {
        return Join-Path $env:LOCALAPPDATA ('Packages\' + $packages[0].PackageFamilyName + '\LocalCache\Roaming\Claude\claude_desktop_config.json')
    }
    return Join-Path $env:APPDATA 'Claude\claude_desktop_config.json'
}
