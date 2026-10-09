# Does thin-first generalize beyond the valve? — Reasoning BEFORE the tests

Date: 2026-09-09. Written before any FASE 4 run.
The thin-first discovery (valve: 9.5x fewer exclusions, +8.5% prisms,
~5x faster) must be verified on cylinder, cube duct, groove1, slot1.

## Fact to explain (measured, FASE 3)

On the valve, the engine `_run_local_termination` with scales
thick→thin [(1.0,0.5), (0.6,0.35), (0.35,0.2), (0.2,0.1), (0.1,0.05)]
accumulates 19,625 exclusions at scales 1.0/0.6/0.35/0.2 (the warnings
show the failures only at those scales) and at 0.1 it validates with the carried
set. If instead one starts at 0.1 with the production iterative logic,
in 3 rounds it closes with 2,058 exclusions (91% of which
is the predictable seed).

Question: is it a behavior specific to the valve (the only geometry
with severe input defects) or does it generalize?

## Mechanics I suspect

In `_run_local_termination` each round of a scale thicker than
another, with the same geometric defects, is MORE PERMISSIVE on the
prisms: larger heights produce less negative volume and fewer
pyramid violations per prism, but they can hide
whack-a-mole — the excluded face opens a defect at its edge
(perimeter of the dropped region), which is "resolved" only at the
next attempt. At a thinner scale, each defect that surfaces
stays surfaced and the exclusion is more surgical.

From the `zero_concave` section of `_build`: a face is discarded if
(bi in drop_bnd) OR (any vertex fade<0.5) OR (max_h<=0). The seed is
deterministic from `pre`. The iterative part covers the faces that
"belong" to core cells with defects in the new faces or inverted
prisms.

## Predictions

### Cylinder (dual 10,618 cells, n_bnd 24,936, pyr=0, low_fade=0)

- `_build` on cylinder: all vertices have fade=1 (smooth), max_h
  large; no drop_bnd; no low-fade.
- Prediction: **thin-first identical to thick-first**. The first scale it
  runs is round 0 → validate → written. Identical in time, prisms,
  metrics. Advantage = 0.

### Cube duct (dual 9 cells, n_bnd 36, pyr=0, low_fade=0)

- Prediction: **identical to thick-first** (no defects, exits round 0).
  Advantage = 0.

### Groove1 (dual 682 cells, n_bnd 3,372, pyr=4, low_fade=2, seed=18)

- LT thick-first produced 10,062 prisms (3 layers × 3,354 faces =
  3,372 - 18 seed) with 0 iterative exclusions. Scale 1.0, round 0,
  validate.
- LT thin-first starts from 0.1: here max_h is scaled by 0.1 (smaller);
  but fade=1 everywhere except the 2 low-fade → same seed.
- Prediction: **identical to thick-first** (0 exclusions in both,
  same prism count). Advantage = 0.

### Slot1 (dual 9,372 cells, n_bnd 26,754, pyr=42, low_fade=17, seed=86)

- LT thick-first: 79,614 prisms, 216 without BL (86 seed + 132 iterative,
  overlap 2), 0 negative.
- LT thin-first starts from 0.1: max_h reduced by 0.1, but fade=1 outside
  the 17 vertices. It is possible that the reduced scale reveals more
  pyramid defects on thin prisms (an effect not captured by `_validate` at
  scale 1.0). Uncertainty: pyr_input is 42, above the threshold.
- Prediction: **thin-first slightly worse or equal in % exclusions**
  (a few more prisms to exclude iteratively at 0.1), but
  **it should still validate in 1-2 rounds** because the seed at 0.1
  is 86 anyway and the geometry is the same.
- If thin-first INCREASES the exclusions vs thick-first: it is a signal that
  the "valve advantage" is not general but depends on the fact that the
  valve has 1,007 input defects (an order of magnitude above slot1):
  thick-first has enough "exclusion budget" at the thick scales
  to "pay" the repair cost, while thin-first must exclude
  everything at once at 0.1. On slot1 with 42 defects, the room for
  manoeuvre is much smaller.

