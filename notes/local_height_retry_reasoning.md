# Batched per-vertex local height retry — prediction before testing

See `notes/bl_local_height_fallback_reasoning.md` for the full history:
the original per-vertex-height idea, why a literal serial retry is
computationally infeasible on the real valve (thousands of candidate
vertices x ~1 rebuild each), and the batched design chosen instead (one
extra rebuild per exclusion ROUND, not per vertex).

## The mechanism

New `_build(..., vertex_height_scale: dict[int, float] | None = None)`
parameter: `max_h[w] *= vertex_height_scale.get(w, 1.0)` right where
`max_h[w] = angle_fade[w] * min(clamp_factor * d, 0.5 * min_edge[w])` is
computed (`bl_poly.py`, per-vertex inversion clamp). Default `None`
(equivalent to an all-1.0 dict) leaves every existing code path
byte-identical.

New opt-in `run(local_height_retry=False)`, only meaningful together with
`local_termination="decoupled_vertex"` (the H4 vertex-exclusion mode).
Inside `_run_local_termination`'s round loop, when a round's rebuild
fails validation and `_collect_exclusions` returns `added` (new failing
faces) -> `gained` (their vertices, minus already-excluded ones):

1. **Without the flag** (today): `gained` is added straight to
   `excluded_verts` and the next round drops those faces entirely.
2. **With the flag**: before excluding, try ONE retry rebuild with
   `vertex_height_scale = {v: s for v in gained}` for a small candidate
   scale `s` (a few values tried in sequence, e.g. `0.3, 0.1, 0.03`,
   stopping at the first that validates for those vertices), keeping
   `sel_cur`/`excluded_verts` UNCHANGED (the gained vertices' faces stay
   selected, just shorter). Re-run `_collect_exclusions` on the retry
   build. Any vertex from `gained` whose faces do NOT reappear in the
   new exclusion set is RESCUED: removed from `gained`, its scale
   persisted in a running `height_scale` dict carried into every future
   `_build` call this run (both this round and all subsequent rounds/
   scales). Any vertex still failing is excluded exactly as today
   (unchanged fallback).

## Prediction

I predict this recovers SOME meaningful fraction of the currently
excluded wall faces on the valve (baseline 7054 excluded at
`normal_method="area_weighted"`, scale=1.0), because the diagnostic
already established (this session, `bl_local_height_fallback_reasoning.md`)
that essentially ALL real exclusions are caused by the FRESHLY EXTRUDED
PRISM being invalid at its FULL requested height — a purely local height
problem at a bounded set of root vertices, not a defect inherited from
the core mesh. A much shorter stack (3-30x thinner) at just those
vertices should very often clear a negative-volume or pyramid-violation
failure, since the failure mode is fundamentally "this direction is fine
close to the surface, but the extrusion overshoots the local sliver at
the requested height" -- exactly the scenario a smaller height fixes by
construction (a shorter prism has smaller motion, so it is harder to
invert or violate the pyramid criterion around a tight concave corner).

**Risk/mitigation**: default `local_height_retry=False` keeps every
existing path byte-identical (verified: default `vertex_height_scale=
None` in `_build` is a no-op by construction, not by branching around a
scale of 1.0 -- the multiply only happens when the dict is provided).
Cost: one extra rebuild per exclusion round (bounded, same order as the
existing loop -- NOT one rebuild per vertex, per the batching decision
above). Validated with real checkMesh on the valve before considering
this more than experimental, per this session's own hard-learned lesson
from `most_visible_normal_reasoning.md` (an in-process/build-time
improvement is not proof of a real quality win).

## Verification plan before trusting the implementation

1. Unit test on a small synthetic case: a healthy cube (no rescues
   needed, `local_height_retry=True` must be byte-identical to `False`)
   -- confirms the flag is a true no-op when nothing fails.
2. A dry-run trace on the real valve comparing `excluded_verts` counts
   round-by-round WITH vs WITHOUT the retry, before trusting the final
   number.
3. Real checkMesh (WSL) on the final accepted valve mesh, exactly like
   the `most_visible`/`smoothed` A/B protocol.

## Implementation

