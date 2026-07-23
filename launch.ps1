# CFMesh-AutoGUI Launcher
# Usage: .\launch.ps1

$PythonPath = "C:\Users\Davide Valoroso\AppData\Local\Programs\Python\Python311\python.exe"
$AppPath = Join-Path $PSScriptRoot "src\cfmesh_autogui\app.py"

if (-not (Test-Path $PythonPath)) {
    Write-Error "Python 3.11 not found at $PythonPath"
    exit 1
}

Write-Host "Starting CFMesh-AutoGUI..." -ForegroundColor Cyan
& $PythonPath $AppPath
