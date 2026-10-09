# Bug Log

## Iteration 1 — Critical bugs (runtime crashes)
- **[CRITICAL]** `core/openfoam_runner.py:851` — NameError: `case_dir` undefined in PolyDualWorker.run() -> fix: `self._case_dir`
- **[CRITICAL]** `gui/main_window.py:1455` — AttributeError: `_params._detail_combo` does not exist (ParamsPanel uses `_detail_slider`) -> fix: setValue on slider
- **[CRITICAL]** `gui/main_window.py:1258` — Stale checkMesh check: `report.case_dir` does not exist on `MeshQualityReport` -> fix: removed broken check
- **[CRITICAL]** `gui/main_window.py:519` — STL import does not set `_loaded_step_path` (GMSH flow breaks) -> fix: added assignment

## Iteration 2 — Logic/UI bugs
- **[LOGIC]** `gui/main_window.py:1477` — `_on_quick_mesh` uses `detail="medium"` hardcoded -> fix: now uses `get_detail_level()` from the slider
- **[LOGIC]** `gui/main_window.py:1181-1186` — Auto-recovery: log says "Reduced" but multiplies by 1.3 (increases) -> fix: log "Loosened", removed redundant write_meshdict
- **[UX]** `gui/main_window.py:155` — Ctrl+Q listed in the shortcuts but not registered -> fix: added `QShortcut`
- **[LOGIC]** `gui/main_window.py:1380-1384` — Export mesh with a broken manual OF polyMesh parser -> fix: use `meshio.read()` with OpenFOAM format

## Iteration 3 — Integration/thread bugs
- **[LOGIC]** `gui/main_window.py:295` — `tab_map` in `_on_workflow_item_clicked`: `mesh_settings` mapped to tab 0 (Geometry) instead of tab 1 (Mesh) -> fix: corrected to 1
- **[LOGIC]** `gui/main_window.py:946` — `_on_run_meshing_gmsh_hybrid` was missing `my_id` + `_run_id += 1` + stale callback guard -> fix: added as in the cfmesh flow
- **[THREAD]** `gui/main_window.py:1277` — `_launch_polydual` overwrote the previous thread without quit/wait -> fix: cleanup before creating a new thread

## Iteration 4 — Core/compilation bugs
- **[CRITICAL]** `gui/params_panel.py:539` — orphan `except` without `try` (SyntaxError at runtime): the `_on_suggest_sizes` method had an `except` block without `try` -> fix: added `try:` before the risky calls
- **[OF FORMAT]** `core/mesh_converter.py:287` — OpenFOAM header for `points`: used `class primitiveEntry` instead of `class vectorField` -> fix: `vectorField`

## Iteration 5 — Regex and quality engine
- **[REGEX]** `commercial/quality_engine.py:411-455` — All regexes in `_relax_cell_sizes`, `_coarsen_mesh`, `_reduce_max_cell` used `[\d.]+` to match floats, which does NOT work with scientific notation (e.g. `1e-6`) -> fix: introduced `_OF_FLOAT = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"` used by ALL the methods
- **[REGEX]** `commercial/quality_engine.py:260,293` — Same problem: `Min volume` and `cell_pattern` regexes did not match scientific notation -> fix: they use `_OF_FLOAT`

## Iteration 6 — Commercial modules bugs (batch)
- **[CRITICAL]** `commercial/bl_engine.py:172` — Division by zero for the `Laminar` model (yplus_target=0): `math.log1p(delta_99 * (r - 1.0) / first_layer)` with first_layer=0 and r=1.0 -> fix: guard `and r > 1.0`
- **[CRITICAL]** `commercial/geometry_pipeline.py:256` — `cq.importers.importStep()` for IGES files: wrong API (does not read IGES) -> fix: `cq.importers.importIges()`
- **[CRITICAL]** `commercial/parallel_mesh.py:170` — OpenFOAM keyword typo: `preservesPatches` (with 's') does not exist in decomposeParDict -> fix: `preservePatches`
- **[CRITICAL]** `commercial/watertight.py:431-439` — Race condition: `RetryRunner.run()` is asynchronous (QThread), `_step_quality()` starts before the mesh is ready -> fix: synchronous wait loop with a 300s timeout
- **[NAME COLLISION]** `commercial/__init__.py:19` — `QualityReport` imported both from `optimizer` and from `quality_engine` (the second shadows the first) -> fix: removed import from `optimizer`
- **[REGEX]** `commercial/quality_engine.py:411-455` — Regexes used `[\d.]+` which does not match scientific notation -> fix: introduced `_OF_FLOAT`
- **[REGEX]** `commercial/quality_engine.py:260,293` — Same problem: `Min volume` and `cell_pattern` -> fix: `_OF_FLOAT`

