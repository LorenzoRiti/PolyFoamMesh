@echo off
rem Doppio click: avvia PolyFoamMesh dall'ULTIMA VERSIONE DEL SORGENTE di questa cartella.
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=python"
echo Avvio PolyFoamMesh da sorgente: %~dp0src
"%PY%" -c "import polyfoammesh,sys;print('Versione:',polyfoammesh.__version__,'|',polyfoammesh.__file__)"
"%PY%" -m polyfoammesh.app
if errorlevel 1 (
  echo.
  echo PolyFoamMesh terminato con errore. Leggi sopra.
  pause
)
