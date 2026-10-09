# Residual Risks And Known Limitations

Status after the Commercial Hardening pass (Deepwork). This document records
the known, accepted limitations that remain after the hardening work, so a
future session does not rediscover them and product decisions have a single
source of truth.

## CI (2026-09-08)

- The GitHub Actions CI job is green again. Root causes of the original
  failure were NOT the package rename itself but what the rename exposed on
  the Linux runner: `pytest-qt` was missing from the installed extras
  (fixed in the rebrand commit), Windows-style case paths were mangled by
  `Path.resolve()` on POSIX (`config.py`, `core/validation.py` — fixed),
  and two trimesh optional dependencies the code paths actually use
  (`networkx` for CAD healing, `rtree` for native-mesher spatial queries)
  were not declared in `pyproject.toml` (fixed). The fast suite is now
  verified green on Linux (WSL mirror of ubuntu-latest) AND Windows before
  every push.
- The full `master` history could not be pushed for months because a
  278 MB installer `.exe` was committed once (blob still reachable from
  every later commit). The pushed `master` branch history was rewritten
  (filter-branch, `installer/output` dropped from the index across the
  range `origin/main..master`; 297 commits preserved, trees verified
  byte-identical). The pre-rewrite history is kept locally in the
  `backup-master-pre-rewrite` branch. NOTE: local `master` SHAs changed —
  references to old SHAs (e.g. in `.opencode/` notes) now point to the
  backup branch.
- Known limitation kept: the Linux run still cannot verify Windows-drive
  filesystem semantics (`test_engine_set_case_dir_nonexistent` is
  win32-only by design); the two conditional venturi-throat skips in
  `test_throat_venturi.py` remain (documented tessellation-dependent
  limitation of the throat detector).

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
- `split_rounds` remains disabled: re-measured on the 2026-09-09
  regenerated valve fixture (`TetPolyDualConverter(case,
  split_rounds=0|1)`; `tools/split_rounds_rebench.py`). Both runs
  produce **identical defect counts** (pyramid 863, non_ortho 203,
  skew 3, total 1069, volume drift 3.4e-16) — the converter's
  keep-best in `_run` selects round 0 and discards later rounds.
  `split_rounds=1` adds ~28 s wall time (138.1 s → 166.4 s, +20%)
  for zero defect improvement. Default unchanged (`split_rounds=0`
  off). A second re-measure on the next converter change is enough
  to update this note.
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
- **Per-patch selective BL (PHASE 1, 2026-08-10)**: `core/bl_poly.py` no
  longer auto-closes the selection over the whole boundary. With `patch_names`
  (runner default: only `wall` patches / name *wall* / geometric roles
  from `infer_patch_roles`), the lateral faces of the prisms at the edge of the
  BL zone lie in the plane of the adjacent wall and become **boundary
  faces of the patch without BL** (owned by the prisms); the wall faces
  not selected move the shared vertices to the last layer. Real verification
  (`tools/bench_bl_poly_partial.py`, WSL checkMesh, re-measured
  2026-09-09): A. GMSH cylinder with patches named inlet/outlet/wall → BL
  only on wall, 60,354 prisms = 3 layers x 20,118 faces (70,976 cells,
  skew 2.264, NOmax 81.1, `Mesh OK`); B. cube duct (in-memory) → 81 cells,
  72 prisms = 3 x 24, skew 2.175, NOmax 44.8, `Mesh OK`. The old numbers
  "72 prisms = 3 x 24" were the cube, not the cylinder.
  `n_prism_cells == n_layers × wall_faces` derived from the input mesh,
  never hardcoded.
- **Solver on the valve (PHASE 0, 2026-08-10)**: see
  `docs/poly_solver_validation.md` — potentialFoam converges on the valve
  poly (final residual 4.6e-6, continuity error 1.6% of the flow, volume
  0.00%), so the concave defect (852 "incorrectly oriented" faces,
  checkMesh non-OK) is a **documented limit**, not a bug: PHASE 3 not
  necessary.
- **PHASE 2 (local layer termination, 2026-08-10)**: machinery
  implemented (per-vertex count nv based on angle_fade, per-face count,
  consistency fixpoint that makes the count uniform per connected
  component, relaxed pyramid gate `n_pyr_after <= n_pyr_before`,
  input violated faces reduced to 1 layer) and verified on healthy meshes
  (cube 6/6: closure 0 cells, volume 1e-6; PHASE 1 gate re-verified PASS).
  On geometries with pre-existing concave defects (valve) the BL still
  fails cleanly with the mesh unchanged: the closure holds in none of the
  strategies measured (199 unclosed cells in the unconstrained construction;
  63,252 in the fixpoint; the drop to 0 layers loses the volume). Root cause:
  angle_fade >= 0.8 on ALL wall vertices of the valve — the
  concavity of the dual cells is not detectable from the wall normals.
   The global fallback over 5 scales remains the valve's behaviour
   while `local_termination` stays opt-in (see PHASE 2 result below).
- **Dual concavity criterion (Lane B, 2026-09-08)**: new opt-in
  parameter `concavity_criterion="dual_convexity"` in `core/bl_poly.py`
  (function `_dual_convexity_wall_fade`; default `"angle_fade"` unchanged and
  byte-identical). The detector measures the convexity of the DUAL CELLS
  directly: for each wall vertex, the fraction of incident wall faces
  whose owner cell is non-convex in the exact sense of checkMesh's
  'face pyramids' test (OpenFOAM centroid of the cell outside one of
  its faces), mapped into the same 0.05..1.0 convention as angle_fade.
  Measured on the valve fixture (1,051,199 points, 1,241,048 faces, 152,086
  cells): the signal fires EXACTLY on the documented defect class —
  393 non-convex dual cells (documented range 268-462), 1007 defective faces
  (= the 1007 pyramid defects of the input), 1123 wall vertices
  with fade < 0.5 against ZERO for angle_fade (fade >= 1.0 on all 226,538
  vertices, confirming the documented root cause). Measured outcome of the two
  complete runs (in-process, checkMesh replica; n_layers=2, h1=1e-5,
  apply_to_all=True, 5 scales):
  - `angle_fade` (baseline): closure failed at every scale — 161 → 106 → 65
    → 45 → 15 unclosed cells, no negative volume, 523 s, mesh unchanged;
  - `dual_convexity`: 24 → 378,605 cells with non-positive volume (global
    winding flip at scale 0.6, 99.99% of the mesh) → 11 → 12 → 6 unclosed
    cells, 599 s, mesh unchanged. Better than the baseline on 4 scales out of 5
    (up to 6.7x at scale=1.0: 24 vs 161), but NO scale reaches 0 unclosed
    cells: no valid BL even with the new criterion.
  Why it does not close G2 (measured, not assumed): the consistency fixpoint
  flattens the count to uniform per connected component — measured
  nv=nf=1 on ALL 226,542 faces under BOTH criteria (the 937 flagged input
  faces force the whole wall to 1 layer in ~100 fixpoint passes).
  A per-vertex detector can therefore change only the local HEIGHT
  (max_h = fade x ...), not the count: it improves closure but does not
  bring it to 0, and it destabilises the winding repair at intermediate scales.
  Recommendation: the dual-convexity signal is a STRICTLY better detector
  than the fade (393 cells vs 0) and more useful for closure at 4/5 scales,
  but by itself it does not close the valve; to exploit it one must relax the
  fixpoint (per-face counts with transition faces that close, or a
  fixpoint limited to the defective region). Default unchanged: re-measure
  before enabling. WSL gate (`tools/bench_bl_valve_fase2.py`) NOT run:
  no scale produces a valid mesh in-process (the engine's verdict
  is decided by the replicator, which on the valve matches checkMesh 895/895),
  so there is no new mesh to submit to checkMesh — the pinned baseline
  remains the valve's behaviour. Health gate
  (`tools/bench_bl_poly_partial.py`): PASS unchanged (cylinder 72 prisms =
  3 x 24, Mesh OK; cube duct 81 cells, skew 2.175, NOmax 44.8, Mesh OK);
  fast tests `tests/test_bl_poly.py`: 6/6 green (9/9 with the new tests
  for the global solver and the binary termination).
- **GLOBAL winding solver (Lane B, 2026-09-08)**: the per-cell
  greedy repair was replaced by an EXACT solution
  (`_solve_global_windings`): parity constraints for each face-side
  (the two faces of a cell on an edge must traverse it in
  opposite directions), solved with BFS on the face graph, then a global sign per
  component (total positive volume). Deterministic, O(F+E). Measured
  on the valve at ALL 5 scales: **0 unclosed cells** (max_rel
  ~1e-12, was 24 at scale 1.0), **volume conserved to 1e-16** (was
  drift), no catastrophic global flip — the old greedy at scale
  0.6 produced 378,605 cells with non-positive volume, now none.
  The solver also reports `n_conflicts` (non-orientable mesh) and
  `n_nonmanifold_edges`: on the valve both 0. Healthy gate
  (`tools/bench_bl_poly_partial.py`) unchanged and PASS: cylinder 60,354
  prisms = 3 x 20,118, `Mesh OK`; cube duct 81 cells, 72 prisms =
  3 x 24, skew 2.175, NOmax 44.8, `Mesh OK`. New regression test:
  `tests/test_bl_poly.py::test_bl_global_winding_solve_is_exact_after_flip`
  (a manually flipped face is exactly re-closed).
