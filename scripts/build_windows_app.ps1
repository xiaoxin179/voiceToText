param([string]$Python = 'C:\app\environment\anaconda3\envs\voice-to-text\python.exe')
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDir
if (-not (Test-Path -LiteralPath $Python)) { throw "Python not found: $Python" }
New-Item -ItemType Directory -Force 'build' | Out-Null
# Reuse the client's own painted icon rather than introducing a second identity.
& $Python -c "from PyQt6.QtWidgets import QApplication; from voice_to_text.qt_app import create_app_icon; app=QApplication([]); assert create_app_icon().pixmap(256).save('build/windows-app.ico', 'ICO')"
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed' }
# Windows PowerShell 5 treats native stderr (including INFO logs) as errors
# when callers redirect it. Judge PyInstaller by its exit code instead.
$ErrorActionPreference = 'Continue'
& $Python -m PyInstaller --noconfirm --workpath 'build/windows-tools' --distpath 'dist/windows-tools' --specpath 'build/windows-tools' --onefile --console --name yt-dlp --collect-all yt_dlp 'yt_dlp_cli.py'
$toolExit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($toolExit -ne 0) { throw 'yt-dlp build failed' }
$ErrorActionPreference = 'Continue'
& $Python -m PyInstaller --noconfirm --workpath 'build/windows-app' --distpath 'dist/windows-app' 'packaging/VoiceToText.spec'
$appExit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($appExit -ne 0) { throw 'Desktop build failed' }
$output = Join-Path $projectDir 'dist/windows-app/VoiceToText'
Copy-Item -LiteralPath 'dist/windows-tools/yt-dlp.exe' -Destination $output -Force
foreach ($name in @('ffmpeg', 'ffprobe')) {
    $tool = (Get-Command $name -CommandType Application -ErrorAction Stop).Source
    Copy-Item -LiteralPath $tool -Destination (Join-Path $output ($name + '.exe')) -Force
}
Write-Output "Built: $output\VoiceToText.exe"
