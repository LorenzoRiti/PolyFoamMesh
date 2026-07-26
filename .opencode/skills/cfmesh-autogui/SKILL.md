---
name: cfmesh-autogui
description: CFMesh-AutoGUI — desktop standalone mesh generator for OpenFOAM via cfMesh/cartesianMesh. WSL2 + PySide6 + PyInstaller.
license: MIT
compatibility: opencode
metadata:
  project: cfmesh-autogui
  openfoam: v2512
  build: pyinstaller
---

# CFMesh-AutoGUI Project Knowledge

## Architecture Overview

```
src/cfmesh_autogui/
├── app.py                          # Entry point, splash screen, QApplication
├── config.py                       # OFConfig: WSL2 command builder, path conversion
├── commercial/
│   ├── parallel_mesh.py            # ParallelMeshEngine: MPI-based cartesianMesh
│   ├── mesh_engine.py              # MeshEngine: multi-algorithm dispatcher
│   ├── quick_mesh.py               # QuickMesh: one-click geometry→mesh
│   └── one_click_run.py            # FullAutoPipeline: full preprocessing pipeline
├── core/
│   ├── meshdict_gen.py             # write_meshdict() — the ONLY meshDict writer
│   ├── openfoam_runner.py          # MeshWorker, RetryRunner, ParallelMeshWorker
│   ├── geometry.py                 # suggest_cell_sizes, validate_cell_sizes
│   └── validation.py               # validate_cell_size
└── gui/
    ├── main_window.py              # MainWindow: orchestration, _do_meshing_pipeline
    ├── params_panel.py             # ParamsPanel: UI spinboxes, tabs
    └── viewer_widget.py            # ViewerWidget: PyVista 3D rendering
```

## Critical Code Paths

### MeshDict Write Flow (Generate Mesh Button)
1. `_on_run_meshing()` → WSL check → feature detection → `_do_meshing_pipeline()`
2. `_do_meshing_pipeline()` reads spinboxes → `raw_max/raw_min` → writes meshDict
3. Dispatches to parallel (`ParallelMeshWorker`) or serial (`RetryRunner`)
4. Engine NEVER writes meshDict — reads existing file only

### NEVER call `write_meshdict()` after `_do_meshing_pipeline()` has run
- `ParallelMeshEngine.run()` does NOT write meshDict
- `_run_cartesian_hex()` writes ONLY if meshDict doesn't exist
- `_regenerate_meshdict_without_bl()` writes ONLY during BL fallback (mesh failure only)

### Parallel Path
- `main_window.py:_run_parallel_mesh()` → `ParallelMeshWorker` → `ParallelMeshEngine.run()`
- `_clamp_cores_to_available_memory()` uses 50% WSL2 RAM, 300MB base + 30x surface per rank
- Returns 1 (force serial) if even 2 ranks can't fit
- Small meshes (<500K cells) forced to serial automatically
- Old worker signals MUST be disconnected before creating new worker
- `my_id` captured in closures to prevent stale callbacks

### Serial Path
- `RetryRunner` → `MeshWorker` → `build_command()` → cartesianMesh
- Timeout: 14400s (4 hours)
- Old runner MUST be cleaned up before creating new one: `_cleanup_runner()`

## Known Bug Patterns & Fixes

### MeshDict Overwrite (session 2026-07-26)
SYMPTOM: meshDict values differ from UI spinbox after clicking Generate Mesh.
ROOT CAUSES (ALL FIXED):
1. `_on_feature_detect_finished()` called `_max_cell.setValue(suggested_max)` — OVERWRITES user values
   FIX: removed setValue calls, feature values logged only
2. `ParallelMeshEngine.run()` called `_write_meshdict()` — OVERWRITES existing meshDict
   FIX: removed write from run(), meshDict must pre-exist
3. `RetryRunner.run()` received `safe_max/safe_min` (clamped) not `raw_max/raw_min` (user values)
   FIX: all paths use raw user values now
4. `_run_cartesian_hex()` always called `write_meshdict()` even when already present
   FIX: guard `if not mesh_dict_path.exists():`

### Edge Cases
- **First mesh fine, second mesh crashes**: old `RetryRunner` thread not cleaned up. FIX: `_cleanup_runner()` before creating new runner
- **Parallel crashes on complex geometries**: memory exhaustion. FIX: aggressive clamp (50% RAM, returns 1 if <2 ranks fit)
- **Mesh seems stuck after meshDict write**: estimate too low due to per-patch sizes. FIX: compute patch_sizes EARLY, use `min(safe_min, min(patch_sizes))` for estimate
- **Viewer freezes on large mesh**: PyVista tries to render all boundary faces. FIX: MAX_VIEWER_CELLS=10M, foamToVTK fast path, LRU cache