- **BL closure on concave (PHASE 2 completed, 2026-09-09)**: opt-in
  `run(local_termination=True)` produces the first VALID BL on the valve.
  Mechanism in two parts, both opt-in:
  1. binary termination (`_build(..., zero_concave=True)`): a face either
     receives the entire layer stack or is excluded (0 layers) — no
     consistency fixpoint that flattens the healthy region to 1 layer;
  2. iterative exclusion (`_collect_exclusions`): the faces whose prisms
     turn out invalid (non-positive volume, pyramids, displaced core
     cells) are removed from the selection and it tries again; up to
     5 scales x 3 rounds, exclusions carried between scales; the mesh is
     written ONLY if the validation gate passes (otherwise it stays
     unchanged, the same contract as the standard path).
  Measured end-to-end on the valve (1,051,199 points, 1,241,048 faces,
  226,542 wall faces; n_layers=2, h1=1e-5, `dual_convexity`):
  success at scale 0.1 after exclusions at 1.0/0.6/0.35/0.2 — **19,625
  faces excluded (8.7%)**, 410,668 prism cells, thickness 2.2e-06, volume
  conserved (0.1218350 vs 0.1218351 input), min cell volume
  1.15e-14, 1602 s. Real checkMesh: **891 "incorrectly oriented" faces vs
  895 of the input (better)**, non-ortho max 123.3, max skewness 46.8 vs
  45.0, "Failed 4" vs "Failed 3" — the only extra check is the aspect ratio
  (max 15522, 15,921 cells), intrinsic to thin BL prisms (h1 = 1e-6 m
  at scale 0.1); no other check worsens. Default unchanged
  (`local_termination=False`): the standard path on the valve still fails
  cleanly as documented (slow test
  `test_valve_fixture_bl_invariants`, 1 passed in 473 s). New tests in
  `tests/test_bl_poly.py` (exact closure, binary drop, success on a healthy
  cube): 9/9 fast green.
  The PHASE 1 bench also exposed a reader bug:
  `foam_mesh_io.read_label_list` mistook for ASCII a binary payload whose
  64-byte probe contained a 0x29 byte (cell indices like 41/296/
  10537), returning an EMPTY list — and the patch rename rewrote the
  corrupted polyMesh (neighbour=0). Fixed with the test "all bytes are
  ASCII text" in place of the `')' in probe` heuristic, plus regression test
  `test_local_refinement_boxes.py::test_read_label_list_binary_payload_with_0x29_byte`.
- **PHASE 3 — does local termination generalise? (2026-09-09)**.
  Protocol: `local_termination=True` on geometries other than the valve,
  REAL checkMesh before/after, same metrics (unclosed cells, % excluded,
  time). Reasoning and predictions written BEFORE the tests in
  `notes/fase3_reasoning.md` (untracked). No default changed; no
  GUI wiring (blocked on the user validation of PHASE 2).

  **Cheap predictor (without extruding anything):** count on the dual
  `pyr_input` (faces that violate the pyramid criterion) and the wall
  vertices with dual fade < 0.5. It ranks the difficulty exactly like the
  runs: cylinder 0/0, cube 0/0, groove1 4/2, slot1 42/17, valve
  1,007/1,123. Cost: 27 s on the valve (vs 115 s of the first build).

  **Outcomes (n_layers=3, h1=0.005, apply_to_all; `standard` vs
  `local_termination` dual_convexity, real checkMesh):**
  - cylinder (dual 10,618 cells, 24,936 faces): standard 74,808 prisms,
    scale 1.0, Mesh OK (skew 2.97, NOmax 81.1) — **LT identical, 0
    excluded**, 12.6 s; `angle_fade` identical too. No worsening
    where the standard path already works.
  - cube duct (9 cells): identical, 108 prisms, Mesh OK.
  - groove1 (682 cells, 3,372 faces; predictor 4/2): standard
    "success=True" at scale 0.1 but with EVERYTHING at 1 layer and checkMesh
    **Failed 2** (2 non-ortho errors, skew 46.2) — a mesh that checkMesh
    rejects. LT dual: **10,062 prisms (3 layers), scale 1.0, Mesh OK**
    (skew 3.29, NOmax 89.1), 18 faces discarded by the binary criterion (0
    iterative exclusions), 1.4 s. LT angle: 10,032 prisms, 28 discarded,
    Mesh OK. LT triples the layers AND makes the mesh valid.
  - slot1 (9,372 cells, 26,754 faces; predictor 42/17): standard
    **FAILS** at all scales (mesh unchanged, 34.5 s). LT dual:
    **79,614 prisms, scale 1.0, Mesh OK** (skew 2.89, NOmax 87.0), 216
    faces without BL = 0.8%, 27.1 s; LT angle: 79,593 prisms, 190 discarded,
    Mesh OK. Second concave geometry where LT unlocks the BL.
  - valve: predictable seed 2,737 faces (1.21%): 937 defective input +
    1,800 with a vertex at fade<0.5 (0 drop-only). Run at 0.1 with the
    production exclusion logic: 3 rounds (114.8/90.7/99.4 s),
    2,058 iterative exclusions, of which **1,828 (88.8%) in the seed** and only
    230 (0.10% of the total) pure cascade; 91.5% of the excluded ones within 2
    adjacency rings of a defective face (ring 0/1/2 =
    803/651/429). checkMesh: 885 wrong-oriented faces vs 895 input,
    "Failed 4" (the fourth = aspect ratio), 597,802 cells.
    Accounting: `local_excluded_faces` counts only the iterative ones; the total
    without BL is 3,684 faces (**1.63%**) because the binary drops are re-evaluated
    at every round (a vertex can drop below 0.5 losing an incident face).
  - **Main finding — the order of the scales is the real cost:**
    the thick-first engine (1.0 → 0.1) accumulates ALL 19,625 iterative
    exclusions before reaching 0.1 (the warnings show failures only
    at 1.0/0.6/0.35/0.2; at 0.1 it validates with the carried set). The same 0.1
    thin-first needs 2,058 of them: **9.5x fewer**, 8.5% more prisms
    (445,716 vs 410,668) and marginally better checkMesh (885 vs 891).
    Recommendation for future work: invert the order of the scales
    (or reset the exclusions when descending a scale) with dedicated tests —
    NOT implemented here: no default changed.
  - **Global winding vs binary termination:** the winding solver is
    an exact solver (no conflict/non-manifold edge on any
    geometry; 0 unclosed cells everywhere). With winding alone the standard
    valve path remains invalid due to negative volumes (22-44 per
    scale, stage1c): the residual is geometric, not orientation. The
    binary termination is the only piece that scales with the difficulty:
    no-op on cylinder/cube, 0.5-0.8% discard on groove/slot, 0.9-1.6%
    on the valve (thin-first).
  - **Predictions vs measurements:** seed, thin-first and rings confirmed. One
    declared wrong prediction: for groove1 I had predicted "tens/
    hundreds" of vertices at low fade, measured 2 — the predictor ranks
    correctly but the easy/difficult threshold must be calibrated. No
    LT failure in the set; the only measured failure is the STANDARD
    path on slot1.
  - **Time:** ~linear in size with fixed overhead (cube 0.03 s;
    groove 1.4-3.2 s; cylinder 12.6-13.5 s; slot1 27.1 s; valve ~100
    s/attempt). The total = attempts × size; the thin-first
    valve costs ~305 s of build against the 1,602 s of thick-first.
