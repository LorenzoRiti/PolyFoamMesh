#Requires -RunAsAdministrator
<#
.SYNOPSIS
    One-shot setup of WSL2 + OpenFOAM v2512 (with cfMesh) for PolyFoamMesh.

.DESCRIPTION
    Run this once on a fresh machine before launching PolyFoamMesh.exe.
    It installs WSL2 with Ubuntu if missing, then installs OpenFOAM v2512
    inside it — cfMesh's cartesianMesh ships bundled in the
    openfoam2512-default package, so no separate cfMesh install step is
    needed.

    Must be run as Administrator (required by `wsl --install`).

.NOTES
    If `wsl --install` requires a reboot (fresh machine with no WSL
    components at all), re-run this script after rebooting and logging
    back in — it detects Ubuntu is already present and skips straight to
    the OpenFOAM step.
#>

$ErrorActionPreference = "Stop"

function Test-WslUbuntuPresent {
    $distros = (wsl.exe -l -q 2>$null) -replace "`0", ""
    return ($distros -split "`n") -contains "Ubuntu"
}

Write-Host "=== PolyFoamMesh: WSL2 + OpenFOAM v2512 setup ===" -ForegroundColor Cyan

if (-not (Test-WslUbuntuPresent)) {
    Write-Host "Installing WSL2 with Ubuntu (this can take several minutes)..." -ForegroundColor Yellow
    wsl.exe --install -d Ubuntu
    Write-Host ""
    Write-Host "WSL2/Ubuntu install kicked off. If Windows asks for a REBOOT," -ForegroundColor Yellow
    Write-Host "restart the machine, log back in, open 'Ubuntu' from the Start" -ForegroundColor Yellow
    Write-Host "menu once to finish first-time setup (pick a username/password" -ForegroundColor Yellow
    Write-Host "for the Linux user — any values work), then re-run this script." -ForegroundColor Yellow
    exit 0
}

Write-Host "Ubuntu is present. Installing OpenFOAM v2512 (includes cfMesh)..." -ForegroundColor Cyan

$installScript = @'
set -e
echo "--- Adding OpenCFD OpenFOAM apt repo ---"
curl -s https://dl.openfoam.com/add-debian-repo.sh | sudo bash
echo "--- apt-get update ---"
sudo apt-get update
echo "--- Installing openfoam2512-default (this includes cartesianMesh/cfMesh) ---"
sudo apt-get install -y openfoam2512-default
echo "--- Verifying cartesianMesh is available ---"
source /usr/lib/openfoam/openfoam2512/etc/bashrc
which cartesianMesh
echo "--- Done ---"
'@

wsl.exe -d Ubuntu -- bash -lc $installScript

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "OpenFOAM v2512 + cfMesh installed successfully." -ForegroundColor Green
    Write-Host "PolyFoamMesh.exe will find it automatically (uses the default WSL distro)." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "Something failed during the OpenFOAM install — see the output above." -ForegroundColor Red
    Write-Host "Common cause: no internet access inside WSL2, or apt-get needing a password prompt" -ForegroundColor Red
    Write-Host "you weren't able to answer non-interactively. Try running the same commands" -ForegroundColor Red
    Write-Host "manually inside an Ubuntu terminal (wsl.exe -d Ubuntu) if this keeps failing." -ForegroundColor Red
    exit 1
}
