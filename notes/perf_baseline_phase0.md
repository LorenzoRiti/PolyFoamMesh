# Poly pipeline performance baseline — Phase 0 (instrument & measure)

Date: 2026-10-10. Scope: **instrument and measure only** — no optimisation, no
behaviour change, no default change. This document is the evidence table the
next phase (and the user) decides from.

Instrumentation added (logging only, output-transparent):

* `POLYFOAMMESH_PROFILE` env switch (no UI): wraps `TetPolyDualConverter.run()`
  and `PolyBoundaryLayerEngine.run()` in `cProfile` **from inside
  `DualPolyWorker`** (the engines themselves are untouched). Dumps
  `profile_dual.prof` / `profile_bl.prof` into the case dir and logs the top-20
  functions by cumtime as `[perf] dual hot:` / `[perf] bl hot:` lines. No-op
  when unset; a profiling failure is caught and never crashes the run.
* A per-run `[perf] stage times: ...` line covering every stage with
  `perf_counter`: GMSH (wall + mode normal/robust), gmshToFoam conversion,
  dual build + its own polyMesh write, BL precompute/build/reflow(if in
  stats)/total, checkMesh, decomposePar, and the poly-worker total. Each
  instrumented worker emits the line for the stages known so far.
* Adaptive-probe hook in `gmsh_wrapper._sample_passage_thickness_field`:
  `[perf] adaptive boundary probe (_extract_boundary_trimesh): Xs`.

Output transparency verified: on `tube`, the SHA-256 of the whole
`constant/polyMesh` tree is identical with profiling OFF and ON
(`a631350717ebfc33…`), and across two consecutive runs.

## Method

* App path: `gmshToFoam`/`checkMesh`/`decomposePar` run in WSL2 (OpenFOAM
  v2512); GMSH runs in the Windows interpreter; dual + BL run in-process in
  `DualPolyWorker` exactly as the GUI reaches them.
* The three small/medium cases were measured from their existing
  `constant/polyMesh_tet_backup` (their `mesh.msh` is reused, GMSH not
  re-run) with the app's `get_bl_params()` block
  (`nLayers=5, thicknessRatio=1.2, firstLayerThickness=0.000637,
  applyToAll=False, minBaseEdgeFraction=0.9, terminateByBudget=True`).
* The profile-on hot-function numbers are cProfile runs: the profiler inflates
  the wall times ~1.3–1.5× (e.g. tube dual 2.5 s → 4.0 s, BL 27.9 s → 37.1 s),
  so compare hot-function *shares*, not the profiled totals.

## Stage tables (perf_counter, seconds)

### tube — `gmsh_direct_20261008_103202` — 132,535 tets → 15,419 dual cells, 48,860 prisms

| stage | s |
|---|---|
| dual | 2.5 |
| dual polyMesh write | 0.2 |
| BL precompute | 11.7 |
| BL build | 13.4 |
| BL total | 27.9 |
| poly worker total | 31.2 |
| checkMesh | 6.8 (PASS, 64,279 cells) |

### cone — `gmsh_direct_20261009_085751` — 218,987 tets → 24,286 dual cells, 69,615 prisms

| stage | s |
|---|---|
| dual | 4.4 |
| dual polyMesh write | 0.3 |
| BL precompute | 19.1 |
| BL build | 24.2 |
| BL total | 47.7 |
| poly worker total | 53.3 |
| checkMesh | 6.8 (PASS, 93,901 cells) |

### venturi (Parte4-derived case) — `gmsh_direct_20261008_103936` — 204,533 tets → 21,798 dual cells, 36,640 prisms

| stage | s |
|---|---|
| dual | 7.3 |
| dual polyMesh write | 0.2 |
| BL precompute | 16.4 |
| BL build | **152.4** (engine fell back to scale=0.35/clamp=0.2, 8 validation attempts, coverage 61 %) |
| BL total | 179.1 |
| poly worker total | 187.6 |
| checkMesh | 6.7 (FAIL, nonOrtho 173.9, skewness 20.1) |