- **PHASE 4 — does thin-first generalise beyond the valve? NO (2026-09-09)**.
  Question: does the thin-first advantage measured on the valve (9.5x fewer
  exclusions, +8.5% prisms, ~5x faster) also hold on
  cylinder/cube/groove1/slot1? Method: prediction written BEFORE
  (`notes/thinfirst_reasoning.md`), two runs per geometry with the same
  internal logic and only the scale tuple changed
  (`tools/bench_bl_thinfirst.py`; thick-first reproduces exactly the
  PHASE 3 LT results, so the patch is faithful).

  **Result: as a blind inversion it does NOT generalise — it introduces a
  serious side effect.** On ALL 4 geometries thin-first
  validates at the FIRST attempt (scale 0.1) and writes a BL with an
  effective `first_height` 10x smaller than requested (5e-4 instead of
  5e-3), silently:
  - cylinder: THICK scale 1.0 vs THIN scale 0.1; same prisms (74,808),
    0 exclusions, identical time (17.9 vs 17.0 s), BUT aspect max 18.3 →
    **104.3**;
  - cube duct: 108 prisms in both, aspect 78.7 → **781.6** (~10x);
  - groove1: 10,062 prisms in both, aspect 15.9 → **90.8**;
  - slot1: THICK 79,614 prisms with 132 iterative exclusions (216 total)
    at scale 1.0 in 2 rounds, 36.5 s; THIN 80,004 prisms with **0 iterative
    exclusions** (86 total = seed only) at scale 0.1 in 1 round, 17.9 s —
    here the valve advantage reappears (fewer exclusions, +390 prisms, 2x
    faster) but again with thickness reduced 10x (aspect 16.7 →
    **128.5**);
  - valve (PHASE 3): no thickness conflict possible — scale
    1.0 failed anyway, 0.1 was the only valid one; there thin-first stays
    better and does no harm.

  **Reading:** the two effects are intertwined. The advantage (fewer
  exclusions, more prisms, faster) exists only where a thick scale
  really fails, and arises from avoiding the accumulation of exclusions before
  reaching the scale that validates. But "accept the first scale that
  validates" means accepting 0.1 even where the requested 1.0
  would work: on a healthy mesh it degrades y+ 10x without failing.
  For this reason the inversion must NOT be proposed as default (nor implemented).
  The variant to measure (future work, explicitly not
  implemented) is **decoupled**: try the requested scale first and,
  when descending a scale, do NOT carry the accumulated exclusions (reset at
  scale change) — so the valve would get ~2,058 exclusions like
  thin-first but the healthy cases would keep the requested thickness.
  **Predictions vs measurements (honest):** I had predicted "thin-first
  identical to thick-first" on cylinder/cube/groove1 — WRONG in
  thickness/scale/aspect (the "exits at round 0" mechanics were right, but
  thin-first's round 0 IS scale 0.1). On slot1 I had predicted
  "equal or slightly worse in % exclusions" — wrong in sign
  (0 vs 132, therefore better), right in the mechanics; missed the thickness
  effect. The PHASE 3 predictor (pyr_input) remains valid: the advantage
  appears only where pyr_input > 0 and grows with the number of defects.
  Default unchanged (`local_termination=False`, scale order unchanged),
  no GUI wiring.
- **PHASE 5 — "decoupled" variant implemented and measured (2026-09-09)**.
  Following PHASE 3/4 the proposed variant was implemented:
  opt-in in the engine `run(local_termination="decoupled")` (`bl_poly.py`):
  same loop (max 3 rounds, scales 1.0 → 0.1) but the set of exclusions
  is **reset at every scale change** — the REQUESTED scale is tried first
  (1.0, full thickness) and only if it fails does it descend, without
  making the thin scales pay for the defects of the thick scales. `True`
  (legacy carry) and the default `False` remain unchanged; new tests
  `test_bl_local_termination_decoupled_mode_on_healthy_cube` and
  `test_bl_local_termination_rejects_unknown_mode` (fast suite 1153
  green). Prediction written before in `notes/decoupled_reasoning.md`.

  Measurements (`tools/bench_bl_thinfirst.py --modes thick,thin,decoupled
  [--valve]`, real checkMesh):
  - cylinder/cube/groove1/slot1: DECOUPLED **identical to THICK** in everything
    — scale 1.0 (full thickness), same prisms (74,808 / 108 / 10,062 /
    79,614), same exclusions (0/0/0/132), same aspect max (18.3 /
    78.7 / 15.9 / 16.7) and times. The reset never comes into play when
    success arrives within the requested scale: no degradation
    of the thickness of the pure thin-first.
  - valve: success at scale 0.1 with **2,058 exclusions** (predicted and
    obtained) and **445,716 prisms** — exactly the thin-first result
    (3,684 faces without BL = 1.63%). checkMesh: **885 wrong-oriented faces
    vs 895 input**, "Failed 4" (the fourth = aspect ratio), skew 47.6,
    non-ortho 55 — identical to thin-first.
  - **Valve time: 2,215 s (37 min)** — slower than the prediction
    (24-30 min) and slower than both thin (305 s) and thick (1,602 s):
    it still tries all the scales (15 builds, 3 rounds per scale). The
    advantage is in exclusions/prisms, not in time.
  - Valve warning trajectory: failures at 1.0 (50→6→6 negative
    volumes), 0.6, 0.35, 0.2, then fresh 0.1 at the third round
    (1,946+112) validates.

  **Verdict**: decoupled hits the target — it captures the thin-first
  advantage on the valve (9.5x fewer exclusions, +8.5% prisms,
  better checkMesh) WITHOUT degrading the thickness on healthy cases
  (they stay at full scale 1.0). It is the natural candidate for the
  semantics of local termination when it is enabled (PHASE 2 in
  user validation): "requested thickness if possible, only the defects
  pay". The time cost on defective geometries remains; a
  possible optimisation (skip intermediate scales or reuse the
  exclusions on identical failure signature) requires a dedicated
  measurement. Default unchanged, no GUI wiring.

  Valve summary table (same fixture input, n_layers=2,
  h1=1e-5, dual_convexity):

  | variant | iterative excl. | faces without BL | prisms | checkMesh | time |
  |---|---|---|---|---|---|
  | THICK (legacy) | 19,625 | 21,208 (9.36%) | 410,668 | 891 wrong | 1,602 s |
  | THIN (experim.) | 2,058 | 3,684 (1.63%) | 445,716 | 885 wrong | 305 s |
  | DECOUPLED | 2,058 | 3,684 (1.63%) | 445,716 | 885 wrong | 2,215 s |
- **PHASE 6 — early-exit-intermediates implemented and measured
  (2026-09-09)**. To answer the question "does an early-exit (stop at the
  first valid scale) reduce the time on the valve?": the engine
  ALREADY has an early-exit at the first valid scale (`if ok: return
  self._accept_built(...)`) — cylinder/cube/groove1 validate at
  scale 1.0 round 0 and do **1 single build** (12.8 s, 1.4 s, 0.0 s).
  The long valve time in decoupled (2,215 s, 15 builds) is not
  a "does not exit" bug: it is that only 0.1 validates and the 4 thick scales
  are all "equally useless" (pyr count 1010-1019 vs input 1007).
  To save those 9 builds the opt-in variant
  `early_exit_intermediates` was implemented: if 1.0 round 0 fails, the
  intermediate scales (0.6, 0.35, 0.2) are skipped and it goes straight to 0.1.
  Implementation in `bl_poly.py` (engine parameter, default off;
  trigger inside `_run_local_termination` after the failure of 1.0
  round 0; statistics in `res.stats["local_termination_early_exit_intermediates"]`).
  Test: `test_bl_local_termination_early_exit_intermediates_noop_on_healthy`
  (fast suite 1154 green). Prediction written before in
  `notes/early_exit_reasoning.md`.

  Measurements (`tools/bench_bl_thinfirst.py --modes early_exit [--valve]`,
  real checkMesh):

  | Geometry | DECOUPLED | EARLY_EXIT | Δ |
  |---|---|---|---|
  | cylinder | scale 1.0, 74,808, 0 excl, 17.8 s, aspect 18.3 | scale 1.0, 74,808, 0 excl, 12.8 s, aspect 18.3 | identical (no-op) |
  | cube | scale 1.0, 108, 0 excl, 0.0 s, aspect 78.7 | scale 1.0, 108, 0 excl, 0.0 s, aspect 78.7 | identical |
  | groove1 | scale 1.0, 10,062, 0 excl, 2.1 s, aspect 15.9 | scale 1.0, 10,062, 0 excl, 1.4 s, aspect 15.9 | identical |
  | **slot1** | scale 1.0, 79,614, 132 excl, 36.3 s, aspect 16.7 | **scale 0.1, 80,004, 0 excl, 25.2 s, aspect 128.5** | **degenerates into thin-first** |
  | **valve** | scale 0.1, 445,716, 2,058 excl, 2,215 s, checkMesh 885 | **scale 0.1, 445,716, 2,058 excl, 414 s, checkMesh 885** | **identical, 5.4x faster** |

  **Verdict**: on the valve it hits the target: same result as
  decoupled (2,058 excl, 445,716 prisms, checkMesh 885) in **414 s
  instead of 2,215 s (5.4x)** with only 4 builds (1.0 round 0 fail +
  skip + 0.1 round 0, 1, 2) instead of 15. Valve warning trajectory:
  1.0 round 0 fail (50 negative volumes) → skip 0.6/0.35/0.2 → 0.1
  round 0 fail (2 neg) → 0.1 round 1 fail (1012 pyramids > 1007
  input) → 0.1 round 2 validates with 2,058 excl.

  **Documented risk**: the heuristic is safe on the valve but
  **degenerates into thin-first on slot1** (scale 0.1, aspect 128.5
  instead of 1.0/16.7): the trigger "1.0 round 0 fails → skip
  intermediates" also fires when 1.0 would have validated after 1 round
  of exclusions (slot1: round 0 fails with 50 pyr > 42 input, but
  round 1 with 132 excl validates). The skip loses that round 1 and
  forces 0.1 with a clean set (= thin-first). So
  `early_exit_intermediates` is a candidate *only* for valve-type
  geometries (where 1.0 has no hope of validating at any round); for
  slot1-type geometries it is too aggressive. A finer heuristic
  ("skip only if round 0 and round 1 of 1.0 both fail") or
  ("skip only if pyr_round0 of 1.0 does not improve while descending") requires
  a dedicated measurement. Default unchanged, no GUI wiring.

  Valve summary table (same fixture input, n_layers=2,
  h1=1e-5, dual_convexity):

  | variant | iterative excl. | faces without BL | prisms | checkMesh | time |
  |---|---|---|---|---|---|
  | THICK (legacy) | 19,625 | 21,208 (9.36%) | 410,668 | 891 wrong | 1,602 s |
  | THIN (experim.) | 2,058 | 3,684 (1.63%) | 445,716 | 885 wrong | 305 s |
  | DECOUPLED | 2,058 | 3,684 (1.63%) | 445,716 | 885 wrong | 2,215 s |

  **CAVEAT (PHASE 7, 2026-09-09)**: the PHASE 6 valve numbers are
  **pinned to the stale fixture** `valve_dual.npz` (generated 2026-08-01 with
  a converter different from the current one) and the trigger of
  `early_exit_intermediates` was **fixed in PHASE 7** (it also skipped
  rounds 1-2 of scale 1.0, not only the intermediates). On an input
  reconverted with the current converter the story changes: see §PHASE 7.
