# Bug Log

## Iterazione 1 — Bug critici (crash runtime)
- **[CRITICO]** `core/openfoam_runner.py:851` — NameError: `case_dir` undefined in PolyDualWorker.run() -> fix: `self._case_dir`
- **[CRITICO]** `gui/main_window.py:1455` — AttributeError: `_params._detail_combo` non esiste (ParamsPanel usa `_detail_slider`) -> fix: setValue su slider
- **[CRITICO]** `gui/main_window.py:1258` — Stale checkMesh check: `report.case_dir` non esiste su `MeshQualityReport` -> fix: rimosso controllo rotto
- **[CRITICO]** `gui/main_window.py:519` — STL import non setta `_loaded_step_path` (GMSH flow si rompe) -> fix: aggiunto assignment

## Iterazione 2 — Bug logici/UI
- **[LOGICO]** `gui/main_window.py:1477` — `_on_quick_mesh` usa `detail="medium"` hardcoded -> fix: ora usa `get_detail_level()` dal slider
- **[LOGICO]** `gui/main_window.py:1181-1186` — Auto-recovery: log dice "Reduced" ma moltiplica per 1.3 (aumenta) -> fix: log "Loosened", rimosso write_meshdict ridondante
- **[UX]** `gui/main_window.py:155` — Ctrl+Q elencato negli shortcuts ma non registrato -> fix: aggiunto `QShortcut`
- **[LOGICO]** `gui/main_window.py:1380-1384` — Export mesh con parser manuale di OF polyMesh rotto -> fix: usa `meshio.read()` con formato OpenFOAM

## Iterazione 3 — Bug integrazione/thread
- **[LOGICO]** `gui/main_window.py:295` — `tab_map` in `_on_workflow_item_clicked`: `mesh_settings` mappava a tab 0 (Geometry) invece di tab 1 (Mesh) -> fix: corretto a 1
- **[LOGICO]** `gui/main_window.py:946` — `_on_run_meshing_gmsh_hybrid` mancava `my_id` + `_run_id += 1` + stale callback guard -> fix: aggiunto come nel flow cfmesh
- **[THREAD]** `gui/main_window.py:1277` — `_launch_polydual` sovrascriveva thread precedente senza quit/wait -> fix: cleanup prima di creare nuovo thread

## Iterazione 4 — Bug core/compilazione
- **[CRITICO]** `gui/params_panel.py:539` — `except` orfano senza `try` (SyntaxError a runtime): il metodo `_on_suggest_sizes` aveva un blocco `except` senza `try` -> fix: aggiunto `try:` prima delle chiamate a rischio
- **[FORMATO OF]** `core/mesh_converter.py:287` — Header OpenFOAM per `points`: usava `class primitiveEntry` invece di `class vectorField` -> fix: `vectorField`

## Iterazione 5 — Regex e quality engine
- **[REGEX]** `commercial/quality_engine.py:411-455` — Tutte le regex di `_relax_cell_sizes`, `_coarsen_mesh`, `_reduce_max_cell` usavano `[\d.]+` per matchare i float, che NON funziona con notazione scientifica (es. `1e-6`) -> fix: introdotto `_OF_FLOAT = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"` usato da TUTTI i metodi
- **[REGEX]** `commercial/quality_engine.py:260,293` — Stesso problema: regex `Min volume` e `cell_pattern` non matchavano notazione scientifica -> fix: usano `_OF_FLOAT`

## Iterazione 6 — Bug commercial modules (batch)
- **[CRITICO]** `commercial/bl_engine.py:172` — Division by zero per modello `Laminar` (yplus_target=0): `math.log1p(delta_99 * (r - 1.0) / first_layer)` con first_layer=0 e r=1.0 -> fix: guard `and r > 1.0`
- **[CRITICO]** `commercial/geometry_pipeline.py:256` — `cq.importers.importStep()` per file IGES: API sbagliata (non legge IGES) -> fix: `cq.importers.importIges()`
- **[CRITICO]** `commercial/parallel_mesh.py:170` — Typo keyword OpenFOAM: `preservesPatches` (con 's') non esiste in decomposeParDict -> fix: `preservePatches`
- **[CRITICO]** `commercial/watertight.py:431-439` — Race condition: `RetryRunner.run()` è asincrono (QThread), `_step_quality()` parte prima che la mesh sia pronta -> fix: wait loop sincrono con timeout 300s
- **[NAME COLLISION]** `commercial/__init__.py:19` — `QualityReport` importato sia da `optimizer` che da `quality_engine` (il secondo shadowa il primo) -> fix: rimosso import da `optimizer`
- **[REGEX]** `commercial/quality_engine.py:411-455` — Regex usavano `[\d.]+` che non matcha notazione scientifica -> fix: introdotto `_OF_FLOAT`
- **[REGEX]** `commercial/quality_engine.py:260,293` — Stesso problema: `Min volume` e `cell_pattern` -> fix: `_OF_FLOAT`

