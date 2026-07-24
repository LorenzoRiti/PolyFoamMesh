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

## Iterazione 6 — Prossimi passi
- [ ] Verificare `_decide_fixes()` vs `QualityFixWorker._on_checkmesh_result()` — i factor dinamici calcolati in `_decide_fixes` sono ignorati, sostituiti da hardcoded
- [ ] Controllare FASE 3: tutti i 22 moduli commercial
- [ ] Controllare FASE 4: cross-module integration (import matching, signature matching)
- [ ] Controllare checklist finale bottoni/tab/menu