## What I expect to see in total

- If thin-first = thick-first on cylinder/cube/groove1 (zero exclusions
  in both orders) and slot1 is within ~2x one way or the other:
  the "valve advantage" is real but does not generalize as an improvement
  on all cases — it is a property of the valve (large input defects
  where the room for manoeuvre at the thick scales is large).
- Predictor: in a case with null or minimal input defects, the order does not
  matter (both exit at round 0 scale 1.0 or 0.1 indifferently).
  In a case with medium defects (slot1), thin-first is ≤ thick-first. Only
  in a case with large defects (valve) does thin-first beat thick-first.

## Reading criterion

- thin-first ≠ thick-first on the cylinder/cube → trust me.
- groove1 identical → confirms the neutrality of the order on minimal defects.
- slot1 ≤ thick-first → confirms that the "valve advantage" scales with
  the magnitude of the input defects, it is not magic.
- slot1 > thick-first → signal that the inversion is not universally
  positive and that thin-first must be enabled as an option, not as a default.

# RESULTS (filled in after the run, 2026-09-09)

## Measurements (tools/bench_bl_thinfirst.py, real checkMesh)

| Geometry | THICK (1.0→0.1) | THIN (0.1→1.0) |
|---|---|---|
| cylinder | ok, scale 1.0, 74,808 prisms, 0 excl, 17.9 s, aspect 18.3 | ok, **scale 0.1**, 74,808, 0 excl, 17.0 s, **aspect 104.3** |
| cube duct | ok, scale 1.0, 108, 0 excl, 0.0 s, aspect 78.7 | ok, **scale 0.1**, 108, 0 excl, 0.0 s, **aspect 781.6** |
| groove1 | ok, scale 1.0, 10,062, 0 excl, 2.0 s, aspect 15.9 | ok, **scale 0.1**, 10,062, 0 excl, 1.9 s, **aspect 90.8** |
| slot1 | ok, scale 1.0, 79,614, 132 iterative excl (216 total), 36.5 s, aspect 16.7 | ok, **scale 0.1**, 80,004, **0 iterative excl** (86 total = seed), 17.9 s, **aspect 128.5** |

All meshes are Mesh OK in both modes; thick-first
reproduces exactly the FASE 3 LT results (faithful patch).

## The real result: thin-first reduces the requested thickness by 10x

On ALL 4 geometries THIN validates at the FIRST attempt (scale 0.1):
the requested `first_height` (5e-3) is written as 5e-4, in
silence. The aspect ratio ~6-10x proves it (cube: 781.6/78.7 =
9.93x). Same prisms (same number of layers), same topology, but
a BL 10x thinner than requested.

The two effects are INTERTWINED: the thin-first advantage (avoiding
the exclusions accumulated by the thick scales) exists only where a thick
scale actually fails (slot1: 0 vs 132; valve: 2,058 vs 19,625), and
there it is real; but "accept the first scale that validates" means accepting
0.1 even where 1.0 would work, degrading y+ by 10x.

## Predictions vs measurements (honest)

- cylinder/cube/groove1: predicted "identical to thick-first" — **WRONG**
  in thickness/scale/aspect (same topology, but scale 0.1 and aspect
  6-10x worse). The mechanics ("exits at round 0") were right, I had
  missed that round 0 of thin-first IS scale 0.1.
- slot1: predicted "equal or slightly worse in % exclusions" —
  **wrong in sign** (0 vs 132, so better), right in the
  mechanics (validates in 1 round); missed the thickness effect entirely.
- The FASE 3 predictor (pyr_input) remains valid: the advantage appears
  only where pyr_input > 0 and grows with the number of defects.

## Conclusion

A blind inversion of the order must NOT be proposed as default: on healthy
meshes it violates the requested `first_height` contract. The variant to
measure (future work, not implemented) is **decoupled**: try the
requested scale first; when stepping down in scale do NOT carry the accumulated
exclusions (reset at the scale change). This way the valve would obtain ~2,058
exclusions like thin-first without degrading the healthy cases. To be measured
before any proposal.
