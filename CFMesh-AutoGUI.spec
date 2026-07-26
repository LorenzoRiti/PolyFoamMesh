# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['src\\cfmesh_autogui\\app.py'],
    pathex=[],
    binaries=[],
    datas=[('templates', 'templates'), ('plugins', 'plugins')],  # ✅ F-028: rimosso src/cfmesh_autogui (gia come scripts)
    hiddenimports=['PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtGui', 'PySide6.QtNetwork', 'gmsh', 'meshio', 'reportlab', 'cadquery', 'pyvista', 'pyvistaqt', 'numpy', 'trimesh', 'pymeshfix'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # This dev machine's global Python environment has a lot of unrelated
    # packages installed for other projects (ML/robotics/DB work), and
    # PyInstaller's static analysis pulled several of them into the build
    # even though nothing in cfmesh_autogui imports them (confirmed via
    # grep across src/plugins/templates — zero hits for every name below).
    # casadi in particular crashed every launch of the frozen exe with
    # "DLL load failed while importing _casadi" (its own native deps
    # weren't collected correctly), even though the app never touches it.
    # Excluding these also cuts the exe from ~715MB back down toward the
    # ~330MB a build with only this app's actual dependencies produces.
    excludes=[
        'casadi', 'torch', 'cv2', 'pyarrow', 'psycopg', 'psycopg2',
        'sentencepiece', 'nlopt', 'av', 'IPython',
        'jupyter', 'notebook', 'tensorflow', 'sklearn',
    ],
    noarchive=False,
    optimize=2,  # ✅ F-029
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CFMesh-AutoGUI',
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