- **PHASE 7 — BL on the PRODUCTION topology (collapsed) + stale
  fixture (2026-09-09)**. Origin: `openfoam_runner.py:1652` converts the
  production poly with `collapse_smooth_edges=True,
  boundary_feature_angle=40.0, collapse_volume_tolerance=0.10`, while
  ALL the PHASE 2-6 BL measurements (fixture and benches) use the converter with the
  defaults (**collapse OFF**, three quads per triangle). The collapsed dual has
  3-6x fewer boundary faces and `bl_poly` extrudes one prism per face:
  measured with `tools/bench_bl_production_topology.py` (new).

  | | exact (default) | production (collapsed) |
  |---|---|---|
  | cylinder, boundary faces | 24,936 (quad) | **4,412** (4-8-gons, 5.65x fewer) |
  | cylinder, volume drift | 1.4e-16 | 0.128% |
  | cylinder, BL 3 layers | scale 1.0, 0 excl, **74,808** prisms, 13.0 s, Mesh OK | scale 1.0, 0 excl, **13,236** prisms, 5.6 s, Mesh OK |
  | valve, boundary faces | 226,542 (quad) | **42,130** (4-9-gons, 5.4x fewer) |
  | valve, input defects (checkMesh wrong) | 863 | **340** |
  | valve, volume drift | 3.4e-16 | 1.08% |
  | valve, BL | **success at scale 1.0** (2 builds): 2,525 excl, 444,924 prisms, 217 s; checkMesh 836 wrong (< 863 input), skew 38.1 (vs 14.6 input), aspect 1,543, Failed 4 | **FAILS**: `decoupled` 15 builds in 892.7 s, 2,189 excl, stall 375-444 pyramids vs 370 input; mesh unchanged |

  - **The valve fixture is STALE**: `tests/fixtures/valve_dual.npz`
    (2026-08-01) records 895 wrong / 55 non-ortho errors / skew 44.3;
    a fresh conversion of the SAME tet backup today gives 863 wrong / 9
    errors / skew 14.6. All the PHASE 2-6 valve numbers are therefore
    representative only of that snapshot: on the current conversion the
    same pipeline behaves differently (example: the success at 0.1
    with 2,058 excl is not reproduced; the new input validates at **scale 1.0
    round 1** with 2,525 excl and full thickness).
  - **PHASE 6 early-exit bug found and fixed**: the trigger
    fired at 1.0 round 0 and also skipped rounds 1-2 of the requested
    scale (not only the intermediates). Masked by the stale fixture
    (where 1.0 was hopeless anyway) and the real cause of the
    "degeneration" on slot1. Now the trigger fires only when scale
    1.0 has exhausted its rounds: on cylinder/cube/groove1 it stays a no-op;
    on the re-measured exact valve it gives success at 1.0 round 1 in 217 s
    (identical to legacy). Fast tests 12/12 green.
  - **Prediction vs measurement**: I had predicted (70%) that the BL would also
    close on the production topology; **measurement: it fails** (30%
    predicted). On healthy cases the prediction was correct (success, 5.65x
    fewer prisms, Mesh OK).
  - **Implications (no default changed)**: (1) the fixture must be
    regenerated and the pinned numbers updated before any other
    valve measurement; (2) the production BL path on concave geometry
    **is not supported today** (the collapsed one is excellent for cell count and
    on healthy cases, but the exclusion→polygon mapping does not close the
    valve): to be tackled as a dedicated lane (e.g. per-vertex exclusion
    or mixed granularity), not in this session; (3) the existing BL benches
    still use the exact dual: stating it in this document avoids
    confusing the two worlds.
- **PHASE 8 — fixture regenerated, fast tests re-pinned, collapsed BL
  measurement (widen=0/1) (2026-09-09)**. Three consecutive outcomes on
  this lane; honest conclusions, **no default changed**.

  1. **Valve fixture `tests/fixtures/valve_dual.npz` regenerated** with
     the current converter (2026-09-09, size 27.3 MB, identical
     to the previous one; the `valve_dual_checkmesh.txt` file recorded
     next to it): checkMesh reports **863 wrong-oriented faces, 9 non-ortho
     errors, skew 14.58, Failed 3**, against the old pinned
     (895/55/44.3) — an improvement of the converter on the same tet
     backup. The in-process detector `_detect_defects` reports
     **863 / 203 / 3** (pyr / non-ortho-det / skew-det): 863 pyr matches
     checkMesh; 203 = checkMesh "nonOrthoFaces" written on the set (the
     863 → 9 "errors" >70° of checkMesh); 3 is the skew-det
     (checkMesh writes 98 "highly skew"). The `tests/test_tet_poly_dual`
     parametrize was updated to
     `{"pyramid": 863, "non_ortho_det": 203, "skew_det": 3}`.
  2. **`poly_fixture_builder.py` fixed**: the initial fvSchemes did not
     contain `divSchemes`/`laplacianSchemes`/`interpolationSchemes`/
     `snGradSchemes` and `checkMesh` 2512 failed with `FATAL IO ERROR:
     Entry 'divSchemes' not found`. Added the complete set. All the
     subsequent builder runs produce a valid checkMesh.
  3. **Slow test `test_valve_fixture_bl_invariants`**: passes on the new
     fixture (373 s, clean-failure: negative volumes per scale 1.0→0.1
     with 104/64/48/40/16 counts; mesh unchanged, polyMesh exists). The
     "must FAIL CLEANLY" docstring remains correct; an accidental
     FileNotFoundError in a previous run was due to a **double
     concurrent execution** of the test (the fixer re-launched the
     command, the second instance deleted `valve_bl` with `rmtree`
     while the first was running) — a concurrency artifact, not an engine
     bug. To reduce the race window, a simple
     solution would be to make the `case` unique per run (e.g. via a
     timestamp in the name) — not implemented here.
  4. **BL collapsed widening experiment (PHASE 8)**: opt-in engine
     parameter `local_exclude_widen: int = 0` (default unchanged) in
     `bl_poly.run()` (validated 0..3), with method `_widen_exclusions`
     that expands the exclusion list collected in every round by N
     rings of boundary neighbours (built from
     `pre["bnd_edge_faces"]`). Test:
     `test_bl_local_exclude_widen_param` (healthy cube succeeds with
     `widen=2`; `widen=4` error). Bench:
     `tools/bench_bl_production_topology.py --exclude-widen N`.
  5. **Collapsed valve measurement with `local_exclude_widen=1`** (production
     + decoupled + `max_rounds=6`): 2,064 s, 25 builds, **2,207
     exclusions** (identical to widen=0), stall 373-378 (0.2) / 379
     (0.1 r3-r5), clean failure. **H6 widen=1 falsified**:
     widening by 1 ring does not add faces to the excludable set (the
     rim frontier is already 1-ring in many positions, or the
     pyr-violation at the rim is not simply "shifted by 1 ring").
     `widen=2` not measured (cost ~30 min, expected negative).
  6. **H2 confirmed, H3 + H6 widen=1 falsified**: the collapsed
     BL defect on the valve cannot be closed with exclusion
     granularity (face + 1 ring). The structural gap (H4, exclusion
     mapping → per-vertex) remains the road to follow to
     support the production BL on concave geometries. Everything
     remains opt-in: no default was changed.
