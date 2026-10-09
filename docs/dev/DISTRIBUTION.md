# How to distribute CFMesh-AutoGUI (v2.1.0)

Guide for the developer/maintainer. The user manual (installer,
requirements, first mesh) is [../INSTALL.md](../INSTALL.md).

## The package (choice: no-WSL demo)

The installer does **not** include WSL2/OpenFOAM: the friend immediately tries the
**no-WSL** paths (CFD Poly GMSH → 100% poly STAR-CCM+ style, FEM Tetra) and
visualization. The WSL paths (cfMesh, checkMesh) show a clear
warning and do not proceed.

```
installer/output/
├── CFMesh-AutoGUI-2.1.0-Setup.exe      ← TO GIVE TO THE FRIEND (279 MB, Inno installer)
└── CFMesh-AutoGUI-2.1.0-Istruzioni.pdf ← PDF instructions
```

## 1. Rebuild the one-dir bundle (on your side, once only)

```bash
python -m PyInstaller --noconfirm --clean CFMesh-AutoGUI.spec
```

Result: `dist/CFMesh-AutoGUI/` (1.3 GB) — self-contained app
(Python + PySide6 + cadquery + trimesh + pyvista + gmsh + pymeshfix):
**no external dependency on the friend's PC**.

Critical points of the spec (verified today):
- **one-dir** (EXE bootloader + `_internal/`): fast startup, no
  extraction to temp at every launch, friendlier with SmartScreen.
- **`gmsh-4.15.dll` added to the datas**: the gmsh wheel installs it in
  `Python311\Lib\` (outside the packages) → PyInstaller does not see it by itself;
  without it, the GMSH path in the frozen build crashes with "DLL load failed".
- **`gmsh_wrapper._cli_main(argv)`**: in the frozen exe the GMSH subprocess
  starts with `--gmsh-volume` on `sys.executable`; `app.py` calls
  `_cli_main` directly (the old `runpy.run_path` on a file that
  does not exist in the bundle failed).
- `excludes` with torch/cv2/pyarrow/... (junk from the global
  environment) — if the exe swells, check there.

## 2. Compile the Inno installer

Portable Inno Setup 6.7.3 in `tools/innosetup/is6/ISCC.exe`
(installed with `/PORTABLE=1`, no admin).

```bash
tools/innosetup/is6/ISCC.exe installer\inno_setup.iss
```

Result: `installer/output/CFMesh-AutoGUI-2.1.0-Setup.exe` (279 MB,
lzma2/max). Properties: **per-user installation without admin**
(`PrivilegesRequired=lowest`), Start Menu+Desktop shortcut, associations
for `.step/.stp/.stl` (HKCU), clean uninstaller.

> **Git-Bash warning**: the `/VERYSILENT` switches get eaten into
> MSYS paths (`C:/Program Files/Git/VERYSILENT`). Use:
> `MSYS2_ARG_CONV_EXCL='*'` (or launch from cmd/PowerShell).

## 3. Quick test of the installer

```bash
# silent installation (path without spaces)
MSYS2_ARG_CONV_EXCL='*' ./installer/output/CFMesh-AutoGUI-2.1.0-Setup.exe \
    /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/DIR=C:\polybench\install_test" /NOICONS
# verify the GMSH path from the installed exe
"C:/polybench/install_test/CFMesh-AutoGUI.exe" --gmsh-volume \
    --step=C:/cfmesh_poly_bench/venturi.stl --msh=C:/polybench/v.msh \
    --detail=coarse --max-cell=0.05 --min-cell=0.02   # → {"success": true}
# uninstall (in the test folder)
"C:/polybench/install_test/unins/unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

## 4. SmartScreen / signing

The exe **is not signed** (paid certificate ~100 €/year). The friend
will have to click "More information → Run anyway". To remove
the warning: Authenticode signing (signtool) or SmartScreen "more info" after
a few installations.

## 5. App log on the other PC

`%APPDATA%\cfmesh-autogui\logs\app.log` — ask the friend to paste it
if something goes wrong.

## 6. If the package WITH WSL/OpenFOAM is needed in the future

- `installer/setup_wsl_openfoam.ps1` installs WSL2 + OpenFOAM v2512 +
  cfMesh on the other PC (admin, ~1-2 GB download).
- After setup, all paths (cfMesh, checkMesh) work.