The fixed h1 (0.000637, the cone's value) is too large for this geometry, so
the engine walks its per-scale fallback chain — the 152 s build is the cost of
8 attempts, not of one clean build. It is still a valid upper bound for the
fallback path.

### Parte4 COARSE — `Desktop\Report\Parte4.stp`, GMSH robust path

| stage | s |
|---|---|
| GMSH (robust, HXT 3D) | 94.6 (mode: robust) |
| gmshToFoam convert | 33.5 |
| dual | 275.7 (845,303 cells) |
| dual polyMesh write | 12.9 |
| BL | **not run** (probe stopped after the dual) |

BL on 845,303 dual cells ≈ 6.8 M faces — under the 8 M-face guard, so it was
*not* blocked by the guard; it was deliberately not attempted because the
machine already spent ~4 GB on the 0.85 M-cell dual and BL memory scales with
faces (the known ~5 M-cell / 41 M-face run exhausted 32 GB). Reporting the
dual-only result is the honest, safe choice the task allowed.

## Hot functions (cProfile, top by cumtime)

dual, tube (profiled 4.0 s): `_build_dual` 2.05, `smooth_dual_mesh` 1.14,
`_build_boundary_collapsed` 0.78, `_emit_edge_face` 0.55 (86,694 calls),
`_newell_dot` 0.49 (96,713 calls), `cross` 0.49, `_walk_edge_fan` 0.26.

dual, venturi (profiled 9.9 s): `smooth_dual_mesh` 5.74, `_build_dual` 2.88,
`_face_geometry` 1.76, `_face_extent` 1.57, `_face_planarity` 1.46,
`_detect_defects` 1.30, `_emit_edge_face` 0.83 (130,137), `_newell_dot` 0.73
(142,274), `_walk_edge_fan` 0.41 (130,137).

BL, tube clean path (profiled 37.1 s): `_build_precompute` 17.5, `_build` 15.2,
`_consume_reflow` 9.0, `_consume_support_layers` 8.5, `builtins.min` 5.9
(509,642 calls), `norm` 5.0 (3,227,711 calls), `_consume_cell_min_edge` 4.0,
`<genexpr>` 3.6 (1,755,393), `_consume_excluded_faces` 3.2,
`_solve_global_windings` 2.2.

BL, venturi fallback (profiled 212.8 s, 8 attempts): `_build` 175.7,
**`_consume_reflow` 124.3 (tottime 124.1 — pure-Python Jacobi/min-diffusion)**,
`_build_precompute` 22.7, `_solve_global_windings` 20.0, `_consume_support_layers`
12.7, `_emit_side` 12.1 (1,009,665 calls), `_validate` 9.8, `_newell` 8.6
(1,649,656), `_mean` 7.0 (1,784,390), `norm` 6.4 (3,772,517),
`_build_face_parity_graph` 6.5.

The dual hot functions are the Python face-assembly / smoothing loops; the BL
hot functions are the per-vertex precompute plus (on the consume path) the
`_consume_reflow` Jacobi solve and the per-vertex norm/min generator expressions.

## Adaptive vs manual sizing (isolates `_extract_boundary_trimesh`)

Geometry: `Desktop\Report\Parte2.stp` (the cone case source), detail `medium`.

| run | wall | nodes |
|---|---|---|
| adaptive (`user_lc=0`) | **159.7 s** | 100,709 |
| manual, resolution-matched (`user_lc=0.005834`) | **11.5 s** | 74,174 |
| delta | **148.2 s (~13.9×)** | |
| isolated `_extract_boundary_trimesh` probe | **15.1 s** | |

The adaptive path costs ~148 s more than an equivalent manual mesh, but the
**2D probe is only ~15 s (~10 %)** of that. The remaining ~133 s is the adaptive
size-field construction/relaxation and the finer mesh it produces, not the
probe. **Optimising `_extract_boundary_trimesh` alone recovers ~15 s, not the
whole adaptive overhead.**

The benchmark `benchmarks/geometries/venturi.step` could **not** be run
adaptive without hanging: the adaptive surface mesher entered a runaway
refinement loop (invalid elements 354 → 1,792 → 3,388, no convergence) and never
produced an output `.msh` (killed after ~40 min). Evidence: no `$Nodes`/`$Mesh`
in any written file and the monotonically growing invalid-element warnings. The
real cone CAD (`Parte2.stp`) runs fine, which is why it is used above.

## Verdict per candidate

| # | candidate | measured s | est. recoverable | risk | call |
|---|---|---|---|---|---|
| 1a | adaptive probe (`_extract_boundary_trimesh`) | 15.1 (probe); adaptive total +148 vs manual | ~15 s (probe); ~130 s is elsewhere in the adaptive path | low | **NOT WORTH IT as scoped** — only ~10 % of the adaptive overhead |
| 1b | BL precompute (per-vertex Python) | 11.7 / 19.1 / 16.4 | ~60–75 % → ~8–14 s on these, ~15 s on 27.8k-vertex production runs | med | **WORTH IT** — steady, local, vectorisable |
| 1c | dual converter (Python face assembly + smoothing) | 2.5 / 4.4 / 7.3 small; **275.7** (Part4, 845k cells) | 50–70 % on large → ~100–190 s on Part4 | med-high (meshing logic) | **WORTH IT for large meshes** — the dominant poly stage there |
| 1d | GMSH threads | not A/B'd; Part4 robust single run 94.6 | unknown | low (env only) | **NOT MEASURED — low priority** (GMSH ≪ dual on the large case; benchmarks already pin threads=1 for determinism) |
| 5 | reflow / consume (`_consume_reflow`, `_consume_support_layers`) | 124.3 + 12.7 (venturi fallback); 9.0 + 8.5 (tube clean) | 70–90 % → up to ~100 s on fallback paths | high (physics) | **WORTH IT** — biggest single BL win when consume is on (default) |
| 6 | polyMesh writer (`write_polymesh`) | 0.2–0.3 small; 12.9 (Part4) | ~50 % → ~6 s on Part4 | low-med | **MINOR** — only worth touching for very large meshes |
| 7 | meshio | 0 in production poly path | 0 | low | **NOT WORTH IT** — confirmed absent: the direct-GMSH poly path uses `gmshToFoam` (WSL); meshio lives only in `commercial/mesh_engine.py`, `commercial/poly_aggregator.py` and `core/mesh_export.py` (export) |

Summary of where the time actually is:

* Small/medium poly (15–24k dual cells): the dual is trivial (2.5–7.3 s); **BL dominates** (28 s clean, up to 179 s on the fallback path). Levers: BL precompute (1b) and consume/reflow (5).
* Large poly (Part4 coarse, 845k dual cells): GMSH 94.6 s + convert 33.5 s + **dual 275.7 s**. The dual converter (1c) is the dominant poly stage.

## Not completed / caveats

* **1d GMSH threads** was not A/B'd — no measured seconds submitted. The task's
  benchmark path already forces `threads=1`; a dedicated Delaunay-3D vs HXT
  threading A/B is needed before any call.
* **Parte4 BL** was intentionally not run (RAM risk on an 845k-cell dual);
  only the dual stage is reported.
* `decomposePar` did not run on any case (no case had a `decomposeParDict`
  set up); its `[perf]` hook is in place and no-op until used.
* The profile totals are cProfile-inflated; use the shares, not the totals.