- **PHASE 9 / H4 — per-VERTEX exclusions: the production collapsed
  valve CLOSES (2026-09-09)**. New opt-in mode
  `local_termination="decoupled_vertex"` (default unchanged):
  identical to `"decoupled"` but the unit of iterative exclusion is the
  wall VERTEX — when a prism/core cell is invalid, ALL the vertices of
  its base face are excluded; a face is
  extruded only if none of its vertices is excluded. On the collapsed
  dual (a boundary polygon = the star of a vertex) a
  defective vertex propagates to all the incident faces in ONE round.
  The initial binary drop (`zero_concave`) remains per-face,
  unchanged; `local_exclude_widen` is a no-op in this mode.
  Implementation in `core/bl_poly.py`
  (`_run_local_termination(..., vertex_exclusions=True)`); unit test
  `test_bl_local_termination_decoupled_vertex_on_healthy_cube`
  (fast suite 15/15 in the file); slow regression
  `tests/test_bl_collapsed_valve.py` (skips if the tet backup of
  C:/polybench/valve1 is missing).

  Measurement (production converter kwargs, `n_layers=2`, h1=1e-5,
  dual_convexity, max_rounds=3; collapsed conversion ~105 s):
  - **success=True at scale 0.6**, 5 builds, ~297 s;
  - **4,008 faces excluded out of 42,130 (9.5%)**, **75,360 prisms**;
  - trajectory: 1.0 r0 fail (54 negative volumes) → r1 383 vs 370 →
    r2 373 vs 370 (vertex-closure fixed point: the 1.0 gate is NOT
    reachable); 0.6 r0 fail (38 neg) → r1 validates;
    `max_rounds=6` gives the same result (1.0 stops anyway);
  - real checkMesh: **340 wrong-oriented faces = exactly
    the input (ZERO added)**, 10 non-ortho errors (= input), NOmax
    95.6 (= input), skew 27.5 (input 22.2), aspect 6,555 (input
    100.8), "Failed 4" vs input "Failed 3" — the only extra check
    is the aspect ratio, intrinsic to thin BL prisms.
  Comparison with the per-face model on the same input: PHASE 7/8
  failed at all scales (stall 373-385 vs 370, 2,189-2,207
  exclusions). The previous prediction (45% closure, 2,300-2,900
  exclusions at 0.1) was **pessimistic on success and conservative on the
  count**: it closed with more faces (4,008) but at a thicker
  scale (0.6). No default changed: the mode is opt-in and
  documented.
  - **Interaction NOT to combine**: `early_exit_intermediates`
    would skip exactly the 0.6 scale that this mode uses to
    validate (1.0 exhausts without closing → the flag goes straight to
    0.1). The combination has not been measured and is not
    recommended; the flag remains intended for the cases where 1.0 is
    hopeless and the intermediates useless (exact valve, fixture).
  - **No-op on healthy cases, verified**: exact cylinder with
    `decoupled_vertex` = identical to `decoupled` (scale 1.0, 0
    exclusions, 74,808 prisms, `Mesh OK`, 13.6 s); healthy cube in the
    unit test. On a mesh without defects the vertex-closure collects
    nothing by construction (break at the first round).
  - **Generalisation to cube/groove1/slot1 (2026-09-11,
    `tools/h4_generalization_rebench.py`)**: prediction written
    before in `notes/h4_generalization_reasoning.md`, measurement with
    real checkMesh via WSL, direct comparison `decoupled` vs
    `decoupled_vertex` on the same input:
    - **cube_duct** (9 cells, 0 input defects): identical in
      everything — 0 exclusions, 108 prisms, scale 1.0, `Mesh OK`.
      Prediction (no-op) **confirmed**.
    - **groove1** (682 cells, 3,372 faces): identical — 0
      exclusions, 10,062 prisms, scale 1.0, `Mesh OK`. Prediction
      (equivalent to `decoupled`) **confirmed**.
    - **slot1** (9,372 cells, 26,754 faces, 22 pyramid defects in
      input): both close (scale 1.0, `Mesh OK`, 0 defects
      added), but **not identical**: `decoupled` excludes 132 faces
      → 79,614 prisms; `decoupled_vertex` excludes **378** faces
      (~3x) → **78,903** prisms (-711). The prediction (0 additional
      exclusions, result numerically identical to `decoupled`) is
      **refuted**: the per-vertex cascade on slot1 propagates to more
      faces than the face-mode while converging to the same
      qualitative outcome (closes, no defect added). No default
      changed; `decoupled_vertex` remains opt-in.
  - **Diagnosis of the ~3x on slot1 (2026-09-11,
    `tools/slot1_exclusion_inspect.py`)**: the initial hypothesis
    "higher per-vertex valence on slot1" (more incident faces
    per vertex → wider per-vertex propagation) is
    **refuted**. Measurement of the valence of the wall vertices on the
    converted dual (number of incident wall faces per vertex)
    on each of the three meshes:

    | geometry | n wall verts | mean | max | p50 | p90 | p99 |
    |---|---|---|---|---|---|---|
    | cube_duct | 38 | 3.79 | 5 | 12.0 | 28.0 | 31.6 |
    | groove1 | 3374 | 4.00 | 9 | 248.0 | 1854.6 | 2208.7 |
    | slot1 | 26756 | 4.00 | 9 | 261.0 | 14271.2 | 17483.1 |

    The mean valence (4.0 vs 4.0 vs 3.79) and maximum (9 vs 9 vs 5)
    are similar between slot1 and groove1 (the cube-duct difference is
    irrelevant for the comparison: it is the "no-op" case). So the
    cause of the ~3x exclusions on slot1 is NOT the local valence but
    the **absolute scale**: slot1 has ~7.9x more wall vertices
    than groove1 (26756 vs 3374), and the vertex-mode cascade propagates
    the exclusion to all the vertices incident to the defective faces
    (1-ring closure). More vertices → more surface in the
    closure → more faces excluded, even with similar valence. The
    observed factor (~3x) is about 38% of the vertex ratio
    (7.9x) because the closure captures only the vertices incident to the
    defects (22 input → ~22 × valence ≈ ~100 vertices captured),
    not all 26756. The 2.86x ratio between the excluded sets (378 vs
    132) is the local projection of the per-vertex propagation
    onto the defect sub-region, not an effect of the
    vertex-mode model on the rest of the mesh. Conclusion: the vertex
    model does its job (closes the mesh, Mesh OK), but on input with
    geometries of high boundary-vertex density (narrow channels,
    cracks), the amplification is proportional to the number of
    vertices incident to the closure — not a bug of the
    vertex criterion, a property of the local topology. No default
    changed.
  - **GUI wiring as an opt-in option (2026-09-11)**: `decoupled_vertex`
    is now reachable by the user — checkbox "Advanced closure for
    concave geometries (experimental)" in the Boundary Layers panel
    (`gui/params_panel.py`, `get_bl_params()["concaveClosure"]`), off
    by default. When checked, `core/openfoam_runner.py`
    (`DualPolyWorker`) passes `local_termination="decoupled_vertex"` to the
    engine instead of leaving it unchanged (`False`). No other
    parameter is exposed (the `concavity_criterion` stays at the default
    `"angle_fade"`, not the `"dual_convexity"` used in the PHASE 9 benchmark).
    **Real end-to-end validation** (not mock — `DualPolyWorker.run()`
    called directly on the production valve, same code path
    as the GUI, checkMesh via WSL): 75,200 prisms (2 layers),
    340 wrong-oriented faces = **exactly the pre-BL baseline** (zero
    added), 10 non-ortho errors (= baseline), NOmax 95.6 (= baseline),
    "Failed 4 mesh checks" (= expected, the concave defect is a known
    limit of the conversion, not introduced by the BL). That is: **even with the
    default criterion `angle_fade`** (not the one used in the
    original benchmark) the vertex-cascade still closes the mesh without
    adding defects — the per-round validation cycle is based on
    direct checkMesh checks (volumes/pyramids), not only on the normal
    fade, so the initial criterion matters less than expected.
    Test coverage: `tests/test_bl_gui_wiring.py` (checkbox
    off-by-default, propagation to `get_bl_params()`),
    `tests/test_dual_poly_options.py` (end-to-end propagation, mocked,
    up to `local_termination` in the engine). No default changed.

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
- **Verified (2026-09-09):** the main GUI meshing path does not go through
  `MeshEngine.run` — it uses `RetryRunner`/`cartesianMesh` directly. A grep
  in `src/polyfoammesh/gui/` for `MeshEngine().run(`, `QuickMesh().run(`
  and `engine.run(` returns zero matches: `_on_quick_mesh` calls only
  `engine.auto_select(...)` (algorithm choice, no escalation) and
  `qm._auto_bl_params(...)` (BL parameter helper, no meshing); the actual
  meshing goes through `self._params.run_meshing` → `self._on_run_meshing`
  → `RetryRunner` (`self._runner = RetryRunner(self._of_config)`). The GUI
  therefore never produces an `algorithm_substituted` from `MeshEngine`,
  so there is no MeshEngine-driven substitution to surface in the GUI log.
  The substitution is recorded in the artefacts that *do* flow through
  `MeshEngine` (quick mesh one-click, full-auto one-click, A/B
  verification — all headless). The 5 substitution/fallback paths the
  GUI *can* trigger (BL off, detail reduced, Gmsh algorithm coarser,
  parallel→serial, BL disabled) are already wired through
  `_log_substitution` and covered by `tests/test_gui_substitution_notice.py`
  (4 tests, all green). If a future change makes the GUI call
  `MeshEngine.run` (so that an `algorithm_substituted` becomes possible
  in the GUI), a wiring + test mirroring the existing pattern is the
  minimal addition.
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
  load. `commercial/mosaic.py` likewise only runs whole-mesh `polyDualMesh
  90` — its "Mosaic" name is historical and its docstrings now say so.
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
- **Sizing estimate rebench (2026-09-09)**: measured the predicted
  vs actual cell count for a cylinder GMSH build at the production
  pipeline (medium fineness, `tools/sizing_estimate_rebench.py`).
  Actual = 52,459 cells. Predicted by
  `estimate_cell_count_geometric(meshes, volume, core_cell,
  patch_sizes)` across a sweep of `max_cell_size` (the slider):
  - max_cell = 0.10: predicted 7,251 (ratio 0.14x)
  - max_cell = 0.05: predicted 58,011 (ratio **1.11x**) ← near budget
  - max_cell = 0.02: predicted 906,431 (ratio 17.28x)
  - max_cell = 0.01: predicted 7,251,455 (ratio 138.23x)
  The "blind" formula (`estimate_cell_count`) gives 58,011 (ratio
  1.11x). The geometric formula is accurate near the production
  target (medium, max_cell=0.05) but diverges massively at finer
  slider values — the slider value is indeed a budget, not a
  guarantee, and the formula's two-zone model (bulk + near-wall
  shell) underestimates the inflation of the boundary layer on
  small core cells. The "omitted on non-watertight" path is enforced
  upstream of the formula (compute_volume in main_window); the
  formula's own fallbacks (volume=0 → floor (100,100,100);
  patch_sizes=None → blind estimate, NOT omitted) are documented.
  No default changed; this is a measurement-only entry.
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

- The frozen EXE is rebuilt on demand (`pyinstaller --clean --noconfirm
  PolyFoamMesh.spec`). The `dist/` artifact is not refreshed automatically
  and may lag the source tree.
- The API server module (`polyfoammesh.api.server`) imports cleanly with
  the installed FastAPI; it remains a CI/CD surface, not part of the GUI.

