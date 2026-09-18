# Residual Risks And Known Limitations

Status after the Commercial Hardening pass (Deepwork). This document records
the known, accepted limitations that remain after the hardening work, so a
future session does not rediscover them and product decisions have a single
source of truth.

## Status as of 2026-09-17

A RAM-exhaustion crash was found and fixed on the boundary-layer /
cell-merge path (a real ~5M-cell run with "concave closure" on exhausted
32GB and crashed the OS). Root cause was Python dict/list structures
built over the whole mesh instead of the cells/faces actually involved;
now chunked/vectorized. A ~5M-cell / 15M-face synthetic run now peaks at
~13GB; the safety guard moved from a rough 1.5M-cell cap to a measured
8M-face threshold. **That threshold is still only measured on a
synthetic hex mesh, not on the real dual/poly path** — see
[#9](https://github.com/LorenzoRiti/PolyFoamMesh/issues/9).

Three literature-grounded boundary-layer extrusion-normal strategies were
evaluated against real `checkMesh` on concave geometries (most-visible
normal, Laplacian-smoothed normal, guided bilateral filter), plus a
batched per-vertex local-height retry. The most-visible + local-height-
retry combination is shipped as an opt-in that measurably improves wall
coverage over either alone; the other variants were measured and are
documented as honest no-ops, not shipped. All of this is opt-in;
defaults are unchanged.

The reference cylinder still has a real aspect-ratio gap against
snappyHexMesh (4.49 vs 3.02). A direct cell-merge fix was tried and
rejected by real checkMesh (non-orthogonality +80%, skewness +233%); a
zonal smoothing pass helped marginally (-0.5%) but hit a topology-bound
wall. The diagnosed fix is a directional split operator, not yet
written — see [#8](https://github.com/LorenzoRiti/PolyFoamMesh/issues/8).

**Open issues tracking the largest known gaps** (good starting points for
contributors — each has a diagnosis, prior attempts, and acceptance
criteria already written up):

- [#8 — Directional split operator for the cylinder aspect-ratio gap](https://github.com/LorenzoRiti/PolyFoamMesh/issues/8)
- [#9 — Validate the RAM safety threshold on real dual/poly geometries](https://github.com/LorenzoRiti/PolyFoamMesh/issues/9)
- [#10 — Vectorize the remaining O(n_cells) loop in the cell-merge repair](https://github.com/LorenzoRiti/PolyFoamMesh/issues/10)

See `CHANGELOG.md` for the full list of what changed in this pass.

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
  longer auto-closes the selection over the whole boundary. With
  `patch_names` (the runner's default: only `wall` patches / name
  *wall* / geometric roles from `infer_patch_roles`), the prism side
  faces at the edge of the BL zone lie in the plane of the adjacent
  wall and become **boundary faces of the non-BL patch** (owned by the
  prisms); the unselected wall faces move their shared vertices to the
  last layer. Real verification (`tools/bench_bl_poly_partial.py`,
  checkMesh WSL, re-measured 2026-09-09): A. GMSH cylinder with patches
  named inlet/outlet/wall → BL only on wall, 60,354 prisms = 3 layers x
  20,118 faces (70,976 cells, skew 2.264, NOmax 81.1, `Mesh OK`);
  B. duct cube (in-memory) → 81 cells, 72 prisms = 3 x 24, skew 2.175,
  NOmax 44.8, `Mesh OK`. The old "72 prisms = 3 x 24" numbers were the
  cube, not the cylinder. `n_prism_cells == n_layers × wall_faces`
  derived from the input mesh, never hardcoded.
- **Solver on the valve (PHASE 0, 2026-08-10)**: see
  `docs/poly_solver_validation.md` — potentialFoam converges on the
  valve's poly mesh (final residual 4.6e-6, continuity error 1.6% of
  flow, volume 0.00%), so the concave defect (852 "incorrectly
  oriented" faces, checkMesh not OK) is a **documented limitation**,
  not a bug: PHASE 3 not needed.
- **PHASE 2 (local layer termination, 2026-08-10)**: machinery
  implemented (per-vertex layer count nv based on angle_fade, per-face
  count, a consistency fixpoint that makes the count uniform per
  connected component, relaxed pyramid gate `n_pyr_after <=
  n_pyr_before`, input-violating faces reduced to 1 layer) and verified
  on healthy meshes (cube 6/6: 0 unclosed cells, volume 1e-6; PHASE 1
  gate re-verified PASS). On geometries with pre-existing concave
  defects (the valve) the BL still fails cleanly with the mesh
  unchanged: closure does not hold under any of the measured strategies
  (199 unclosed cells in the unconstrained construction; 63,252 in the
  fixpoint; dropping to 0 layers loses volume). Root cause: angle_fade
  >= 0.8 on ALL of the valve's wall vertices — the dual cells'
  concavity is not detectable from wall normals. The global 5-scale
  fallback remains the valve's behaviour as long as `local_termination`
  stays opt-in (see the PHASE 2 result below).
- **Dual-convexity concavity criterion (Lane B, 2026-09-08)**: new
  opt-in parameter `concavity_criterion="dual_convexity"` in
  `core/bl_poly.py` (function `_dual_convexity_wall_fade`; default
  `"angle_fade"` unchanged and byte-identical). The detector measures
  the DUAL CELLS' convexity directly: for each wall vertex, the
  fraction of incident wall faces whose owner cell is non-convex in the
  exact sense of checkMesh's 'face pyramids' test (the OpenFOAM cell
  centroid falls outside one of its faces), mapped onto the same
  0.05..1.0 convention as angle_fade. Measured on the valve fixture
  (1,051,199 points, 1,241,048 faces, 152,086 cells): the signal fires
  EXACTLY on the documented defect class — 393 non-convex dual cells
  (documented range 268-462), 1,007 defective faces (= the input's
  1,007 pyramid defects), 1,123 wall vertices with fade < 0.5 versus
  ZERO for angle_fade (fade >= 1.0 on all 226,538 vertices, confirming
  the documented root cause). Measured outcome of the two full runs
  (in-process, checkMesh replica; n_layers=2, h1=1e-5, apply_to_all=True,
  5 scales):
  - `angle_fade` (baseline): closure failed at every scale — 161 → 106
    → 65 → 45 → 15 unclosed cells, no negative volumes, 523 s, mesh
    unchanged;
  - `dual_convexity`: 24 → 378,605 non-positive-volume cells (a global
    winding flip at scale 0.6, 99.99% of the mesh) → 11 → 12 → 6
    unclosed cells, 599 s, mesh unchanged. Better than the baseline on
    4 of 5 scales (up to 6.7x at scale=1.0: 24 vs 161), but NO scale
    reaches 0 unclosed cells: no valid BL even with the new criterion.
  Why it doesn't close G2 (measured, not assumed): the consistency
  fixpoint flattens the count to uniform per connected component —
  measured nv=nf=1 on ALL 226,542 faces under BOTH criteria (the
  input's 937 flagged faces force the whole wall to 1 layer within
  ~100 fixpoint passes). A per-vertex detector can therefore only
  change the local HEIGHT (max_h = fade x ...), not the count: it
  improves closure but does not bring it to 0, and destabilises the
  winding repair at intermediate scales. Recommendation: the dual
  convexity signal is a STRICTLY better detector than the fade
  (393 cells vs 0) and more useful for closure at 4/5 scales, but on
  its own it does not close the valve; using it needs the fixpoint
  relaxed (per-face counts with transition faces that close, or a
  fixpoint restricted to the defective region). Default unchanged:
  re-measure before enabling. WSL gate (`tools/bench_bl_valve_fase2.py`)
  NOT run: no scale produces a valid mesh in-process (the engine's
  verdict is decided by the replica, which on the valve matches
  checkMesh at 895/895), so there is no new mesh to submit to
  checkMesh — the pinned baseline remains the valve's behaviour.
  Health gate (`tools/bench_bl_poly_partial.py`): PASS unchanged
  (cylinder 72 prisms = 3 x 24, Mesh OK; duct cube 81 cells, skew 2.175,
  NOmax 44.8, Mesh OK); fast tests `tests/test_bl_poly.py`: 6/6 green
  (9/9 with the new global-solver and binary-termination tests).
- **GLOBAL winding solver (Lane B, 2026-09-08)**: the greedy per-cell
  repair was replaced with an EXACT solution (`_solve_global_windings`):
  parity constraints for every face-side (the two faces of a cell on an
  edge must traverse it in opposite directions), solved with a BFS on
  the face graph, then a global sign per component (positive total
  volume). Deterministic, O(F+E). Measured on the valve at ALL 5 scales:
  **0 unclosed cells** (max_rel ~1e-12, was 24 at scale 1.0), **volume
  conserved to 1e-16** (was drifting), no catastrophic global flip — the
  old greedy repair at scale 0.6 produced 378,605 non-positive-volume
  cells, now none. The solver also reports `n_conflicts` (non-orientable
  mesh) and `n_nonmanifold_edges`: both 0 on the valve. Health gate
  (`tools/bench_bl_poly_partial.py`) unchanged and PASS: cylinder 60,354
  prisms = 3 x 20,118, `Mesh OK`; duct cube 81 cells, 72 prisms =
  3 x 24, skew 2.175, NOmax 44.8, `Mesh OK`. New regression test:
  `tests/test_bl_poly.py::test_bl_global_winding_solve_is_exact_after_flip`
  (a manually flipped face is closed exactly).
- **BL closure on concave geometry (PHASE 2 complete, 2026-09-09)**:
  opt-in `run(local_termination=True)` produces the first VALID BL on
  the valve. Two-part mechanism, both opt-in:
  1. binary termination (`_build(..., zero_concave=True)`): a face
     either gets the whole layer stack or is excluded (0 layers) — no
     consistency fixpoint flattening the healthy region to 1 layer;
  2. iterative exclusion (`_collect_exclusions`): faces whose prisms
     turn out invalid (non-positive volume, pyramids, moved core cells)
     are removed from the selection and it retries; up to 5 scales x
     3 rounds, exclusions carried across scales; the mesh is written
     ONLY if the validation gate passes (otherwise it is left
     unchanged, same contract as the standard path).
  Measured end-to-end on the valve (1,051,199 points, 1,241,048 faces,
  226,542 wall faces; n_layers=2, h1=1e-5, `dual_convexity`): success
  at scale 0.1 after exclusions at 1.0/0.6/0.35/0.2 — **19,625 excluded
  faces (8.7%)**, 410,668 prism cells, thickness 2.2e-06, volume
  conserved (0.1218350 vs 0.1218351 input), min cell volume 1.15e-14,
  1602 s. Real checkMesh: **891 "incorrectly oriented" faces vs 895 in
  the input (better)**, max non-ortho 123.3, max skewness 46.8 vs 45.0,
  "Failed 4" vs "Failed 3" — the one extra check is aspect ratio
  (max 15522, 15,921 cells), intrinsic to thin BL prisms (h1 = 1e-6 m
  at scale 0.1); no other check gets worse. Default unchanged
  (`local_termination=False`): the standard path on the valve still
  fails cleanly as documented (slow test
  `test_valve_fixture_bl_invariants`, 1 passed in 473 s). New tests in
  `tests/test_bl_poly.py` (exact closure, binary drop, success on a
  healthy cube): 9/9 fast, green.
  The PHASE 1 bench also exposed a reader bug:
  `foam_mesh_io.read_label_list` misclassified a binary payload as
  ASCII when its 64-byte probe contained a 0x29 byte (cell indices like
  41/296/10537), returning an EMPTY list — and the rename patch then
  wrote back a corrupted polyMesh (neighbour=0). Fixed with an
  "every byte is ASCII text" test instead of the `')' in probe`
  heuristic, plus a regression test
  `test_local_refinement_boxes.py::test_read_label_list_binary_payload_with_0x29_byte`.
- **PHASE 3 — does local termination generalise? (2026-09-09)**.
  Protocol: `local_termination=True` on geometries other than the
  valve, REAL checkMesh before/after, the same metrics (unclosed cells,
  % excluded, time). Reasoning and predictions written BEFORE testing
  in `notes/fase3_reasoning.md` (untracked). No default changed; no GUI
  wiring (blocked on user validation of PHASE 2).

  **Cheap predictor (without extruding anything):** count, on the dual,
  `pyr_input` (faces violating the pyramid criterion) and wall vertices
  with dual fade < 0.5. It orders difficulty exactly like the runs:
  cylinder 0/0, cube 0/0, groove1 4/2, slot1 42/17, valve 1,007/1,123.
  Cost: 27 s on the valve (vs 115 s for the first build).

  **Outcomes (n_layers=3, h1=0.005, apply_to_all; `standard` vs
  `local_termination` dual_convexity, real checkMesh):**
  - cylinder (dual 10,618 cells, 24,936 faces): standard 74,808 prisms,
    scale 1.0, Mesh OK (skew 2.97, NOmax 81.1) — **LT identical, 0
    excluded**, 12.6 s; `angle_fade` also identical. No regression
    where the standard path already works.
  - duct cube (9 cells): identical, 108 prisms, Mesh OK.
  - groove1 (682 cells, 3,372 faces; predictor 4/2): standard
    "success=True" at scale 0.1 but with EVERYTHING at 1 layer and
    checkMesh **Failed 2** (2 non-ortho errors, skew 46.2) — a mesh
    checkMesh rejects. LT dual: **10,062 prisms (3 layers), scale 1.0,
    Mesh OK** (skew 3.29, NOmax 89.1), 18 faces dropped by the binary
    criterion (0 iterative exclusions), 1.4 s. LT angle: 10,032 prisms,
    28 dropped, Mesh OK. LT triples the layer count AND makes the mesh
    valid.
  - slot1 (9,372 cells, 26,754 faces; predictor 42/17): standard
    **FAILS** at every scale (mesh unchanged, 34.5 s). LT dual:
    **79,614 prisms, scale 1.0, Mesh OK** (skew 2.89, NOmax 87.0), 216
    faces with no BL = 0.8%, 27.1 s; LT angle: 79,593 prisms, 190
    dropped, Mesh OK. Second concave geometry where LT unlocks the BL.
  - valve: predictable seed of 2,737 faces (1.21%): 937 defective in
    the input + 1,800 with one vertex at fade<0.5 (0 drop-only). A run
    at 0.1 with the production exclusion logic: 3 rounds
    (114.8/90.7/99.4 s), 2,058 iterative exclusions, of which
    **1,828 (88.8%) in the seed** and only 230 (0.10% of the total)
    pure cascade; 91.5% of the excluded faces within 2 adjacency rings
    of a defective face (ring 0/1/2 = 803/651/429). checkMesh: 885
    wrongly-oriented faces vs 895 in the input, "Failed 4" (the fourth
    = aspect ratio), 597,802 cells. Accounting: `local_excluded_faces`
    counts only the iterative part; the total with no BL is 3,684 faces
    (**1.63%**) because the binary drops are re-evaluated every round
    (a vertex can drop below 0.5 and lose an incident face).
  - **Main finding — scale ORDER is the real cost:** the thick-first
    engine (1.0 → 0.1) accumulates ALL 19,625 iterative exclusions
    before reaching 0.1 (the warnings show failures only at
    1.0/0.6/0.35/0.2; at 0.1 it validates with the carried set). The
    same 0.1 thin-first needs only 2,058: **9.5x fewer**, 8.5% more
    prisms (445,716 vs 410,668) and marginally better checkMesh (885 vs
    891). Recommendation for future work: reverse the scale order (or
    reset exclusions when descending a scale) with dedicated tests —
    NOT implemented here: no default changed.
  - **Global winding vs binary termination:** the winding solver is an
    exact solver (no conflicts/non-manifold edges on any geometry; 0
    unclosed cells everywhere). With winding alone the valve's standard
    path remains invalid due to negative volumes (22-44 per scale,
    stage1c): the residual is geometric, not orientation. Binary
    termination is the only piece that scales with difficulty: a no-op
    on cylinder/cube, 0.5-0.8% dropped on groove/slot, 0.9-1.6% on the
    valve (thin-first).
  - **Predictions vs measurements:** seed, thin-first, and rings
    confirmed. One wrong prediction reported honestly: for groove1 I
    had predicted "tens/hundreds" of low-fade vertices, measured 2 —
    the predictor orders correctly but the easy/hard threshold needs
    calibrating. No LT failure in the set; the only measured failure is
    the STANDARD path on slot1.
  - **Time:** ~linear in size with fixed overhead (cube 0.03 s; groove
    1.4-3.2 s; cylinder 12.6-13.5 s; slot1 27.1 s; valve ~100
    s/attempt). Total = attempts × size; the valve's thin-first costs
    ~305 s of build time against the thick-first's 1,602 s.
- **PHASE 4 — does thin-first generalise beyond the valve? NO
  (2026-09-09)**. Question: does the thin-first advantage measured on
  the valve (9.5x fewer exclusions, +8.5% prisms, ~5x faster) also hold
  on cylinder/cube/groove1/slot1? Method: prediction written BEFORE
  testing (`notes/thinfirst_reasoning.md`), two runs per geometry with
  the same internal logic and only the scale tuple changed
  (`tools/bench_bl_thinfirst.py`; thick-first reproduces PHASE 3's LT
  results exactly, so the patch is faithful).

  **Result: as a blind inversion it does NOT generalise — it introduces
  a serious side effect.** On ALL 4 geometries thin-first validates on
  the FIRST attempt (scale 0.1) and silently writes a BL whose
  effective `first_height` is 10x smaller than requested (5e-4 instead
  of 5e-3):
  - cylinder: THICK scale 1.0 vs THIN scale 0.1; same prisms (74,808),
    0 exclusions, identical time (17.9 vs 17.0 s), BUT max aspect 18.3
    → **104.3**;
  - duct cube: 108 prisms either way, aspect 78.7 → **781.6** (~10x);
  - groove1: 10,062 prisms either way, aspect 15.9 → **90.8**;
  - slot1: THICK 79,614 prisms with 132 iterative exclusions (216 total)
    at scale 1.0 in 2 rounds, 36.5 s; THIN 80,004 prisms with **0
    iterative exclusions** (86 total = seed only) at scale 0.1 in 1
    round, 17.9 s — here the valve-like advantage reappears (fewer
    exclusions, +390 prisms, 2x faster) but again with a 10x reduced
    thickness (aspect 16.7 → **128.5**);
  - valve (PHASE 3): no thickness conflict is possible — scale 1.0
    always failed, 0.1 was the only valid one; there thin-first stays
    better and does no harm.

  **Reading:** the two effects are intertwined. The advantage (fewer
  exclusions, more prisms, faster) exists only where a thick scale
  genuinely fails, and comes from avoiding accumulating exclusions
  before reaching the validating scale. But "accept the first scale
  that validates" means accepting 0.1 even where the requested 1.0
  would have worked: on a healthy mesh it degrades y+ by 10x without
  failing. This is why the inversion should NOT be proposed as a
  default (nor implemented). The variant to measure (future work,
  explicitly not implemented) is **decoupled**: try the requested scale
  first and, when descending, do NOT carry the accumulated exclusions
  (reset on scale change) — so the valve would get ~2,058 exclusions
  like thin-first while healthy cases would keep the requested
  thickness. **Predictions vs measurements (honest):** I had predicted
  "thin-first identical to thick-first" on cylinder/cube/groove1 —
  WRONG on thickness/scale/aspect (the "exits at round 0" mechanics
  were right, but thin-first's round 0 IS scale 0.1). On slot1 I had
  predicted "equal or slightly worse in % excluded" — wrong in sign
  (0 vs 132, so better), right in mechanics; missed the thickness
  effect. The PHASE 3 predictor (pyr_input) remains valid: the
  advantage only appears where pyr_input > 0 and grows with the number
  of defects. Default unchanged (`local_termination=False`, scale order
  unchanged), no GUI wiring.
- **PHASE 5 — "decoupled" variant implemented and measured
  (2026-09-09)**. Building on PHASE 3/4, the proposed variant was
  implemented: opt-in engine mode `run(local_termination="decoupled")`
  (`bl_poly.py`): same loop (max 3 rounds, scales 1.0 → 0.1) but the
  exclusion set is **reset on scale change** — the REQUESTED scale
  (1.0, full thickness) is tried first and only descended if it fails,
  without making the thin scales pay for the thick scales' defects.
  `True` (legacy carry) and the default `False` remain unchanged; new
  tests `test_bl_local_termination_decoupled_mode_on_healthy_cube` and
  `test_bl_local_termination_rejects_unknown_mode` (fast suite 1153
  green). Prediction written before testing in
  `notes/decoupled_reasoning.md`.

  Measurements (`tools/bench_bl_thinfirst.py --modes thick,thin,decoupled
  [--valve]`, real checkMesh):
  - cylinder/cube/groove1/slot1: DECOUPLED **identical to THICK** in
    every respect — scale 1.0 (full thickness), same prisms (74,808 /
    108 / 10,062 / 79,614), same exclusions (0/0/0/132), same max
    aspect (18.3 / 78.7 / 15.9 / 16.7) and times. The reset never comes
    into play when success arrives within the requested scale: no
    thickness degradation versus pure thin-first.
  - valve: success at scale 0.1 with **2,058 exclusions** (predicted
    and obtained) and **445,716 prisms** — exactly the thin-first
    result (3,684 faces with no BL = 1.63%). checkMesh: **885 wrongly
    oriented faces vs 895 in the input**, "Failed 4" (fourth = aspect
    ratio), skew 47.6, non-ortho 55 — identical to thin-first.
  - **Valve time: 2,215 s (37 min)** — slower than predicted (24-30
    min) and slower than both thin (305 s) and thick (1,602 s): it
    still tries every scale (15 builds, 3 rounds per scale). The
    advantage is in exclusions/prisms, not time.
  - Valve warning trajectory: failures at 1.0 (50→6→6 negative
    volumes), 0.6, 0.35, 0.2, then a fresh 0.1 validates on the third
    round (1,946+112).

  **Verdict**: decoupled hits the target — it captures the thin-first
  advantage on the valve (9.5x fewer exclusions, +8.5% prisms, better
  checkMesh) WITHOUT degrading thickness on healthy cases (they stay
  at full scale 1.0). It is the natural candidate for local-termination
  semantics once enabled (PHASE 2, pending user validation): "requested
  thickness where possible, only defects pay". The time cost on
  defective geometries remains; a possible optimisation (skipping
  intermediate scales or reusing exclusions with an identical failure
  signature) needs a dedicated measurement. Default unchanged, no GUI
  wiring.

  Valve summary table (same fixture input, n_layers=2, h1=1e-5,
  dual_convexity):

  | variant | iterative excl. | faces with no BL | prisms | checkMesh | time |
  |---|---|---|---|---|---|
  | THICK (legacy) | 19,625 | 21,208 (9.36%) | 410,668 | 891 wrong | 1,602 s |
  | THIN (experimental) | 2,058 | 3,684 (1.63%) | 445,716 | 885 wrong | 305 s |
  | DECOUPLED | 2,058 | 3,684 (1.63%) | 445,716 | 885 wrong | 2,215 s |
- **PHASE 6 — early-exit-intermediates implemented and measured
  (2026-09-09)**. To answer "does an early-exit (stop at the first
  valid scale) reduce the valve's time?": the engine ALREADY has an
  early-exit at the first valid scale (`if ok: return
  self._accept_built(...)`) — cylinder/cube/groove1 validate at scale
  1.0 round 0 and do **just 1 build** (12.8 s, 1.4 s, 0.0 s). The
  valve's long time under decoupled (2,215 s, 15 builds) is not an
  "it never exits" bug: it's that only 0.1 validates and the 4 thick
  scales are all "equally useless" (pyr count 1010-1019 vs input
  1007). To save those 9 builds, the opt-in variant
  `early_exit_intermediates` was implemented: if 1.0 round 0 fails,
  the intermediate scales (0.6, 0.35, 0.2) are skipped and it goes
  straight to 0.1. Implemented in `bl_poly.py` (engine parameter,
  default off; triggers inside `_run_local_termination` after 1.0
  round 0 fails; stats in
  `res.stats["local_termination_early_exit_intermediates"]`). Test:
  `test_bl_local_termination_early_exit_intermediates_noop_on_healthy`
  (fast suite 1154 green). Prediction written before testing in
  `notes/early_exit_reasoning.md`.

  Measurements (`tools/bench_bl_thinfirst.py --modes early_exit
  [--valve]`, real checkMesh):

  | Geometry | DECOUPLED | EARLY_EXIT | Δ |
  |---|---|---|---|
  | cylinder | scale 1.0, 74,808, 0 excl, 17.8 s, aspect 18.3 | scale 1.0, 74,808, 0 excl, 12.8 s, aspect 18.3 | identical (no-op) |
  | cube | scale 1.0, 108, 0 excl, 0.0 s, aspect 78.7 | scale 1.0, 108, 0 excl, 0.0 s, aspect 78.7 | identical |
  | groove1 | scale 1.0, 10,062, 0 excl, 2.1 s, aspect 15.9 | scale 1.0, 10,062, 0 excl, 1.4 s, aspect 15.9 | identical |
  | **slot1** | scale 1.0, 79,614, 132 excl, 36.3 s, aspect 16.7 | **scale 0.1, 80,004, 0 excl, 25.2 s, aspect 128.5** | **degenerates into thin-first** |
  | **valve** | scale 0.1, 445,716, 2,058 excl, 2,215 s, checkMesh 885 | **scale 0.1, 445,716, 2,058 excl, 414 s, checkMesh 885** | **identical, 5.4x faster** |

  **Verdict**: on the valve it hits the target: the same result as
  decoupled (2,058 excl, 445,716 prisms, checkMesh 885) in **414 s
  instead of 2,215 s (5.4x)** with only 4 builds (1.0 round 0 fail +
  skip + 0.1 round 0, 1, 2) instead of 15. Valve warning trajectory:
  1.0 round 0 fail (50 negative volumes) → skip 0.6/0.35/0.2 → 0.1
  round 0 fail (2 neg) → 0.1 round 1 fail (1012 pyramids > 1007
  input) → 0.1 round 2 validates with 2,058 excl.

  **Documented risk**: the heuristic is safe on the valve but
  **degenerates into thin-first on slot1** (scale 0.1, aspect 128.5
  instead of 1.0/16.7): the "1.0 round 0 fails → skip intermediates"
  trigger also fires when 1.0 would have validated after 1 round of
  exclusions (slot1: round 0 fails with 50 pyr > 42 input, but round 1
  with 132 excl validates). The skip loses that round 1 and forces
  0.1 with a clean set (= thin-first). So
  `early_exit_intermediates` is a candidate *only* for valve-like
  geometries (where 1.0 has no hope of validating at any round); for
  slot1-like geometries it is too aggressive. A finer heuristic
  ("skip only if both round 0 and round 1 of 1.0 fail") or ("skip
  only if 1.0's pyr_round0 does not improve on descending") needs a
  dedicated measurement. Default unchanged, no GUI wiring.

  Valve summary table (same fixture input, n_layers=2, h1=1e-5,
  dual_convexity):

  | variant | iterative excl. | faces with no BL | prisms | checkMesh | time |
  |---|---|---|---|---|---|
  | THICK (legacy) | 19,625 | 21,208 (9.36%) | 410,668 | 891 wrong | 1,602 s |
  | THIN (experimental) | 2,058 | 3,684 (1.63%) | 445,716 | 885 wrong | 305 s |
  | DECOUPLED | 2,058 | 3,684 (1.63%) | 445,716 | 885 wrong | 2,215 s |

  **CAVEAT (PHASE 7, 2026-09-09)**: the PHASE 6 valve numbers are
  **pinned to the stale fixture** `valve_dual.npz` (generated
  2026-08-01 with a different converter than the current one) and the
  `early_exit_intermediates` trigger was **fixed in PHASE 7** (it was
  also skipping rounds 1-2 of scale 1.0, not just the intermediate
  scales). On an input reconverted with the current converter the
  story changes: see §PHASE 7.
- **PHASE 7 — BL on the PRODUCTION (collapsed) topology + stale
  fixture (2026-09-09)**. Origin: `openfoam_runner.py:1652` converts
  the production poly with `collapse_smooth_edges=True,
  boundary_feature_angle=40.0, collapse_volume_tolerance=0.10`, while
  ALL PHASE 2-6 BL measurements (fixture and bench) use the converter
  with defaults (**collapse OFF**, three quads per triangle). The
  collapsed dual has 3-6x fewer boundary faces and `bl_poly` extrudes
  one prism per face: measured with
  `tools/bench_bl_production_topology.py` (new).

  | | exact (default) | production (collapsed) |
  |---|---|---|
  | cylinder, boundary faces | 24,936 (quad) | **4,412** (4-8-gons, 5.65x fewer) |
  | cylinder, volume drift | 1.4e-16 | 0.128% |
  | cylinder, BL 3 layers | scale 1.0, 0 excl, **74,808** prisms, 13.0 s, Mesh OK | scale 1.0, 0 excl, **13,236** prisms, 5.6 s, Mesh OK |
  | valve, boundary faces | 226,542 (quad) | **42,130** (4-9-gons, 5.4x fewer) |
  | valve, input defects (checkMesh wrong) | 863 | **340** |
  | valve, volume drift | 3.4e-16 | 1.08% |
  | valve, BL | **success at scale 1.0** (2 builds): 2,525 excl, 444,924 prisms, 217 s; checkMesh 836 wrong (< 863 input), skew 38.1 (vs 14.6 input), aspect 1,543, Failed 4 | **FAILS**: `decoupled` 15 builds in 892.7 s, 2,189 excl, stalled at 375-444 pyramids vs 370 input; mesh unchanged |

  - **The valve fixture is STALE**: `tests/fixtures/valve_dual.npz`
    (2026-08-01) records 895 wrong / 55 non-ortho errors / skew 44.3; a
    fresh conversion of the SAME tet backup today gives 863 wrong / 9
    errors / skew 14.6. All PHASE 2-6 valve numbers are therefore only
    representative of that snapshot: on the current conversion the same
    pipeline behaves differently (example: success at 0.1 with 2,058
    excl does not reproduce; the new input validates at **scale 1.0
    round 1** with 2,525 excl and full thickness).
  - **PHASE 6 early-exit bug found and fixed**: the trigger fired at
    1.0 round 0 and was also skipping rounds 1-2 of the requested scale
    (not just the intermediates). Masked by the stale fixture (where
    1.0 was hopeless anyway) and the real cause of the "degeneration"
    on slot1. Now the trigger only fires once scale 1.0 has exhausted
    its rounds: on cylinder/cube/groove1 it stays a no-op; on the
    re-measured exact valve it now succeeds at 1.0 round 1 in 217 s
    (identical to legacy). Fast tests 12/12 green.
  - **Prediction vs measurement**: I had predicted (70%) that the BL
    would close on the production topology too; **measured: it fails**
    (30% predicted). On healthy cases the prediction was correct
    (success, 5.65x fewer prisms, Mesh OK).
  - **Implications (no default changed)**: (1) the fixture must be
    regenerated and the pinned numbers updated before any other valve
    measurement; (2) the production BL path on concave geometry **is
    not supported today** (collapsed is great for cell count and on
    healthy cases, but the exclusion→polygon mapping does not close the
    valve): to be addressed as a dedicated lane (e.g. per-vertex
    exclusion or mixed granularity), not in this session; (3) the
    existing BL benches still use the exact dual: stating this here
    avoids confusing the two worlds.
- **PHASE 8 — fixture regenerated, fast tests re-pinned, collapsed BL
  measurement (widen=0/1) (2026-09-09)**. Three consecutive outcomes
  on this lane; honest conclusions, **no default changed**.

  1. **Valve fixture `tests/fixtures/valve_dual.npz` regenerated** with
     the current converter (2026-09-09, 27.3 MB, identical size to the
     previous one; the `valve_dual_checkmesh.txt` file recorded
     alongside it): checkMesh reports **863 wrongly oriented faces, 9
     non-ortho errors, skew 14.58, Failed 3**, against the old pinned
     numbers (895/55/44.3) — an improvement in the converter on the
     same tet backup. The in-process detector `_detect_defects` reports
     **863 / 203 / 3** (pyr / non-ortho-det / skew-det): 863 pyr matches
     checkMesh; 203 = checkMesh's "nonOrthoFaces" set (the 863 → 9
     "errors" >70° in checkMesh); 3 is the skew-det (checkMesh writes
     98 "highly skew"). `tests/test_tet_poly_dual`'s parametrize was
     updated to `{"pyramid": 863, "non_ortho_det": 203, "skew_det": 3}`.
  2. **`poly_fixture_builder.py` fixed**: the initial fvSchemes did not
     contain `divSchemes`/`laplacianSchemes`/`interpolationSchemes`/
     `snGradSchemes` and checkMesh 2512 failed with `FATAL IO ERROR:
     Entry 'divSchemes' not found`. Added the full set. All subsequent
     builder runs produce a valid checkMesh.
  3. **Slow test `test_valve_fixture_bl_invariants`**: passes on the
     new fixture (373 s, clean-failure: negative volumes for scale
     1.0→0.1 with counts 104/64/48/40/16; mesh unchanged, polyMesh
     exists). The "must FAIL CLEANLY" docstring remains correct; an
     accidental FileNotFoundError in a previous run was caused by a
     **concurrent double execution** of the test (the fixer had
     re-launched the command, and the second instance deleted
     `valve_bl` with `rmtree` while the first was still running) — a
     concurrency artefact, not an engine bug. To reduce the race
     window, a simple fix would be to make `case` unique per run
     (e.g. via a timestamp in the name) — not implemented here.
  4. **Collapsed-BL widening experiment (PHASE 8)**: new opt-in engine
     parameter `local_exclude_widen: int = 0` (default unchanged) in
     `bl_poly.run()` (validated 0..3), with method `_widen_exclusions`
     that expands the exclusion list collected each round by N rings
     of boundary-edge neighbours (built from `pre["bnd_edge_faces"]`).
     Test: `test_bl_local_exclude_widen_param` (healthy cube succeeds
     with `widen=2`; `widen=4` errors). Bench:
     `tools/bench_bl_production_topology.py --exclude-widen N`.
  5. **Collapsed valve measurement with `local_exclude_widen=1`**
     (production + decoupled + `max_rounds=6`): 2,064 s, 25 builds,
     **2,207 exclusions** (identical to widen=0), stalled at 373-378
     (0.2) / 379 (0.1 r3-r5), clean failure. **H6 widen=1 falsified**:
     widening by 1 ring does not add faces to the excludable set (the
     rim's frontier is already 1-ring in many spots, or the pyramid
     violation at the rim is not simply "shifted by 1 ring"). `widen=2`
     not measured (cost ~30 min, expected negative).
  6. **H2 confirmed, H3 + H6 widen=1 falsified**: the collapsed BL
     defect on the valve cannot be closed with exclusion granularity
     (face + 1 ring). The structural gap (H4, exclusion→per-vertex
     mapping) remains the path to take to support production BL on
     concave geometries. Everything stays opt-in: no default changed.
- **PHASE 9 / H4 — per-VERTEX exclusions: the production collapsed
  valve CLOSES (2026-09-09)**. New opt-in mode
  `local_termination="decoupled_vertex"` (default unchanged): identical
  to `"decoupled"` but the iterative exclusion unit is the wall
  VERTEX — when a prism/core cell is invalid, ALL vertices of its base
  face are excluded; a face is extruded only if none of its vertices is
  excluded. On the collapsed dual (one boundary polygon = a vertex's
  star) a defective vertex propagates to all incident faces in ONE
  round. The initial binary drop (`zero_concave`) stays per-face,
  unchanged; `local_exclude_widen` is a no-op in this mode.
  Implementation in `core/bl_poly.py`
  (`_run_local_termination(..., vertex_exclusions=True)`); unit test
  `test_bl_local_termination_decoupled_vertex_on_healthy_cube` (fast
  suite 15/15 in the file); slow regression
  `tests/test_bl_collapsed_valve.py` (skipped if the tet backup at
  C:/polybench/valve1 is missing).

  Measurement (production converter kwargs, `n_layers=2`, h1=1e-5,
  dual_convexity, max_rounds=3; collapsed conversion ~105 s):
  - **success=True at scale 0.6**, 5 builds, ~297 s;
  - **4,008 excluded faces out of 42,130 (9.5%)**, **75,360 prisms**;
  - trajectory: 1.0 r0 fail (54 negative volumes) → r1 383 vs 370 →
    r2 373 vs 370 (fixed point of the vertex-closure: the 1.0 gate is
    NOT reachable); 0.6 r0 fail (38 neg) → r1 validates;
    `max_rounds=6` gives the same result (1.0 stalls regardless);
  - real checkMesh: **340 wrongly oriented faces = exactly the
    input (ZERO added)**, 10 non-ortho errors (= input), NOmax 95.6
    (= input), skew 27.5 (input 22.2), aspect 6,555 (input 100.8),
    "Failed 4" vs input "Failed 3" — the one extra check is aspect
    ratio, intrinsic to thin BL prisms.
  Compared to the per-face model on the same input: PHASE 7/8 failed
  at every scale (stalled at 373-385 vs 370, 2,189-2,207 exclusions).
  The earlier prediction (45% chance of closure, 2,300-2,900
  exclusions at 0.1) was **pessimistic on success and conservative on
  the count**: it closed with more faces (4,008) but at a thicker
  scale (0.6). No default changed: the mode is opt-in and documented.
  - **Interaction that should NOT be combined**: `early_exit_intermediates`
    would skip exactly the scale 0.6 that this mode uses to validate
    (1.0 exhausts without closing → the flag goes straight to 0.1).
    The combination has not been measured and is not recommended; the
    flag remains intended for cases where 1.0 is hopeless and the
    intermediates are useless (exact valve, fixture).
  - **No-op on healthy cases, verified**: exact cylinder with
    `decoupled_vertex` = identical to `decoupled` (scale 1.0, 0
    exclusions, 74,808 prisms, `Mesh OK`, 13.6 s); healthy cube in the
    unit test. On a defect-free mesh the vertex-closure collects
    nothing by construction (breaks on the first round).
  - **Generalisation to cube/groove1/slot1 (2026-09-11,
    `tools/h4_generalization_rebench.py`)**: prediction written before
    testing in `notes/h4_generalization_reasoning.md`, measured with
    real checkMesh via WSL, direct comparison of `decoupled` vs
    `decoupled_vertex` on the same input:
    - **cube_duct** (9 cells, 0 defects in the input): identical in
      every respect — 0 exclusions, 108 prisms, scale 1.0, `Mesh OK`.
      Prediction (no-op) **confirmed**.
    - **groove1** (682 cells, 3,372 faces): identical — 0 exclusions,
      10,062 prisms, scale 1.0, `Mesh OK`. Prediction (equivalent to
      `decoupled`) **confirmed**.
    - **slot1** (9,372 cells, 26,754 faces, 22 pyramid defects in the
      input): both close (scale 1.0, `Mesh OK`, 0 defects added), but
      **not identical**: `decoupled` excludes 132 faces →
      79,614 prisms; `decoupled_vertex` excludes **378** faces (~3x)
      → **78,903** prisms (-711). The prediction (0 extra exclusions,
      numerically identical to `decoupled`) is **contradicted**: the
      per-vertex cascade on slot1 propagates to more faces than the
      face-mode while still converging to the same qualitative outcome
      (closes, no defects added). No default changed;
      `decoupled_vertex` stays opt-in.
  - **Diagnosis of the ~3x on slot1 (2026-09-11,
    `tools/slot1_exclusion_inspect.py`)**: the initial hypothesis
    ("higher per-vertex valence on slot1" — more incident faces per
    vertex → wider per-vertex propagation) is **contradicted**.
    Measured the wall vertices' valence on the converted dual (number
    of incident wall faces per vertex) on each of the three meshes:

    | geometry | n wall verts | mean | max | p50 | p90 | p99 |
    |---|---|---|---|---|---|---|
    | cube_duct | 38 | 3.79 | 5 | 12.0 | 28.0 | 31.6 |
    | groove1 | 3374 | 4.00 | 9 | 248.0 | 1854.6 | 2208.7 |
    | slot1 | 26756 | 4.00 | 9 | 261.0 | 14271.2 | 17483.1 |

    Mean valence (4.0 vs 4.0 vs 3.79) and max (9 vs 9 vs 5) are similar
    between slot1 and groove1 (the cube-duct difference is irrelevant
    to the comparison: it's the "no-op" case). So the cause of the
    ~3x exclusions on slot1 is NOT local valence but **absolute
    scale**: slot1 has ~7.9x more wall vertices than groove1 (26756 vs
    3374), and the vertex-mode cascade propagates the exclusion to
    every vertex incident to the defective faces (1-ring closure).
    More vertices → more surface in the closure → more excluded faces,
    even with similar valence. The observed factor (~3x) is about 38%
    of the vertex ratio (7.9x) because the closure only captures the
    vertices incident to the defects (22 input → ~22 × valence ≈ ~100
    captured vertices), not all 26756. The 2.86x ratio between the
    excluded sets (378 vs 132) is the local projection of per-vertex
    propagation onto the defect sub-region, not an effect of the
    vertex-mode model on the rest of the mesh. Conclusion: the vertex
    model does its job (closes the mesh, Mesh OK), but on inputs with
    a high density of boundary vertices (narrow channels, slits), the
    amplification is proportional to the number of vertices incident
    to the closure — not a bug in the vertex criterion, a property of
    the local topology. No default changed.
  - **GUI wiring as an opt-in option (2026-09-11)**: `decoupled_vertex`
    is now reachable by the user — a "Advanced closure for concave
    geometries (experimental)" checkbox in the Boundary Layers panel
    (`gui/params_panel.py`, `get_bl_params()["concaveClosure"]`), off
    by default. When checked, `core/openfoam_runner.py`
    (`DualPolyWorker`) passes `local_termination="decoupled_vertex"` to
    the engine instead of leaving it unchanged (`False`). No other
    parameter is exposed (`concavity_criterion` stays at the default
    `"angle_fade"`, not the `"dual_convexity"` used in the PHASE 9
    benchmark). **Real end-to-end validation** (not mocked —
    `DualPolyWorker.run()` called directly on the production valve, the
    same code path as the GUI, checkMesh via WSL): 75,200 prisms
    (2 layers), 340 wrongly oriented faces = **exactly the pre-BL
    baseline** (zero added), 10 non-ortho errors (= baseline), NOmax
    95.6 (= baseline), "Failed 4 mesh checks" (= expected, the concave
    defect is a known limitation of the conversion, not introduced by
    the BL). In other words: **even with the default `angle_fade`
    criterion** (not the one used in the original benchmark) the
    vertex-cascade still closes the mesh without adding defects — the
    per-round validation loop is based on direct checkMesh checks
    (volumes/pyramids), not just the normal fade, so the initial
    criterion matters less than expected. Test coverage:
    `tests/test_bl_gui_wiring.py` (checkbox off-by-default, propagation
    to `get_bl_params()`), `tests/test_dual_poly_options.py`
    (end-to-end propagation, mocked, down to `local_termination` in the
    engine). No default changed.

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

- The frozen EXE is rebuilt on demand (`pyinstaller --clean --noconfirm PolyFoamMesh.spec`). The `dist/` artifact is not refreshed automatically
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
  commit `2c87293`, "rimossi dal tracking i binari grandi"), so it does
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
(FASE 9): none of the individual techniques used there turned out to be
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

## Test Matrix

- Fast (no WSL) suite: green.
- Real WSL/OpenFOAM smoke (GMSH tet -> gmshToFoam -> dual poly -> checkMesh):
  `Mesh OK` on the reference cylinder case.
- Full WSL regression runs are not part of the fast suite; they require
  OpenFOAM v2512 in WSL2 and are executed manually or via the harnesses in
  `tools/`/`tests/bench_*`.
