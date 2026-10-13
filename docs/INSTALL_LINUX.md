# Installing PolyFoamMesh on Linux

PolyFoamMesh runs **natively on Linux** — no WSL hop. OpenFOAM commands are
executed through `bash -lc`, exactly as they would be from your own shell, and
application data is stored under the XDG data directory. This guide covers the
whole path: system libraries, Python setup, OpenFOAM, running the app, and what
to do when something does not start.

If you only want the two-minute version, jump to
[Installing the app](#3-install-the-app); the rest is detail for when a step
fails.

- New to the app? Read [USER_GUIDE.md](USER_GUIDE.md) after installing.
- The short version of this page also appears as **Option D** in
  [INSTALL.md](INSTALL.md).

## What runs on Linux, with and without OpenFOAM

PolyFoamMesh has two families of meshing paths:

- **Pure GMSH paths** — **CFD Poly (GMSH)**, the app's default mesher —
  plus the 3D viewer. These need **no OpenFOAM**; GMSH is bundled as a
  Python dependency.
- **OpenFOAM paths** — the **Cartesian (cfMesh)** mesher and **checkMesh**
  validation (which runs automatically after a polyhedral build). These need
  OpenFOAM installed natively.

So: install OpenFOAM only if you want cfMesh, or if you want automatic
`checkMesh` reporting. Everything else works without it, and the app shows a
clear warning (not a crash) when it needs OpenFOAM and cannot find it.

## 1. System prerequisites

PySide6 (the GUI toolkit), pyvista (the 3D viewer) and gmsh are Python
packages, but they load native shared libraries at import time. On a normal
desktop install these are already present. On a **minimal or headless image**
you must install them, otherwise the app — and `pytest` at collection — dies
with errors such as:

```
ImportError: libEGL.so.1: cannot open shared object file
ImportError: libGLU.so.1: cannot open shared object file
```

The required packages are: `libegl1`, `libgl1`, `libglu1-mesa`,
`libxkbcommon0`, `libxkbcommon-x11-0`, `libdbus-1-3`, `libfontconfig1`.

### Debian / Ubuntu (apt)

```bash
sudo apt-get update
sudo apt-get install -y \
    libegl1 libgl1 libglu1-mesa libxkbcommon0 libxkbcommon-x11-0 \
    libdbus-1-3 libfontconfig1
```

### Fedora / RHEL (dnf)

```bash
sudo dnf install -y \
    mesa-libEGL mesa-libGL mesa-libGLU libxkbcommon \
    libxkbcommon-x11 dbus-libs fontconfig
```

### Arch (pacman)

```bash
sudo pacman -S --needed \
    mesa libxkbcommon libxkbcommon-x11 dbus fontconfig
```

Package names shift between distributions and releases; if one of the names
above is not found, install the package that provides the missing `.so` file
(`libEGL.so.1`, `libGLU.so.1`, ...) using your distribution's search tool.

## 2. Python environment

PolyFoamMesh needs **Python 3.11 or newer**. Verify with:

```bash
python3 --version
```

### Recommended: a virtual environment

A virtual environment keeps the app's dependencies out of your system Python.
On Debian/Ubuntu this needs the `python3-venv` package:

```bash
sudo apt-get install -y python3-venv   # Debian/Ubuntu only
python3 -m venv .venv
. .venv/bin/activate
```

Once activated, `pip` and `python` in your shell point at the virtual
environment. On Fedora and Arch, `venv` is included with the interpreter, so
the first `apt-get` line does not apply.

### Fallback: no virtual environment, no sudo

On a locked-down or containerised system without `python3-venv` and without
root, you can install into your user site directory. This is a **fallback, not
the default** — it mixes the app's dependencies with your user-wide Python
packages, which is why the virtual environment is preferred:

```bash
pip install --user --break-system-packages -e ".[test]"
```

This installs everything under `~/.local`, so no administrator rights are
needed. Python 3.12 on Debian/Ubuntu marks the environment as
externally managed; `--break-system-packages` is what tells `pip` to proceed
anyway. Prefer the virtual environment whenever you can use it.

## 3. Install the app

From the repository root, with your environment active:

```bash
git clone https://github.com/LorenzoRiti/PolyFoamMesh.git
cd PolyFoamMesh
pip install -e ".[test]"
```

The `.[test]` extra pulls in the test stack (pytest, ruff, ...) alongside the
runtime dependencies and the console scripts. If you only want to run the app
and never run the tests, `pip install -e .` is enough.

Getting past the "libEGL / libGLU not found" errors is the single most common
stumbling block on fresh machines — if `pip install` succeeded but nothing
starts, revisit [section 1](#1-system-prerequisites).

## 4. OpenFOAM (only for cfMesh / checkMesh)

Skip this section unless you need the Cartesian (cfMesh) mesher or automatic
`checkMesh` validation.

Install any reasonably recent OpenFOAM — a distribution package or an
OpenFOAM Foundation build both work (for the Foundation apt repository, see
the instructions on openfoam.org; the package is not in the stock Ubuntu
repositories).

Whichever build you use, PolyFoamMesh needs to find three executables —
`cartesianMesh`, `checkMesh` and `foamToVTK` — in the environment of the shell
that launches the app. The app achieves this by sourcing the OpenFOAM
environment script (`etc/bashrc`) before every OpenFOAM command. The default
path is:

```
/usr/lib/openfoam/openfoam2512/etc/bashrc
```

If your OpenFOAM lives somewhere else (a manual or source build, a different
version), point the app at it with the `POLYFOAMMESH_OF_BASHRC` environment
variable before launching:

```bash
export POLYFOAMMESH_OF_BASHRC=/opt/openfoam2512/etc/bashrc
./launch.sh
```

Put that `export` in your `~/.bashrc` to make it permanent. Because the app
sources `POLYFOAMMESH_OF_BASHRC` itself, you do **not** have to source OpenFOAM
in `~/.bashrc` for the app to work — but doing so makes the same tools
available to your own terminal, which is convenient when inspecting a case by
hand.

### A note for Windows users

If you are coming from Windows and only know OpenFOAM through WSL: on Linux
there is no WSL layer. The app runs as an ordinary process and calls `bash`
directly. The same `POLYFOAMMESH_OF_BASHRC` variable is used, but it now points
at a path on the native filesystem (for example
`/usr/lib/openfoam/openfoam2512/etc/bashrc`), not inside a WSL distribution.

## 5. Running the app

### GUI

From the repository root:

```bash
./launch.sh          # or: bash launch.sh
```

`launch.sh` is the Linux counterpart of `launch.ps1`. It probes each candidate
interpreter (`$POLYFOAMMESH_PYTHON`, then `python3`, then `python`) for the
GUI dependencies and runs the first one that imports PySide6 and pyvista
successfully. This avoids the classic "wrong venv on PATH" failure.

If you installed the app and prefer the console script:

```bash
python -m polyfoammesh
```

### Headless / CI

On a machine with no display server (a build server, a container, an SSH
session without X forwarding), select Qt's offscreen platform plugin. The GUI
tests then run without a display:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -m "not slow and not wsl" -q
```

The same variable lets the app itself start headless for scripted runs.

## 6. Verification

With your environment active, from the repository root:

```bash
# Fast test suite (skips slow and WSL-marked tests).
python -m pytest -m "not slow and not wsl" -q

# Lint (bug-catching rules).
ruff check src/ tests/
```

A healthy Linux checkout reports a green fast suite (1516 tests pass on the
reference WSL Ubuntu 24.04 / Python 3.12 setup) and a clean ruff run. The tests
that exercise the OpenFOAM paths are marked and skipped automatically when
OpenFOAM is not installed.

## 7. Troubleshooting

- **`ImportError: libEGL.so.1` / `libGLU.so.1`**, or pytest dies during
  collection. The native GL/EGL libraries are missing. Install the packages
  from [section 1](#1-system-prerequisites).
- **`qt.qpa.plugin: Could not load the Qt platform plugin "xcb"`**, or the app
  aborts immediately on a headless machine. There is no display server. For
  tests and scripted runs set `QT_QPA_PLATFORM=offscreen`; for an interactive
  session, connect a display or forward X11.
- **OpenFOAM not found / cfMesh and checkMesh unavailable.** The app logs a
  clear warning instead of proceeding. Confirm the three tools are reachable
  from a plain `bash -lc 'command -v cartesianMesh checkMesh foamToVTK'`, and
  if not, set `POLYFOAMMESH_OF_BASHRC` to the correct `etc/bashrc` as in
  [section 4](#4-openfoam-only-for-cfmesh--checkmesh).
- **Blank or crashing 3D viewer / GPU or software-GL issues.** On machines
  with no GPU or with broken OpenGL drivers (common in VMs and containers),
  force software rendering:
  ```bash
  LIBGL_ALWAYS_SOFTWARE=1 ./launch.sh
  ```
  This is slower but reliable. On a real desktop with working drivers you do
  not need it.
- **`externally-managed-environment` from pip.** You are installing into a
  system Python. Use the virtual environment from
  [section 2](#2-python-environment), or the documented fallback with
  `--user --break-system-packages`.

## What is verified vs not

*Verified* on a live **WSL Ubuntu 24.04 / Python 3.12** environment:

- native Linux execution (no WSL hop) with the default
  `POLYFOAMMESH_OF_BASHRC`;
- the fast test suite green: `python -m pytest -m "not slow and not wsl" -q`
  → **1516 passed**;
- `ruff check src/ tests/` clean.

*Not yet verified:*

- GPU / hardware OpenGL rendering on a real desktop (testing used software
  rendering and offscreen Qt);
- distributions other than Ubuntu (the apt/dnf/pacman package names above are
  the standard ones, but have not been exercised end-to-end on Fedora or
  Arch);
- the AppImage build. A PyInstaller spec (`PolyFoamMesh-linux.spec`) exists,
  but the AppImage is **deferred and not shipped in 2.3.0** — run from source
  for now.

---

Author: Lorenzo Riti
