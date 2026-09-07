# CFMesh-AutoGUI Launcher
# Usage: .\launch.ps1
# Auto-detects Python 3.11+ on PATH or common install locations.

$AppPath = Join-Path $PSScriptRoot "src\cfmesh_autogui\app.py"

# Try PATH first, then common install locations
$PythonPath = Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
if (-not $PythonPath -or -not (Test-Path $PythonPath)) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Python311\python.exe",
        "C:\Python3\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $PythonPath = $c; break }
    }
}

if (-not $PythonPath -or -not (Test-Path $PythonPath)) {
    Write-Error "Python 3.11+ not found. Install Python or add it to PATH."
    exit 1
}

Write-Host "Starting CFMesh-AutoGUI (Python: $PythonPath)..." -ForegroundColor Cyan
& $PythonPath $AppPath
