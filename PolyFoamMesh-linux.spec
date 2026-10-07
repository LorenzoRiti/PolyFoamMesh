# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-dir spec for Linux.

Mirrors PolyFoamMesh.spec but is platform-aware for the two native-library
differences:

  * gmsh's shared object: on Windows the wheel drops ``gmsh-*.dll`` under
    ``Lib/``; on Linux it ships ``libgmsh.so`` somewhere under
    ``sys.prefix/lib*``. We glob for it and ship it next to the frozen
    ``gmsh.py`` so ctypes can dlopen it. If the glob misses (layout changed)
    we fall back to ``collect_all('gmsh')`` and print a warning instead of
    failing the whole build.
  * no Windows job-object / UPX / msvcp concerns.
"""

from PyInstaller.utils.hooks import collect_all

import glob as _glob
import os
import sys

# --- gmsh native shared object ------------------------------------------
_gmsh_candidates: list[str] = []
for _pat in (
    "lib*/libgmsh.so*",
    "lib*/gmsh*.so*",
    "lib*/python*/site-packages/libgmsh.so*",
    "lib*/python*/site-packages/gmsh*/libgmsh.so*",
    "lib*/python*/site-packages/gmsh*.libs/libgmsh.so*",
    "lib*/python*/site-packages/gmsh-*.dist-info/../libgmsh.so*",
):
    _gmsh_candidates += _glob.glob(os.path.join(sys.prefix, _pat))

# collect_all also picks up gmsh's own package data/binaries; keeps the
# frozen `gmsh` import consistent even if the explicit glob found nothing.
gmsh_datas, gmsh_binaries, gmsh_hidden = collect_all("gmsh")

_gmsh_binaries: list[tuple[str, str]] = []
if _gmsh_candidates:
    _GMSH_SO = sorted(set(_gmsh_candidates))[-1]
    _gmsh_binaries.append((_GMSH_SO, "."))
else:
    print("WARNING: PolyFoamMesh-linux.spec: libgmsh.so not found under "
          f"{sys.prefix}/lib* — relying on collect_all('gmsh'); verify the "
          "frozen app's GMSH path.")

# casadi ships many sibling shared libraries; collect_all keeps submodules,
# data and binaries consistent (same rationale as the Windows spec).
casadi_datas, casadi_binaries, casadi_hidden = collect_all("casadi")

a = Analysis(
    [os.path.join("src", "polyfoammesh", "app.py")],
    pathex=[],
    binaries=casadi_binaries + gmsh_binaries + _gmsh_binaries,
    datas=[("templates", "templates"), ("plugins", "plugins")] + casadi_datas + gmsh_datas,
    hiddenimports=[
        "PySide6.QtCore", "PySide6.QtWidgets", "PySide6.QtGui",
        "PySide6.QtNetwork", "gmsh", "meshio", "reportlab", "cadquery",
        "pyvista", "pyvistaqt", "numpy", "trimesh", "pymeshfix",
    ] + casadi_hidden + gmsh_hidden,
    hookspath=[],
    hooksconfig={},
    # Cross-platform guarded no-op on Linux (rthook only acts on win32).
    runtime_hooks=["rthook_casadi_dlls.py"],
    excludes=[
        "torch", "cv2", "pyarrow", "psycopg", "psycopg2",
        "sentencepiece", "av", "IPython",
        "jupyter", "notebook", "tensorflow", "sklearn",
    ],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PolyFoamMesh",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX is not guaranteed on the CI runner; skip cleanly.
    console=False,
    disable_windowed_traceback=True,
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
    upx=False,
    name="PolyFoamMesh",
)
