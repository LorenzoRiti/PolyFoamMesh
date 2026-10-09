# FASE 3 — Reasoning and predictions (written BEFORE the tests)

Date: 2026-09-09. Written before launching any FASE 3 benchmark.
The predictions must be compared with the results at the end of the work; a
wrong prediction is a datum, not a failure.

## Available measured facts (from FASE 2)

| Fact | Value |
|---|---|
| Valve (dual) | 152,086 cells, 1,241,048 faces, 226,542 wall faces |
| Valve INPUT defects (face pyramids, in-process = checkMesh) | 1,007 (895 checkMesh) |
| Non-convex dual cells (dual_convexity) | 393 |
| Wall vertices with dual fade < 0.5 | 1,123 (0 with angle_fade) |
| Global winding, standard path, per scale | 0 not closed; neg volumes 44/23/22/32/29 (stage1c) |
| local_termination thick-first (engine, dual_convexity) | success at 0.1, 19,625 excluded (8.7%), 1602 s |
| local_termination scale 0.1 only (stage2e, fresh) | success, 2,060 excluded (0.9%) in 2 rounds |

Mechanics from the code (`bl_poly.py`):
- `zero_concave` discards a face if: `bi in drop_bnd` (the 1,007 ownership
  faces of already non-convex cells in the input) OR at least one of its
  vertices has `angle_fade < 0.5` OR `max_h <= 0`.
- `angle_fade[w] = fade * min(clamp_factor * support_d[w], 0.5 *
  min_edge[w])`; with `dual_convexity`, `fade = max(0.05, 1 -
  n_bad/n_owner)` per vertex.
- After each failure `_collect_exclusions` adds the faces whose
  prisms have volume <= 0 or violate the pyramid criterion (including the core
  cells with new violated faces); the exclusions carry across scales;
  max 3 rounds per scale, scales from the THICKEST (1.0) to the THINNEST
  (0.1).

## Q1 — Why 8.7%? Is it predictable a priori?

Prediction: the 19,625 are NOT real defects but for the most part the product
of two distinct layers:

1. **Predictable seed (a priori, without building anything)**: union of
   (a) the ~1,007 `drop_bnd` faces and (b) the faces with a vertex at dual
   fade < 0.5, i.e. the neighborhood of the 1,123 vertices. With ~2-6 faces per
   vertex, I expect **2,000-5,000 seed faces**, computable in
   advance with `_build_precompute` and no attempt.
2. **Cascade (not predictable without building)**: the faces at the EDGE of the
   excluded region, where the transition prism has some vertices
   extruded and others not. I predict the cascade dominates the total:
   **cascade = 19,625 - seed ≈ 15,000-17,000 faces**, concentrated in the
   rounds of the thick scales. Indirect evidence already available: at the
   warnings of the engine run, EVERY thick scale fails with ~1,010-1,019
   pyramids (input 1,007) DESPITE the exclusions already made — the
   defect REGENERATES at the edge of the excluded region (whack-a-mole);
   3 rounds x 3 thick scales x ~1-2k faces/round produce the order of
   magnitude observed.
3. **Geometric pattern**: the excluded ones lie at radius 0-2 faces from the
   1,007 defective input faces (concave junctions among the 89
   surfaces). Prediction: >= 90% of the excluded within 2 adjacency rings of a
   defective face; the rest within 3-4.

Predicted test: measure the exact seed, the composition of the final set by
adjacency rings, seed/final overlap.

## Q2 — Global winding vs binary termination: two different reliabilities?

Prediction: YES, they are two components with different natures.

- `_solve_global_windings` is an EXACT solver of a topological problem
  (orientability): O(F+E), deterministic, no parameter. It fails
  only on non-orientable meshes or with non-manifold edges, and in that case it
  DECLARES it (`n_conflicts`, `n_nonmanifold_edges`) instead of repairing.
  Prediction: on non-extreme geometries (cylinder, cube, groove, slot)
  it will do practically nothing (0 flips or few), because the closure
  problem does not arise.
- Binary termination is a geometric heuristic: the success depends
  on the local density of defects and on the layer scale. It is the only
  piece that scales with the difficulty of the geometry.
- So: **the winding alone is NOT enough on the valve** (already measured:
  neg volumes 22-44 per scale with the standard path), because the residual
  problem is geometric (inverted prisms), not orientational.
  On "less extreme" geometries the winding is probably sufficient
  because there is nothing to repair: the binary termination becomes a
  no-op at ~zero cost (it exits at the first attempt).