## Performance — poly conversion / smoothing speed (2026-09-13)

- **Real production run observed the problem before any synthetic
  benchmark**: a GUI session on a real CAD part (`Parte4.stp`, 10.3M tet
  cells after GMSH) logged the barycentric-dual conversion + quality
  smoothing taking **~34 minutes total, of which ~27.5 minutes (80%) was
  the smoothing pass alone**, for a marginal defect improvement (738 vs
  750 total, ~1.6%).
- **Root cause found by profiling (`cProfile`) the real valve case, not
  guessing**: `poly_smoother._laplacian_relax` (one Laplacian relaxation
  step, called once per smoothing candidate) had an explicit
  `for v in range(len(points))` Python loop over every vertex — on the
  152K-point valve this function alone was **~50% of total conversion
  time** (81.7s of 165.5s); on the 11M-point production mesh from the
  real session, the same per-vertex Python loop explains the observed
  185–206s per smoothing step.
- **Fix**: rewrote `_laplacian_relax` as one vectorised scatter-add
  (`np.add.at` over a flattened (vertex, neighbour) edge list built once
  per smoothing run via the new `_flatten_adjacency`, not per step) —
  same weighted/unweighted Laplacian formula, verified against an
  independent reference loop written fresh in
  `tests/test_laplacian_relax_matches_independent_reference_loop`
  (`rtol=1e-12`). No behaviour change: the valve conversion still
  produces the identical 526 residual defects, at less than half the
  total conversion time.
- **Measured (valve1, same `cProfile` run before/after, `total` stage
  time)**: **165.5s → 100.3s (-39%)**. `_laplacian_relax` no longer
  appears in the top-30 profiled functions at all. The remaining
  smoothing cost is now dominated by the per-candidate quality
  re-evaluation (`_face_geometry`/`_detect_defects`/`_face_extent`,
  called once per accepted/rejected smoothing step over the *whole*
  mesh) — a legitimate further optimisation (incremental/local
  re-evaluation instead of whole-mesh), not yet attempted.
- Not yet re-measured on the original 11M-point production case (too
  slow to iterate on for every change); the valve number is the honest
  basis for the -39% claim. Expect a larger relative win on bigger
  meshes since the fixed cost scaled with vertex count, not mesh
  complexity.

### Boundary layer "silent stall" — real bug found and fixed (not just slow)

- **Root cause of a real production incident**: a GUI session on the
  10.3M-tet CAD part (`Parte4.stp`) produced zero log output for 22+
  minutes after the poly conversion finished, until the app's own
  liveness watchdog fired ("Task 'polydual' stalled (no liveness for
  1362s)") and force-cancelled the task as a zombie — the user then had
  to force-quit the app. This looked like (and functioned as) a hang,
  even though it may not have been an infinite loop.
- **Cause, found by reading (not guessing)**: `PolyBoundaryLayerEngine.
  _build_precompute` runs several genuinely expensive, pure-Python,
  per-wall-vertex loops (normals, feature-angle fade, interior-support
  distance, boundary edge map) before the BL engine's own progress
  logging ("building prism side faces X%...") ever starts. Its own
  `_gil_yield` helper — whose docstring already admits *"the minutes the
  BL step takes"* — only does `time.sleep(0)`, with **no log output at
  all** during this phase. On a mesh with hundreds of thousands of wall
  vertices this phase can legitimately run for many minutes with zero
  visible sign of life, indistinguishable from a genuine deadlock to
  both the user and the app's own watchdog.
- **Fix (this pass)**: added periodic (~20 steps) progress logging to
  all four precompute loops in `_build_precompute` (`bl_poly.py`),
  matching the existing "[bl] ... X%..." style used elsewhere in this
  file. Purely additive — no computed value changed; verified via a
  real end-to-end run on the valve (`DualPolyWorker.run()`, GUI code
  path, `concaveClosure=True`) producing the byte-identical result
  measured before (75,200 prism cells, 340 wrong-oriented faces,
  `Failed 4 mesh checks`) and the fast suite unchanged (1161 passed).
  This does not make the phase faster — it makes a long-but-alive phase
  distinguishable from a hang, so the watchdog stops false-firing and
  the user sees real progress instead of an apparently frozen app.
- **Bigger finding while profiling this (real cProfile run, valve
  case, `PolyBoundaryLayerEngine.run()` with
  `local_termination="decoupled_vertex"`)**: `_build_precompute` itself
  is *not* the single largest cost — `_solve_global_windings` (the
  exact BFS-based face-orientation solver, `bl_poly.py`) is, at 153.9s
  tottime / 195.5s cumtime over 5 build attempts (~31–39s each),
  slightly ahead of `_build_precompute`'s 35.5s/106.4s. It is
  pure-Python (`defaultdict(list)` graph construction + BFS), which
  explains the cost, but it is also the function whose docstring
  records a documented history of catastrophic correctness bugs in a
  previous (greedy, non-exact) implementation — see the "previous
  repair was a per-cell greedy flip" note in its own docstring. **Not
  touched in this pass**: optimising it safely needs the same
  discipline as its original validation (checkMesh on multiple real
  geometries, not just the fast unit suite) and more time than this
  session had; flagged here as the next, larger, real lever rather than
  attempted under time pressure.

- **`_solve_global_windings` graph construction vectorized (2026-09-13)**,
  addressing the lever flagged above — but WITHOUT touching the
  correctness-critical part. The function has two phases: (1) build the
  face-adjacency parity graph from the ragged face list (pure data
  restructuring: which two face-sides share which edge, with what
  parity), and (2) a BFS over that graph to solve for a consistent
  winding per connected component (the part with the documented history
  of catastrophic bugs in a prior *different, greedy* algorithm). Only
  phase 1 was rewritten, as `_build_face_parity_graph` — from two nested
  Python loops (one dict-append per face-edge-side) to a numpy
  flatten + lexsort + group-by-run construction. Phase 2 (the BFS,
  `deque`-based, one component at a time) is byte-for-byte unchanged.

  Correctness: verified the new function produces an **exactly
  identical** graph (same face pairs, same parities, same non-manifold
  edge count) to an independently-written reference loop, on (a) a
  hand-built synthetic mesh with mixed face degrees (tri/quad/pentagon)
  and a deliberate non-manifold edge, and (b) the real recorded valve
  dual fixture (1,240,048 faces, 6,193,947 face-vertex entries) — not
  just spot-checked, the full graph compared key-for-key. See
  `notes/global_windings_speed_reasoning.md` and the two new tests in
  `tests/test_bl_poly.py` (`test_face_parity_graph_matches_reference_on_synthetic_ragged_mesh`,
  `test_global_windings_vectorized_graph_matches_reference_on_valve_dual`).

  Speed: measured on the valve (`local_termination="decoupled_vertex"`,
  5 build attempts, real cProfile run) — `_solve_global_windings`
  cumtime **195.5s → 181.3s (-7.3%)**. Honest disclosure: the BEFORE
  number is from the original profiling session (documented above); an
  attempt to re-measure BEFORE on the same run in this session was
  aborted because the host was unusually loaded (a live rerun of the
  unmodified code made too little CPU progress over 45+ minutes to be
  practical) — the comparison is same-methodology, same-fixture,
  cross-session rather than same-session back-to-back. The gain is real
  but modest: most of the function's cost lives in the BFS itself
  (untouched, and not vectorizable — it's an inherently sequential
  graph traversal with early conflict detection) and in downstream
  per-cell volume/closure checks, not in the graph construction that was
  rewritten. Fast suite green (1163 passed, 2 skipped, 1 xfailed), ruff
  clean. Not a big win, but a safe, fully-verified one with zero
  behavioural change to the correctness-critical solve.

- **`_build_precompute` normals loop — vectorized, then REVERTED (negative
  result, 2026-09-13)**. Same session, next candidate lever: the
  per-vertex-normals loop called `_newell` once per boundary face
  (~40k calls per build on the valve) in a Python `for` loop. Batched all
  boundary faces into one vectorized Newell + scatter-add pass
  (`_batched_wall_normals`), sidestepping `_newell`'s own documented
  per-call overhead (its docstring already says numpy is slower than pure
  Python for a single small polygon — this was a different thing, doing
  all faces in ONE numpy pass rather than optimizing one call). Verified
  exactly equivalent to an independent per-face reference loop, on a
  synthetic mixed-degree mesh and the real valve fixture's boundary faces
  (`rtol=1e-9`). Fast suite green (1165 passed).

  Measured on the valve, same profiling script: total run time **434.6s,
  UP from 418.5s (+3.9%)**; `_build_precompute` cumtime **116.5s, up from
  111.5s (+4.5%)**. The eliminated loop was not actually the dominant
  cost — most `_newell` calls (and cost) come from the SEPARATE
  angle-fade loop (per wall vertex, over its incident faces), which this
  change did not touch, so removing ~40k of ~600k `_newell` calls bought
  little, and the new flatten/lexsort-free bookkeeping (`np.fromiter`,
  `np.add.at`) added enough overhead to be a net wash or slight
  regression within measurement noise on this run.  **Reverted** — kept
  the code at its previous (already-committed) state rather than carry
  correctness-verified-but-unhelpful complexity. This is exactly the
  "measure, don't assume" discipline this project runs on: a predicted
  win that didn't pan out, written down honestly instead of merged
  anyway. Any future attempt at this function should target the
  angle-fade loop's `_newell` calls instead, not the boundary-normals
  loop.
