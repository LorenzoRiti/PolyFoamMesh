## How to install

| You want | Download | Then |
|---|---|---|
| **Windows, installer** (recommended) | `PolyFoamMesh-{VERSION}-Setup.exe` | Double-click. Per-user install, no administrator rights, Start Menu entry. |
| **Windows, no install** | `PolyFoamMesh-{VERSION}-win64.zip` | Extract the folder anywhere and run `PolyFoamMesh.exe`. |
| **Linux** | `PolyFoamMesh-linux-x86_64.tar.gz` | `tar -xzf` it and run `PolyFoamMesh/PolyFoamMesh`. Needs native OpenFOAM, see [docs/INSTALL_LINUX.md](https://github.com/LorenzoRiti/PolyFoamMesh/blob/v{VERSION}/docs/INSTALL_LINUX.md). |
| **From source** | the repository | `pip install -e ".[test,ftetwild]"`, then `polyfoammesh`. See [docs/INSTALL.md](https://github.com/LorenzoRiti/PolyFoamMesh/blob/v{VERSION}/docs/INSTALL.md). |

Notes:

- The executable and the installer are **not code-signed**. Windows SmartScreen shows
  "Windows protected your PC": choose **More info → Run anyway**.
- WSL2 and OpenFOAM are **not included**. Meshing with GMSH works on its own; cfMesh and
  `checkMesh` need OpenFOAM v2512 (the app explains this on first use;
  `installer/setup_wsl_openfoam.ps1` sets it up on Windows).
- These packages are built by GitHub Actions from the tagged source. They have **not been
  tested on a clean computer** and the Linux package has not been run on a real Linux
  machine. See [docs/KNOWN_LIMITATIONS.md](https://github.com/LorenzoRiti/PolyFoamMesh/blob/v{VERSION}/docs/KNOWN_LIMITATIONS.md).

## What changed in {VERSION}

