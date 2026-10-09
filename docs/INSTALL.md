# Installation

There are two ways to get PolyFoamMesh: a **prebuilt Windows installer** (the
recommended route — no Python involved, nothing else to set up) or **from
source** (if you are a developer or want to contribute).

## Option A — Windows installer (recommended)

### Requirements

Windows 10 or 11, 64 bit, with roughly 2 GB of free RAM (4+ recommended) and
about 2.5 GB of free disk space. That's it — no Python, no WSL, no OpenFOAM
needed for the basics.

### Steps

1. Grab `PolyFoamMesh-<version>-Setup.exe` from the
   [Releases](https://github.com/LorenzoRiti/PolyFoamMesh/releases) page of
   the repository. If the Releases section is still empty, the installer for
   the current version hasn't been built yet — in that case go straight to
   Option B below, the app works just as well from source.
2. Double-click the installer. Windows SmartScreen may warn ("Windows
   protected your PC"): the executable is not signed with a paid certificate,
   which is a common false positive for software distributed by independent
   developers. Choose **"More info" → "Run anyway"**.
3. The installer asks a couple of simple questions and then starts the app
   on its own. From then on you can launch it any time from
   **Start → PolyFoamMesh** or the desktop icon.

### What works right away (nothing else to install)

The meshing paths that run purely on Windows need no WSL or OpenFOAM: **CFD
Poly (GMSH, no-WSL)** generates a tetrahedral GMSH mesh and converts it to a
100% polyhedral dual mesh, **FEM Tetra (GMSH, no-WSL)** produces a pure
tetrahedral mesh for FEM solvers, and the **3D viewer** shows both geometry
and mesh.

### What needs one extra step

The **Cartesian (cfMesh)** path and the **checkMesh validation** run OpenFOAM
inside WSL2 (see Option C below). Without OpenFOAM installed, the app shows a
clear warning and does not proceed on those paths — that's expected behaviour,
not an error, until you install OpenFOAM.

### Try it in two minutes

1. **File → Load Geometry...** and open a closed (watertight) STL — for
   example one of the test models shipped with the app.
2. In the Mesh tab pick **"CFD Poly (GMSH, no-WSL)"**.
3. Choose a case directory **without spaces** (e.g. `C:\prova`).
4. Hit **Run**: in the log you'll see GMSH generate the tetrahedra, then the
   polyhedral dual.

### Known limits (honest)

Self-intersecting STLs (overlapping triangles, holes left open by the CAD
tool) can make GMSH fail with `PLC Error: two segments intersect` — export
clean STLs instead. The formats we recommend are **closed STL** (watertight)
or **STEP**. See [residual_risks.md](residual_risks.md) for the full list of
known limitations of the meshing engine.

### Uninstalling

**Start → PolyFoamMesh → Uninstall**, or Settings → Apps → PolyFoamMesh →
Uninstall. Logs stay in `%APPDATA%\polyfoammesh\logs` and can be deleted by
hand.

## Option B — From source (developers)

```bash
git clone https://github.com/LorenzoRiti/PolyFoamMesh.git
cd PolyFoamMesh
pip install -e ".[test]"
polyfoammesh
```

You need Python 3.11+. The `.[test]` extra pulls in the test stack and the
console scripts (`polyfoammesh` and the headless helpers). If you plan to
touch the code, run `python -m pre_commit install` once: every commit then
runs ruff (bug-catching rules) and the fast subset of pure-logic tests.

## Option C — Adding OpenFOAM/cfMesh (optional, for both options)

Only needed for the Cartesian cfMesh and checkMesh paths.

```powershell
wsl --install -d Ubuntu
# inside Ubuntu:
sudo apt-get update
sudo apt-get install openfoam2512
```

Alternatively use the bundled script `installer/setup_wsl_openfoam.ps1`
(requires admin privileges, ~1–2 GB of downloads). After the setup, every
path (cfMesh, checkMesh) works without any further changes.

## Option D — Linux (from source)

PolyFoamMesh runs natively on Linux. The pure-GMSH paths (CFD Poly,
FEM Tetra, viewer) need no OpenFOAM; the Cartesian cfMesh and checkMesh
paths need OpenFOAM on `PATH`.

### Install the app

```bash
git clone https://github.com/LorenzoRiti/PolyFoamMesh.git
cd PolyFoamMesh
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
./launch.sh          # or: bash launch.sh
```

`launch.sh` is the Linux equivalent of `launch.ps1`: it probes each candidate
interpreter for PySide6 + pyvista and uses the first that works. If you
prefer, `polyfoammesh` (the console script installed by pip) launches the app
directly. You may need the usual Qt/X11 runtime libraries on a minimal
install (Debian/Ubuntu: `sudo apt-get install libegl1 libgl1 libglu1-mesa
libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 libfontconfig1`).

### Install OpenFOAM (for cfMesh / checkMesh only)

Either the distribution package or the OpenFOAM Foundation build:

```bash
# Option 1 — OpenFOAM Foundation apt repo (default layout)
#   env script: /usr/lib/openfoam/openfoam2512/etc/bashrc
# Option 2 — a source/manual build
#   env script: /opt/openfoam2512/etc/bashrc
```

Then make sure `cartesianMesh`, `checkMesh` and `foamToVTK` are on the
`PATH` of the shell that launches PolyFoamMesh — normally by sourcing the
OpenFOAM `etc/bashrc` in `~/.bashrc`. If the env script lives somewhere
non-standard, point the app at it explicitly:

```bash
export POLYFOAMMESH_OF_BASHRC=/opt/openfoam2512/etc/bashrc
./launch.sh
```

On Linux there is no WSL hop: every OpenFOAM command is run as a plain
`bash -lc`. The `cfMesh`, `checkMesh` and `foamToVTK` binaries must therefore
be resolvable by `bash` itself.

### Where your data lives

On Linux the app stores sessions/logs under the XDG data directory:
`$XDG_DATA_HOME/polyfoammesh` (default `~/.local/share/polyfoammesh`), with
logs in `logs/app.log`. On Windows this is unchanged
(`%APPDATA%\polyfoammesh`).

## Problems?

Open an issue and attach the contents of `%APPDATA%\polyfoammesh\logs\app.log`
— see [CONTRIBUTING.md](../CONTRIBUTING.md).