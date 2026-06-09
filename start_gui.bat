@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"
set "ENV_NAME=voice-to-text"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "ACTIVATE_BAT="
if exist "C:\app\environment\anaconda3\Scripts\activate.bat" set "ACTIVATE_BAT=C:\app\environment\anaconda3\Scripts\activate.bat"
if not defined ACTIVATE_BAT if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" set "ACTIVATE_BAT=%USERPROFILE%\anaconda3\Scripts\activate.bat"
if not defined ACTIVATE_BAT if exist "%USERPROFILE%\miniconda3\Scripts\activate.bat" set "ACTIVATE_BAT=%USERPROFILE%\miniconda3\Scripts\activate.bat"

if not defined ACTIVATE_BAT (
  echo Could not find conda activate.bat.
  pause
  exit /b 1
)

call "%ACTIVATE_BAT%" "%ENV_NAME%"
if errorlevel 1 (
  echo Failed to activate conda environment: %ENV_NAME%
  echo Try creating it first:
  echo conda env create -f environment.yml
  pause
  exit /b 1
)

python app.py
if errorlevel 1 pause
