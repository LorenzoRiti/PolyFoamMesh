# CFMesh-AutoGUI — Bug Hunt Hard Mode

## Repository
`C:\Users\Davide Valoroso\cfmesh-autogui`

## Mission
Comb the entire `cfmesh-autogui` app from the first to the last line, find EVERY existing bug and fix it. Do not stop until you have inspected ALL the .py files and verified that ALL buttons/tabs/menus/items do what they should.

## Absolute constraints
1. **DO NOT repeat already-fixed bugs** — keep track in `_buglog.md`. Read it at the start.
2. **Priority**: runtime crash > wrong logic > broken UI/button that does nothing > performance > code smell.
3. **Every fix MUST**:
   - Identify file:line with `Read`
   - Explain the bug in Italian (what happens, what should happen)
   - Apply the fix with `Edit`
   - Verify with `python -m py_compile`
4. **Every iteration** at least 1 new bug found and fixed.
5. **At the end of every iteration**: compile ALL modified files, update `_buglog.md`, call `opencode_loop_goal_progress`.
6. **If a fix is complex** (>5 lines or multi-file), explain the plan before executing.

---

## INSPECTION PROTOCOL (in order, follow ALL the steps)

### PHASE 1 — BUTTONS AND UI (src/cfmesh_autogui/gui/)

For EVERY file in gui/:

**main_window.py:**
- Read `_setup_ribbon()` — does every ribbon button have a `clicked.connect`? Does the recipient exist?
- Read `_setup_menu()` — does every menu action have a working trigger? Is `Ctrl+Q` registered? Do `Ctrl+R`, `Ctrl+N`, `Ctrl+M` work?
- Read `_setup_workflow_tree()` — is `itemClicked` connected? Are the tab indexes in the map correct?
- Read `_on_ribbon_tab()` — does tab_index_map cover all ribbon buttons?
- Read `_on_workflow_item_clicked()` — do tab_map and ribbon_tab cover all stages?
- Read `_on_new_case_wizard()` — do the `self._params` attribute names exist? (`_detail_combo` exists? `_bl_checkbox` exists?)
- Read `_on_quick_mesh()` — correct parameters? Does it use `get_detail_level()` or hardcoded?
- Read `_on_auto_fix_quality()` — thread safety? `deleteLater`? Duplicate connections?
- Read `_on_export_mesh()` — is the OF polyMesh parser correct? Does it use meshio?
- Read `_on_export_pdf()` — does `quality_data` have the right structure for `MeshReportPDF.generate()`?
- Read `_on_load_step()` — is `_loaded_step_path` set for STL? For STEP?
- Read `_on_run_meshing()` — is `case_dir` created correctly? Does `guarded_finished` check `my_id`? Feature detection values validated?
- Read `_on_meshing_finished()` — auto-recovery: does cell size increase or decrease? Is the log accurate? Redundant `write_meshdict`?
- Read `_launch_checkmesh()` — thread quit/wait? `deleteLater`?
- Read `_on_checkmesh_finished()` — does `report.case_dir` exist on `MeshQualityReport`?
- Read `_on_launch_paraview()` — correct shell for Windows? Does `subprocess.Popen` catch exceptions?
- Read `closeEvent()` — runner terminated? gmsh shutdown? settings saved?

**params_panel.py:**
- `_on_quick_mesh()` — does parent traversal work? Does the widget hierarchy change?
- `_on_suggest_sizes()` — is `suggest_cell_sizes` called with `_suggest_meshes` populated?
- `_on_template_selected()` — does the template path exist? JSON parsed correctly?
- `_on_detail_changed()` — label updated? Correct 0-4 map?
- `_validate_params()` — max > min checked?
- `_on_mesher_changed()` — poly_check visibility toggle?
- `set_all_enabled()` — ALL widgets listed? None forgotten?
- `save_params()` / `restore_params()` — do the keys match the QSettings schema?

**viewer_widget.py:**
- `_init_vtk()` — pyvistaqt import safe? fails gracefully?
- `_on_pick_toggled()` / `_on_measure_toggled()` — mutual exclusion with blockSignals?
- `_on_mesh_picked()` — `pvdata_to_name` populated? memory_address stable?
- `_display_cad()` / `_display_mesh()` — clear before add? edge_color correct for background?
- `_on_section_toggled()` — face count check before clip?
- `read_openfoam_mesh_patches()` — cache works? boundary parsing matches?

**quality_panel.py:**
- `_load_thresholds()` — correct QSettings default type?
- `show_report()` — do the expected metrics exist? fails gracefully if missing?
- `_on_export_pdf()` — case_hint from raw_log works? `MeshReportPDF.generate()` correct parameters?
- `_on_auto_fix()` — signal `fix_requested` connected in main_window?

**new_case_wizard.py:**
- `Step1GeometryPage.registerField("geometry_path*", ...)` — required field works?
- `Step2MeshSettingsPage` — registered fields match `get_params()`?
- `NewCaseWizard.get_params()` — all fields present?