## Q3 — Why is the valve the worst case?

Prediction: because it is the only geometry in which the dual INPUT mesh
already violates the pyramid criterion (1,007 faces; cylinder/cube 0). The
mechanism is structural: a dual cell that wraps a concave CAD edge is
non-convex; extruding a thin layer from a wall above
that cell produces prisms that inherit the non-convexity (checkMesh
'face pyramids'). The number of concave edges and their density relative to
the mesh decide the difficulty.

**Proposed predictor (cheap):** count on a dual mesh, without
extruding anything, `pyr_input` (faces that violate the pyramid in the input) and
`n_wall_verts_fade_lt_half` (dual_convexity). Easy = 0/0; hard =
high/high. The valve = 1,007 / 1,123 is extreme. Prediction for the others:
cylinder 0/0, cube 0/0, groove1 a few tens/hundreds, slot1 the same.

## Q4 — Which geometries WILL FAIL? (prediction before the test)

I predict success (with 0-5% excluded) on:
- cylinder and cube: 0 excluded, outcome identical to the standard path.
- groove1 (L-shaped groove, 1 straight reentrant edge): success at the
  first or second scale, a few % excluded (persons around the edge).
- slot1 (slit): success, possibly more excluded than groove1 if the
  reentrant edge has more contiguous faces; < 10%.

I predict FAILURE (clean error, mesh unchanged) on:
- geometries with concave defects DENSER than the mesh: many reentrant
  edges 1-2 faces from each other (castings/molds with bosses, channel
  networks). The exclusion cascade does not close in 3 rounds and the edge
  mesh remains invalid.
- very coarse meshes around a concave edge: discarding 1-2
  faces removes ~all the support of the vertex (max_h <= h1 everywhere) and
  the engine reaches "dropped every selected face" / "excluded too many".
- geometries with degenerate dual cells (sliver) at the boundary: the binary
  criterion has no middle ground (1 layer) and will prefer the clean error.

And above all: I expect that **the order of the scales is a measurable
artefact**: starting from 0.1 (thin-first) the same valve should
close with ~2,000-3,000 excluded instead of 19,625 (ratio ~7-10x),
because the thick rounds exclude faces that at 0.1 would be valid. If
thin-first does NOT reduce the excluded, my explanation of the cascade is
wrong.

## Q5 — Time as a function of size

Prediction: the cost per attempt is ~linear in F (faces), dominated by
`_build_precompute` (~58 s on the valve, which has 1.9M faces in the built
mesh) + `_build` + validation. The TOTAL time = n_attempts x
cost_per_attempt. Healthy geometries: 1 attempt (scale 1.0), so
~0 overhead vs standard. Valve: 13 attempts (~123 s each) = 1602 s.
Prediction for the cylinder (~70k cells): ~10-20 s per attempt, 1
attempt.

## Reading criterion for the results

- If seed ~2k and thin-first ~2k: "the 8.7% is not intrinsic, it is an
  artefact of the order of the scales; the intrinsic cost of the binary
  criterion is ~0.9%".
- If the seed/final overlap is high and the rings are 0-2: the set is
  predictable a priori and one can replace the iteration with a computed
  drop (future work, NOT in this phase).
- If groove/slot pass with few excluded: the predictor pyr_input +
  low-fade-verts separates easy/hard. If they fail: the predictor must be
  refined (density of defects, not just count).

# RESULTS (filled in after the tests)

## Valve — prediction vs measurement

| Prediction | Measurement | Outcome |
|---|---|---|
| Predictable seed 2,000-5,000 faces | **2,737** (1.21%) | ✓ |
| Thin-first 2,000-3,000 excluded | **2,058** (0.91%) | ✓ |
| >=90% of the excluded within 2 rings of a defective face | **91.5%** (803+651+429 of 2,058) | ✓ |
| Cascade dominant in the 19,625 thick-first (~15-17k) | thin-first cascade = **230** (0.1%); the rest of the 19,625 is overreaction of the thick scales | ✓ in substance |

Measured details (thin-first run, scale 0.1 only, production exclusion
logic):
- round 0: 447,610 prisms, 2 negative volumes, 1,226 pyramids (input 1,007)
  -> 1,946 excluded, 114.8 s;
- round 1: 0 negatives, 1,012 pyramids -> +112, 90.7 s;
- round 2: 1,007 pyramids (= input), validate OK, +0, 99.4 s.
- checkMesh of the validated mesh: 597,802 cells, **885 wrongly oriented faces
  vs 895 input** (better), skew 47.6, aspect max 15,419, "Failed 4"
  (the fourth is the aspect ratio, intrinsic). Prisms preserved: 445,716 vs
  410,668 of thick-first (+8.5%).

