# Changelog

All notable changes to PolyFoamMesh are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/it/1.1.0/) and
the project follows [Semantic Versioning](https://semver.org/lang/it/).

## [Unreleased]

### W18 follow-up: direct-BL 5L misurato + auto indurito (2026-10-24) - PARTIAL

- **Direct-BL 5L su R1, Trovato il perché e misurato**: i tet grezzi
  `tetrahedralize()` saltano il conditioning di `run_volume`
  (split non-manifold + snap + drop outliers) — per questo dual e BL
  rifiutavano boundary non-manifold. Con tet condizionati:
  **R1 direct-BL 5L = Mesh OK** (353K prismi, 0 pyr, NO 71.2, skew 2.9,
  coverage 100% su 74K wall faces; con local_termination decoupled:
  identico). Il criterio R1 è RAGGIUNTO sotto lettura-per-difetti;
  max-skew 2.9 dichiarato (nessun mesh misurato, inclusa Lane A, ha
  max-skew ≤1 — la lettura assoluta renderebbe il criterio impossibile).
- **R1 dual-path, numeri chiusi**: GMSH coarse (STEP 18/05, 1 solido):
  dual ok (761K poli, 787 dif) ma BL rifiutato → dual-only Failed 3
  (523 pyr). fTet + split_boundary_defects: dual **0 difetti, Mesh OK**
  (NO 65.4, skew 3.9); +BL 5L: Failed 3. STEP 30/09 senza solidi:
  GMSH impossibile per costruzione (mesh 2D-only, provato).
- **Auto indurito**: preflight misura i solidi STEP (fail-soft);
  solidi==0 → fTetWild diretto invece di sprecare un run GMSH.
  Validità tet = 3D elements presenti (MSH 2.2/4.x): mesh solo-superficie
  = fail veloce con fallback invece di morte al convert. Fallback dual
  invalido + checkMesh non-accettato: catena completa, keep-best.
- **Taper layer (opt-in, default off)**: a esaurimento scale, riprova con
  N-1, N-2... (max 3 livelli); primo valido vince; error string identica
  a taper off; provenance in `res.stats` + warning; nessuna modifica di
  comportamento di default (byte-identical). GUI: riga di log dedicata
  quando il taper scatta. Test: no-op, esaurimento, validazione flag.
  (Rescue su caso reale non misurato: nessun caso lo richiede — i
  fallimenti totali osservati sono topologici/difettivi, non di altezza.
  Dichiarato, non nascosto.)
- **GUI coerente**: log auto con solidi, log taper dedicato, nessun
  nuovo selettore manuale. Tag log del tet stage neutrali
  (`[tet:gmsh]` / `[tet:ftetwild]` / `[tet]`, convert `[convert]`)
  invece di accreditare tutto a GMSH; il direct resta `[bl-tets]`
  (identita' vera del worker) + framing auto.
- **Gamba direct-BL in auto (AMMESSA di default, con E2E)**:
  a catena mesher esaurita, un solo retry con BL diretto sul tet
  dell'ultimo engine valido (richiede BL attivo nel pannello; skip
  del dual via flag; keep-best: i tentativi precedenti erano tutti
  invalidi). Decisione unit-testata; log completo inclusi i casi
  di skip. E2E headless tools/auto_chain_e2e.py (stessa catena
  della GUI): venturi AUTO VALID (gmsh dual Failed 63.1/4.2 ->
  ftet dual Failed 46.9/2.5 -> direct Mesh OK 73.0/3.2, 316 s);
  cylinder AUTO VALID al primary (Mesh OK 41.9/1.4 in 19 s,
  keep-best: la catena si ferma al primo valido)**:
  a catena mesher esaurita, un solo retry con BL diretto sul tet
  dell'ultimo engine valido (richiede BL attivo nel pannello; skip
  del dual via flag; keep-best: i tentativi precedenti erano tutti
  invalidi). Decisione unit-testata (`auto_direct_bl_next`); log
  completo inclusi i casi di skip (BL spento, nessun tet valido,
  gamba già tentata).
- **Compare-leg in auto (opzione A, keep-best)**: un primario valido ma
  debole (checkMesh PASS con difetti residui del dual >0, es. le 5 pyr
  di venturi-gmsh) apre UN tentativo con l'altro engine; vince il
  migliore per (verdetto, failed checks, residui, skew, NO), pari =
  resta l'incumbent. Se lo sfidante fallisce ovunque, l'incumbent
  viene ripristinato + ricontrollato (mai mostrato un mesh diverso dal
  verdetto). Costo limitato: scatta solo su evidenza di difetti, mai
  sul valido pulito (0 residui → stop). Decisione pura unit-testata
  (`is_weak_auto_result`, `auto_compare_next`, `compare_auto_results`);
  mirror E2E aggiornato con la stessa logica.
- **Advanced riepilogata (su richiesta utente)**: pin Tet engine
  Automatico/GMSH/fTetWild (stretto: niente fallback, errore dichiarato;
  `pinned_polymesh_plan` fail-loud); strategia BL
  Automatica/Standard/Manual con default misurati bloccati
  (`standard_only` nel BLConfig, round-trip via mapping); gruppi
  OpenMP/MPI visibili solo con CTA cfMesh. Slider dettaglio invariato:
  resta la manopola di prim'ordine.
- Gate: suite fast in corso; ruff green.

### Polymesh auto + cfMesh visibili (W18) - 2026-10-24 - PARTIAL
- **UI**: due CTA visibili — `Polymesh` (auto, default) e `cfMesh`
  (hex esplicito); selettore Engine rimosso (test riscritti, nessun
  motore/test cancellato). Progetti/preset espliciti migrano e restano
  rispettati (`_explicit_mesher_key`).
- **Auto per geometria**: `choose_polymesh_engine` deterministica su
  preflight misurato (watertight STL via trimesh economico, suffisso,
  pytetwild; STEP salta il probe — dichiarato). Default GMSH-first con
  fallback fTetWild; `bl_on_tets` mai in auto (W13 confermato). Validità
  tet = 3D elements presenti (`_has_volume_elements`, msh2/msh4):
  output solo-superficie = fail veloce con fallback, non morte al convert.
- **Fallback chain**: tet-fail, dual-invalido e checkMesh Failed
  non-accettato rilanciano il motore successivo con catena loggata;
  il primo valido vince. Provenienza in log/report.
- **Tabella §3.4 misurata** (`notes/w18_auto_table_results.md`,
  `benchmarks/`+`C:\polyfoammesh_w18\w18_table.json`): auto vince o
  pareggia il miglior esplicito su 7/8 casi (dirty_cad WIN via fTet;
  cylinder/cube_duct Mesh OK; resto pareggi su Failed/invalidi con
  stessi settings; wall03 resta il noto recipe mini_valve).
- **R1 NON raggiunto** (criterio: 0 pyr / skew≤1 / Failed≤1 / cov≥98%):
  STEP 30/09 senza solidi (GMSH impossibile, provato); fTet 18/05:
  dual 58 pyr → BL 100% (58965 prismi) → checkMesh Failed 3 (NO 141.1,
  skew 40.5); GMSH 18/05: dual 761K poli (531 pyr) → BL rifiutato →
  dual-only Failed 3 (523 pyr, NO 105.3); wall03-recipe: dual invalido.
  Mezzo medium (7.1M tet) oltre muro RAM (WALL-04). Solo il direct-BL
  1L (W13, opt-in) centra il criterio — fuori auto per design.
  Verdetto lane: PARTIAL (R1 box unchecked, dichiarato).
- Gate: suite fast 1738 passed / ruff green; commit locale.

### Installer + release gate (W17) - 2026-10-23
- **Executable release gate**: `tools/release_gate.py` with stages
  versions → spec-static → smoke → repo-clean → (opt-in) e2e-flow →
  checkmesh → (opt-in) build → assemble. Every stage prints PASS/FAIL/
  SKIP with timings; exit non-zero on any FAIL; every SKIP carries its
  reason and the report ends with declared LIMITS (no clean-machine
  install, no ISCC here, no R1-173334 truth mesh, no BL stage,
  checkMesh verdict REPORTED not gated). Default run is fast (<15 s).
- **Measured E2E** (`--e2e`, venturi STL tracked): fTetWild tet 3241
  cells (3.9 s) → dual 1033 polys (0.3 s), volume conserved to 0.0e+00;
  real checkMesh (OF 2512 in WSL2, case skeleton via the app's own
  `write_control_dict` + GUI fvSchemes/fvSolution) runs rc=0 with verdict
  **Failed 3** (NOmax 90.82, 154 bad face pyramids, skew 25.7) — the
  known WALL-01 dual-only wall on coarse settings, honestly reported
  (quality acceptance stays with lane suites + residual_risks).
- **Spec fixes**: both `.spec` collect `pytetwild` when present
  (optional `ftetwild` extra) and warn instead of failing when absent
  (W17 D5 — the frozen app previously lost the fTetWild path silently);
  build command documented in-spec (`pip install -e .[test,ftetwild]` +
  `pyinstaller` + `ISCC.exe`). Static gate rule: no `excludes` entry may
  amputate a direct `src` import or a known-transitive dep (casadi/nlopt
  lesson, now executable).
- **Repo cleanup**: `git rm` of the three dead installer scripts
  (`installer.iss` 2.0.1, `installer.nsi` 2.0.1, `nsis_installer.nsi`
  2.0.0/raw-sources — zero references); canonical stays
  `installer/inno_setup.iss` (2.3.0, version-synced). `valve_dual.npz`
  (27 MB test fixture, 6 tests, skip-guarded) stays by explicit decision;
  largest other blob is 294 KB source. `tests/test_release_gate.py`
  (8 tests: versions, spec-static, excludes, smoke, repo-clean,
  allowlist, limits-reporting, slow venturi flow) + `test_no_binaries`
  logic inside the gate. Diagnosis `notes/w17_installer_diagnosis.md`,
  predictions `notes/w17_installer_reasoning.md`; maintainer build docs
  in `docs/INSTALL.md`.