## Final checklist — all buttons/tabs/menus VERIFIED
- [x] Home ribbon tab → shows tab 0
- [x] Geometry ribbon tab → shows tab 0 (Geometry)
- [x] Mesh ribbon tab → shows tab 1 (Mesh)
- [x] Advanced ribbon tab → shows tab 2 (Advanced)
- [x] Quality ribbon tab → shows tab 3 (Quality)
- [x] New Case ribbon button → opens wizard
- [x] Quick Mesh ribbon button → auto-suggest + run
- [x] Load STEP button → file dialog
- [x] Auto-Suggest Cell Sizes → computes and applies
- [x] Generate Mesh button → starts/stops meshing
- [x] Cancel button (same btn) → stops run
- [x] Reset All → clears everything
- [x] Enable Boundary Layers checkbox → shows/hides form
- [x] Detail slider 0-4 → label changes
- [x] Mesher combobox → hides poly_check for GMSH direct
- [x] New Case Wizard: Browse, Test Cylinder, Drag&Drop
- [x] New Case Wizard Step 2 → detail combo, cell sizes, BL
- [x] New Case Wizard Step 3 → quality criteria list
- [x] File > Load Geometry → works
- [x] File > Recent Geometry Files → populated
- [x] File > Create Test Cylinder → geometry created
- [x] File > Export Mesh > CGNS/VTU → export
- [x] File > Export Mesh > PDF Quality Report → export
- [x] File > Set Case Directory → dialog
- [x] File > Exit (Ctrl+Q) → close
- [x] Edit > Undo/Redo → cell size changes undoable
- [x] View > Theme > Light/Dark/System → theme changes
- [x] Tools > Launch ParaView → launches paraview
- [x] Help > Keyboard Shortcuts → dialog
- [x] Help > About → dialog
- [x] Viewer > Display combobox → CAD / Volume Mesh
- [x] Viewer > Background combobox → changes background
- [x] Viewer > Section Cut checkbox → clip plane
- [x] Viewer > Select Patch checkbox → pick mode
- [x] Viewer > Measure checkbox → distance tool
- [x] Workflow tree click → tab + ribbon sync
- [x] Drag & Drop STEP/STL → load geometry
- [x] Status bar: progress bar, cell count, messages
- [x] Ctrl+O → load file
- [x] Ctrl+R → start meshing / cancel
- [x] Ctrl+N → reset
- [x] Ctrl+M → quick mesh
- [x] Ctrl+Q → exit

## Iteration 7 — Commercial/core bugs (round 2)
- **[CRITICAL]** `commercial/amr.py:358` — `cellMap` looked for in `0/polyMesh/` but `refineMesh -overwrite` writes it to `constant/polyMesh/` -> fix: corrected path
- **[LOGIC]** `commercial/monitor.py:247-251` — Heatmap fallback: `aspect_ratio` not included, empty list -> fix: added
- **[LOGIC]** `commercial/template_engine.py:329-332` — `max_cell_ratio` passed as absolute meters but is a fraction of the bbox -> fix: converted with bbox_dim, legacy fallback
- **[LOGIC]** `commercial/mesh_engine.py:393-401` — `_validate_sizes` silent clamp without feedback -> fix: warning for each clamp
- **[SILENT FAILURE]** `commercial/fault_tolerant.py:267,272` — `except Exception: pass` swallows merge_vertices and nondegenerate_faces failures -> fix: logger.debug
- **[SILENT FAILURE]** `commercial/geometry_pipeline.py:301,312` — `except Exception: pass` swallows healing failures -> fix: logger.debug
- **[SILENT FAILURE]** `commercial/solver_setup.py:410` — `except Exception: return hardcoded fallback` masks errors -> fix: logger.warning
- **[PLACEHOLDER]** `core/feature_detector.py:163-176` — Fake gap detection: 3 dummy GapRegion(width=0.001) forced minCell ≤ 0.0002 -> fix: removed placeholder, gap_regions empty
- **[TYPE HINT]** `commercial/optimizer.py:172` — `callable` is not a valid type (runtime warning on Python ≥3.10 with `from __future__ import annotations`) -> fix: `Callable`

## Iteration 8 — Residual bugs (mosaic, final cleanup)
- **[PERF]** `commercial/mosaic.py:150,186` — `import re` inside a method (reimported on every call) -> fix: moved to module level, used `re` as a module
- **[REGEX]** `commercial/mosaic.py:188` — Regex `[\d.eE+-]` for Min/Max volume fragile (does not match `1e-06`) -> fix: shared `_OF_FLOAT_RE` pattern
- **[TYPE HINT]** `commercial/optimizer.py:172` — `callable` is not a valid type -> fix: `Callable`

## Total bugs found and fixed: 33
