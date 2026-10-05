# POLY NON-ORTHO (dual-side: dissolve tiny boundary stars) — reasoning (2026-10-05)

Written BEFORE any mesher change (zero production code touched by this note).
Lane: NON-ORTHO, **poly path only** — our own GMSH-tet → barycentric dual
engine (`core/tet_poly_dual.py`), not cfMesh.

Gap rows from `benchmarks/results/GAP_ANALYSIS.md` (numbers verbatim):

- **Row 8** — concave_bracket / poly / threshold miss / `non_ortho_max`:
  us **105.41**, best snappy **64.76** (also fails `skewness_max` 4.37).
- **Row 10** — cube_duct / poly / threshold miss / `non_ortho_max`:
  us **80.64**, best snappy **46.21** (also fails `skewness_max` 4.92).
- **Row 16** — venturi / poly / threshold miss / `non_ortho_max`:
  us **146.41**, best snappy **60.26** (worst absolute in the baseline).
- Relative losses: rows 18/20/22/25/26/27 (cylinder/groove1/slot1 non-ortho
  and skew vs snappy).

A-side baseline: `benchmarks/results/comparison_20261005T113621.json`
(commit `b1bc71f`); fast-tier valid baseline `comparison_20261005T135839.json`.

## Read-only probes BEFORE the prediction (no repo changes)

### 1. Dual vs dual+BL isolation (benchmark settings, exact re-run)

| geometry | dual only | dual + BL | BL prism delta |
|---|---|---|---|
| cube_duct | **51.87 PASS** (skew 3.93, aspect 7.5) | 80.64 FAIL (skew 4.92, aspect 554.2) | +28.8 |
| venturi | 74.00 FAIL (skew 4.02) | **146.41** FAIL (aspect 108.7) | +72.4 |
| concave_bracket | 62.66 + 36 face-pyramids | 105.41 FAIL (aspect 566.6) | +42.8 |

Average non-ortho is essentially FLAT across BL (+0.0 to +0.6 deg): the
damage is a handful of extreme faces, not a global quality shift.

### 2. BL configuration matrix (existing opt-ins, read-only probe)

- Production `poly_bl_patch_selection`: **no-op** (146.41 / 80.64 / 105.41
  unchanged) — the bad prisms are on wall patches, not inlets.
- `local_termination="decoupled_vertex"` + `local_height_retry=True`:
  146.41 → 85.44, 80.64 → 81.29, 105.41 → 88.51 — better, still FAIL, and
  aspect on concave worsens (566.6 → 1145.8).
- `first_height` × 10: no effect (the per-vertex inversion clamp binds).

### 3. Worst-face localization (in-process checkMesh replica)

- The gate-breaking faces are **tiny** (area 1e-8…2.4e-6 m²) shared faces
  between a **sliver cell** (volume 1e-12…5e-11, 5-6 faces = prism) and a
  larger cell, with centre-to-centre distance 10-100× the face size.
- Venturi dual boundary face areas: min 4.97e-8, p1 2.85e-7,
  median 2.36e-5; **83 of 2059 boundary faces < 1e-6 m²**.
- The DUAL-ONLY venturi max (74.00) has the same signature: its four
  >70° faces are all tiny-face pairs (areas 1.8e-7…4.2e-7); the fifth
  worst is 67.53. Same on cube_duct (the 78.6° family sits on 1e-8 m²
  faces) and concave_bracket.

### 4. Spike: post-hoc cell+polygon merge is NOT the fix (negative result)

A throwaway prototype merged each tiny boundary polygon into an adjacent
same-patch polygon (loop union) and merged the two dual cells:

- with a per-merge area-validity gate: 9 of 83 merges accepted;
- the merged dual measured **worse** than unmerged (NOmax 96.73 vs 74.00,
  skew 15.28 vs 4.02): merging the cells enlarges the centre distance |d|
  against the tiny faces that remain, exactly the effect
  `core/poly_cell_merge.py` already documented for pre-BL merges.