- **Mesh I/O (`foam_mesh_io.read_faces`/`read_points`) investigated,
  no worthwhile win found** — an honest negative result, not skipped.
  Both readers have comments admitting multi-minute cost on 12M-point
  meshes, and the converter's own output (verified on a real 1M-face
  `constant/polyMesh/faces` file) is written in the slow ASCII
  `faceList` path, not the vectorisable binary/compact one (`gmshToFoam`
  is configured to write binary via `case_setup.py`, but our own
  `write_faces`/`write_polymesh` always write ASCII, so anything that
  re-reads our own converter's output — the BL engine, the viewer —
  hits this path). Tried replacing the per-line Python parse with one
  bulk regex pass over the whole data block
  (`re.finditer(r"\(([^()]*)\)", block)`): verified byte-for-byte
  identical output against the original parser on that same real file,
  but only **1.782s → 1.657s (-7%)** on 1,056,636 faces — the actual
  cost is the per-face `int()`/`split()` conversion, which a regex
  front-end doesn't remove. Reverted rather than keep a small win that
  also drops the periodic `time.sleep(0)` GIL-yield (the same class of
  "silent-freeze" risk just fixed in the BL precompute above) for a
  gain too small to justify the added complexity and re-introduced
  risk. A real win here would need a fundamentally different
  representation (e.g. switching the project's own writer to the
  compact/binary format end-to-end) — a bigger, cross-cutting change
  than a mesh-I/O micro-optimisation, out of scope for this pass.
- **Cartesian cfMesh and FEM Tetra (GMSH direct) paths checked for
  Python-side overhead, none found worth chasing**: both meshers are
  thin Python orchestration around an external binary
  (`cartesianMesh`/`gmsh` via WSL2 or subprocess) — the actual meshing
  time is spent inside that external process, which this codebase does
  not control. The shared preprocessing step both paths go through
  first, geometry tessellation (`geometry.py::tessellate_patches`), has
  no explicit per-vertex Python loop (unlike `bl_poly.py`/
  `poly_smoother.py` before this pass) — it already delegates to
  OCC/cadquery's own tessellation. No further mesher-side speedup
  identified beyond the poly-conversion/BL work above.

## Repository Hygiene (Lane F, 2026-09-11)

- **`tests/fixtures/valve_dual.npz` (27 MB, tracked) stays committed —
  regeneration measured, not assumed.** `tools/poly_fixture_builder.py`
  rebuilds it from the real valve tet backup (824,661 tetrahedra,
  1,687,079 faces): the barycentric dual conversion alone (round 0,
  before any smoothing or checkMesh) took >120 s in a real run started
  for this check (dual-cell pass at 9.2 s, quality pass at 3.4 s, then
  multiple smoothing iterations still running when stopped). Generating
  this fixture on every test invocation would make the "fast suite"
  not fast. Keeping the pre-built npz committed is the correct call;
  no change made.
- **No other tracked file in the 5-50 MB range**: `find tests/ tools/
  -size +5M -size -50M -type f` returns only `valve_dual.npz` and
  `tools/innosetup/innosetup-6.7.3.exe` — the latter is **untracked**
  (`git ls-files` confirms; removed from tracking and gitignored in
  commit `2c87293`, "large binaries removed from tracking"), so it does
  not affect clone size. Nothing to change.