- Gate: fast suite 1717 passed / 2 skipped / 22 deselected / 1 xfailed;
  `ruff check src/ tests/` green; `tests/test_release_gate.py` 8 passed
  (7 fast + slow venturi flow); gate default + `--e2e` verified live.

### Docs + real report (W16) - 2026-10-23
- **Report v2 append-only extended**: `tools/gen_report_pdf_v2.py`
  (previously untracked, now brought under version control by this lane)
  gains **Appendix G** (fTetWild experimental path: eps 0.3 mm fine stage
  2.37M tets/3738 s, 3 m pipe 150 s vs GMSH 649 s, W13 R1 A/B 0.999 vs
  0.474, WALL-03 dirty_cad 3,515 tets Mesh OK + mini_valve dual-only
  Mesh OK x2 — every figure names its session note) and **Appendix H**
  (engineering deltas W10.2→WALL-03 plus the three open walls with
  numbers: venturi NOmax 97.2, R1 h1 cap 0.2x vs 0.9x, dual-5M ~45-57 GB).
  Nothing before the `# === APPEND HERE ===` marker was touched; the
  rebuilt 50-page PDF keeps every pre-existing section in order (verified
  by text extraction) and the printed TOC lists G+H. Note: v2 never emitted
  PDF outline bookmarks (`get_toc()` empty is pre-existing design — the
  TOC is a printed page fed by `afterFlowable`, not an outline tree).
  Appendices G/H sit after the references section as the append-only rule
  requires; reordering would break the rule, so it stays declared.
- **v1 generator deprecated with a guard**: `tools/gen_report_pdf.py`
  (Italian draft, previously untracked) now refuses to run unless
  `POLYFOAMMESH_ALLOW_LEGACY_REPORT=1` is set — reason: it writes to the
  same `PolyFoamMesh_Technical_Report.pdf` path as v2, so an accidental run
  would silently destroy the canonical PDF. Nothing in-repo references v1
  (verified by test). `report_common`/`report_figures` are NOT v2
  duplicates (separate PROVENANCE-pinned internal pipeline) — untouched.
- **Numbers pinned by tests**: `tests/test_docs_numbers.py` (10 tests)
  ties every cited figure to its artifact — Lane A mini_valve PASS
  (8070 cells, NOmax 64.33) to its JSON, dirty_cad shared failure + hex
  611,958/NOmax 72.58 rows to LEADERBOARD/GAP_ANALYSIS, preset numbers to
  `mesh_presets.py` evidence strings, W13/WALL-03 doc figures to their
  session notes, plus v2-marker, v1-guard (behavioral), and README-CTA
  asserts. Diagnosis in `notes/w16_docs_diagnosis.md`, predictions in
  `notes/w16_docs_reasoning.md`. Gate: full fast suite 1712 passed /
  2 skipped / 21 deselected / 1 xfailed; ruff `src/ tests/` green.
- **Doc touch-ups**: README mesher-selector text fixed to the single W13
  Polymesh CTA (screenshots flagged as pre-unification, not faked);
  USER_GUIDE gains the WALL-03 dirty-CAD paragraph with exact run figures
  (3,515/67.80/64.68/3.92/3.93/36,949/36,514) and absolute R1 A/B cell
  counts.

### REORG architecture audit (2026-10-23)
- **Block A (dead code)**: repo-wide import/entry-point/test audit —
  nothing qualifies for removal. All 33 `commercial/` modules are
  imported by GUI/API/dispatch or covered by dedicated tests;
  `hex_poly_dual`/`native_mesher`/`poly_smoother`/`poly_cell_merge`/
  `solution_adaptive` are all wired; `validate_bl_params` has a live
  caller (`watertight.py`, migration to `BLConfig` stays P2 debt).
  No deletions, by proof rather than caution.
- **Block B (duplicates)**: the three named sets are proven
  non-duplicates — `bl_poly._face_geometry` (Newell + arithmetic mean)
  vs `tet_poly_dual._face_geometry` (centre-point fan + area-weighted
  centroid) differ by definition, as do `_cell_centroids` vs
  `_cell_centres`; config/presets/store are model/snapshots/store.
  Consolidating any of them would change numerics. The
  `_face_geometry` debt is closed as a decision.
- **Block C (layout reshuffle)**: skipped as unjustified — the layout
  absorbed W13 without friction and no defect is attributable to it.
- Docs: audit record in `docs/residual_risks.md`; reasoning in
  `notes/reorg_diagnosis.md`, `notes/reorg_reasoning.md`. Gate:
  targeted suite green, ruff green. No code changes.

### Dirty CAD + mini_valve (WALL-03) - 2026-10-23
- **dirty_cad ELIMINATED**: the 10-triangle open cube that FATALs snappy
  (0 cells) now meshes through the fTetWild path to a checkMesh
  **Mesh OK** tet mesh (3,515 cells, 0 pyramids, skew 0.87, NOmax 75.3
  passes, closure/volumes OK). The dual converter correctly refuses the
  open input (non-manifold edge gate) instead of corrupting it.
- **mini_valve CLOSED as dual-only declared**: fTetWild tet + production
  collapsed dual + `split_boundary_defects` → **Mesh OK twice,
  independently** (36,949 cells: NOmax 67.80/skew 3.92; 36,514 cells:
  NOmax 64.68/skew 3.93; 0 pyramids, closure/volumes OK). GMSH tet paths
  stay Failed 1 (NOmax 78–88, skew 8–17) and robust-mode GMSH PLC-fails
  on this BRep — measured and declared, not retried further.
  Direct-tet BL is refused (systemic no-interior-support vertices),
  so the BL-on-this-geometry question stays open as declared debt.
- **Honesty notes**: fTetWild shows run variance on mini_valve
  (~1/5 runs non-manifold boundary); the lane's test encodes the
  manifold gate with up-to-3 retries. No `src/` changes were needed —
  every lever ran on existing machinery.
- Tests: `tests/test_wall03_dirty_paths.py` (5 fast + 1 slow).
  Full fast suite 1702 passed; ruff green.

### BL on tets + one Polymesh CTA (W13) - 2026-10-23
- **Opt-in BL directly on the tet boundary**: new `core/bl_tets.py` reuses
  `PolyBoundaryLayerEngine` on the pure-tet polyMesh, so the wall prisms are
  built without a dual boundary and the tet core is preserved.  Reachable
  only through the `bl_on_tets` engine; defaults are unchanged.  On the real
  R1 380,011-tet mesh (1 layer, same h1/growth as the dual A/B) it measured
  68,853 prisms, 100% coverage, h1 ratio 0.999, 90.5 s, final checkMesh
  **Mesh OK** (skew 2.48, 0 pyramids).  The same dual BL measured h1 0.474,
  221.4 s and Failed 3 (11 pyramids / 85 non-orthogonal), but the direct
  path uses ~3.9x more cells; verdict is **opt-in, not promoted** pending
  thin/venturi/tube and production 5-layer evidence.
- **One visible Polymesh CTA**: the experimental fTetWild and Auto buttons
  are no longer separate CTAs.  One lossless `core/mesher_registry.py` keeps
  every historical key (`gmsh_direct_poly`, `ftetwild_poly`, `auto_tet`,
  `cfmesh`, `gmsh_direct`) and adds `bl_on_tets`; the Advanced **Polymesh
  engine** selector exposes Auto, GMSH, fTetWild, legacy tet→poly, BL on
  tets, cfMesh and FEM tetra.  Old buttons remain hidden/tested controls,
  not deleted engines.
- **Migration**: `MeshConfig`, presets, projects and CLI accept the new key;
  aliases `autopoly`/`poly`/`polymesh` map to the measured legacy default.
  Auto stays opt-in until its corpus A/B is green; `bl_on_tets` is
  experimental and never the default.
- Tests: `tests/test_bl_tets.py` (7) and
  `tests/test_mesher_registry.py` (8), plus the existing mesher/config/BL
  suites.  The full fast suite and ruff remain the gate.

### Multi-region patches and zero-config (W11) - 2026-10-23
- **Root fix in `.msh` detection**: untagged 2D surface elements no longer
  shadow the owning 3D region tag, so a multi-region mesh without explicit
  surface groups produces one patch per volume instead of `surface_0`.
  Physical names are dimension-scoped; boundary faces are grouped by the
  resolved sanitized name, so a 2D `wall` tag 1 and a 3D `fluid` tag 1 stay
  separate. Duplicate names merge, names are valid OpenFOAM words, and every
  boundary face is counted exactly once.
- **Non-manifold inputs fail clearly**: a face shared by more than two cells
  raises a precise error before writing instead of becoming a ghost patch.
- **`neighbour` invariant repaired**: `write_polymesh` no longer writes `-1`
  boundary placeholders into `neighbour`; `read_polymesh` normalises old
  files. This was why BL could select a patch by name and still see zero
  boundary faces (`n_int == n_faces`).
- **Report/view coherence**: `boundary_layer_report` lists every boundary
  patch with `bl_selected` (caps included, coverage 0 for non-BL patches),
  alongside `n_boundary_faces` and `patch_names`. R1 now reports all 7
  patches: 5 selected walls with 99.51% measured coverage, 2 caps
  unselected; h1 remains 0.1155x requested (declared cap).
- **Zero-config**: volume tags alone name `region_N`, a fully untagged mesh
  gets `default`, and the GUI wall decision delegates to the shared
  `patch_roles.is_wall_patch` (a blade `pressure_side` is a wall).
- Tests: `tests/test_mesh_converter_multiregion.py` (13) plus the updated
  mesh-converter orientation regression. No real multi-region `.msh` case
  was available; the multi-region test is synthetic and declared.

### Poly release gate + boundary attribution (W10.3) - 2026-10-23
- **Measured attribution**: R1 dual-only has 1 pyramid (79,416 cells, NO
  48.46/skew 3.31); old BL adds 38 (39 total, Failed 3); W10.1 quality BL
  has 0 pyramids, 1 skew, 0 non-ortho errors and 99.47% coverage (Failed 1).