**pdf_report.py:**
- `generate()` — can `quality_data` be a bare dict or have a "metrics" key? Does it handle both?
- `_status()` correct thresholds? (nonortho: warn 65 fail 85, skew: warn 4 fail 10, aspect: warn 1000 fail 5000)

**about_dialog.py:**
- `_make_logo()` — QPainter started/finished? memory leak?
- `_qt_version()` — import safe?

**app.py:**
- `_init_i18n()` — translator path exists?
- `_load_plugins()` — `plugin_base.py` import safe? `on_install` exception safe?

**log_panel.py:**
- `append_log()` — thread-safe? html injection? scrollbar exists?

**theme.py, style.py, design_tokens.py, branding.py, constants.py, log_tags.py, settings_migration.py:**
- Circular imports? Constants defined before use? Migration version parsing?

---

### PHASE 2 — CORE LOGIC (src/cfmesh_autogui/core/)

**openfoam_runner.py:**
- `MeshWorker.run()` — reader thread interrupt? `_stop_reader` + `_reader_done` Event correct?
- `_kill_process_tree()` — Windows `taskkill /T` works? `wsl --terminate` fallback?
- `RetryRunner._on_attempt_finished()` — exit 15 handled? BL fallback works? Does `_on_finished` receive 3 arguments?
- `RetryRunner._cleanup_previous()` — thread quit/wait/deleteLater correct order? (B14 fix)
- `PolyDualWorker.run()` — `self.finished.emit(case_dir)` — **is case_dir defined?** (NameError!)
- `CheckMeshWorker.run()` — 120s timeout adequate? output parsing?
- `QualityFixWorker._on_checkmesh_result()` — `self._kwargs.get("boundary_cell_size")` passed from main_window?
- `parse_checkmesh_output()` — regex for all metrics? Does the Min volume regex cover scientific notation?
- `analyze_error()` — pattern matching for ALL known cfMesh errors?
- `_extract_detail()` — last N lines with error keyword?

**geometry.py:**
- `load_step()` — OCP exception caught? Workplane.val() safe?
- `load_stl()` — ASCII multi-solid parsing correct? temp files cleaned? binary STL handled?
- `load_geometry()` — extension dispatcher correct?
- `classify_faces_auto()` — normalized normal? adequate tolerance? fallback on other axes?
- `tessellate_patches()` — >50% failure raises RuntimeError? tolerance/angle_tolerance parameters?
- `analyze_local_thickness()` — per-patch allocation with floor? weighted average vs min-strategy?
- `suggest_cell_sizes()` — bbox fallback if sampling fails? correct clamping?
- `validate_cell_sizes()` — max > bbox/2 clamped? min > max/2 clamped? ratio warning?
- `compute_volume()` / `estimate_cell_count()` — non-finite volume? avg_cell <= 0?

**meshdict_gen.py:**
- `build_meshdict_lines()` — correct FoamFile syntax? boundaryLayers v2512 syntax? regex patch names with special characters?
- `write_meshdict()` — system_dir created? ASCII encoding?

**boundary_reader.py:**
- `parse_boundary()` — OF comments removed? multi-patch regex? FoamFile header skipped?

**case_setup.py:**
- `setup_case()` — all files created? 0/p, 0/U, 0/k, 0/omega, transportProperties, turbulenceProperties? correct dimensions? BCs mapped by patch name?

**mesh_converter.py:**
- `msh_to_of_polymesh()` — cell_phys_map per physical group tag? correct boundary face extraction? startFace computed well? points/faces/owner/neighbour/boundary written in valid OF format?

**validation.py:**
- `validate_cell_size()` — correct ranges? bbox check? warning for extremes?
- `validate_bl_params()` — nLayers 1-10? thicknessRatio >0 <=1? expansionRatio 1-2?
- `validate_settings()` — ALL QSettings keys validated?

**gmsh_wrapper.py:**
- `_ensure_gmsh()` — double-checked locking thread-safe?
- `compute_sizing()` — bounding box computed? curvature estimation?
- `generate_surface_stl()` — 2D mesh generation? STL export? physical groups mapping?
- `generate_volume_mesh()` — 3D mesh + BL? BoundaryLayer field config? MSH export?

**feature_detector.py:**
- `analyze_step()` — cache key with mtime? curvature from getCurvature? mesh-based dihedral angle?
- GMSH initialize/finalize balanced? mesh.clear() called?
- Is gap detection a placeholder or real?

**stl_writer.py:**
- `heal_mesh()` — process/validate? degenerate faces? fill_holes? merge_vertices? fix_normals?
- `write_binary_stl()` / `write_ascii_stl()` — patch name sanitized? valid STL format?
- `export_surface_file()` — constant/triSurface/ created?

---

### PHASE 3 — COMMERCIAL MODULES (src/cfmesh_autogui/commercial/)

For EVERY file in commercial/:

**quick_mesh.py, one_click_run.py, geometry_pipeline.py, mesh_engine.py, quality_engine.py, bc_editor.py, solver_setup.py, template_engine.py, exporter.py, parallel_mesh.py, amr.py, bl_engine.py, cad_healer.py, fault_tolerant.py, mosaic.py, monitor.py, optimizer.py, batch_mesh.py, cloud_mesh.py, journal.py, watertight.py:**

