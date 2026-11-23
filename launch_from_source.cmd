@echo off
rem Double-click to start PolyFoamMesh from the LATEST SOURCE in this folder.
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=python"
echo Starting PolyFoamMesh from source: %~dp0src
"%PY%" -c "import polyfoammesh;print('Version:',polyfoammesh.__version__,'|',polyfoammesh.__file__)"
"%PY%" -m polyfoammesh.app
if errorlevel 1 (
  echo.
  echo PolyFoamMesh exited with an error. See the messages above.
  pause
)