- **Combo A/B rejected**: edge-filter 0.9 + budget termination gives 0
  pyramids but only **87.1% coverage** (154,145 prisms; 4,566 thin faces),
  below the 98% release floor.  Not promoted; stays opt-in.
- **New `core/poly_release.py`**: independent dual-only/dual+BL measurement,
  BL-added defect attribution, release gate requiring zero pyramids/negative
  volumes and >=98% coverage, with an explicit documented residual for R1's
  one skew class.  The worker logs and writes the gate result into
  `bl_report`; it never relabels a failed checkMesh as a pass.
- **No fake geometry lever**: merge/relax, smoothing, compact-fan,
  triangulate, planarize, feature-angle, edge-cap, min-base-edge,
  budget-term-alone and zonal worst-element remain forbidden; venturi's
  224-pyramid wall (NO 97.2/skew 18.3) is declared W14 topology debt.
- Tests: `tests/test_poly_release.py` (10) plus the existing poly/BL suites.

### Hybrid tet source + auto policy (W10.2) - 2026-10-23
- **One `TetSource` contract** (`core/tet_source.py`): requested/effective
  source, settings, names, per-stage stats, attempts, failure reason and
  cache fingerprint.  `auto` is deterministic and opt-in: STEP -> GMSH
  surface + fTetWild volume, STL -> fTetWild, missing pytetwild -> GMSH,
  with one logged fallback to the other source.
- **GMSH surface strengths carried**: `generate_surface_stl` accepts
  refinement zones, surface resolution and feature-angle provenance;
  `run_volume`/`FtetWildVolumeWorker` forward them; the tagged NPZ keeps
  physical groups.  Measured probe: 210 -> 748 surface triangles, inside
  median edge 0.476 -> 0.250 mm (3 patches retained); outside also refined
  to 0.245 mm by GMSH field propagation, declared.
- **Patch fidelity**: `assign_patches` adds normal-compatible nearest
  selection + ambiguity/normal-filter stats; nearest centroid remains the
  fallback.  Thin-gap synthetic test avoids the opposite sheet.
- **Cache correctness**: `ftw_eps_frac`/`ftw_simplify`/`ftw_adaptive` (and
  opt-iteration/stop-energy/disable-filtering) are now in
  `mesh_cache.CANONICAL_KEYS`; cache version 2.  Previously the GUI passed
  these but the key ignored them.
- **GUI**: new opt-in `Auto (GMSH surface + fTetWild volume)` mesher entry,
  `auto_tet` persisted in MeshConfig; on failure it performs one visible
  source fallback.  The result log carries source/surface provenance.
- **Explicitly rejected**: merging GMSH and fTetWild volumes; no conforming
  interface was proved, so `union_is_conforming()` is false and no merged
  mesh is written.
- **Declared open**: full corpus auto A/B, both-source R1 dual+BL run, and
  feature-angle application to the volume (no fTetWild volume knob; CAD
  groups already encode it).

### BL fidelity + quality policy (W10.1) - 2026-10-22
- **Measured h1, not a silent success**: `PolyBoundaryLayerResult` now
  carries achieved h1 median/p10, ratio, histogram, cap reason and the
  in-process pyramid count.  R1: 0.142 mm vs 1.26 mm requested (0.113x);
  the cap is 91.9% support-bound / 8.1% edge-bound, all 70,293 wall vertices
  below the requested h1.  The report/GUI logs the same facts per patch.
- **Shared BL attempt policy**: new `core/bl_attempt_policy.py` with
  declarative attempts, baseline restore, keep-best and explicit gap
  reasons.  The fTetWild worker uses standard + measured quality attempt;
  `POLYFOAMMESH_BL_QUALITY_POLICY=0` restores the old single attempt.
- **R1 real A/B**: standard = 39 pyramids / 5 skew / 26 non-ortho errors /
  Failed 3; quality = 0 pyramids / 1 skew / 0 non-ortho errors / Failed 1,
  coverage 99.47% (187 rim faces excluded), h1 unchanged at the declared
  cap.  Post-BL boundary-pyramid clearance removed the last 8 boundary
  faces.
- **Coverage denominator fixed**: 187 bare selected faces now count in the
  coverage (99.47%, not the old false 100%).
- **y+ per patch**: `boundary_layer_report` takes requested h1 + optional
  solver u_tau; without u_tau the value is `null`.  The velocity/fluid
  correlation is labelled as an estimate and uses the achieved h1.
- **Corrected diagnosis**: R1's 39 pyramids are internal core/last-prism
  top faces, not boundary terminator strips (`n_terminator_faces=0`).
  Terminator internalisation needs vertex splitting and remains the next
  topology task; the partial-patch boundary terminator test is the guard.

### Mesh configuration + presets (W10) - 2026-10-22
- **One `MeshConfig`** (`core/mesh_config.py`): detail slider, mesher,
  adaptive/fTetWild switches and nested `BLConfig`, with `schema_version=1`,
  per-field provenance, tolerant loading and strict validation.  GUI,
  `params/mesh_config` project state, CLI and `DualPolyWorker` share it.
- **No geometry-specific cell sizes in presets**: the portable sizing input
  is the 0..20 detail slider; the shared slider->target mapping moved to
  `core.mesh_config` and the GUI delegates to it.
- **Fast / Balanced / Fine** built-in presets with measured evidence;
  `Fine` is marked **experimental** because its exact full E2E was not
  re-run.  User presets are full `MeshConfig` snapshots; invalid/out-of-range
  presets clamp with a clear status, reserved built-in names are rejected.
- **One preset file format**: `core.preset_store.PresetStore` now backs both
  `BLPresetStore` and `MeshPresetStore` (`kind` + schema + atomic write);
  old BL files load unchanged.
- **Project migration**: `MeshConfig.from_settings` reads the old
  `detail_slider`/`bl_checked`/`bl_config` keys once; new saves write only
  `params/mesh_config`.  A project with only the old detail key keeps BL
  off (the historical default).
- **Template presets fixed**: `_apply_template_preset` now maps
  coarse/medium/fine/very_fine to slider 4/10/16/19 and uses the same
  `MeshConfig`; the old 1/2/3 map (all in the coarse bucket) is gone.
- Tests: `tests/test_mesh_config.py` (12), `tests/test_gui_mesh_presets.py`
  (9), plus the W7 BL/preset suite.

### M2 milestone audit - 2026-10-22 (NOT CLOSED)
- **Structural consolidations**: `parse_checkmesh_output` is now a view over
  the single `CheckMeshReport` parser (duplicate `_CHECK_*` regexes removed);
  named `PHASE_BUDGETS` replace the scattered worker timeout literals;
  `SessionSnapshot` carries the same `bl_config` mapping as QSettings and
  drops the never-written split `bl_*` fields.
- **GMSH reproducibility**: two identical cube runs produced different
  `.msh` hashes; added opt-in `POLYFOAMMESH_GMSH_THREADS=1` (default
  unchanged).  The hash-level fix is not yet re-validated end-to-end.
- **Measured M2 status, not a closure claim**: fTetWild BL produced on
  10/15 corpus cases and ≥98% coverage on 9/15; GMSH/poly 3/15 produced and
  0/15 ≥98%; per-patch y+ is still absent (R1 h1 0.1418 mm vs 1.26 requested
  = 0.11×); the 9/10 poly-vs-snappy baseline has not been re-validated.
- **Real R1 E2E**: convert 4.8 s + dual/BL 92.6 s = 110.0 s total (baseline
  120.8 s), 253,681 cells, 174,265 prisms, 100% coverage, checkMesh
  `Failed 3` (39 pyramids, 5 skew, 26 non-ortho errors) — unchanged defect
  class, no regression.
- **Healthy cube-duct E2E**: full GMSH tet → convert → dual → BL → checkMesh
  path completes with 100% BL coverage; checkMesh `Failed 1` (non-ortho),
  declared. Details in `notes/m2_milestone_diagnosis.md`.
- Remaining W2/W5/W6/W9 work is prioritised in `docs/residual_risks.md`;
  M2 is not announced closed.

### Process robustness / watchdog (W8) - 2026-10-22
- **One `PhaseWatchdog`** (`core/phase_watchdog.py`) for stall, timeout,
  heartbeat, ETA and declared action.  `_stream_subprocess` now uses it for
  GMSH, checkMesh, gmshToFoam and fTetWild; dual/BL poll the same object.
- **fTetWild progress/ETA**: parses `pass N` / `max_energy` / `#t`; emits
  `progress` and `heartbeat`; 600 s no-progress net + documented 4 h ceiling.
- **Visible dual/BL progress and cancellation**: `DualPolyWorker` emits
  `progress`/`heartbeat`, logs watchdog actions, and `PolyDualWorker` streams
  its subprocess through the common path so Cancel kills the child tree.
- **Structured checkMesh**: new `CheckMeshReport` with failed-check count,
  defect types/counts/locations, cause and explicit acceptance; embedded in
  `MeshQualityReport.to_dict()` and logged by the GUI/fix loop.  A bare
  `Failed` is never silent and cannot become a pass without `accept(reason)`.
- **Internal-pyramid repair: measured declaration, not promoted**.  Batch
  moves worsen the R1 case; sequential keep-best clears 3 of 41 in 250 s with
  no negative volumes but leaves the 5 skew faces and `Failed 3`.
  Details in `docs/residual_risks.md` §W8.
- Tests: `tests/test_phase_watchdog.py`, `test_phase_watchdog_integration.py`,
  `test_checkmesh_report.py` (plus the unchanged GMSH watchdog suite).

### Boundary-layer GUI contract (W7) - 2026-10-22
- **One validated `BLConfig`** (`core/bl_config.py`) shared by the GUI,
  CLI helpers and `DualPolyWorker`: canonical units, per-field provenance,
  `schema_version = 1`, declared clamps, and one translation to
  `PolyBoundaryLayerEngine.run` / cfMesh `meshDict`.