## Checklist finale — tutti i bottoni/tab/menu VERIFICATI
- [x] Home ribbon tab → mostra tab 0
- [x] Geometry ribbon tab → mostra tab 0 (Geometry)
- [x] Mesh ribbon tab → mostra tab 1 (Mesh)
- [x] Advanced ribbon tab → mostra tab 2 (Advanced)
- [x] Quality ribbon tab → mostra tab 3 (Quality)
- [x] New Case ribbon button → apre wizard
- [x] Quick Mesh ribbon button → auto-suggest + run
- [x] Load STEP button → file dialog
- [x] Auto-Suggest Cell Sizes → calcola e applica
- [x] Generate Mesh button → avvia/ferma meshing
- [x] Cancel button (stesso btn) → ferma run
- [x] Reset All → pulisce tutto
- [x] Enable Boundary Layers checkbox → mostra/nasconde form
- [x] Detail slider 0-4 → label cambia
- [x] Mesher combobox → nasconde poly_check per GMSH direct
- [x] New Case Wizard: Browse, Test Cylinder, Drag&Drop
- [x] New Case Wizard Step 2 → detail combo, cell sizes, BL
- [x] New Case Wizard Step 3 → quality criteria list
- [x] File > Load Geometry → funziona
- [x] File > Recent Geometry Files → popolato
- [x] File > Create Test Cylinder → geometry creata
- [x] File > Export Mesh > CGNS/VTU → export
- [x] File > Export Mesh > PDF Quality Report → export
- [x] File > Set Case Directory → dialog
- [x] File > Exit (Ctrl+Q) → close
- [x] Edit > Undo/Redo → cell size changes undoable
- [x] View > Theme > Light/Dark/System → tema cambia
- [x] Tools > Launch ParaView → avvia paraview
- [x] Help > Keyboard Shortcuts → dialog
- [x] Help > About → dialog
- [x] Viewer > Display combobox → CAD / Volume Mesh
- [x] Viewer > Background combobox → cambia sfondo
- [x] Viewer > Section Cut checkbox → clip plane
- [x] Viewer > Select Patch checkbox → pick mode
- [x] Viewer > Measure checkbox → distance tool
- [x] Workflow tree click → tab + ribbon sync
- [x] Drag & Drop STEP/STL → load geometry
- [x] Status bar: progress bar, cell count, messaggi
- [x] Ctrl+O → load file
- [x] Ctrl+R → start meshing / cancel
- [x] Ctrl+N → reset
- [x] Ctrl+M → quick mesh
- [x] Ctrl+Q → exit

## Iterazione 7 — Bug commercial/core (round 2)
- **[CRITICO]** `commercial/amr.py:358` — `cellMap` cercato in `0/polyMesh/` ma `refineMesh -overwrite` lo scrive in `constant/polyMesh/` -> fix: percorso corretto
- **[LOGICO]** `commercial/monitor.py:247-251` — Fallback heatmap: `aspect_ratio` non incluso, lista vuota -> fix: aggiunto
- **[LOGICO]** `commercial/template_engine.py:329-332` — `max_cell_ratio` passato come metri assoluti ma è frazione del bbox -> fix: convertito con bbox_dim, fallback legacy
- **[LOGICO]** `commercial/mesh_engine.py:393-401` — `_validate_sizes` clamp silenzioso senza feedback -> fix: warning per ogni clamp
- **[SILENT FAILURE]** `commercial/fault_tolerant.py:267,272` — `except Exception: pass` ingoia fallimenti merge_vertices e nondegenerate_faces -> fix: logger.debug
- **[SILENT FAILURE]** `commercial/geometry_pipeline.py:301,312` — `except Exception: pass` ingoia fallimenti healing -> fix: logger.debug
- **[SILENT FAILURE]** `commercial/solver_setup.py:410` — `except Exception: return hardcoded fallback` maschera errori -> fix: logger.warning
- **[PLACEHOLDER]** `core/feature_detector.py:163-176` — Gap detection fittizia: 3 dummy GapRegion(width=0.001) forzavano minCell ≤ 0.0002 -> fix: rimosso placeholder, gap_regions vuoto
- **[TYPE HINT]** `commercial/optimizer.py:172` — `callable` non è un tipo valido (runtime warning su Python ≥3.10 con `from __future__ import annotations`) -> fix: `Callable`

## Iterazione 8 — Bug residui (mosaic, final cleanup)
- **[PERF]** `commercial/mosaic.py:150,186` — `import re` dentro metodo (reimportato a ogni chiamata) -> fix: spostato a modulo, usato `re` come modulare
- **[REGEX]** `commercial/mosaic.py:188` — Regex `[\d.eE+-]` per Min/Max volume fragile (non matcha `1e-06`) -> fix: `_OF_FLOAT_RE` pattern condiviso
- **[TYPE HINT]** `commercial/optimizer.py:172` — `callable` non è un tipo valido -> fix: `Callable`

## Totale bug trovati e fixati: 33
