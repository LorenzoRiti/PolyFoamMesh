# Building and distributing PolyFoamMesh

Guide for maintainers. The end-user installation guide is
[../INSTALL.md](../INSTALL.md).

## What gets shipped

| Artifact | How it is made | Notes |
|---|---|---|
| `dist/PolyFoamMesh/` (one-dir bundle, about 1.3 GB) | PyInstaller, `PolyFoamMesh.spec` | Self-contained: Python, PySide6, cadquery, trimesh, pyvista, gmsh and the rest. No Python needed on the target machine. |
| `PolyFoamMesh-X.Y.Z-win64.zip` (portable) | Zip of the bundle | Extract and run `PolyFoamMesh.exe`. No Start Menu entry or uninstaller. |
| `installer/output/PolyFoamMesh-X.Y.Z-Setup.exe` | Inno Setup 6, `installer/inno_setup.iss` | Per-user install (no admin), Start Menu entry, `.step`/`.stp`/`.stl` associations, clean uninstaller. |

The package does **not** include WSL2 or OpenFOAM. The GMSH-based paths work
standalone; the cfMesh and `checkMesh` paths need OpenFOAM, which the app explains on
first use. `installer/setup_wsl_openfoam.ps1` installs WSL2 + OpenFOAM v2512 + cfMesh
(administrator, about 1–2 GB download) if you want to provide it.

Built artifacts are never committed (`dist/` and `installer/output/` are ignored).

## Release checklist

1. Update the version in `pyproject.toml`, `src/polyfoammesh/_version.py` and
   `installer/inno_setup.iss`; `python tools/check_version_sync.py` checks they agree.
2. Move the `[Unreleased]` entries in `CHANGELOG.md` under the new version.
3. Run the tests:
   ```bash
   pytest tests/ -m "not slow and not wsl" -q
   pytest tests/ -m "slow or wsl" -q        # needs WSL2 + OpenFOAM, run it alone
   ```
4. Run the release gate:
   ```bash
   python tools/release_gate.py --e2e       # versions, spec checks, smoke, repo-clean, E2E, checkMesh
   python tools/release_gate.py --build     # adds the PyInstaller bundle (about 7 minutes)
   python tools/release_gate.py --assemble  # adds the Inno Setup installer (needs ISCC)
   ```
   Every stage prints PASS / FAIL / SKIP; a SKIP always states its reason. The gate
   prints its declared limits (no clean-machine install check, checkMesh verdict
   reported but not gated).
5. Smoke-test the bundle: run `dist/PolyFoamMesh/PolyFoamMesh.exe` and check that the
   window opens and the About dialog shows the new version.
6. Tag and push (`vX.Y.Z`); see "Release pipeline" below.

## Rebuilding the bundle by hand

```bash
python -m PyInstaller --noconfirm --clean PolyFoamMesh.spec
```

Points that matter in the spec:

- One-dir (bootloader plus `_internal/`): fast start-up, no extraction on every launch.
- Native dependencies that static analysis misses (for example `casadi` and `nlopt`) must
  stay out of the spec's `excludes`; `release_gate.py` checks this.
- `rthook_casadi_dlls.py` is a runtime hook the spec needs.
- `templates/` and `plugins/` are bundled as data.

## Testing the installer

```bash
# silent install into a path without spaces
installer/output/PolyFoamMesh-X.Y.Z-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/DIR=C:\polybench\install_test" /NOICONS
# uninstall
"C:/polybench/install_test/unins/unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

Install on a second, clean machine when you can: the gate verifies module equivalence of
the bundle, not a real clean install.

## Release pipeline

Pushing a `v*` tag runs `.github/workflows/release.yml` on Windows: lint, version sync,
the fast test suite, the PyInstaller build, the Inno Setup installer, and a zip of the
bundle. The `## [X.Y.Z]` section of `CHANGELOG.md` becomes the body of the GitHub
Release, so keep that heading format. A Linux job builds a tarball.

## Code signing

The executable and installer are not signed, so Windows SmartScreen shows "More info →
Run anyway" on first run. Authenticode signing (`signtool`, paid certificate) removes
the warning.

## Where the app writes logs

`%APPDATA%\polyfoammesh\logs\app.log` on Windows. Ask for this file when debugging a
report from another machine.
