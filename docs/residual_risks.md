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

- The barycentric dual is the only tet->poly path in use. `wedge_cells` and
  `median_faces` are enabled by default (measured best); a post-construction
  quality smoothing pass (`core/poly_smoother.py`, keep-best, interior
  vertices only, boundary pinned) is enabled by default. On CAD parts with
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

## Viewer

- Volume-mesh display renders the internal VTU wireframe. On meshes above
  ~2M cells the view is decimated for interactivity; the on-disk mesh is
  never modified. The Section Cut view shows the true internal cells.
- The viewer depends on `foamToVTK` being available inside WSL2/OpenFOAM.
  When it is missing or times out, the viewer shows a ParaView hint instead
  of the mesh.

## Sizing And Estimates

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
- `test_watertight_meshdict.py::test_volume_mesh_and_quality_steps_do_not_need_a_qt_event_loop`
  is order-sensitive (shared QApplication state); it passes in isolation.

## Packaging And Runtime

- The frozen EXE is rebuilt on demand (`pyinstaller --clean --noconfirm
  CFMesh-AutoGUI.spec`). The `dist/` artifact is not refreshed automatically
  and may lag the source tree.
- The API server module (`cfmesh_autogui.api.server`) imports cleanly with
  the installed FastAPI; it remains a CI/CD surface, not part of the GUI.

## Test Matrix

- Fast (no WSL) suite: green.
- Real WSL/OpenFOAM smoke (GMSH tet -> gmshToFoam -> dual poly -> checkMesh):
  `Mesh OK` on the reference cylinder case.
- Full WSL regression runs are not part of the fast suite; they require
  OpenFOAM v2512 in WSL2 and are executed manually or via the harnesses in
  `tools/`/`tests/bench_*`.
