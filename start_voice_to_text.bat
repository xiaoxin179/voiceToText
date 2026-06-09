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
  echo Please install Anaconda/Miniconda or update this file with your conda path.
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

:menu
cls
echo Voice To Text
echo.
echo [1] Open desktop GUI
echo [2] Start listening - GPU, both microphone and system audio
echo [3] Start listening - CPU fallback, both microphone and system audio
echo [4] Check environment and devices
echo [5] List audio devices
echo [6] Record 5 second test wav files
echo [7] Install CUDA runtime dependencies
echo [8] Download Whisper models
echo [0] Exit
echo.
set /p "choice=Choose: "

if "%choice%"=="1" goto gui
if "%choice%"=="2" goto listen_gpu
if "%choice%"=="3" goto listen_cpu
if "%choice%"=="4" goto doctor
if "%choice%"=="5" goto devices
if "%choice%"=="6" goto record_test
if "%choice%"=="7" goto install_cuda
if "%choice%"=="8" goto init_models
if "%choice%"=="0" exit /b 0
goto menu

:gui
cls
python app.py
if errorlevel 1 pause
goto menu

:listen_gpu
cls
echo Starting GPU listening. Press Ctrl+C to stop.
echo.
python main.py listen --source both --model medium --device cuda --compute-type float16
pause
goto menu

:listen_cpu
cls
echo Starting CPU fallback listening. Press Ctrl+C to stop.
echo.
python main.py listen --source both --model tiny --device cpu --compute-type int8
pause
goto menu

:doctor
cls
python main.py doctor
pause
goto menu

:devices
cls
python main.py devices
pause
goto menu

:record_test
cls
python main.py record-test --source both --seconds 5
pause
goto menu

:install_cuda
cls
echo Installing CUDA runtime dependencies. This can take a long time.
echo.
python -m pip install -r requirements-cuda.txt
pause
goto menu

:init_models
cls
python init_models.py --models tiny medium
pause
goto menu