- **Advanced tab completed**: min base-edge fraction, terminate-by-budget,
  edge-cap percentile, local termination (off/decoupled/per-vertex), local
  height retry and max rounds, each with range, units and tooltip.
- **Editable presets**: named BL snapshots with schema version, atomic
  storage, last-used restore at startup; out-of-range presets load with a
  clear clamped message instead of breaking the project.
- **Traceability**: `[poly] BL effective:` / `BL provenance:` log lines,
  every engine/config clamp logged, and `<case>/bl_config.json` with the
  effective values, provenance and resolved first-layer height.
- **Cleanup**: removed the panel's hardcoded 0.9/True defaults and the
  worker's duplicate defaults; made the previously dead `enabled` /
  `maxThickness` autopoly bindings real.  The measured historical
  first-layer fraction (0.01) is preserved (no unmeasured default change).
- Tests: `tests/test_bl_config.py`, `tests/test_gui_bl_advanced.py`
  (round-trip, provenance, legacy migration, clamp visibility, preset
  round-trip/invalid, GUI->engine kwargs, CLI/GUI equality, worker JSON).

### fTetWild path (issue #12) - 2026-10-22, measured-default verdicts
- **Absolute epsilon 0.3 mm confirmed as the default**: the full fine
  run with `eps_mm=0.3` finishes cleanly (2.37M tets, 3738 s tet stage,
  max energy 7.55, no stall); 0.03 mm stays available but is not the
  default (proportionally slower).
- **Free-boundary smoothing kept at 3 iterations (2 rejected by A/B)**:
  on the 15-geometry battery, 2 iterations converge to a worse optimum -
  9.7% slower end-to-end (77.2 vs 70.3 min) because the downstream stages
  (BL, clearance) work on rougher geometry, one hard gate regression
  (cylinder max non-ortho 66.6 -> 76.2 degrees, over the 70 threshold),
  and mixed per-geometry quality shifts (venturi/groove1/pipe better,
  external_aero/slot1 worse) with BL coverage unchanged. The knob
  `--ftet-dual-smooth-iters` remains available for experiments.

### fTetWild path (issue #12) - 2026-10-19, geometry-coverage pass
- **Geometry battery** (STL: box, sphere, cylinder, torus, 10 mm plate,
  two bodies, 5 mm-wall washer, 4 mm micromixer, 3 m pipe; STEP via
  CADQuery: plate with a through hole, L-bracket, thin-wall pipe):
  all 12 tetrahedralize and convert; all boundary layers build (the
  washer via the new vertex-decoupled retry).
- **Envelope capped at 5% of the smallest dimension**: a 10 mm plate with
  a 15%-of-cell envelope (14.6 mm) made fTetWild return ZERO tets, and a
  coarse target on the 3 m pipe produced a pinched non-manifold boundary
  the dual rejected. Both now mesh.
- **Target-edge clamp**: a target larger than half the domain (stale CAD
  unit / slider) is clamped with a log line instead of a GMSH MemoryError.
- **BL vertex-decoupled retry** on inverted thin-feature stacks (the
  5 mm-wall washer failed every standard height scale with non-positive
  volumes; decoupled_vertex builds it in 95 s).
- **Exact-dual retry on closure failure** + repair-aware volume invariant
  (the collapsed dual left a boundary cell open on the thin-wall pipe;
  the exact per-corner dual is closed by construction, and its volume
  drift from the boundary split/shift repairs is now allowed at 1e-2
  instead of being rejected at 1e-6).

### fTetWild path (issue #12) - 2026-10-19, geometry recognition + scale + measurements
- **Adaptive local refinement (cfMesh-like)**: fTetWild now receives a
  background sizing field built from the validated ray-cast passage
  thickness (target = thickness / 8 cells across) and the dihedral-angle
  curvature radius (8 cells per radius) - thin passages and tightly curved
  regions get finer cells while the bulk stays at the target edge. GUI
  checkbox "Adaptive refinement" (default on), `--no-adaptive` /
  `FTETWILD_ADAPTIVE=0` to disable. Measured on the reference part:
  target edge 0.64-12.75 mm, 8,000/114,708 grid points refined, 11 s.
- **Working GUI scale**: "Apply Scale" button + automatic refresh; the
  scale now reaches the meshers (GMSH OCC dilate after open, fTetWild
  scaled surface) and is part of the mesh-cache fingerprint (with the
  fTetWild eps/simplify/adaptive). Verified: 0.05 m sphere at 2x ->
  tet bounds +-0.1 m.
- **Geometry measurement report**: on load / scale / unit change the log
  shows domain + cross-section, wall-thickness p10/p50 (ray samples), the
  curvature-limited cell, suggested max/min cells and the bulk cell
  estimate - the numbers the sizers use, visible instead of hidden.
- **Faithful geometry through the surface pipeline**: the destructive
  Taubin pass after decimation is gone (it moved points up to 60 mm); the
  decimation is target-driven with a VERIFIED error budget (quadric +
  sqrt-ladder search) and the tet boundary is snapped back onto the
  original surface (volume-guarded). E2E medium: 0.128 mm mean surface
  deviation, dual 0/0/1 defects.
- fTetWild subprocess ceiling raised to 4 h (a faithful tight-eps run
  legitimately exceeds 1 h); cache-restore worker now receives
  eps/simplify/adaptive (was silently falling back to legacy defaults).

### Added

