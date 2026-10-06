# PolyFoamMesh Launcher
# Usage: .\launch.ps1
#
# 2026-10-06 fix: the launcher used to take the FIRST `python` on PATH. On a
# machine where PATH points at an unrelated venv (e.g. an agent's Hermes
# venv) that Python has no PySide6: the app started and died with an
# ImportError, the console window closed instantly and nothing seemed to
# happen. Now every candidate is probed for the app's GUI dependencies
# (PySide6 + pyvista) and the first one that passes is used; failures are
# printed and the window is kept open.

$ErrorActionPreference = "Continue"
$AppPath = Join-Path $PSScriptRoot "src\polyfoammesh\app.py"

function Test-AppPython([string]$exe) {
    if (-not $exe -or -not (Test-Path -LiteralPath $exe)) { return $false }
    try {
        & $exe -c "import PySide6, pyvista" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# Candidate order: explicit override, the known project interpreter, other
# common installs, PATH last (it is the one that may be an unrelated venv).
$candidates = @()
if ($env:POLYFOAMMESH_PYTHON) { $candidates += $env:POLYFOAMMESH_PYTHON }
$candidates += "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
$candidates += "C:\Python311\python.exe"
$candidates += "C:\Python3\python.exe"
$pathPy = Get-Command python -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty Source
if ($pathPy) { $candidates += $pathPy }

$PythonPath = $null
foreach ($c in $candidates) {
    if (Test-AppPython $c) { $PythonPath = $c; break }
}

if (-not $PythonPath) {
    Write-Host ""
    Write-Host "PolyFoamMesh: nessun Python con le dipendenze dell'app trovato." -ForegroundColor Red
    Write-Host "Cercati (PySide6 + pyvista mancanti in tutti):"
    foreach ($c in $candidates) { Write-Host "  - $c" }
    Write-Host ""
    Write-Host "Installa le dipendenze, ad esempio:"
    Write-Host '  & "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pip install -e ".[gui]"'
    Write-Host "oppure imposta la variabile POLYFOAMMESH_PYTHON sul percorso giusto."
    Read-Host "Premi INVIO per chiudere"
    exit 1
}

Write-Host "Starting PolyFoamMesh (Python: $PythonPath)..." -ForegroundColor Cyan
& $PythonPath $AppPath

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "PolyFoamMesh e' terminata con errore (codice $LASTEXITCODE)." -ForegroundColor Red
    Write-Host "Se il messaggio sopra non basta, avvia a mano per vedere tutto:"
    Write-Host "  & `"$PythonPath`" `"$AppPath`""
    Read-Host "Premi INVIO per chiudere"
}
