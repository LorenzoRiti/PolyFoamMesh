# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['src\\cfmesh_autogui\\app.py'],
    pathex=[],
    binaries=[],
    datas=[('templates', 'templates'), ('plugins', 'plugins')],  # ✅ F-028: rimosso src/cfmesh_autogui (gia come scripts)
    hiddenimports=['PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtGui', 'PySide6.QtNetwork', 'gmsh', 'meshio', 'reportlab', 'cadquery', 'pyvista', 'pyvistaqt', 'numpy', 'trimesh'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
