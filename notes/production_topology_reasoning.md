# FASE 7 — BL on the PRODUCTION topology (dual collapsed) vs exact

Date: 2026-09-09. Written before any run. Question born while reading
`openfoam_runner.py:1652` while looking for the next problem to measure.

## The fact (verified in the code, not assumed)

- Production (GUI, poly path) converts tet->poly with
  `TetPolyDualConverter(case, collapse_smooth_edges=True,
  boundary_feature_angle=40.0, collapse_volume_tolerance=0.10)`
  (`core/openfoam_runner.py:1652`).
- ALL the FASE 2-6 BL measurements (valve fixture, tool benches,
  stage2e/stage2f) use `TetPolyDualConverter(case, log=...)` with the
  DEFAULTS: `collapse_smooth_edges=False` → "exact" dual, three quads per
  boundary triangle. The fixture `tests/fixtures/valve_dual.npz` was
  generated the same way (`tools/poly_fixture_builder.py:67`, default).
- Consequence: no BL measurement so far has been made on the topology that
  the app actually delivers. The collapsed dual has ~3-6x FEWER boundary faces
  (one polygon per boundary vertex, with feature edges kept) and
  `bl_poly` extrudes ONE stack of prisms per boundary face → prisms,
  exclusions and defects must be revisited.

## Predictions (to be compared with the numbers)

Valve (same tet backup `C:/polybench/valve1`, same protocol
n_layers=2, h1=1e-5, dual_convexity, apply_to_all=True):

1. **Dual cells**: identical between exact and collapsed (the collapse only
   affects the boundary faces) — expected ~152,086 for both.
2. **Boundary faces**: exact ~226,542 (26% of total faces); collapsed
   predicted **40,000-80,000** (ratio 3-6x, like the user case
   462,846 → 80,111, 5.8x).
3. **Converter volume drift (collapsed)**: < 1% (measured
   -0.19%/-0.46% on the venturis, -0.00% on the user mesh). The EXACT is
   ~1e-16.
4. **Input defects (face pyramids, in-process)**: UNCERTAIN. The pyramid
   test looks at faces (including the boundary polygons) against the
   cell centroid; merging 3 quads into 1 polygon can change
   the outcome. Wide prediction: **600-1,400** (exact = 1,007, measured).
5. **BL `decoupled`+`early_exit` on the collapsed**: prediction
   **success at scale 0.1 (70% confidence)**, exclusions
   **< 8% of boundary faces** (in face units it will be a small number,
   ~1,000-5,000, because the faces are 3-6x fewer), prisms
   ~2x the remaining faces; checkMesh "wrong oriented" **not worse than
   895** (50% confidence: the polygon merge can shift the
   count in either direction).
6. **Time**: collapsed conversion slower than exact (polygon
   walk), BL faster (fewer faces to extrude). Total estimated
   10-25 min for the valve.

Cylinder (GMSH, protocol n_layers=3, h1=0.005):

7. Exact boundary faces ~24,936 → collapsed predicted **~4,000-6,000**;
   BL success at scale 1.0 with 0 exclusions; prisms ~3x remaining faces;
   `Mesh OK` in both.

## Why it matters

If the BL works on the collapsed dual too, the FASE 2-6 measurements
transfer to production (with different numbers but the same story).
If it does NOT work (or degrades), then the `local_termination` mode is
tuned on a topology the app does not use, and the production
poly+BL path on concave geometries is effectively not validated — a critical
negative result to write immediately.

## Reading criterion

- Three-level comparison: converter statistics (boundary faces, drift),
  input defects, BL outcome (success, scale, exclusions, prisms, time,
  real checkMesh).
- If the collapsed does not close even with early_exit: try `decoupled`
  alone and, if it fails too, document the failure with the numbers
  (and do NOT touch the engine in this phase).

# RESULTS (filled in after the runs, 2026-09-09)

## Measurements

| | exact (default) | production (collapsed) |
|---|---|---|
| cylinder boundary faces | 24,936 | **4,412** (5.65x) |
| cylinder drift | 1.4e-16 | 0.128% |
| cylinder BL early_exit | scale 1.0, 0 excl, 74,808 prisms, 13.0 s, **Mesh OK** | scale 1.0, 0 excl, 13,236 prisms, 5.6 s, **Mesh OK** |
| valve boundary faces | 226,542 | **42,130** (5.4x) |
| valve input defects (wrong) | 863 | **340** |
| valve drift | 3.4e-16 | 1.08% |
| valve BL | **success scale 1.0** (2 builds): 2,525 excl, 444,924 prisms, 217 s; checkMesh 836 wrong (< 863), skew 38.1, aspect 1,543, Failed 4 | **FAILS**: decoupled 15 builds, 892.7 s, 2,189 excl, stall 375-444 pyr vs 370 input |

## Predictions vs measurements (honest)

- P1/P2 (identical cells; boundary faces 40-80k): ✓ (152,086 cells; 42,130 collapsed).
- P3 (drift < 1%): ✓ (1.08% valve is just above; 0.128% cylinder).
- P4 (defects 600-1,400): ✗ — measured **340** (the collapse REDUCES the input defects).
- P5 (BL closed at 70%): ✗ — **fails** on the production topology; the
  predicted 30% materialized.
- P6 (time): collapsed conversion faster (100-115 s vs 137-140 s),
  BL faster on healthy cases; on the production valve the decoupled BL
  spent 892.7 s to fail.
- P7 (cylinder): ✓ identical to the prediction.

## Side discoveries (more important than the main result)

1. **The valve fixture is stale** (2026-08-01): 895 wrong / 55 non-ortho err. /
   skew 44.3 vs fresh conversion 863 / 9 / 14.6. All the FASE 2-6 valve numbers
   are pinned to that snapshot; on the current converter the
   exact valve validates at **scale 1.0** with 2,525 exclusions (not at 0.1
   with 2,058).
2. **FASE 6 early-exit bug**: the trigger fired at 1.0 round 0 and
   also skipped rounds 1-2 of the requested scale; masked by the
   stale fixture, it was the cause of the "degeneration" on slot1. Fixed:
   the trigger fires only when scale 1.0 is exhausted. Re-measure valve exact:
   success 1.0 round 1, 217 s (identical to legacy).

## Conclusion

- The production topology (collapsed) works on healthy cases but does **NOT**
  support the BL on the valve with the current local termination.
- Before any other valve work: regenerate the fixture and
  re-pin the numbers; then a dedicated lane for the exclusion→polygons
  mapping (or mixed granularity).
- No default changed; the early-exit correction is internal to the opt-in
  mode.