**Consequence for the design:** the tiny boundary polygons must be
eliminated *by construction* (no tiny face left behind), not merged
after the fact. That means dissolving the tiny vertex stars **inside the
boundary walk**, where the survivor's polygon covers both stars.

## Hypothesis

- `core/tet_poly_dual.py::_build_boundary_collapsed` emits ONE boundary
  polygon per primal boundary vertex: the rotational walk around the
  vertex through its incident boundary-triangle centroids (with midpoints
  only at feature edges).
- A primal boundary vertex whose incident triangle star is tiny (fillets,
  feature points, sliver tessellation) therefore gets a tiny polygon:
  venturi has 83 sub-1e-6 m² boundary faces, min 4.97e-8 m².
- `core/bl_poly.py` extrudes EVERY selected boundary face into a prism
  stack. On a tiny base the prism is a sliver (volume ~1e-11..1e-12).
  Its shared face with the adjacent core cell is tiny relative to the
  centre-to-centre distance, which is exactly what the non-orthogonality
  angle measures — hence 80-146°, aspect 100-560, while the average stays
  flat.
- The dual itself is already marginal/failing on venturi for the same
  reason; the BL amplifies the same defect class by 1-2 orders of
  magnitude in the reported MAX.

## Fix being predicted: walk-level dissolution of tiny boundary stars

New opt-in on `TetPolyDualConverter` (name provisional):
`dissolve_tiny_boundary_stars: float = 0.0` — threshold as a fraction of
the MEDIAN boundary-star area (0 = off, byte-identical default).

- Before the walk, compute each boundary vertex's star area (sum of its
  incident boundary triangle areas); vertices below
  `threshold × median_star_area` are marked dissolved.
- A dissolved vertex `v` is assigned to an adjacent boundary vertex `u`
  across a SMOOTH boundary edge (no feature midpoint), same patch,
  preferring the largest star. The walk for `u` splices `v`'s walk at the
  two shared triangles (the union polygon), so no tiny polygon survives.
- The dual cell of `v` merges into `u` (drop their shared internal face,
  remap owner/neighbour); unused points are dropped and renumbered.
- Validation before acceptance: closure, positive volumes, face-pyramid
  non-regression, manifold patches — keep-best against the unmerged dual
  with the in-process `_detect_defects`; on any failure the merge is
  refused (mesh stays byte-identical). Default off; no production caller
  changes.

## Prediction (write BEFORE the experiment)

- **cube_duct**: dual ~unchanged (51-53); final dual+BL **52-58 PASS**
  (was 80.64), aspect ≤30 (was 554), skew ≤4.0 (was 4.92).
- **venturi**: dual 74.00 → **65-69** (its only >70 faces are the four
  tiny-face pairs; next worst 67.53); final dual+BL **66-72**, PASS or
  borderline (was 146.41).
- **concave_bracket**: non-ortho **63-70** (was 105.41) but the 36 input
  face-pyramids remain → checkMesh still reports Failed; poly stays FAIL
  there (pyramid tier is a separate defect class, not this lane).
- **Healthy geometries** (cylinder, groove1, slot1, thin_gap, multi_inlet):
  no sub-threshold stars → dissolution does not fire → identical metrics.
- **Poly pass rate: 4/10 → 6/10** (cube_duct + venturi; concave still
  pyramid-blocked), matching snappy_base/expert's 6/10.
- **Cost**: ≤5% dual cells removed on the gap geometries; wall time
  +10-20% (walk + validation); zero on healthy geometries.

## Measurement plan

- **A-side (current code)** — measured (this note): dual-only isolation
  numbers above + committed baseline `comparison_20261005T113621.json`.
- **B-side (with fix)** — opt-in flag on the poly runner (e.g.
  `--poly-sliver-dissolve`), same commands:
  `benchmarks/run_comparison.py --tier fast --geometry cube_duct --geometry venturi --runner poly --poly-sliver-dissolve`
  and the full-tier `concave_bracket` row; then the full fast tier and the
  full-tier poly runner for regressions.
- **Kill criterion**: if cube_duct does not PASS (<70 non-ortho, skew <4,
  aspect <1000), or venturi improves by <5° from 74.00, or any healthy
  fast-tier geometry regresses (>2° on the primary metric, or a passing
  row starts failing), the lane is killed and documented as a measured
  negative result (repo precedent).

