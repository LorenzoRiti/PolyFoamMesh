# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

import os
import sys

# The gmsh wheel installs its native DLL directly under the Python Lib
# folder (not inside the package), so PyInstaller's import scanning never
# sees it.  Without it the frozen exe's GMSH path fails with
# "ImportError: DLL load failed".  Copy it next to the frozen gmsh.py
# (moduledir = the _internal bundle root in one-dir mode).
import glob as _glob
_gmsh_candidates = _glob.glob(os.path.join(sys.prefix, "Lib", "gmsh-*.dll"))
if not _gmsh_candidates:
    raise SystemExit("gmsh DLL not found (gmsh-*.dll) — GMSH poly path "
                     "would be broken in the frozen exe.")
_GMSH_DLL = sorted(_gmsh_candidates)[-1]  # highest version if multiple

# casadi ships ~100 sibling DLLs (optional solver plugins: bonmin, cbc,
# clp, ipopt, ...) directly in its own package folder rather than a
# `.libs` subfolder PyInstaller's automatic scanner handles well, and
# has no official PyInstaller hook. collect_dynamic_libs() alone still
# left the frozen exe failing with "DLL load failed while importing
# _casadi" (its binaries ended up in a differently-structured location
# than where _casadi.pyd — discovered separately via normal import
# scanning — was placed). collect_all() keeps submodules/data/binaries
# consistent as a single unit, which is the officially recommended fix
# for exactly this "large native package, no hook" scenario. casadi is
# a REAL dependency here despite polyfoammesh never importing it
# directly: cadquery/assembly.py -> occ_impl/solver.py does
# `import casadi as ca` unconditionally at module load time, and
# main_window.py does `import cadquery as cq` at its own module level
# — so it's on the very first import of the GUI, not a lazily-triggered
# path an end user could avoid by not using some optional feature.
casadi_datas, casadi_binaries, casadi_hidden = collect_all('casadi')

a = Analysis(
    [os.path.join('src', 'polyfoammesh', 'app.py')],
    pathex=[],
    binaries=casadi_binaries,
    datas=[('templates', 'templates'), ('plugins', 'plugins'),
           (_GMSH_DLL, '.')] + casadi_datas,  # ✅ F-028: rimosso src/polyfoammesh (gia come scripts)
    hiddenimports=['PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtGui', 'PySide6.QtNetwork', 'gmsh', 'meshio', 'reportlab', 'cadquery', 'pyvista', 'pyvistaqt', 'numpy', 'trimesh', 'pymeshfix'] + casadi_hidden,
    hookspath=[],
    hooksconfig={},
    # collect_all('casadi') gets every DLL into the bundle, but the OS
    # loader still failed to find them at runtime ("DLL load failed
    # while importing _casadi") — Python 3.8+ requires an explicit
    # os.add_dll_directory() call for sibling-DLL discovery, which
    # casadi's own __init__.py doesn't make. This runtime hook runs
    # before any user code and registers casadi's extracted DLL folder.
    runtime_hooks=['rthook_casadi_dlls.py'],
    # This dev machine's global Python environment has a lot of unrelated
    # packages installed for other projects (ML/robotics/DB work), and
    # PyInstaller's static analysis pulled several of them into the build
    # even though nothing in polyfoammesh imports them.
    #
    # casadi and nlopt both turned out to be REAL dependencies, not bloat
    # — cadquery.occ_impl.sketch_solver uses both as optional constraint-
    # solver backends. nlopt is a top-level import (visible in
    # warn-CFMesh-AutoGUI.txt); casadi's usage wasn't visible there at
    # all (likely imported inside a function, not at module load), which
    # is why excluding it produced a clean "No module named 'casadi'"
    # rather than showing up as a warning — static analysis missed it,
    # but the app hit it at runtime regardless. Lesson: for this app,
    # "not referenced in warn-CFMesh-AutoGUI.txt" is NOT sufficient proof
    # a package is safe to exclude, only a real functional test of the
    # built exe is. Excluding these also cuts the exe from ~715MB toward
    # ~390MB.
    excludes=[
        'torch', 'cv2', 'pyarrow', 'psycopg', 'psycopg2',
        'sentencepiece', 'av', 'IPython',
        'jupyter', 'notebook', 'tensorflow', 'sklearn',
    ],
    noarchive=False,
    # optimize=2 strips ALL docstrings from compiled bytecode, including
    # numpy's own — numpy's C extension init calls add_docstring() at
    # import time and REQUIRES a real string there, so the frozen exe
    # crashed on first launch with "argument docstring of add_docstring
    # should be a str" before a single line of app code ever ran. 0 keeps
    # docstrings (and assert statements) intact; the exe is a few MB
    # larger, that's the whole cost.
    optimize=0,
)
pyz = PYZ(a.pure)

# one-dir build: the EXE is just the bootloader + embedded PYZ; the
# libraries/data live next to it in dist/PolyFoamMesh/ (fast startup,
# no per-run temp extraction, and friendlier to SmartScreen than a
# 500 MB one-file exe).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PolyFoamMesh',
    icon=os.path.join('installer', 'assets', 'icon.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['*msvcp*', '*vcruntime*', '*concrt*'],  # ✅ F-031
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=True,  # ✅ F-030
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=['*msvcp*', '*vcruntime*', '*concrt*'],
    name='PolyFoamMesh',
)
