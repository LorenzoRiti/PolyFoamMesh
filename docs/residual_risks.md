# Residual Risks And Known Limitations

Status after the Commercial Hardening pass (Deepwork). This document records
the known, accepted limitations that remain after the hardening work, so a
future session does not rediscover them and product decisions have a single
source of truth.

## Meshing Timeouts

- GMSH volume meshing uses a 3600 s wall-clock timeout in every path
  (GUI worker, subprocess helper, adaptive solver). A Very Fine run capped at
  20M cells can still exceed it on very large parts; the failure is surfaced
  clearly in the log, but the estimate in the UI should be consulted first.
- `gmshToFoam` uses 900 s. Extremely large MSH files (many millions of
  elements) may exceed it; the case directory is left in a convertible state.

## Polyhedral Conversion

- The barycentric dual is the only tet->poly path in use. `wedge_cells` and `median_faces` are disabled by default. On the valve A/B measurement, `median_faces=True` produced 1,056 residual defects versus 1,027 with `median_faces=False`, so the default remains off. `wedge_cells` made no difference in the same A/B measurement and is incompatible with the collapse configuration. A post-construction quality smoothing pass (`core/poly_smoother.py`, keep-best, interior vertices only, boundary pinned) is enabled by default. On CAD parts with
  many concave re-entrant features the dual can still produce a small number
  of non-convex boundary cells (a few hundred on the reference valve),
  failing checkMesh; the poly mesh is then kept and shown with a warning
  instead of silently falling back to tet.
- `split_rounds` remains disabled: measured to add cost without reducing
  defects on the reference part. Re-measure before enabling.
- `TerminalFaceWorker` was removed as dead code; `terminal_face.py` remains
  only for the historical benchmark harness (`tests/bench_tet_poly.py`).
- **BL + poly path (P3a validated)**: `tools/bench_bl_poly.py` runs
  cartesianMesh (hex) -> polyDualMesh -> checkMesh on the venturi. Result:
  poly mesh has 2848 prism cells + 2720 polyhedra, checkMesh `Mesh OK`
  (skew 1.29, NOmax 54.6). This matches the historical documented value
  (2848 prism cells for venturi) and confirms the cfMesh path already
  produces the prism+poly combo required for wall-resolved CFD. The hex
  mesh before polyDualMesh reports 0 prisms for this geometry/config — the
  prisms appear through the dualisation itself; see bench for details.
- **BL on OUR OWN poly mesh (P3b validated)**: `tools/bench_bl_poly_dual.py`
  runs GMSH tet -> gmshToFoam -> barycentric dual -> `core/bl_poly.py`
  (our own advancing-layer engine) -> checkMesh on a cylinder. Result:
  505,950 prism cells + 72,483 polyhedra, checkMesh `Mesh OK` — i.e. the
  definitive all-ours CFD mesher (poly interior + prism boundary layers).
  The GUI now keeps BL enabled on the "Polyhedral (CFD)" path: the
  converter still only ever sees pure tetrahedra, the layers are added
  after conversion.
- **BL selettivo per patch (FASE 1, 2026-08-10)**: `core/bl_poly.py` non
  auto-chiude più la selezione su tutto il boundary. Con `patch_names`
  (default del runner: solo patch `wall` / nome *wall* / ruoli geometrici
  da `infer_patch_roles`), le facce laterali dei prismi al bordo della zona
  BL giacciono nel piano della parete adiacente e diventano **facce di
  boundary della patch senza BL** (possedute dai prismi); le facce di parete
  non selezionate muovono i vertici condivisi all'ultimo layer. Verifica
  reale (`tools/bench_bl_poly_partial.py`, checkMesh WSL): cilindro con
  patch nominate inlet/outlet/wall → BL solo su wall, 72 prismi =
  3 layer × 24 facce, `Mesh OK`; cubo duct idem (81 celle, skew 2.18,
  NOmax 44.8, `Mesh OK`). `n_prism_cells == n_layers × facce_di_parete`
  derivato dal mesh di ingresso, mai hardcoded.
