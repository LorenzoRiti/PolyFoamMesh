@echo off
REM Compile i18n: Python sources -> .ts -> .qm
REM Requires: pylupdate5 (from PySide6) and lrelease (from Qt)
setlocal

set DIR=%~dp0
set PRO=%DIR%cfmesh_autogui.pro

echo [i18n] Running pylupdate5 on %PRO%...
pylupdate5 "%PRO%"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] pylupdate5 failed. Install PySide6 with: pip install PySide6
    exit /b %ERRORLEVEL%
)

echo [i18n] Running lrelease on %PRO%...
lrelease "%PRO%"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] lrelease failed. Ensure Qt/bin is in PATH or install Qt tools.
    exit /b %ERRORLEVEL%
)

echo [i18n] Done. .qm files generated in locale\it\LC_MESSAGES\