- Lazy import works (global module variables)?
- Does every function have type hints?
- Does every dataclass/class have consistent fields?
- Octopoda logging present?
- Exceptions caught? fail gracefully?
- UI integration points (main_window calls) exist?

---

### PHASE 4 — INTEGRATION (cross-module)

- Does `main_window.py` import `compute_sizing` as `gmsh_compute_sizing` — does the name match the `gmsh_wrapper.py` export?
- Does `main_window.py` import `from_step as gmsh_read_step` — does it exist in `gmsh_wrapper.py`?
- Does `commercial/quick_mesh.py` use `_lazy_geom().suggest_cell_sizes(...)` — signature matching?
- Is `commercial/mesh_engine.py` called by `main_window._on_run_meshing()`? Or only by `quick_mesh.py`?
- Is `setup_case()` called by `_on_meshing_finished` — patches in correct format? (`boundary_reader.PatchInfo`)
- Is `export_surface_file()` called by `_on_run_meshing()` before `write_meshdict()`?
- Is `gmsh_shutdown()` called in `closeEvent()`?

---

### PHASE 5 — FINAL COMPILATION

After inspecting ALL the files:
```powershell
Get-ChildItem -Path "C:\Users\Davide Valoroso\cfmesh-autogui\src\cfmesh_autogui" -Filter "*.py" -Recurse | ForEach-Object { python -m py_compile $_.FullName }
```

If a file does not compile -> fix -> repeat.

---

### PHASE 6 — FINAL REPORT

Create/update `_buglog.md` with this format:
```markdown
# Bug Log

## Iteration 1
- **[CRITICAL]** `core/openfoam_runner.py:851` — NameError: `case_dir` undefined in PolyDualWorker.run() -> fix: `self._case_dir`
- **[CRITICAL]** `gui/main_window.py:1455` — AttributeError: `_params._detail_combo` does not exist (ParamsPanel uses _detail_slider) -> fix: setValue on slider
- **[CRITICAL]** `gui/main_window.py:1258` — Stale checkMesh check: `report.case_dir` does not exist on MeshQualityReport -> fix: removed
- **[CRITICAL]** `gui/main_window.py:519` — STL import does not set `_loaded_step_path` -> fix: added

## Iteration 2
- ... new bugs ...

## Iteration N
- ...
```

---

## FINAL CHECKLIST (all buttons/tabs/menus)

- [ ] Home ribbon tab → shows tab 0
- [ ] Geometry ribbon tab → shows tab 0 (Geometry)
- [ ] Mesh ribbon tab → shows tab 1 (Mesh)
- [ ] Advanced ribbon tab → shows tab 2 (Advanced)
- [ ] Quality ribbon tab → shows tab 3 (Quality)
- [ ] New Case ribbon button → opens wizard
- [ ] Quick Mesh ribbon button → auto-suggest + run
- [ ] Load STEP button → file dialog
- [ ] Auto-Suggest Cell Sizes → computes and applies
- [ ] Generate Mesh button → starts meshing (disables during run)
- [ ] Cancel button (same btn, text changes) → stops run
- [ ] Reset All → clears everything
- [ ] Enable Boundary Layers checkbox → shows/hides form
- [ ] Detail slider 0-4 → label changes
- [ ] Mesher combobox → hides poly_check for GMSH direct
- [ ] New Case Wizard Step 1 → Browse, Test Cylinder, Drag&Drop
- [ ] New Case Wizard Step 2 → detail combo, cell sizes, BL
- [ ] New Case Wizard Step 3 → quality criteria list
- [ ] File > Load Geometry → works
- [ ] File > Recent Geometry Files → populated
- [ ] File > Create Test Cylinder → geometry created
- [ ] File > Export Mesh > CGNS/VTU → export
- [ ] File > Export Mesh > PDF Quality Report → export
- [ ] File > Set Case Directory → dialog
- [ ] File > Exit (Ctrl+Q) → close
- [ ] Edit > Undo/Redo → cell size changes undoable
- [ ] View > Theme > Light/Dark/System → theme changes
- [ ] Tools > Launch ParaView → launches paraview
- [ ] Help > Keyboard Shortcuts → dialog
- [ ] Help > About → dialog
- [ ] Viewer > Display combobox → CAD / Volume Mesh
- [ ] Viewer > Background combobox → changes background
- [ ] Viewer > Section Cut checkbox → clip plane
- [ ] Viewer > Select Patch checkbox → pick mode
- [ ] Viewer > Measure checkbox → distance tool
- [ ] Workflow tree click → tab + ribbon sync
- [ ] Drag & Drop STEP/STL → load geometry
- [ ] Status bar: progress bar, cell count, messages
- [ ] Ctrl+O → load file
- [ ] Ctrl+R → start meshing / cancel
- [ ] Ctrl+N → reset
- [ ] Ctrl+M → quick mesh
- [ ] Ctrl+Q → exit