- **Solver sulla valvola (FASE 0, 2026-08-10)**: vedi
  `docs/poly_solver_validation.md` — potentialFoam converge sulla poly della
  valvola (residuo finale 4.6e-6, continuity error 1.6% del flusso, volume
  0.00%), quindi il difetto concavo (852 facce "incorrectly oriented",
  checkMesh non-OK) è un **limite documentato**, non un bug: FASE 3 non
  necessaria.
- **FASE 2 (terminazione locale dei layer, 2026-08-10)**: macchina
  implementata (conteggio per-vertice nv basato su angle_fade, conteggio per
  faccia, fixpoint di consistenza che rende il conteggio uniforme per
  componente connessa, gate piramidi rilassato `n_pyr_after <= n_pyr_before`,
  facce violate dell'input ridotte a 1 layer) e verificata su mesh sane
  (cubo 6/6: chiusura 0 celle, volume 1e-6; gate FASE 1 ri-verificato PASS).
  Sulle geometrie con difetti concavi pre-esistenti (valvola) il BL fallisce
  ancora pulito con mesh invariata: la chiusura non regge in nessuna delle
  strategie misurate (199 celle non chiuse nella costruzione non vincolata;
  63.252 nel fixpoint; il drop a 0 layer perde il volume). Causa radice:
  angle_fade >= 0.8 su TUTTI i vertici di parete della valvola — la
  concavità delle celle duali non è rilevabile dalle normali di parete.
  Il fallback globale su 5 scale resta il comportamento della valvola.
- **Criterio di concavità duale (Lane B, 2026-09-08)**: nuovo parametro
  opt-in `concavity_criterion="dual_convexity"` in `core/bl_poly.py`
  (funzione `_dual_convexity_wall_fade`; default `"angle_fade"` invariato e
  byte-identico). Il detector misura la convessità delle CELLE DUALI
  direttamente: per ogni vertice di parete, frazione delle facce di parete
  incidenti il cui owner cell è non-convesso nel senso esatto del test
  'face pyramids' di checkMesh (centroide OpenFOAM della cella fuori da una
  sua faccia), mappata nella stessa convenzione 0.05..1.0 di angle_fade.
  Misurato sulla fixture valvola (1.051.199 punti, 1.241.048 facce, 152.086
  celle): il segnale scatta ESATTAMENTE sulla classe di difetto documentata —
  393 celle duali non-convesse (intervallo documentato 268-462), 1007 facce
  difettose (= i 1007 difetti piramide dell'input), 1123 vertici di parete
  con fade < 0.5 contro ZERO per angle_fade (fade >= 1.0 su tutti i 226.538
  vertici, confermando la causa radice documentata). Esito misurato dei due
  run completi (in-process, replica checkMesh; n_layers=2, h1=1e-5,
  apply_to_all=True, 5 scale):
  - `angle_fade` (baseline): chiusura fallita a ogni scala — 161 → 106 → 65
    → 45 → 15 celle non chiuse, nessun volume negativo, 523 s, mesh invariata;
  - `dual_convexity`: 24 → 378.605 celle a volume non positivo (flip globale
    del winding alla scala 0.6, 99.99% della mesh) → 11 → 12 → 6 celle non
    chiuse, 599 s, mesh invariata. Meglio della baseline su 4 scale su 5
    (fino a 6.7x a scale=1.0: 24 vs 161), ma NESSUNA scala arriva a 0 celle
    non chiuse: nessun BL valido nemmeno col criterio nuovo.
    Perché non chiude G2 (misurato, non assunto): il fixpoint di consistenza
    appiattisce il conteggio a uniforme per componente connessa — misurato
    nv=nf=1 su TUTTE le 226.542 facce sotto ENTRAMBI i criteri (le 937 facce
    dell'input segnalate forzano l'intera parete a 1 layer in ~100 passate di
    fixpoint). Un detector per-vertice può quindi cambiare solo l'ALTEZZA
    locale (max_h = fade x ...), non il conteggio: migliora la chiusura ma non
    la porta a 0, e destabilizza la riparazione del winding a scale intermedie.
    Raccomandazione: il segnale di convessità duale è un detector STRETTAMENTE
    migliore del fade (393 celle vs 0) e più utile per la chiusura a 4/5 scale,
    ma da solo non chiude la valvola; per sfruttarlo serve rilassare il
    fixpoint (conteggi per-faccia con facce di transizione che chiudono, o
    fixpoint limitato alla regione difettosa). Default invariato: ri-misurare
    prima di abilitare. Gate WSL (`tools/bench_bl_valve_fase2.py`) NON eseguito:
    nessuna scala produce una mesh valida in-process (il verdetto dell'engine
    è deciso dal replicatore, che sulla valvola coincide con checkMesh 895/895),
    quindi non esiste una mesh nuova da sottoporre a checkMesh — la baseline
    pinnata resta il comportamento della valvola. Gate salute
    (`tools/bench_bl_poly_partial.py`): PASS invariato (cilindro 72 prismi =
    3 x 24, Mesh OK; cubo duct 81 celle, skew 2.175, NOmax 44.8, Mesh OK);
    test veloci `tests/test_bl_poly.py`: 6/6 verdi.

## Boundary Layer Patch Selection — single source of truth

- `core/patch_roles.py` is now the **only** place that answers "is this
  boundary patch a wall?". It used to be answered independently, and
  differently, in `gui/main_window.py::_is_wall_patch`,
  `commercial/bl_engine.py::detect_wall_patches`,
  `commercial/bc_editor.py::_name_to_type`, `core/case_setup.py::_patch_role`
  and `core/meshdict_gen.py::infer_patch_type`. All but the last now delegate
  (`infer_patch_type` maps to an OpenFOAM *boundary type*, which needs finer
  distinctions than a role — e.g. `wedge` and `cyclic` are not symmetry
  planes).
- Two-level API on purpose: `classify_patch()` defaults a silent name to
  `wall` (the safe majority, used where a decision is mandatory — BL
  extrusion); `match_role()` returns `None` for a silent name so callers with
  a geometric fallback (`bc_editor`, `case_setup`) still reach it. GMSH names
  every patch `surface_N`, so treating "no keyword" as "wall" pre-empted
  geometry on exactly the cases geometry exists to solve.
- **The "apply BL to every patch" fallback is gone.** When neither the
  boundary type/name nor the geometric inference identifies a wall — closed
  domains, external aero, multi-inlet manifolds — the poly worker
  (`core/openfoam_runner.py::poly_bl_patch_selection`) now excludes only what
  is positively known to be an inlet/outlet/symmetry and extrudes from the
  rest; if nothing is left it **skips the boundary layers**. Previously it
  extruded into all patches, i.e. prisms in the inlets and outlets, on
  precisely the geometries where the least is known. checkMesh accepts that,
  so the failure only surfaced when the solver diverged. A mesh without
  layers is a recoverable warning; a mesh with layers in the inlet is a wrong
  answer.
- An explicit user override ("Apply BL to all patches") is still honoured
  literally — the guard must not make a deliberate choice impossible.
- Regression coverage: `tests/test_poly_bl_patch_selection.py` (the decision
  function) and `tests/test_wall_detection_chain.py` (all four entry points
  must agree, and must agree on "skip", never "everywhere").

## Poly Mesh Quality Remediation

- `commercial/poly_remediation.py` (new). The tet→poly path previously had
  **no** automated remediation: `QualityEngine.auto_fix` and
  `MeshOptimizer.optimize` both drive their loop through a cfMesh
  `cartesianMesh` re-run, which `mesh_remeshable` refuses whenever a tet
  backup exists — i.e. exactly for the dual-poly path. A defective poly mesh
  was analysed and then left untouched.
- The loop is strictly non-regressive: one keep-best `poly_smoother` pass per
  iteration (interior dual vertices, boundary pinned), accepted only when the
  defect count measured by the in-process checkMesh replica strictly
  decreases; otherwise the candidate is discarded and the mesh left
  byte-identical. Bounded by iteration and wall-time caps.
- The concave-feature defect class (boundary-face pyramid failures) is
  detected and **skipped**, per the measured dead ends in
  `docs/handoff_poly_bl_deepseek.md`.
- Degrading to the tet mesh is available but **opt-in only** (reported, never
  applied automatically): the user asked for poly.
- Local refinement is deliberately NOT implemented here:
  `local_refinement_boxes_from_checkmesh_sets` emits cfMesh
  `objectRefinements`, which only make sense as input to a cfMesh re-mesh,
  and this path has no re-mesh.

## Adaptive Escalation Substitutes Algorithms — by design, now reported

- `MeshEngine` escalation is **on by default** (`adaptive_escalation=True`,
  `max_escalation_steps=3`). When the requested algorithm fails the quality
  gates the engine escalates along
  `CartesianHex → HexCorePoly → Tetrahedral → PolyAggregated → SnappyHexMesh`
  and returns `success=True` with a **different topology than requested** — a
  user whose solver needs hex-dominant could get tet and a green checkmark.
- `MeshEngineResult` now exposes `algorithm_substituted` plus
  `original_algorithm`, `escalation_reason` and `metrics_before/after`, and
  this propagates to `QuickMeshResult`, `FullAutoResult`, the one-click JSON
  report and the `verification` A/B suite (which flags `[SUBSTITUTED]`).
  `success` still means "a mesh was produced" — `quality_passed` is the
  separate, authoritative quality flag.
- **Known limitation:** the main GUI meshing path does not go through
  `MeshEngine` (it uses `RetryRunner`/`cartesianMesh` directly and never
  builds a `MeshEngineResult`), so a GUI user is not yet warned in the log.
  The substitution is recorded in the artefacts that do flow through
  `MeshEngine` (quick mesh, one-click, A/B verification). Wiring a GUI
  warning is the remaining work.
- **Redundant rung removed (2026-09-02):** `HEX_CORE_POLY` and `POLYHEDRAL`
  dispatch to the identical pipeline (`_run_cartesian_hex` +
  `_run_polyhedral`), i.e. `polyDualMesh` over the whole mesh. The ladder
  used to climb `Polyhedral → HexCorePoly`, which re-ran the entire pipeline
  only to rebuild the same dual mesh — a no-op that consumed one of just
  three escalation attempts. The rung is now `POLYHEDRAL` (rank 2) instead of
  `HEX_CORE_POLY` (rank 3): identical result for a hex start, and a
  `POLYHEDRAL` start escalates straight to tetrahedral instead of into
  itself. Guarded by `test_escalation_ladder_no_redundant_dispatch` and
  `test_next_escalation_from_polyhedral_skips_the_no_op`.
- **`HexCorePolyBoundary` is not a Mosaic mesher.** It was labelled "Hex Core
  + Poly Boundary (Mosaic-style)" while producing no hex core, no poly
    transition zone and no prisms. Relabelled "Hex → Polyhedral (cfMesh
    polyDualMesh)"; the enum value is unchanged so existing case configs still
    load. `commercial/mosaic.py` likewise only runs whole-mesh `polyDualMesh 90` — its "Mosaic" name is historical and its docstrings now say so.
    Implementing a true Mosaic topology remains out of scope.
- **`polyDualMesh` increases the cell count (~15-30%)**, it does not reduce
  it. Two descriptions claimed a 40-60% reduction; corrected. The *tet→poly
  barycentric dual* is the one that reduces cells (~4-5x, measured:
  box_obstacle 12597 → 2575, pipe 10014 → 2437). Different code paths — do
  not conflate them.

## Viewer

- Volume-mesh display renders the internal VTU wireframe. On meshes above
  ~2M cells the view is decimated for interactivity; the on-disk mesh is
  never modified. The Section Cut view shows the true internal cells.
- The viewer depends on `foamToVTK` being available inside WSL2/OpenFOAM.
  When it is missing or times out, the viewer shows a ParaView hint instead
  of the mesh.

## Sizing And Estimates

- **Manual refinement zones preserve geometry sizing (2026-09-08)**: the
  refinement-zones block used to call `setAsBackgroundMesh()` on the zone
  fields alone, silently DISCARDING the geometry-adaptive background field
  (curvature/small-feature/gap sizing) whenever a manual refinement box was
  active — documented as an unfixed bug in
  `docs/dev/solution_adaptive_handoff.md` §1.4. Zones are now MIN-combined
  with the existing field (`_combine_zone_fields_with_bg`), so a zone can
  only ask for smaller cells, never change sizing elsewhere. Regression
  tests: `test_gmsh_refinement_boxes.py::test_single_zone_min_combined_with_existing_bg`
  and friends.
- The Mesh Fineness slider value is a budget/cap (10K..20M cells), not a
  guarantee. The displayed geometry estimate is derived from the tessellated
  solid volume and the derived cell sizes; when the volume cannot be trusted
  (open/non-watertight tessellation) the estimate is omitted rather than
  invented.
- The GMSH adaptive path sizes from real geometry features; the cfMesh path
  uses the derived Max/Min cell sizes. The two meshers can therefore produce
  different cell counts for the same slider position.
- Pre-existing test failure (not caused by the hardening pass, reproduced on
  commit d0337b5): `tests/test_sizing_resolves_features.py` failed because
  `cq.Workplane` fixtures were authored in millimetres while
  `tessellate_patches` converts to metres — local thickness was measured
  ~1000x smaller than the test expected (p1 ≈ 5.99e-05 vs 0.06). The
  unit-of-authoring contract for in-memory CAD fixtures needed a product
  decision (author fixtures in metres, or scale before tessellation); do not
  patch the failing assertions to hide it.
  **RESOLVED (2026-08-03)**: the fixtures are now authored at metre scale
  in cadquery millimetres (0.1 m rod = `circle(100)`, throat = `circle(30)`),
  matching the app's documented contract that tessellation converts mm→m.
  The assertions were not touched; the test passes (4/4).
- **Closed curved bodies tessellate watertight now (2026-09-08)**: the old
  "sphere → not watertight" finding from `docs/dev/notes/NIGHT_LOOP_PROMPT.md`
  is fixed. OCC tessellation emits coincident duplicate vertices at the
  seams/poles of curved faces; `tessellate_patches` now merges them and drops
  the degenerate pole triangles, so a cadquery sphere passes
  `check_watertight()` (regression: `test_geometry.py::test_tessellate_closed_curved_body_is_watertight`).
- `test_watertight_meshdict.py::test_volume_mesh_and_quality_steps_do_not_need_a_qt_event_loop`
  is order-sensitive (shared QApplication state); it passes in isolation.

## Packaging And Runtime

- The frozen EXE is rebuilt on demand (`pyinstaller --clean --noconfirm PolyFoamMesh.spec`). The `dist/` artifact is not refreshed automatically
  and may lag the source tree.
- The API server module (`polyfoammesh.api.server`) imports cleanly with
  the installed FastAPI; it remains a CI/CD surface, not part of the GUI.

## Test Matrix

- Fast (no WSL) suite: green.
- Real WSL/OpenFOAM smoke (GMSH tet -> gmshToFoam -> dual poly -> checkMesh):
  `Mesh OK` on the reference cylinder case.
- Full WSL regression runs are not part of the fast suite; they require
  OpenFOAM v2512 in WSL2 and are executed manually or via the harnesses in
  `tools/`/`tests/bench_*`.
