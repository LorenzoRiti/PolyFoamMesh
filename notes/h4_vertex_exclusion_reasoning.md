# H4 — per-VERTEX exclusion mapping (plan + prediction)

Date: 2026-09-09. Written BEFORE implementing and measuring.
Context: FASE 7/8 showed that the BL on the production topology
(collapsed) of the valve does NOT close with the current per-FACE exclusion
model:
- baseline (decoupled, max_rounds=3, widen=0): fails at 0.1 r2 with
  385 pyr vs input 370, 2,189 exclusions;
- max_rounds=6: plateau at 379 (0.1 r3-r5), 2,207 exclusions;
- widen=1 (expansion of 1 ring of the per-face set): IDENTICAL to widen=0
  (2,207 exclusions, same trajectory) — the per-face cascade reaches
  the same fixed point with 6 rounds, so widening by one ring adds
  nothing to the fixed point;
- H2: 360/370 of the input defects are on boundary faces (excludable).

## Why a per-vertex model (rationale)

In the collapsed dual a boundary face is a polygon ~= the star of
a primal boundary vertex; its VERTICES (dual vertices on the boundary) are
shared with the neighboring faces. The current model excludes whole faces and
propagates one round at a time; the shared vertex is the real unit of
topological sharing of the problem (a prism is defective because of how its
vertices are extruded, not because of the face itself).

"vertex-closure" semantics: I keep `excluded_verts` (wall vertices);
a selected face is extruded only if NONE of its vertices is
excluded. When a prism/core cell is invalid, I exclude ALL the vertices
of its base face (not the face). This makes the propagation
consistent with the topology: all faces incident to a bad vertex
fall in the same round, instead of one at a time in the iterations.

Design note: the change concerns ONLY the ITERATIVE exclusions; the initial
binary drop (`zero_concave`, fade < 0.5 / drop_bnd) stays per-face
unchanged, to isolate the measured effect.

## Hypotheses to verify

H4a — The ~9 residual pyr (379-385 vs 370) are attributable to a few faces
KEPT whose vertices are not (yet) excluded, but which are adjacent (by
vertex) to already excluded faces. In that case vertex-closure captures them and the
collapsed valve CLOSES.
H4b — The residuals come from faces whose vertices are NOT touched by
any exclusion (a defect mappable neither by face nor by vertex).
In that case vertex-closure does not change the fixed point and H4 fails like
widen=1: the problem is that the gate `n_pyr <= input` is not reachable
with the binary construction, and another route is needed (e.g. treating the collapsed
dual differently for the BL).

## Prediction (expected numbers)

- **P1 (closure probability)**: 45%. The widen=1 no-op suggests that
  the per-face fixed point is already "closed by adjacency"; vertex-closure
  coincides for the most part with that fixed point. The margin is small
  (9 faces), so it is not impossible, but the signal is weak.
- **P2 (if it closes)**: success at scale 0.1, effective exclusions
  (faces without BL) **2,300-2,900** (a little more than the 2,189 per-face,
  plus the extra incident faces), prisms ~2x the remaining faces; real
  checkMesh expected with "wrong oriented" <= 340 (input) — we cannot
  guarantee it but the in-process gate is the condition.
- **P3 (if it does not close)**: clean failure like the baseline, stall
  <= 385; the excluded set will be a SUPERSET of the per-face fixed point
  (2,207 <= excl <= 3,500) and the pyr count will not fall below ~379.
- **P4 (time)**: collapsed conversion ~2 min; BL run 3-6 builds
  (~60-100 s each) if it closes (~5-10 min), 15 builds (~15-18 min) if
  it fails. checkMesh +1 min.
- **P5 (healthy cases)**: cylinder/cube/groove/slot: vertex-closure = no-op
  (0 exclusions), same outcome as face-level; the fixed point coincides.