## Build Process
```powershell
# Rebuild frozen executable
pyinstaller --clean --noconfirm CFMesh-AutoGUI.spec
# Output: dist\CFMesh-AutoGUI.exe
```

- Must use `workdir = project root` for correct relative paths
- `.spec` file references `rthook_casadi_dlls.py` at project root
- Build takes ~4 minutes
- The `cfmesh-autogui` command (pip console script) runs SOURCE, not frozen exe

## Testing
```powershell
pytest tests/ -v --tb=short
```
Key test files:
- `tests/test_parallel_mesh.py` — ParallelMeshEngine + clamp tests + real WSL test
- `tests/test_mesh_engine.py` — algorithm selection, parameter validation
- `tests/test_meshdict_gen.py` — meshDict content generation
- `tests/test_config.py` — WSL path conversion, command building

## WSL2 Integration Patterns

### Memory Management
- WSL2 has its own memory cap (default ~50% host RAM)
- `free -m` from WSL gives available memory; parse `Mem:` line field 7 (available)
- Parallel ranks each load full geometry → memory scales with core count
- Memory estimate: 300MB base + 30x STL file size per rank

### Path Conversion
```python
# Windows → WSL: C:\path → /mnt/c/path
cfg.wsl_linux_case_path(Path("C:/case"))  # → "/mnt/c/case"
```

### File System
- OpenFOAM can't handle spaces in paths
- Native WSL tmpfs (/tmp/) REQUIRED for MPI (mmap/SIGSEGV on /mnt/c/)
- Script files need `newline=""` or Windows CRLF corrupts shebang

### MPI + cartesianMesh
- `OMPI_MCA_btl=^openib,openfabric,uct` — single negation prefix, NOT comma-separated
- `processorN/` dirs must exist with system/ + constant/ inside tmpfs
- `reconstructParMesh -constant` merges per-rank meshes back
- Cell counts extracted via awk on max owner index + 1 (no nCells: header in per-rank owner files)

## Qt Thread Safety
- QThreads must be properly cleaned up: `quit()` + `wait()` + `deleteLater()`
- Always disconnect old worker signals BEFORE creating new worker
- Always capture `my_id = self._run_id` in closures (not at runtime)
- Use `QMetaObject.invokeMethod(..., Qt.QueuedConnection)` for cross-thread calls
- `QThread: Destroyed while thread is still running` = thread leak bug

## Boundary Layer System

### BL params dict contract
```python
bl_params = {
    "nLayers": int,                # 1-20, capped by physics
    "thicknessRatio": float,       # growth ratio >1 (e.g. 1.2)
    "expansionRatio": float,       # legacy alias for growth (fallback)
    "firstLayerThickness": float,  # ABSOLUTE metres
    "wallPatches": list[str],      # patch names for BL application
}
```

### Auto-reduction in tight zones
`throat_detector.py:check_bl_throat_compatibility()` computes total BL thickness as geometric series and compares with 50% of the narrowest passage gap. If exceeded:
1. Halve nLayers first
2. Reduce firstLayerThickness second
3. Logs every reduction clearly

### cfMesh BL relaxation params (meshdict_gen.py)
```
maxThicknessToMedialRatio 0.3;      # caps layer growth near thin gaps
reCalculateNormals       1;         # recalc extrusion direction near sharp angles
maxBoundaryLayerAngle    60;        # skip BL on faces with normals beyond 60°
optimiseLayer           1;
untangleLayers          1;
```

### Fallback "retry without BL" — all paths:
- RetryRunner (serial): `_regenerate_meshdict_without_bl()` ✓
- mesh_engine parallel: BL fallback + serial fallback ✓
- mesh_engine serial: BL fallback ✓
- gmsh_hybrid: uses RetryRunner ✓
- gmsh_direct: now has BL→no-BL retry loop ✓
- quality_engine: regex-based disable/reduce BL ✓
- optimizer: disables BL on high non-ortho ✓

### Wall patch detection
`_is_wall_patch()` strips numeric suffixes, normalizes case, handles Italian names.
Treats everything not matching known non-wall keywords as wall.

### Physics clamping (bl_engine.py)
- firstLayerThickness clamped to [1e-9, ref_length*0.1]
- nLayers capped at 20 (was 40)
- growth_rate clamped to [1.05, 1.5]
- Total BL thickness capped at 30% of ref_length

## Key Config Values
- Parallel timeout: 14400s (4h)
- Serial timeout: 14400s (4h)
- Reconstruct timeout: 3600s (1h)
- checkMesh timeout: 600s (10min)
- Large mesh confirm: 2M cells OR 600s estimate
- Viewer max: 10M cells
- Auto-serial threshold: 500K cells
- Memory clamp: 50% available, 300MB + 30x surface per rank
- BL throat threshold: 50% of passage radius (auto-reduce)