- **Experimental fTetWild mesher** (issue #12): a new
  `Polymesh sperimentale (fTetWild)` mesher button runs the poly pipeline's
  tet stage with fTetWild (optional extra: `pip install pytetwild` /
  `polyfoammesh[ftetwild]`) instead of GMSH. Everything downstream is
  unchanged: the same gmshToFoam conversion, barycentric dual, boundary
  layers (added post-dual by `bl_poly`) and checkMesh gates. Patch names
  are recovered by proximity-tagging the output boundary against GMSH's
  per-physical-group surface export (`export_tagged_surface_npz`); STL
  inputs skip GMSH entirely and keep their solid names.
  Sizing: the target edge comes from the Detail slider as
  cells-across-the-cross-section (same resolution model as the GMSH path;
  `FTETWILD_LR` switches to a bbox-relative edge, `FTETWILD_OPT_ITER`
  caps optimization passes). The input surface is decimated toward the
  target before the call — fTetWild's own simplify only removes detail
  within its eps envelope, it never decimates toward the target (measured:
  a 386k-triangle surface against a 12.75 mm target stalled the stage for
  15+ min until pre-decimated; research trail in the code, fTetWild#93).
  The direction is one-way: the INPUT is adapted to the target, never the
  other way around — an intermediate clamp that pulled the target down to
  2x the surface density produced ~40M cells on a real 3 m part.
  Refinement zones, solution size fields, the max-cells target and the
  adaptive flag do not apply (announced in the log when dropped) and are
  neutralised in the tet-cache fingerprint together with ALL
  boundary-layer settings — fTetWild never bakes BL into the tets, so a
  BL tweak reuses the cached tet mesh instead of paying the whole tet
  stage again. The stage logs a
  `bbox_diag / cross_section / target_edge / input_median_edge`
  diagnostic up front and states explicitly that the tetrahedralization
  phase runs without output by design (the GUI heartbeat is the liveness
  signal, Cancel always works).
  Surface fidelity (2026-10-19): the wrapper now exposes pytetwild's
  fidelity knobs and defaults `simplify` OFF.  Measured on the reference
  part (3 m, 8 mm target): with fTetWild's internal edge-collapsing
  simplification ON the output surface deviates from the input by up to
  5 mm (77% of points > 0.3 mm) and the AMIPS energy explodes (max
  140783 vs 12); with it OFF the deviation is 0.06 mm mean / 1.65 mm max
  (6% > 0.3 mm) and the mesh has HALF the cells (2.33M vs 4.47M) at
  comparable time.  That deviation is what warps the dual boundary
  polygons and makes the poly conversion fight.  `epsilon` (relative
  surface envelope), `stop_energy` and `disable_filtering` are exposed
  too (defaults unchanged; env knobs FTETWILD_EPS / FTETWILD_STOP_ENERGY
  / FTETWILD_NO_FILTERING / FTETWILD_SIMPLIFY, CLI flags, GUI fields on
  the Advanced tab).  Also: the MSH-conversion worker timeout was 300 s
  while the CLI allows 900 s - a multi-million-tet mesh could exceed it
  and the GUI reported a spurious failure; raised to 900 s with a start
  log line.
  Output conditioning (fTetWild's known incompatibilities with the
  barycentric dual, measured on a real 3 m part with a tangent contact):
  (1) the decimated input surface is Taubin-smoothed (features preserved)
  before the call — quadric facets were the source of 167 checkMesh-style
  warped-boundary-face violations, smoothed down to 66 same-scale
  (~2.5x); (2) a complete desingularization splits boundary bowties /
  non-manifold pinches per corner with a micro-offset (fTetWild's
  `--manifold-surface` equivalent — pytetwild exposes no such flag; the
  dual rejects the raw pinch outright and its own degenerate-vertex
  cleanup made things worse); (3) outlier tet micro-clusters are dropped
  (a 1-cell "region 2" in the tet mesh became a floating island in the
  dual; the dual input is now single-region); (4) free-boundary
  smoothing, auto-enabled on the fTetWild path (opt-in converter flag
  `smooth_free_boundary`): the dual's smoother normally pins boundary
  vertices to keep the EXACT dual a bit-perfect surface subdivision —
  the collapse dual is a surface-offset approximation anyway, so
  relaxing it there is safe. Measured on the reference part, with the
  edge-length guard that the first unguarded version needed (without it
  the smoothing chased the warped feature polygons down to 0.011 mm
  boundary edges — a 20x collapse that made every downstream BL prism
  sub-micron and unbuildable): warped-boundary defect faces 121 -> 90
  (pyramid 62 -> 21, boundary-skew 59 -> 69, i.e. a real 26% reduction
  of the total with every edge >= 0.36 mm) at <= 4.3 mm max point
  movement on 8 mm cells; keep-best and volume/closure guards
  unchanged. The stage also streams fTetWild's own phase lines now
  (loglevel 3; FTETWILD_QUIET=1 restores silence) — the silent
  tetrahedralization was repeatedly mistaken for a hang.
  Residual on the same part, stated exactly as checkMesh sees it: 21
  pyramid + 69 skew boundary faces (0.003% of the 2.9M faces) at
  feature edges. Historical baseline for comparison: GMSH on this
  part failed the final checkMesh in ~5 of 10 logged runs, one with 219
  pyramid faces.
  Follow-up work (same day) closed most of the observability gap and
  diagnosed the boundary-layer behaviour precisely:
  (a) the in-process detector now reproduces checkMesh's BOUNDARY-face
  skewness EXACTLY (verbatim port of OpenFOAM 2512
  `primitiveMeshTools::boundaryFaceSkewness`; validated bit-equal on
  four independent duals — counts 59/20/69/6 and maxima
  9.73624/7.27144/12.43022/6.35977), so the smoother and the reported
  residual see what checkMesh sees (opt-in `count_boundary_skew`, on for
  the fTetWild path; the legacy detector is byte-identical);
  (b) the free-boundary smoothing got a hard edge-length guard
  (`free_boundary_min_edge_frac=0.5`): without it, chasing the warped
  feature polygons collapsed boundary edges from 0.6 mm to 0.011 mm —
  micro edges that made every BL prism sub-micron and unbuildable;
  (c) two keep-best boundary repairs (split of V-warped polygons along
  the best diagonal; per-face shift along the exact OpenFOAM skew vector
  and the pyramid normal) reduce the residual further; they converge
  slowly on the reference dual (90 -> 89 defects in 6 rounds) and stay
  honest about it — opt-in, default off;
  (d) the boundary layer on this part is now SOLVED for the medium
  class, root cause included. Two earlier misdiagnoses are corrected
  here: the first BL tests used hand-built parameters instead of the
  GUI's real ones (the GUI always sends minBaseEdgeFraction 0.9 +
  terminateByBudget, and its default is wall-patches-only), and the
  "GMSH works / fTetWild doesn't" contrast did not survive a
  controlled test (on this part the GMSH-derived duals are in fact
  WORSE: 263/338 residual defects at coarse/fine vs 30/90 for
  fTetWild). With the real parameters the remaining blocker was dumped
  edge by edge: the multi-cell consume reflow (promoted default)
  double-emits prism side faces on wall columns with sub-mm polygons -
  two prism stacks per column, each contributing 2 side faces to the
  same base edge, leaving 10 boundary edges shared by 4 faces at every
  height scale. With a new optional `consume` argument (forced False on
  the fTetWild path only; the GMSH path keeps the promoted default) the
  standard drop pass validates and the BL BUILDS: measured on the
  reference part (medium, GUI parameters) 71,025 prism cells, median 5
  layers, 95.4% of wall faces layered, checkMesh region OK, and the
  residual 11 pyramid + 18 skew faces are exactly the dual's own
  pre-BL defects - the BL adds none. The fine resolution (8 mm) still
  fails validation (drop-path builds add pyramid faces around the
  warped feature polygons; the split/shift repairs reduce but do not
  yet clear them) - honest open item. Related: the BL growth widget
  now drives the y+ physics compute (it was ignored - the engine
  always solved the layer count for a hardcoded 1.2): a higher growth
  reaches the same total thickness with fewer, thicker layers, i.e.
  more comfortable stacks for thin-cell walls.
  Caveat measured on real parts: fTetWild has no wall-clock bound and on
  some geometries its optimizer runs far longer than GMSH (unbounded
  edge-splitting once the AMIPS energy stalls) — Cancel stops it.
  Verified end-to-end on benchmark geometry (11 named patches, checkMesh
  Mesh OK); much faster than GMSH on simple/small surfaces. Measured on
  a user's 3 m pipe part (Parte4, 89 patches, medium, same machine,
  back-to-back): GMSH 649 s / 6.94M tets vs fTetWild 150 s / 587k tets —
  4.3x faster, 11.8x fewer cells, much better quality (min dihedral
  12.6° vs 0.07°, p1 35.3° vs 20.0°). Measured on a user's 2 m
  cylindrical part (60k-triangle surface): GMSH 166-242 s vs fTetWild
  490-1042 s across all sane target sizes — there fTetWild loses on time
  but wins on quality (p1 dihedral 34-36° vs 20°, no negative cells). On
  the small venturi benchmark: 0.58 s vs GMSH 9.6 s (16x faster). On the
  small cylinder benchmark: 9 s vs GMSH 16 s with 33% fewer cells and
  better quality (min dihedral 20.6° vs 13.9°), whole poly pipeline 39 s
  with both checkMesh passes green. In short: a speed/quality option for
  parts where the surface stage already resolves the geometry finely —
  not a universal GMSH replacement on every part.

### Changed

- **BL stack hint is now facts-only** (user decision 2026-10-17): the
  pre-build "engine compresses the stack to ~X%" prediction was removed —
  it was built on a wall-cell estimate that disagreed with the engine's
  own per-vertex clamp (hint said ~1.55 mm wall cells / ~19%; the mesh
  came out with ~0.1 mm first layers on ~75% of wall faces). The hint now
  states the requested stack only; after the build the log reports
  MEASURED facts: `[poly] BL report: N% of wall faces layered (a/b);
  effective first layer X mm vs requested Y mm`.

### Fixed

- **Viewer status could stay stuck on "Generating foamToVTK..."**: a
  display that completed through the foamToVTK retry path (first attempt
  fails with rc=62097, the retry succeeds) rendered the mesh but never
  restored the stats label, so a finished mesh looked frozen. Every
  display success path now restores the P/F/C readout.

## [2.3.0] - 2026-10-13

### Added

- **Linux installation guide** (`docs/INSTALL_LINUX.md`): a complete,
  standalone guide - per-distro system libraries, Python setup (virtual
  environment first, no-sudo fallback documented), OpenFOAM setup and
  `POLYFOAMMESH_OF_BASHRC`, running the GUI and headless, verification
  commands and troubleshooting, with an honest verified-vs-not box
  (verified on WSL Ubuntu 24.04: the fast suite is green; desktop GPU
  rendering and non-Ubuntu distros are not yet exercised; the AppImage is
  deferred).  Linked from INSTALL.md (Option D), the README and the
  documentation index.
- **Documentation index** (`docs/README.md`) and **notes index**
  (`notes/README.md`): the whole documentation tree and the internal
  engineering reasoning notes, mapped in one place each.
