@echo off
chcp 65001 >nul
setlocal

set "PROJECT_DIR=C:\code\ai-advance\voiceToText"
set "GUI_LAUNCHER=%PROJECT_DIR%\scripts\start_gui.bat"

if not exist "%GUI_LAUNCHER%" (
  echo Voice To Text launcher was not found:
  echo %GUI_LAUNCHER%
  echo.
  echo Update PROJECT_DIR in this file if the project has moved.
  pause
  exit /b 1
)

call "%GUI_LAUNCHER%"