## Status

- [x] Prediction written (before any mesher change)
- [x] A-side measured (baseline + isolation/config/localization probes)
- [x] Fix implemented (follow-up plan — walk-level dissolution, opt-in, default off)
- [x] B-side measured (refused; no metric moved)
- [x] Verdict: KILLED — not promoted; no default changed

## B-side (with walk-level star dissolution, --poly-sliver-dissolve)

Measured 2026-10-05 (commits ef0901d..3060e26; artefacts committed in 0bcf86e).

| geometry | A-side NOmax | B-side NOmax | cells | wall s | pass |
|---|---|---|---|---|---|
| cube_duct | 80.64 | 80.64 | 18258 | 13.26 | FAIL (identical) |
| venturi | 146.41 | 146.41 | 4732 | 7.92 | FAIL (identical) |
| concave_bracket | 105.41 | 105.41 | 35097 | 39.82 | FAIL (identical) |

The dissolution FIRED on all three geometries (171 / 97 / 163 sub-threshold
stars at fraction 0.5) but EVERY build was REFUSED batch-wide by the
keep-best gate, so the unmerged dual was kept and every metric is
byte-identical to the A-side:

- cube_duct: 'a cell is not closed' (relative closure 2.94e-1)
- concave_bracket: 'a cell is not closed' (relative closure 3.74e-2)
- venturi: 'pyramid non-regression failed (84 vs 15)' after 3 per-vertex
  splice refusals
- retry at fraction 0.25: same refusals (cube_duct 30 stars, closure 2.48e-1;
  venturi 54 stars, 44 vs 15 pyramids); concave_bracket had zero candidates.

VERDICT: **KILLED** (measured, not promoted). The keep-best gate did its
job � no worse mesh was ever written � but the walk-level dissolution as
implemented does not produce closed merged cells on real geometry: the
single-star cube fixture closes (1e-15), while real batches do not.

Refusal provenance: the star counts and refusal reasons above are
transcribed from the run logs (logs are not committed); the committed
B-side JSONs carry `warnings: []` because `_benchmark_poly_one` did not
propagate `DualPolyResult.warnings` into the record at the time of the run
(now fixed in the harness for future runs). What the committed artefacts
DO prove independently: the B-side `cell_count` is identical to the A-side
baseline on all three geometries, and a successful dissolution compacts
absorbed cells away — so no merged mesh was ever written.

Failure modes scoped for a possible follow-up:
(a) refined root cause (code-derived, not yet empirically isolated): after
    a splice, `feat_idx` is recomputed from the UNION of all feature
    midpoints (`_build_boundary_collapsed`). A non-corner survivor starts
    with ≤2 midpoints (chords cancel pairwise), but absorbing a tiny star
    that sits at a feature point contributes 3+, and with nf ≥ 3 at a
    non-`feat_vertex` loop the arcs' closing chords no longer cancel —
    the emitted sub-faces' area vectors stop summing to the loop's Newell
    vector, giving the measured closure deficits (2.9e-1 / 3.7e-2). The
    cube fixture passes because cube corners ARE feature points (fan
    branch, exact cancellation). This can fire on a SINGLE splice, so the
    earlier "multi-star interaction" label was imprecise;
(b) the union validity gate (`_loop_is_simple`) is projection-based and
    blind to collinear/touching edges on non-planar stars;
(c) the merged cell can become non-convex even when the union is simple
    (pyramids: 84 vs 15 on venturi).

Revival checklist (before the flag is ever promoted): gate or fix the
nf ≥ 3 arc split at non-feature vertices; add the keep-branch test (merged
mesh actually written with the pyramid gate active); reuse the pyramid
baseline instead of rebuilding it twice; keep the harness `warnings`
propagation so future B-side records are self-evidencing.

- [x] Fix implemented (walk-level dissolution, opt-in, default off)
- [x] B-side measured (refused; no metric moved)
- [x] Verdict: KILLED � not promoted; no default changed
