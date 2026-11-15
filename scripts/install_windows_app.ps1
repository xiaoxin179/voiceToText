param([string]$PackageDir = '', [switch]$KeepDesktopBat)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
if (-not $PackageDir) { $PackageDir = Join-Path $projectDir 'dist/windows-app/VoiceToText' }
$PackageDir = (Resolve-Path -LiteralPath $PackageDir).Path
$installDir = Join-Path $env:LOCALAPPDATA 'Programs/VoiceToText'
$executable = Join-Path $installDir 'VoiceToText.exe'
if (-not (Test-Path -LiteralPath (Join-Path $PackageDir 'VoiceToText.exe'))) { throw 'Build the application first.' }
$running = Get-Process -Name VoiceToText -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $executable }
if ($running) { throw 'Close VoiceToText before installing an update.' }
New-Item -ItemType Directory -Force $installDir | Out-Null
Get-ChildItem -LiteralPath $PackageDir -Force | Copy-Item -Destination $installDir -Recurse -Force
$desktop = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
$shell = New-Object -ComObject WScript.Shell
# Construct Chinese filenames explicitly so Windows PowerShell 5's script
# encoding cannot corrupt them on machines with a different ANSI code page.
$appName = -join ([char[]](0x8bed, 0x97f3, 0x8f6c, 0x6587, 0x5b57))
$oldName = (-join ([char[]](0x542f, 0x52a8))) + $appName + '.bat'
foreach ($linkPath in @((Join-Path $desktop ($appName + '.lnk')), (Join-Path $programs 'Voice Studio.lnk'))) {
    $shortcut = $shell.CreateShortcut($linkPath)
    $shortcut.TargetPath = $executable
    $shortcut.WorkingDirectory = $installDir
    $shortcut.IconLocation = $executable + ',0'
    $shortcut.Description = 'Voice Studio - speech to text'
    $shortcut.Save()
}
if (-not $KeepDesktopBat) {
    $oldBat = Join-Path $desktop $oldName
    if (Test-Path -LiteralPath $oldBat) {
        $backup = Join-Path $projectDir 'build/desktop-launcher-original.bat'
        if (-not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $oldBat -Destination $backup }
        @('@echo off', 'start "" "%LOCALAPPDATA%\Programs\VoiceToText\VoiceToText.exe"', 'exit /b') |
            Set-Content -LiteralPath $oldBat -Encoding Ascii
    }
}
Write-Output "Installed: $executable"
Write-Output "Desktop shortcut: $(Join-Path $desktop ($appName + '.lnk'))"
