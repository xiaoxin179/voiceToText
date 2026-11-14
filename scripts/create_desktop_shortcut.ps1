$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$pythonw = 'C:\app\environment\anaconda3\envs\voice-to-text\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'voice-to-text Python environment not found' }
$shortcutPath = Join-Path $PSScriptRoot 'Voice To Text.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + (Join-Path $projectDir 'app.py') + '"'
$shortcut.WorkingDirectory = $projectDir
$shortcut.WindowStyle = 1
$shortcut.Description = '实时语音转文字 — 独立桌面窗口'
$shortcut.Save()
Write-Output $shortcutPath