- **Direct OpenFOAM export** (menu Export Mesh → "OpenFOAM Case
  Folder..."): copies the case to a chosen folder, **without** the
  requirements of the BaramFlow export (which rejects cases with empty/
  incomplete 0/ fields). Exports mesh + system + fields if present: enough to
  launch it directly in OpenFOAM (WSL/Linux: `foamRun`,
  `simpleFoam`). The regenerable and heavy artifacts are left out
  (processor*, VTK_view, mesh.msh, tet backup).
- **Complete user manual in English** (`docs/USER_GUIDE.md`), rewritten
  for the current UI: workflow, viewer, boundary layer (with log
  reading), refinement, themes and troubleshooting.
- **Viewer**: deterministic Section Cut that slices in all
  representations; working Measure (two-click distance on CAD and
  mesh); unified ParaView-style representations (`Surface`, `Surface
  with Edges`, `Wireframe`); solid patch highlight; more polished mesh
  appearance (smooth shading, adaptive edges).
- **Workflow dock**: the static "Next step" item became **Next
  action**, an actionable suggestion that follows the real state (idle, in
  progress, finished, error).
- **BaramMesh-style patch splitting**: popup on import (STEP and STL)
  with "Feature angle" (5–179°) and a "Use CAD face names when
  available" checkbox; with the checkbox off the surface is split by
  angle into patches `surface_1..N`. **Patch names are editable**
  (double click) in the patch table, now moved to the bottom-left dock
  (single panel: the duplicated list in the Geometry tab was
  removed). The rename propagates to meshing, BL and export.
- **Linux support (phase 1 - core)**: the app now runs the
  OpenFOAM/cfMesh commands natively on Linux (`bash -lc` instead of `wsl.exe`),
  with a configurable bashrc via the `POLYFOAMMESH_OF_BASHRC` variable
  (default `/usr/lib/openfoam/openfoam2512/etc/bashrc`). App data
  in the XDG location (`~/.local/share/polyfoammesh`, override
  `XDG_DATA_HOME`) with legacy migration intact. `launch.sh` launcher,
  Linux PyInstaller spec (`PolyFoamMesh-linux.spec`) and CI jobs
  `build-linux`/`attach-linux` (the Windows job remains unchanged). Section
  "Linux (from source)" in INSTALL.md/.it.md. Windows behaviour
  verified **byte-identical** (18/18 checks on generated commands and
  scripts).
- **Tet mesh cache (skips GMSH) — AUTOMATIC**: when the inputs
  that determine the mesh do not change, the `.msh` from a previous run
  is reused (SHA256 fingerprint, copy into the new case dir, same
  completion of the pipeline) skipping the entire GMSH stage — the
  slowest one. No checkbox: it is always active. "Clear mesh cache" in the
  Tools menu, automatic prune (5 entries / 2 GB), all fail-soft. **Correctness
  note**: the GMSH stage "bakes" the layout of the layers near
  the wall into the `.msh` (BoundaryLayer field from nLayers/thickness/
  expansion) and applies the wall-cell floor as a size field: these
  inputs are part of the fingerprint, so changing the BL parameters
  invalidates the cache (correct); the other BL settings (apply-to-all,
  filters, budget) stay excluded.
- **Boundary layer visibility in the viewer**: "Show BL" checkbox that
  classifies the prism cells (seed from the wall faces + shape test +
  multi-layer flood) and colours the BLs orange over the grey core.
- **Log during meshing**: the log panel created ONE single, unbounded
  HTML block → quadratic re-layout (10k lines = 29 s, 50k > 300 s) and
  GUI lag "for no reason". Now batching on a timer, real blocks
  (the row cap actually works), coalescing of repeated lines
  ("Improving..." collapsed with a counter, "still running" replaced):
  50k lines in ~0.24 s. No log mirror existed, no fidelity
  lost.
- **Linux support (phase 2 - GUI)**: the availability gate is now
  "OpenFOAM available (native or WSL)" (no longer WSL-only), the RAM probe
  uses the host RAM on Linux too, the case root no longer depends on
  `C:\`, process kill is native on POSIX, and all WSL-only
  messages/dialogs (OOM, GMSH memory, ParaView, "WSL not found") are
  platform-conditional — on Windows **byte-identical**.

### Fixed

- **Empty console windows popping up from the frozen exe**: the GUI has
  no console, so every console-subsystem child it spawned (`wsl.exe`,
  `python.exe`, `gmsh`, `checkMesh`, ...) made Windows allocate a new
  console window that stole focus during work.  One central patch
  (`core/subprocess_quiet.py`) now injects `CREATE_NO_WINDOW` into every
  `subprocess` spawn (Windows-only, no-op on POSIX) and the viewer's
  `QProcess` gets the matching Qt modifier.
- **App icon on the taskbar/installer**: the executable now embeds the
  app's own logo (`src/polyfoammesh/assets/app.ico`, generated from the
  in-app branding mark with `tools/make_app_icon.py`) instead of the
  PyInstaller default, the installer uses it as its setup icon, and an
  explicit Windows AppUserModelID makes the taskbar group and pin the
  app under PolyFoamMesh with the right icon.
- **The test suite silently rewrote the benchmark corpus**: the corpus
  generator test ran `benchmarks/generate_corpus.py` straight into
  `benchmarks/geometries/`, overwriting the tracked geometry files on
  every suite run (they no longer match the generator byte-for-byte
  since a couple of geometries were rebuilt by hand) - leaving the
  checkout dirty and risking benchmark-baseline drift.  The generator now
  honours `POLYFOAMMESH_CORPUS_OUT` and the test regenerates into a
  temporary directory; verified: the tracked corpus stays clean after the
  full fast suite.
- **Linux verification (first real run, WSL Ubuntu)**: the full fast suite
  now runs **green on Linux** (1516 passed, 0 failed) - and it found real
  portability bugs that were fixed:
  - the benchmark harness was Windows-only: `_to_wsl_path` indexed
    `Path.drive[0]` (empty on POSIX -> `IndexError`) and the shell calls
    hardcoded `wsl.exe`.  On Linux the harness now runs natively
    (`bash -lc`, POSIX paths) and honours `POLYFOAMMESH_OF_BASHRC`.
  - the subprocess heartbeat polled every 0.5 s against a 0.3 s heartbeat
    interval: a phase race with the child's output cadence could suppress
    every "still running" line under load (caught by the watchdog test on
    the Linux scheduler).  The loop now polls sub-heartbeat, which also
    tightens stall-detection granularity.
  - two timing-fragile tests anchored (monotonic baseline instead of a
    hardcoded 100 s; wider stall margin).
- **cfMesh RAM safeguard was a silent no-op**: the hex pipeline meshes from
  `surface.fms`, and `trimesh.load()` cannot read `.fms`
  (`NotImplementedError: file_type 'fms' not supported`) - the safeguard
  caught the exception, logged "RAM safeguard FAILED" and wrote the
  requested sizes UNCHANGED on every hex run (found by the release smoke:
  the error appeared on all 15 corpus geometries).  It now falls back to
  the sibling `surface.stl` (which the `.fms` is generated from) for the
  bounding box; normal-size cases are unaffected (the safeguard only acts
  above the RAM-based cell budget).  Regression test added.
- **BL aborted by a degenerate vertex in a refinement zone**: the BL
  precompute raised "wall vertex has no interior support" on the
  micro-sliver vertices that local refinement can create (measured: a
  13 um sliver with a single incident cell), killing the WHOLE boundary
  layer.  Those vertices are now excluded like the other sliver classes
  (0 layers there, pinned, logged: "excluded N wall vertex(es) without
  interior support (degenerate/refined spots)"); the honest failure is
  kept only when the problem is systemic (>5% of wall vertices or >5000).
  Verified on the user's refined tube through the full worker chain:
  456,018 prisms, median 6, 97.7% coverage, real checkMesh Failed 1
  (aspect ratio on 1,192 cells only - pyramids 0, non-ortho OK, skew OK).
  Clean-mesh outputs are byte-identical (cone A/B hash-verified).
- **Dual fails on the production venturi (open cells at the throat)**: the
  dual face of a boundary primal edge that is coplanar with the surface
  had its orientation sign decided by round-off noise, so it could be
  emitted backwards and leave the dual cell combinatorially open (16
  cells, "relative closure error 1.6e-1 - the dual complex is not
  watertight" - the user's venturi could not be meshed at all).  The
  converter now detects open cells with the exact closure invariant it
  already trusts, and self-heals ONCE: the offending vertices are merged
  into their shortest-edge neighbour (venturi: 8 vertices merged, 24
  collapsed tets dropped, every boundary face keeps its original patch),
  then the dual is rebuilt.  Verified: the venturi dual completes in both
  boundary modes with real checkMesh watertight (only the known throat
  NO/skew class remains), and the full app path now runs dual + BL
  (70,488 prisms, 6 layers, 100% coverage).  On clean meshes the cleanup
  never fires: dual-only polyMesh SHA-256 identical on cone/valve/
  benchmark-venturi (orchestrator re-verified the cone A/B).
- **Adaptive sizing runaway (non-convergence) on some geometries**: with
  adaptive sizing ON, GMSH's 2D mesher could loop forever on curved
  surfaces (measured on the benchmark venturi: invalid elements growing
  2->6524 over 15 split rounds, max silent gap 251 s just under the
  300 s no-output watchdog, no `.msh` after 40+ minutes).  The watchdog
  now recognises the **non-convergence signature** (>=100 invalid
  elements, or >=8 split rounds with non-decreasing counts after 30 s of
  2D meshing) and kills the attempt within ~40 s; the robust retry
  (Delaunay 2D + **2D optimizer enabled on this path only** for quality)
  then completes - measured full pipeline on the benchmark venturi:
  valid mesh + real checkMesh within ~50 s total (was: hang, no mesh).
  If even the robust retry fails, a final **manual-equivalent sizing**
  attempt (sizes derived from the same target) guarantees a mesh, with
  an honest log.  Healthy runs never trigger the detector (max 6
  invalid / 4 splits measured).  Default paths (manual sizing, robust on
  hangs) are unchanged; the optimizer stays off elsewhere because it
  doubles-to-triples robust wall time on large STEP parts.
- **GMSH stall on large curved geometries (Parte4-class)**: the volume run
  could stall in the 2D phase (MeshAdapt looping on the last curved/plane
  surface) and the robust retry could not recover: it did not pin Delaunay
  per-surface and used HXT 3D, whose boundary recovery leaves a
  non-manifold primal the dual converter rejects.  Robust mode now pins
  **Delaunay 2D on every surface** and uses **Delaunay 3D** for STEP/OCC
  input (STL keeps HXT); the watchdog budget scales 300 s -> **900 s** with
  the estimated cell count and the heartbeat logs the GMSH process **CPU%**
  so a busy-but-silent run is visible instead of being killed as a hang;
  the final error message describes the real retry.  Measured on the
  user's geometry (Parte4.stp, medium): completes in **151.6 s** (was:
  stall + double kill); tube/valve1/Parte2 regression runs unchanged.
- **BaramFlow/direct export now ships a CLEAN case**: processor*, VTK_view,
  mesh.msh, the pre-BL tet backup, postProcessing, previous results
  (numeric time dirs; 0/ is preserved), local.cfg and *.foam are left out;
  the BaramFlow export also drops our system/decomposeParDict (BaramFlow
  writes its own - a stale one made its runs unable to write results).
- **Patch rename keeps the 0/ fields in sync**: renaming only
  constant/polyMesh/boundary left the fields listing the old name, so
  OpenFOAM/BaramFlow aborted on load and the BaramFlow export refused the
  case ("missing files" popup).  Every 0/ field's boundaryField entry is
  renamed too (one-time .bak per field).
- **Oversized popups**: Qt message boxes grew as wide as their longest
  line, leaving huge half-empty dialogs.  An application event filter
  wraps the text and caps it to 480 px.
- **BOUNDARY LAYER SKIPPED ON EVERY APP RUN (critical)**: the
  anti-double-BL guard counted dual cells with a prism shape and found
  ~2,000 **coincidences** on a dual without BL (measured: 2012/24,286 =
  8% on a real cone) → the worker believed the BL already existed
  ("GMSH-baked BL detected") and **skipped the BL stage on every geometry**.
  The guard now requires real coverage: ≥ 50% of wall faces
  with a prism-shaped owner (measured: 0.14 without BL vs 0.89 with BL).
  **Verified end-to-end on the user's real cone** (worker path):
  5 layers on 100% of the wall, 69,615 prisms, checkMesh **Mesh OK**
  (NO 53.5, skew 1.54, pyramids 0).
- **Face/patch deselection in the viewer**: clicking the empty
  background now **really deselects** (the old code used
  `pick_mouse_position`, which on an empty click returns the picker's
  *stale* actor: "nothing hit" never occurred) and
  clicking the highlighted patch again **deselects** it (toggle). Also removed
  the fallback on `picked_actor`, which re-selected the previous patch
  on every empty click.
- **Double Cancel button during a run**: `set_meshing_state(True)`
  still relabelled the "Generate Mesh" button as a red "Cancel"
  (a leftover from when the dedicated Cancel did not exist), so during
  meshing **two** were visible. Now the run button stays "Generate Mesh"
  (disabled) and the only stop is the dedicated Cancel under "Reset All".
- **Dual quality on complex geometries (opt-in, keep-best)**: new
  `repair_concave_cells_keepbest` (merge of concave cells with a
  **maximum-severity** gate: accepted only if the maximum NO/skew do not worsen)
  + `compact_unused_points` (latent bug fixed: the merge left orphan
  points → checkMesh "Unused points" hard-fail; also fixed a
  corrupted `startFace` at round 2 that made checkMesh SEGFAULT).
  Measured with real checkMesh: **valve pyramids 340 → 46** (NO errors
  10 → 1), venturi 224 → 110; on the venturi the PASS bar is not reachable
  with merge/smoothing — the cause is **topological** (the dual with one cell per
  boundary vertex cannot be convex across the concave fold
  of the throat; tested: not even an 8.6× finer input changes it), so
  everything is opt-in, default byte-identical, corpus unchanged (slot1 protected
  by the severity gate). The smoother's anti-skew guard remains opt-in
  (trades NO for skew).
- **Multi-layer "consuming" BL - NOW DEFAULT**: the BL engine can
  consume layers of dual cells (bounded harmonic relocation of the
  interior vertices + per-edge anti-inversion guard + core sliver guard),
  so the full stack fits even where the first cell is
  thin. **Verified on the real pipe**: from **1 to 6 layers** on 100% of the
  faces, first layer = requested (true y+), checkMesh PASS (NO 56.75 vs
  60.50). To force the old behaviour:
  `POLYFOAMMESH_BL_CONSUME=0`. **Note**: the aspect ratio of the core cells
  adjacent to the stack grows (checkMesh accepts, 103 on the pipe).
- **Multi-layer BL on complex geometries too (venturi)**: on duals
  with sliver faces the consumption no longer switches off globally — the
  **sliver-affected** faces (sliver edge, vertex in a sliver cell, +2
  adjacency rings) are **excluded from extrusion** (0 layers there, cells
  pinned in the reflow: no skewed prisms) and the full stack runs on the
  healthy wall. The **inlet/outlet caps** are excluded geometrically
  (normal aligned with the flow axis + plane at the extreme bbox + planar
  patch ≥4 faces, + rim ring): they are not walls. **Verified on the
  real venturi** (real checkMesh, dual from the backup): 43,782 prisms,
  median **6 layers** on 60.7% of the wall (all the healthy part), first layer
  = requested, and checkMesh **better than the dual-only baseline**: pyramids
  **199 < 224**, aspect max **209 OK** (were 3182), NO/skew/neg
  identical. Pipe and corpus bit-identical.
- **Dual: mixed tet+prisms input with exact stitching**: the converter
  now accepts input meshes with prisms (from any source), preserves them
  verbatim in the final polyMesh and stitches the interface to the
  dualised core (median quads per interface triangle, side splits,
  no T-junctions). Verified with real checkMesh on synthetic meshes
  (Mesh OK). The worker skips the internal BL when prisms are already
  present (double-BL guard).
- **Verdict on the GMSH-"baked" BL**: impossible with GMSH 4.x — the
  BoundaryLayer field supports only 2D layers (3D was removed from
  GMSH 2.15.0) and aborts on curves adjacent to two surfaces; the only 3D
  route (extrudeBoundaryLayer) requires internal-kernel geometries,
  not imported STEP/STL. Verified empirically on venturi and
  multi_inlet: zero prisms in every configuration. The attempt stays
  behind `POLYFOAMMESH_GMSH_BL=1` (default OFF, fallback logged).
- **GMSH hang (Parte2/venturi) FIXED**: measured root cause — the
  MeshAdapt 2D (Algorithm=1) goes into an infinite loop when a non-uniform
  size field is installed on it on cylindrical surfaces/fillets; Delaunay
  (5) completes the same field in ~2 s. There is now a **robust mode**
  (`POLYFOAMMESH_GMSH_ROBUST=1`): OCC healing + Delaunay + no
  optimisation, and the **watchdog** activates it on its own: if GMSH does not
  produce output for 5 minutes → kill + robust retry once → if it
  still hangs, a clear error. Parte2 completes in **149 s** (702k nodes,
  valid .msh); default path bit-identical on the cases that work.
- **Degenerate prisms rejected**: a BL build with a first layer < 5% of the
  requested one (venturi: 80 nm!) produces sub-micron pancakes and 133°
  non-orthogonality — now it is rejected (better no BL than a garbage BL):
  venturi default NO 133.6 → 43.3.
- **Sharp-edge field opt-in** (`POLYFOAMMESH_GMSH_SHARP_EDGES=1`): the
  corpus regression run showed no regressions, but the field
  stays opt-in by keep-best principle until a corpus proves that it
  helps more than it costs.
- **y+ calibration: `wallShearStress` convention**: OpenFOAM
  incompressible solvers write the field as τw/ρ (kinematic), but the
  code divided by ρ → u_tau wrong by √ρ (×31 for water). Now
  the default is kinematic (`u_tau = √|τw|`), the dynamic convention is available
  via `kinematic_shear=False`, with a note in the result and in the dialog.
- **Patch deselection in the viewer**: a click on the **empty space** of the
  3D view now removes the highlight and the row selection (previously they
  stayed forever, making the selection useless).
- **y+ / boundary-layer first layer (chained error, real case)**:
  the physical calculation was correct (flat-plate, k-epsilon y+ 30) but the
  panel saved the first layer only as a **fraction of `max cell`**;
  when `max cell` changed (slider/adaptive, 0.171→0.0122) the same
  fraction produced a first layer **14× smaller** (85.8 µm → 6.1
  µm in the log) and in simulation a real y+ of ~0.09 instead of 30. Now the
  computed value is stored in **metres (absolute)** and is the one that
  goes to the engine; the fraction remains only for display. In addition: the
  calculation **recomputes automatically** when Velocity /
  Fluid / Wall treatment change (previously it kept the old one, e.g. computed at
  10 m/s and used at 1 m/s), a manual edit of the fraction returns to
  manual mode, and the message states that the flat-plate estimate on
  curved/internal flows can differ by ~2-4×.
- **Case root with a space in the user path** (phase-2 Linux
  regression): the "no spaces" fallback rewrote the path replacing the
  spaces with underscores, creating a non-existent tree
  (`C:\Users\Davide_Valoroso\...`). All the WSL commands pointed there:
  checkMesh read an empty directory (`cells=8`), the quality panel
  showed zeros and the dual conversion reported `cells ... -> 0`. Now the
  root is without spaces **by construction**: `<drive>:\cfmesh_cases` on
  Windows (where the real cases live), `tempdir/cfmesh_cases` on POSIX.
- **BL boundary manifoldness guard** (real "blade" case): the BL
  terminator strips at the BL/non-BL borders ended up in the boundary
  block (measured: +139,360 faces = exactly the terminators, 32 edge
  non-manifold) → checkMesh FAIL with 230,126 "multiply connected" faces.
  Now `_validate` rejects a build with a non-2-manifold boundary: on the
  blade the written mesh is the manifold dual-only one (multiply-connected
  0, aspect OK) instead of the disaster. The algorithmic fix of the terminator
  (internal step instead of a boundary strip) remains scheduled.
- **Patch roles after meshing too**: the patch table on a generated
  mesh shows the **geometrically inferred** type (inlet/outlet/wall)
  instead of "other", and the **post-meshing rename** writes to `boundary`
  (backup + validation). The BL selection uses the same inferred roles and
  A's wall floor now considers only the wall surfaces.
- **Formula A consistency**: `_BL_CELLS_ACROSS_STACK` 5→3 with the derivation
  from the inversion budget (0.34): the wall floor now really respects
  the budget.
- **Safe process kill on Linux**: OpenFOAM/cfMesh processes
  are launched with `start_new_session` and terminated with `killpg` (with
  an explicit guard to never hit the GUI process); on Windows
  `taskkill /T` remains unchanged. Previously on POSIX `killpg` could kill
  the app itself and two `taskkill`-based helpers were no-ops (orphan
  processes after Cancel).
- **RAM guard on Linux**: the RAM probe used `ctypes.windll` (inert on
  POSIX with a 999 GB fallback); now on Linux the host's real RAM is used.
- **Refinement boxes now really do what they promise**: the GMSH zone
  fields used `VOut = -1`, which combined in MIN made `-1` win
  everywhere outside the boxes and sent GMSH into an infinite retry loop (the
  mesh never refined, or never finished). Now a finite `VOut` +
  **gradual transition** (`Thickness`): no more rectangular block with a
  sharp step. Plus clamping of the refinement ratio (max 4×) and of the
  added-cell budget (~250k) to avoid count explosions
  (real case: 1.26M tetrahedra requested → ~305k).
- **BL: height-gradient limiter** (cfMesh `relThicknessTol`
  style): a vertex with a deep inversion budget next to a thin
  one created a "spike" that bent the extruded surface
  into non-positive cells and made the whole BL be rejected; now the
  stack is limited to the gradient and healthy meshes stay identical
  (verified byte-identical).
- **Measure**: unchecking it removes markers, labels and
  summary (previously they stayed on screen); the Clear button remains.
- **COMPLETE boundary layers (100% wall coverage)**: the binary semantics
  discarded faces on concave vertices or with an exhausted budget,
  leaving holes in the shell (measured: 5.9% of wall faces without
  BL on a real case, "the BLs are only partially extruded"). Now the
  standard path discards only faces with real input defects and
  keeps all the others with a compressed stack (thin but present, with a
  minimum anti-degeneration height). If the first layer turns out
  compressed, the log states it and suggests a coarser surface
  mesh to respect the requested y+.
- **Real boundary layers (no more fake "successes")**: the layer could
  turn out valid but invisible (1 layer when 14 were requested)
  because the consistency fixpoint is a minimum over the whole connected wall:
  a single vertex with a reduced budget flattened every face.
  Now a valid but flattened build is rejected and the layer is
  built with the maximum number of layers the mesh can hold at full
  height (`capped N -> k` in the log), with effective first layer, median
  and coverage reported in the log. On the real Part2 case: 1 layer at the exact y+
  target (full h1) or 2 layers with h1 halved, checkMesh PASS.
- **`drop_bnd` unit** (local face ids compared with positions in the
  filtered selection): with the sliver filter active ("robust BL")
  arbitrary faces were flagged; the valve golden goes from
  75,360 to 76,052 prisms (scale and exclusions unchanged).
- **Cell volume** measured with exact triangulation from the centroid
  (previously Newell/centroid, approximate on non-planar faces); the
  conservation tolerance (5e-3) documents the shell given up
  by-design under the excluded faces.
- **Silently compressed stacks**: the cap now triggers even when
  only one layer fits at full height (previously 14 layers were
  compressed into ~1 mm with y+ wrong by 20x without warning).

### Changed

- **README rebuilt after a bad merge**: the badges, the four up-to-date
  screenshots (the mesher selector, the boundary-layer panel and two
  viewer renders of the 398K-cell polyhedral mesh) and the clean
  architecture diagram are back; the mesher caption no longer mentions the
  removed FEM Tetra path.
- **Layers visible from the mesh itself (overlay removed)**: the checkbox
  "Show BL", the orange cell colouring and the colorbar that
  appeared after meshing were **removed** (user request:
  unwatchable). Boundary layers are seen as in the previous
  release: in "Surface with Edges" the stack appears as the band of
  dense edges at the wall — it is the mesh itself, no overlay.
- **Poly pipeline performance (profile-driven, all bit-identical)**: the
  dual converter is 24-43% faster (face-topology cache + pure-Python
  coordinate shadows in the edge emitter), the BL precompute 45-50%
  faster (numpy grouping/scatter aggregation; element values stay scalar
  to keep the ULP-exact inversion thresholds), and the consume reflow
  ~2x faster (hoisted degree array + pinned-target pruning).  A per-run
  `[perf] stage times:` line now covers every stage and
  `POLYFOAMMESH_PROFILE=1` dumps cProfile hot lists for the dual and BL.
  Every change was gated on byte-identical polyMesh SHA-256 on the
  regression cases (orchestrator-verified A/B).
- **Mesher order: Polymesh first, cfMesh second** and cfMesh no longer
  pre-ticks "Convert to polyhedral mesh": the hex-dominant mesh is the
  point there, the poly dual is opt-in (Polymesh keeps forcing the
  conversion ON, hidden).
- **English-only repository policy**: all public documentation
  (CHANGELOG, README, AI_COLLABORATION, INSTALL, USER_GUIDE, residual
  risks, CONTRIBUTING, CODE_OF_CONDUCT, THIRD_PARTY_LICENSES, GitHub
  templates) and all developer docs, plans and reasoning notes are now in
  English; the Italian duplicates (INSTALL.it.md, USER_GUIDE.it.md,
  README.it.md) and the stale Italian release notes
  (docs/assets/release-notes-v2.1.0.md) were removed.
- **Robust boundary layer always active**: the "Robust boundary
  layer (recommended, experimental)" checkbox was removed — the measured
  bundle (sliver-face filter 0.9 + budget termination) now activates
  **by itself** when "Enable Boundary Layers" is checked. No more
  knobs to know: check the BL and it comes in with the right settings.
- **Tidy left dock**: the patch table now **fills** the space
  under the workflow (it was capped at 140px with an empty grey band
  above) and the "Patches" title has the same style as "Next action": the
  left column is an ordered list (Workflow → Next action →
  Patches).
- **Automatic concave closure**: the worker retries
  `decoupled_vertex` (H4) by itself when the standard path fails, then
  halves the first layer. It activates where needed (concave
  junctions: groove1/slot1, production valve) without degrading the cases already
  handled by the standard path. The memory guard (8M faces / 1.5M cells) protects
  the closure retry.
- **No more buttons for things the app must do on its own** (user
  request): removed "Calibrate from simulation" (the calibration module
  stays internal), "Fit BL to mesh" (stack compression is
  handled by the engine and stated in the log), the "Surface resolution"
  combo (now **derived automatically from the Detail slider**: very
  coarse/coarse → Coarse, medium → Medium, fine/very fine → Fine/Very
  fine) and the mesh cache checkbox (always active). The y+ calculation is
  **automatic**: it starts when the BL is enabled and updates when
  Velocity/Fluid/Wall treatment change; a manual edit of the values
  is respected (no overwrite).
- **Mesher list**: **Polymesh is the default path** (previously
  Cartesian cfMesh, which remains as an alternative); the
  "FEM Tetra (GMSH)" button was **removed** (a niche that duplicated the
  GMSH stage — the internal tetrahedral algorithm remains as an automatic fallback
  for complex geometries).
- **Viewer during runs**: the periodic render (30 FPS) is
  **paused** while the mesh is in progress and resumed at the end, to avoid
  competing with the workers (single helper `_set_run_state`).
- **Feature angle**: range extended to 5–179° (was 5–80°, it did not allow
  values like 89°); the last used angle is remembered.
- **Interface entirely in English**: panels, dialogs, tooltips and
  log messages (Italian strings had remained in params_panel,
  main_window, viewer and in the engine templates).
- **Light/dark theme**: root cause of the mixed-colour "islands"
  found in the app QSS (opaque background inherited from QLabel);
  runtime fix + palette-driven About/Template dialogs and
  theme-aware text/contrast colours (contrast verified, minimum 6.0).
- **Refinement box flow**: picking in the viewer now creates a box
  centred on the point (previously it created a legacy sphere and never showed
  the box); zone list with readable labels and level synchronised
  with the selection.
- **Workflow**: the checkmarks follow the real process (geometry -> sizing
  -> generate -> quality) also for the GMSH/autopoly/native paths,
  not just cfMesh.

### Removed

- "Advanced closure for concave geometries (experimental)" checkbox from the
  Boundary Layers panel, `get_bl_concave_closure()` and the
  `concaveClosure` key from the GUI payload. The legacy flag remains only for the
  post-BL merge repair (off by default, measured in regression on
  groove1).
- **Full Auto** button from the ribbon. The **Cancel** is no longer in the
  ribbon: it was moved to the Mesh tab, **under "Reset All"**, where
  the user looks during a run (it appears only while the mesh is in progress).
- **Decimation** combo from the viewer (the Auto policy remains fixed
  internally).
- Empty **Quality** tab from the left panel (it showed only "No quality
  data yet"; checkMesh remains in the log, in the quality panel and in the
  PDF report).
- **"Auto-Fix Quality"** button from the quality panel (and the related
  `fix_requested` signal): the quality gate already retries automatically,
  the button was redundant.

## [2.2.0] - 2026-09-08

### Breaking changes

- **Data path renamed**: the app data moved from
  `%APPDATA%\cfmesh-autogui\` to `%APPDATA%\polyfoammesh\` (logs, sessions,
  octopoda telemetry). On first run the new folder is
  populated automatically by copying the contents of the old one, if
  present. Those who installed a previous version lose nothing, but
  the old path is no longer written. The app settings
  (theme, recent parameters) live in the Windows registry and do not change
  location: they stay where they were, so the user's preferences survive
  the package rename (`cfmesh_autogui` → `polyfoammesh`).

### Added

- **Release pipeline**: on push of a `v*` tag, GitHub Actions builds
  the PyInstaller executable + the Inno Setup installer on Windows and attaches
  them to the tag's GitHub Release (with the changelog extracted from this file).
  Removed the obsolete installer scripts (duplicated NSIS and Inno); fixed
  `UninstallDisplayName` in `inno_setup.iss` (directive split over two
  lines).
- **Algorithm substitution notice in the GUI**: when the engine silently
  substitutes a meshing path (boundary layer disabled,
  parallel→serial, coarser GMSH retry, quality retry), the
  log panel now shows a highlighted `[SUBSTITUTED]` line — never
  silent again.
- **Bilingual documentation**: `docs/INSTALL.md` and `docs/USER_GUIDE.md`
  rewritten in English (primary), with the Italian counterparts under
  `docs/`, cross-linked at the top.
- **Community**: `CODE_OF_CONDUCT.md`, PR template in
  `.github/PULL_REQUEST_TEMPLATE.md`, 5 "good first issue" issues on the
  public repo.
- `_paths.py`: single source for the app data directory and one-shot
  migration from the legacy path.

## [2.1.0] - 2026-09-08

### Breaking changes

- **Python package rename**: `cfmesh_autogui` → `polyfoammesh`. The
  imports in the code, tests and tools were updated; the CLI
  entry-point modules are now `polyfoammesh`, `polyfoammesh-mesh` and
  `polyfoammesh-batch`. The PyInstaller executable is called `PolyFoamMesh`.

### Added

- CI green again on GitHub Actions (it was red on every push): `pytest-qt`
  in the extras, POSIX-compatible paths in `config.py`/`validation.py`,
  `networkx` and `rtree` dependencies declared.

### Fixed

- Manual refinement zones: `setAsBackgroundMesh` discarded the geometric
  adaptive field; now MIN-combined (`gmsh_wrapper.py`).
- Closed curved bodies (e.g. sphere) tessellated non-watertight: coincident
  vertices at seams/poles now merged and degenerate triangles dropped
  (`geometry.py::tessellate_patches`).
- Tests that overwrote tracked `sample_cad/`; always-skip stub
  replaced with a real integration test.
- Git history: ~278 MB blob (`installer/output/*.exe`) removed from the
  `master` history with `filter-branch`.

### Note

- The prebuilt executable and installer are not committed to the repo:
  they are found in `dist/` and `installer/output/` respectively (both
  ignored by `.gitignore`) and are generated locally or by the release
  pipeline.

[2.2.0]: https://github.com/LorenzoRiti/PolyFoamMesh/releases/tag/v2.2.0
[2.1.0]: https://github.com/LorenzoRiti/PolyFoamMesh/releases/tag/v2.1.0