- **No orphaned/broken scripts found in `tools/`** (34 files). A quick
  `ast.parse` sweep flagged `tools/poly_fixture_builder.py` for a
  leading UTF-8 BOM (`U+FEFF`) — a false positive of the probe method
  (`ast.parse` on text decoded as plain `utf-8` doesn't strip the BOM
  the way Python's own `utf-8-sig`-aware source loader does); running
  the script directly (`python tools/poly_fixture_builder.py
  --regression-fixture`) works and was confirmed doing real work in
  this check. The convention already documented in
  `.pre-commit-config.yaml` ("tools/ holds tracked scratch") means
  one-off benchmark scripts from finished investigations are kept
  intentionally, not deleted once their result lands here. No script
  was found to be genuinely broken or duplicated; no cleanup performed.

## Non-star-shaped ("face pyramid") cell repair via merge (2026-09-13)

Separate investigation from the speed work above, triggered by a
literature check on the H4/decoupled_vertex boundary-layer closure
(PHASE 9): none of the individual techniques used there turned out to be
novel (barycentric dual-mesh conversion, per-vertex local BL
termination, and BFS-based consistent face orientation are all
established techniques — see the literature citations in the session
transcript), but the specific combination for a no-WSL Python pipeline
wasn't found published anywhere searched.

**Root cause measured**: on the production-collapsed valve dual
(152,086 cells, 42,130 wall faces), 348 cells (0.23%) fail checkMesh's
"face pyramids" test (a cell must be star-shaped w.r.t. the exact
centroid OpenFOAM's own `primitiveMeshTools::makeCellCentresAndVols`
computes — not a formula we can change, it IS checkMesh's ground
truth). These 348 cells are the root cause of the H4 boundary layer's
own exclusion cascade (the BL validation gate is "don't add MORE
pyramid violations than the input already had" — `bl_poly.py` line
~1922 — so fewer input violations tightens that budget, an important
and initially counter-intuitive mechanism: reducing violations doesn't
automatically reduce BL exclusions one-for-one).

**Rejected approach**: Lee (2015)'s literature method for concave dual
cells ("cut-along-concave-edge") assumes the concavity is a dihedral
fold between TWO faces meeting at a known model edge. Measured: 345 of
358 concave cells here (96%) fail on exactly ONE face with no natural
"concave edge" partner — a structurally different defect (a collapsed,
irregular polygon's plane not containing the cell centroid), not the
one that method fixes. Splitting the one offending face doesn't help
either (a planar face split into pieces keeps the same orientation
problem against the centroid).

**Working approach**: merge the offending cell with an interior
neighbour (removing the shared face), conceptually the same operation
as OpenFOAM's own cell agglomeration. Implemented in
`core/poly_cell_merge.py` (`merge_cell_groups`, `find_merge_candidates`,
`repair_concave_cells_if_safe`), see
`notes/poly_cell_merge_reasoning.md`. Measured on the valve: 302/348
(86.8%) concave cells fixed by merging with the first interior
neighbour whose virtual merge passes the pyramid test.

**A real bug was found and fixed during this work**: the merge
renumbers cells, which can silently break OpenFOAM's "upper triangular
face order" requirement (owner non-decreasing across the whole internal
face list) even though every individual face still has owner <
neighbour — checkMesh reported it separately as "Faces not in upper
triangular order" on a real 227k-cell mesh. Fixed by sorting internal
faces by `(owner, neighbour)` after renumbering; covered by
`test_poly_cell_merge.py::_assert_upper_triangular_order`, invoked by
every mesh-validity test in that file.

**Timing matters, measured**: merging BEFORE building the boundary
layer works but has a real cost — on the valve, real checkMesh: wrong-
oriented faces 340 → 61 (-82%), but max cell aspect ratio nearly
TRIPLED (6,555 → 19,665) and severely non-orthogonal faces rose
859 → 1,320. Merging AFTER the boundary layer is built (so the merge
selection sees the actual prism geometry it sits next to) avoids that
regression entirely on the valve: 340 → 58 wrong-oriented (-83%),
aspect ratio UNCHANGED (6,555, identical), non-orthogonality slightly
BETTER (839 severe vs 859, 4 errors vs 10). Two follow-up hypotheses to
explain/fix the pre-BL regression were tested and both falsified:
compactness-based neighbour selection (no different result — usually
only one viable candidate exists per cell, not a real choice) and
volume-ratio-based selection (merged-pair volume ratio, median 2.3x,
is not an outlier vs the mesh-wide median of 1.6x). The actual
mechanism is the interaction between the merged cell and the already-
built anisotropic BL prisms sitting on it, not the neighbour choice.

**Not a universal win — measured on slot1 too**: the same post-BL
merge technique, applied to the slot1 reference geometry (already
`Mesh OK` at baseline), introduced 3 new highly-skewed faces (max
skewness 2.93 → 4.55) that checkMesh flags as a failure the baseline
did not have — even though the in-process replica showed a real
pyramid-violation improvement (15 → 1) and exact volume conservation.
Attempted fix: re-running this project's own already-validated
`poly_smoother.smooth_dual_mesh` (keep-best Laplacian relax) as a post-
merge quality pass — did NOT help, because its in-process skew detector
reported 0 both before and after while real checkMesh still found 3
skewed faces: the in-process skew metric does not match checkMesh's own
skewness formula closely enough to see this specific regression.

**Final design — a guardian, not a blanket fix**:
`repair_concave_cells_if_safe` applies the merge only if it does not
increase the in-process pyramid-violation count and conserves volume
(both metrics ARE exact matches to checkMesh, unlike skew) — otherwise
it returns the mesh completely unchanged. This makes the repair safe by
construction for the metrics it can check exactly, but it is explicitly
NOT a guarantee against every possible regression (the slot1 skewness
case would still pass this in-process gate, since the gate cannot see
it) — the module docstring and this section both say so. **Not wired
into the GUI or any default path.** Any real use of this repair on a
specific production case should be confirmed with a real `checkMesh`
run before trusting it, exactly like every other opt-in feature in this
project. Tests: `tests/test_poly_cell_merge.py` (7 tests, including one
on the real valve fixture and a healthy-geometry no-op check). Fast
suite green throughout.

## Skewness formula bug found and fixed (2026-09-13)

Follow-up from the cell-merge investigation above, motivated by a
directed literature/source search after the in-process skew detector
missed a real regression on slot1. Fetched OpenFOAM's actual
`primitiveMeshTools::faceSkewness` C++ source
(cpp.openfoam.org/v11/primitiveMeshTools_8C_source.html): the skewness
normalisation distance is `max(0.2*|d|, max-per-vertex-projection)`.
Our `_detect_defects` (tet_poly_dual.py) and `_continuous_metrics`
(poly_smoother.py) both computed it as a **sum** instead —
`fd = 0.2*dn + _face_extent(...)` — which silently inflates the
denominator and under-reports skewness everywhere, the root cause of
the earlier finding that our detector said "0 skewed faces" on a mesh
real checkMesh flagged with 3.

Fixed both call sites to use `np.maximum(...)`. Verified against 5 real
comparisons (see `notes/skewness_formula_fix_reasoning.md`): exact match
(4-5 decimal places) on two independent post-boundary-layer meshes
(valve baseline and merge-repaired, both 22.16451 vs checkMesh's
22.1645); much closer but not exact on two others (slot1 post-BL-repair,
and the `valve_dual.npz` fixture which stores points as float32 — a
precision source already documented elsewhere in this file). Honest
disclosure: the two non-exact cases are NOT fully explained (leading
hypothesis is float32 precision / a stale regenerated file, not
reconfirmed) — flagged rather than hand-waved. Updated the pre-existing
`test_detect_defects_matches_recorded_checkmesh` regression test (which
had explicitly pinned the OLD wrong count with a comment admitting the
gap) to the new, closer count. Fast suite green (1170 passed).

Practical consequence: this materially improves confidence in the
in-process quality gates used elsewhere in this project (including
`poly_cell_merge.repair_concave_cells_if_safe`), but does not eliminate
the need to confirm with a real `checkMesh` run before trusting a
production case — the residual gap on two of five comparisons is proof
of that on its own.

## Smoothing silently skipped on healthy meshes — found via a snappyHexMesh comparison (2026-09-14)

Triggered by a direct, measured comparison against `snappyHexMesh` on a
plain cylinder at a comparable cell count (10,649 our poly cells vs
11,880 snappy cells): real checkMesh showed snappy winning on every
quality metric (aspect ratio 3.02 vs 4.43, max non-orthogonality 29.1°
vs 51.3°, max skewness 0.86 vs 1.53).

**Root cause**: `TetPolyDualConverter`/`hex_poly_dual.py` default
`smooth=True` (the quality-driven Laplacian smoothing pass is meant to
always run), but the call site gated it behind `best_defects > 0` —
which only counts THRESHOLD-crossing cells (pyramid <= 0, non-ortho
> 70°, skew > 4). Our cylinder had 0/0/0 by that count, so smoothing
was silently skipped despite real headroom below those thresholds.

**Fix**: removed the `best_defects > 0` gate in both files — the
smoother is keep-best by construction, so this can only help or leave
the mesh unchanged. Measured on the cylinder (real checkMesh): max
non-orthogonality 51.3° → 41.7° (-19%), max skewness 1.53 → 1.28 (-16%).
Aspect ratio essentially unchanged (4.43 → 4.46) — snappyHexMesh still
wins on all three metrics, this closes PART of the gap, not all of it.

**A second, real bug found while fixing the first**: removing the gate
exposed that the tie-breaking acceptance never tracked face planarity
as a quality axis — on tiny/degenerate synthetic test meshes (a single
tet, a 12-tet perturbed cube) with zero real non-ortho/skew defects to
improve, an accepted "tied" step (using `<=`, "not worse") measurably
distorted exactly-planar faces (deviation 0.0021 vs a 1.7e-6 tolerance)
chasing a microscopic, meaningless objective difference. Fixed with two
changes: (1) strict `<` instead of `<=` for tie-breaking (a real
improvement required, not merely "not worse" — alone insufficient,
since the accepted step really was marginally "better" by an objective
blind to planarity), and (2) a hard planarity guard in
`poly_smoother.smooth_dual_mesh`, same pattern as the existing
volume-positivity/volume-ratio guards: track the worst per-face
planarity deviation and reject any candidate that makes it meaningfully
worse than the current best. One pre-existing test's pinned exact face
count (122 → 162) needed updating as a consequence — not a regression,
the properties that matter (triangulation truly eliminates non-planarity)
still hold exactly; see `notes/smoothing_gate_fix_reasoning.md` for the
full account. Fast suite green (1170 passed).

## GUI release polish (2026-10-06)

- **Section Cut**: now a deterministic static plane through the model centre
  (was a fragile draggable box that only worked in one mode). Reliable in
  every representation, but not movable with the mouse; a draggable plane is
  a possible follow-up.
- **Theme QSS**: the disk stylesheets (`.ui-design/qss/light.qss`,
  `dark.qss`) still hardcode theme-specific hex; the light-island bug is
  fixed at runtime by a palette fix-up in `theme.py`. Converting the QSS to
  palette roles is a follow-up.
- **Viewer BL visibility**: prism cells are still not colored differently
  from the core; the BL truth lives in the `[bl]` log lines (median,
  coverage, first layer). Cell-type coloring is a follow-up.
- **Quality tab**: removed from the left panel (it was an empty
  placeholder). Quality lives in the log, the quality panel and the PDF
  report; if a dedicated surface is wanted again it must be populated, not
  re-added empty.

## Real boundary layer (2026-10-06)

Limits accepted after the "real BL" work (commits `d654cdb`, `8f4b0e4`,
`2ea4191`; full account in `notes/bl_real_layers_reasoning.md`):

- **Architectural thickness limit**: the stack cannot exceed the
  depth of the first dual cell (`support_d`, p50 2.35 mm on Part2):
  the engine caps at the number of layers that 90% of the vertices hold at
  full height and states it in the log. For thicker BLs you need larger
  surface cells (the warning suggests it) or a BL that consumes
  more layers of interior cells — a big change, not in this release.
- **Viewer**: the prism cells are not coloured differently from the core;
  BL visibility relies on the log (median, coverage, effective first
  layer). Cell-type colouring = follow-up.
- **Automatic concave closure**: the chain standard → concave → h1/2 is
  covered by the unit tests but was not re-measured end-to-end on
  groove1/slot1 with the new standard semantics (the engine did not change;
  the order of attempts changes).
- **Benchmark**: the 9/10 vs snappy was measured with the old
  BL semantics (concave cases passed even with a failed BL); it must be
  re-validated with the real BL before the release.
- **Volume tolerance 5e-3**: the shell under the excluded faces is given up
  by-design (worst measured: 1.16e-3 on Part2); a real hole
  fails first the closure test at 1e-8, which is the exact gate.

## Boundary layer - terminator manifold defect (2026-10-07)

Measured on a real blade geometry (1.5M cells, 89 unnamed patches):

- **Root cause**: at BL/non-BL borders the terminator strips are emitted
  as BOUNDARY faces; when they coincide with the wall band the boundary
  block becomes non-manifold (+139,360 faces = exactly n_terminator_faces,
  32 boundary edges with incidence 4 -> checkMesh "230,126 multiply
  connected faces").
- **Current mitigation**: `bl_poly._validate` refuses any build whose
  boundary is not a 2-manifold (`_boundary_manifold_defects`). On such
  geometries the app writes the manifold DUAL-ONLY mesh (multiply
  connected 0, aspect OK) and the log reports the refusal - no BL is
  better than a broken BL.
- **Scheduled fix (post-release)**: make the BL/non-BL step INTERNAL
  (against the adjacent cell) instead of a boundary strip; this is a
  deep bl_poly change that must re-validate the valve/Part2/cylinder
  goldens.
- **Converter-intrinsic defects on the same geometry**: 351 incorrectly
  oriented faces + one skew-767.8 face remain in the dual; a kwargs sweep
  (planarize/triangulate/smoothing/dissolution) does not remove them.
- **A-floor note**: the BL wall-size floor is stack/3 (derived from the
  0.34 inversion-budget relation, capped at 10% of the model extent);
  even fully honoured, very thick stacks on fine wall meshes can remain
  compressed - the panel hint and "Fit BL to mesh" are the honest path.

## Linux support status (2026-10-07)

- Phase 1 (core) + phase 2 (GUI) are implemented: platform-dispatched
  OFConfig (native `bash -lc` on POSIX, Windows byte-identical), XDG app
  data, safe POSIX process kills, OpenFOAM availability gate, packaging
  skeleton (`launch.sh`, `PolyFoamMesh-linux.spec`, release `build-linux`
  tarball job).
- **Verified on Windows only**: the fast suite stays green and POSIX
  command generation is unit-tested; the real Linux run is verified by
  the ubuntu CI job (dispatch step) and must be smoke-tested on a real
  Linux machine before announcing Linux support.
- Not done: AppImage/deb packaging (tarball only for now), native
  OpenFOAM integration tests in CI, `mpirun` as non-root.

## Test Matrix

- Fast (no WSL) suite: green.
- Real WSL/OpenFOAM smoke (GMSH tet -> gmshToFoam -> dual poly -> checkMesh):
  `Mesh OK` on the reference cylinder case.
- Full WSL regression runs are not part of the fast suite; they require
  OpenFOAM v2512 in WSL2 and are executed manually or via the harnesses in
  `tools/`/`tests/bench_*`.