`_build(..., vertex_height_scale=None)`: `max_h[w] *= vertex_height_scale.
get(w, 1.0)` where the inversion-clamp budget is computed. Verified
no-op by construction: a direct regression test builds the same mesh
with `vertex_height_scale=None` vs `{}` and asserts byte-identical
points. `_retry_local_height()`: bounded to 2 extra rebuilds per
exclusion round (candidate scales 0.2, then 0.05 for whatever remains
unrescued), persists rescued vertices' scale in a `height_scale` dict
carried across every subsequent `_build` call in the same `run()`.
New opt-in `run(local_height_retry=False)`, validated to require
`local_termination="decoupled_vertex"`. 3 new unit tests
(no-op-on-healthy-cube, rejects-without-decoupled-vertex,
vertex_height_scale-no-op-regression-guard). Full `test_bl_poly.py`
suite: 27/27 pass.

## Real measurement on the production valve (2026-09-14, identical A/B
## protocol: fresh pristine `valve_baseline` copy, `normal_method=
## "area_weighted"`, `local_termination="decoupled_vertex"`, `n_layers=2`,
## `first_height=1e-5`, `growth_rate=1.2`)

Round-1 trace confirms the mechanism runs as designed: calls #2 and #3
are the two retry-scale rebuilds (still at the full 603,254-cell count,
since `sel_cur` is unchanged during retry -- only vertex heights
shrink), reducing the round's pyramid-violating-face count from 2384 to
1578 before the round's survivors move on; call #4 is the first REAL
next round with the still-failing vertices finally excluded (n_cells
drops to 590,016).

| metric | area_weighted (baseline) | local_height_retry | change |
|---|---|---|---|
| total local_excluded_faces | 7054 | 6704 | -350 (-5.0%) |
| n_cells_after | 589113 | 589813 | +700 cells kept |

Real, positive, modest coverage gain -- less than `most_visible`'s -6.3%
but via a genuinely different, more targeted mechanism (only touches
vertices ALREADY failing; every already-succeeding vertex is untouched).

## Real checkMesh (WSL, `-allGeometry -allTopology`)

| metric | area_weighted | local_height_retry | change |
|---|---|---|---|
| non-ortho average | 18.5196 | 18.5286 | ~same (+0.009) |
| severely non-orthogonal faces (>70deg) | 9614 | 9739 | worse (+125, +1.3%) |
| face pyramid errors | 841 | 841 | IDENTICAL |
| max skewness | 16.245 | 16.245 | IDENTICAL |
| highly skew faces | 86 | 86 | IDENTICAL |
| face-tet errors | 2375 | 2356 | better (-19) |
| high aspect ratio cells | 1000 | 1000 | IDENTICAL |
| concave cells | 57479 | 57479 | IDENTICAL |
| min cell volume | 3.568e-13 | 3.568e-13 | IDENTICAL |
| edges too small (count) | 6 | 196 | MUCH worse (+190) |

**Result: mixed, same honest pattern as `most_visible`.** Most checkMesh
metrics are literally IDENTICAL (confirms the design worked exactly as
intended: vertices that already succeed at full height are completely
untouched, byte-for-byte). The real cost of the -5.0% coverage gain is
concentrated at the rescued vertices themselves: a small non-
orthogonality regression (+1.3% severely non-orthogonal faces) and,
more notably, a large jump in very short edges (6 -> 196) -- an expected
and direct consequence of the retry mechanism deliberately shrinking
those vertices' already-tiny height (`first_height=1e-5`) by a further
5-20x, which is exactly what "rescues" them from a negative-volume/
pyramid failure but also creates much smaller (and therefore more
restrictive for an explicit-timestep CFD solver) edges. Face-tet quality
improves slightly (-19).

**Conclusion**: keep `local_height_retry=False` as the default (byte-
identical to today). `local_height_retry=True` is a genuinely working,
honestly-mixed opt-in: it recovers real wall coverage at a real but
bounded quality cost concentrated exactly where expected, with zero
effect on the rest of the mesh. Whether that trade is worth it depends
on whether the target solver cares more about wall coverage or about
avoiding very short edges -- a decision for the user/case, not something
to bake in as a new default. Not combined with `most_visible` this
session (each was measured in isolation per the A/B protocol); stacking
both remains an open, unexplored combination for a future session.