Anatomy of the final set (2,058):
- 1,828 = seed (88.8%), 230 = pure cascade (11.2%);
- total seed 2,737: 66.8% actually ends up excluded (the seed
  over-approximates: 909 seed faces did NOT need to be excluded);
- composition: 803 inside drop_bnd (the defective input faces), 1,828
  touch a vertex with fade<0.5, 0 only drop_bnd;
- rings from a defective face: 0->803, 1->651, 2->429, 3->80, >3->95.
- the predictor costs 27.1 s (vs 114.8 s of the first build): the
  excluded set is computable a priori with 88.8% recall.

## Predictor on the other geometries (before the BL runs)

| Geometry | pyr_input | low-fade dual | seed | Prediction | Measurement |
|---|---|---|---|---|---|
| valve | 1,007 | 1,123 | 2,737 | hard | LT ok @0.1, 2,058 excluded iter |
| cylinder | 0 | 0 | 0 | no-op | **no-op confirmed** |
| cube duct | 0 | 0 | 0 | no-op | **no-op confirmed** |
| groove1 | 4 | 2 | 18 | few excluded | **ok @1.0, 18-28 discarded** |
| slot1 | 42 | 17 | 86 | few excluded | **standard FAILS; LT ok @1.0, 132-186 excluded** |

Honest note on a wrong prediction: I had predicted for groove1
"a few tens/hundreds" of low-fade vertices; the measurement is **2**
(defects 4). The predictor remains correct in the DIRECTION (0 = easy) but
my mental scale of the order of magnitude was inflated by ~1-2 orders.

## Small geometries — complete outcomes

| Geometry | standard | local_termination (dual) | local_termination (angle) |
|---|---|---|---|
| cylinder | 74,808 prisms, scale 1.0, Mesh OK, 13.5 s | **identical** (0 excluded, 12.6 s) | identical (13.2 s) |
| cube (9 cells) | 108 prisms, Mesh OK, 0.0 s | identical (0 excluded) | identical |
| groove1 (682) | 3,372 prisms = ALL at 1 layer, scale 0.1, **checkMesh Failed 2** (2 non-ortho err., skew 46.2) | **10,062 prisms (3 layers), scale 1.0, Mesh OK** (skew 3.29), 18 discarded, 1.4 s | 10,032 prisms, Mesh OK, 28 discarded, 3.2 s |
| slot1 (9,372) | **FAILS** (all scales invalid, 34.5 s) | **79,614 prisms, scale 1.0, Mesh OK** (skew 2.89), 216 without BL = 0.8%, 27.1 s | 79,593 prisms, Mesh OK, 190 discarded, 27.7 s |

Q4 predictions: none of the predicted LT failures occurred on
this set; the only measured failure is the STANDARD path on slot1.
LT on slot1 is the strongest case: it unblocks a BL that the standard path
cannot build at any scale, with 0.8% of the wall uncovered and
Mesh OK.

## Accounting correction (important)

`res.stats["local_excluded_faces"]` counts only the ITERATIVE exclusions;
the faces discarded by the binary criterion (`zero_concave`, seed) are
separate and are re-evaluated at each round (a vertex can drop
below 0.5 when it loses an incident "good" face). Therefore:
- valve thin-first: iterative 2,058, but total without-BL = 3,684 =
  1.63% (445,716 prisms / 2 layers = 222,858 covered faces);
- valve thick-first (engine): iterative 19,625, total without-BL =
  21,208 = 9.36% (410,668 prisms).
- groove1 lt_dual: 18 discarded = seed (0 iterative); lt_angle: 28 = 4
  drop_bnd + 24 iterative. slot1 lt_dual: 216 = 86 seed + 132 iterative
  − 2 in overlap.

## Time vs size

| Geometry | dual cells in | build cells | engine time |
|---|---|---|---|
| cube duct | 9 | 117 | ~0.03 s |
| groove1 | 682 | 10,744 | 1.4-3.2 s |
| cylinder | 10,618 | 85,426 | 12.6-13.5 s |
| slot1 | 9,372 | 88,986 | 27.1 s |
| valve | 152,086 | ~598k | ~100 s/attempt |

Roughly linear with a fixed overhead; the total = attempts x cost. The
valve thick-first pays 13 attempts (1,602 s) against the 3 of thin-first
(~305 s of build + checkMesh).