- **P6 (regression test)**: new `tests/test_bl_collapsed_valve.py`
  (marker slow; skip if `C:/polybench/valve1/.../polyMesh_tet_backup`
  is missing): converts collapsed (converter production kwargs), runs
  `local_termination="decoupled_vertex"` with the valve protocol
  (n_layers=2, h1=1e-5, dual_convexity), and pins the measured outcome:
  - if it closes: assert success, scale==0.1, exclusions in the measured
    range, prisms > 0, + mesh invariants (without WSL);
  - if it fails: assert clean-failure (errors non-empty, n_prism_cells==0,
    mesh byte-identical) as the slow fixture exact test does.

## Reading criterion

- If H4a: implement `decoupled_vertex` as a documented opt-in mode
  (NOT default), write the numbers in docs (FASE 9) and the
  regression test with the measured values. The BL-on-collapsed lane
  would have its solution with the production topology.
- If H4b: document the negative with the diagnostics of the residual
  faces (why the mapping is missing) and the structural conclusion:
  the binary construction does not close the valve on the collapsed dual; the
  production poly+BL path on concave geometries remains
  unsupported and must be decided at the product level (e.g. exact dual for the
  BL, or dedicated handling of the collapsed).

# RESULTS (filled in after the measurements, 2026-09-09)

## H4 SUCCESS — the production collapsed valve CLOSES

Measurement (`tools/bench_bl_production_topology.py --cases valve
--variants production --modes decoupled_vertex`, default max_rounds=3,
criterion dual_convexity; collapsed conversion ~105 s):

- **success=True at scale 0.6**, 5 builds, 296.8-297.6 s;
- **4,008 faces excluded** out of 42,130 (9.5%);
- **75,360 prisms** (2 layers x 37,680 covered faces);
- trajectory: 1.0 r0 (54 neg) -> r1 383 vs 370 -> r2 373 vs 370
  (rounds exhausted: the fixed point of the vertex-closure at 1.0 does NOT fall
  below the gate); 0.6 r0 (38 neg) -> r1 validates with the exclusion;
- real checkMesh: **340 wrong oriented (= input, ZERO added)**,
  10 non-ortho errors (= input), NOmax 95.615 (= input), skew 27.47
  (input 22.16), aspect max 6,555 (input 100.8), **Failed 4** vs input
  Failed 3 (the only extra check is the aspect ratio, intrinsic to the
  thin BL prisms as on the exact valve).
- max_rounds=6: identical (1.0 stops at the fixed point at r2 anyway;
  0.6 closes in 2 rounds). The gate at scale 1.0 is not reachable.

## Predictions vs measurements (honest)

- P1 (closure 45%): **YES, it closed** — the probability was too
  pessimistic; the "widen=1 no-op" signal did not imply the uselessness of
  vertex-closure (which at 0.6 captures in ONE round a set 4x larger).
- P2 (exclusions 2,300-2,900, scale 0.1): **measured 4,008 at scale 0.6**
  — more faces than predicted (9.5% vs 5.5-7% expected) but **a thicker
  scale** (0.6) and therefore a better boundary layer than predicted.
- P3/P4 (if it failed / times): not applicable; measured times 297 s
  (5 builds), consistent with the predicted range.
- P5 (healthy no-op cases): confirmed by the unit test on the healthy cube.
- P6 (regression test): implemented in
  `tests/test_bl_collapsed_valve.py` (slow, skip if the tet backup
  is missing), pins success, scale 0.6, excl=4,008, prisms=75,360;
  verified green in 406.6 s (6:46).

## Reading

H4a confirmed: the vertex-closure captures in a single round the rim that
the per-face model took more rounds to (not) close; the
decisive difference is that the vertex-closure at 0.6 excludes the faces
incident to the defective vertices ALL TOGETHER, while the per-face at
0.1 stopped 9-15 pyr above the gate. The mode stays OPT-IN
("decoupled_vertex"); no default changed. On the exact dual the
previous behavior does not change (same mode as the rest).
