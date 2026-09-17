# Normal smoothing (Alauzet) — prediction before testing

## What the paper actually says (re-read verbatim, `scratchpad/alauzet.txt`)

> Standard Laplacian based smoothing in topological space is applied over
> multiple iterations (typically 20). The smoothing level is varied by a
> weighted average between the true normal vector and the fully smoothed
> normal vector. The weight varies between 0 and 1 with the distance from
> the wall. Full smoothing is applied once the distance exceeds a
> threshold (0.5 of the surface spacing).

And on WHY it helps (same section, measured on F117/projectile):

> Concave ridges stop earlier to grow without smoothing while a full grow
> of the BL was possible with the normal smoothing. Indeed, the normal
> smoothing creates space around the normal issued from the ridge, which
> allows it to continue to progress without violating any quality
> criteria. At convex ridges, the BL growing leads to distorted elements
> [...] while the normal smoothing preserves nice shaped elements.

This is presented in the paper as a SEPARATE, LATER stage than "most
visible normal" (which our `normal_method="most_visible"`, committed this
session, already implements and measured MIXED/slightly negative on real
checkMesh — see `notes/most_visible_normal_reasoning.md`). The two are not
mutually exclusive in the paper (Table 2 tests "most visible" and "most
visible + smoothing" as separate columns, with smoothing giving the best
result of the three).

## Why our engine cannot implement this literally

The paper's smoothing weight varies with **distance from the wall
during BL growth** — each layer gets a normal blended by how far it has
already travelled, because their closed-advancing-layer method recomputes
the front's normal freshly EVERY layer as the front deforms.

Our engine (`bl_poly.py`) is architecturally different: it computes ONE
extrusion direction per wall vertex up front (`_build_precompute`'s
`normals` array) and extrudes the WHOLE prism stack along that fixed
direction, scaled by the cumulative layer heights. There is no per-layer
front to recompute a distance-varying weight against.

**Honest scope reduction**: implement the topological Laplacian smoothing
of the normal field exactly as described (wall-vertex adjacency graph,
~20 iterations), then blend the smoothed field with the true per-vertex
normal using a SINGLE per-vertex weight driven by the concavity signal we
already compute (`angle_fade`, low value = concave ridge = exactly where
the paper says smoothing helps most) instead of "distance from the wall"
(which does not exist as a per-vertex quantity in our fixed-direction
model). `blend_weight = 1 - angle_fade` — a flat vertex has `angle_fade
~= 1` so `blend_weight ~= 0` (true normal kept, matches "full orthogonality
close to the surface" intent since flat regions do not need smoothing at
all), and a sharp concave ridge has `angle_fade` as low as 0.05 so
`blend_weight ~= 0.95` (nearly fully smoothed, matches "full smoothing
applied" near discontinuities). This reuses an existing, already-verified
signal rather than inventing a new distance metric, and is the most
faithful mapping of the paper's INTENT (smooth more at ridges, keep exact
near flat walls) onto our single-direction architecture.

## The idea

New opt-in `normal_method="smoothed"`:
1. Compute the per-vertex normals exactly as today (area-weighted, or
   optionally reuse `most_visible` — kept as `area_weighted` base for
   this first experiment to isolate the effect of smoothing alone).
2. Build the wall-vertex adjacency graph from boundary-face edges
   (already available as `edge_sides`/boundary faces in `_build_precompute`).
3. Laplacian-smooth the normal field over this graph for 20 iterations:
   `n_smooth[v] = normalize(mean(n[v], n[neighbours of v]))` (self included,
   standard Laplacian smoothing convention for a vector field, re-normalized
   every iteration since we are smoothing DIRECTIONS not free vectors).
4. Blend: `n_final[v] = normalize((1 - w[v]) * n[v] + w[v] * n_smooth[v])`,
   `w[v] = 1 - angle_fade[v]`.

## Prediction

I predict this REDUCES the freshly-extruded-prism-invalid exclusion count
at concave wall vertices, similarly to (possibly more than) `most_visible`
alone, because the paper explicitly attributes the mechanism to "creating
space around the normal issued from the ridge" — a genuinely different
effect from most_visible's angular-equidistance optimum (smoothing pulls
the ridge normal toward its NEIGHBOURS' consensus direction, damping the
sharp local divergence rather than optimally splitting it).

**Real-checkMesh risk** (learned from `most_visible`'s own honest result):
an in-process/build-time improvement does NOT guarantee a real checkMesh
quality win. This will be validated the same way: real checkMesh via WSL
on the valve, not just the build success/exclusion count, BEFORE any
conclusion is drawn.

**Verification plan before implementation is trusted**: the Laplacian
smoothing step will be checked against a hand-computable tiny case (a
5-vertex star: 1 centre + 4 neighbours with known normals) to confirm the
iteration converges to the expected consensus direction and stays unit
length, before running it on the real valve.

**Risk/mitigation**: purely local precompute change (new function,
opt-in `normal_method="smoothed"`), does not touch `_build`'s construction
logic at all — same safety profile as `most_visible`. Default
(`normal_method="area_weighted"`) stays byte-identical.

## Implementation

`_laplacian_smooth_normal_field(normals, wall_verts, adjacency, iters=20)`
in `bl_poly.py`: self+neighbours mean, re-normalised every iteration.
Verified against the hand-computable 5-vertex star
(`scratchpad/verify_normal_smoothing.py`): converges to a stable fixed
point by iteration 20, preserves unit length exactly, pulls a divergent
"ridge" normal from 90deg to ~33deg toward its neighbours' consensus.
Wired into `_build_precompute` right after `angle_fade` is fully computed
(needs the whole per-vertex fade array for the blend weight), building the
wall-vertex adjacency graph from the SAME selected-boundary-face edges
already used for `wall_face_of`. 6 new unit tests (2 in this batch:
`test_laplacian_smooth_normal_field_matches_hand_computed_star` directly
tests the primitive; `test_bl_normal_method_smoothed_*` mirror the
`most_visible` wiring tests). Full `test_bl_poly.py` suite: 24/24 pass.

## Real measurement on the production valve (2026-09-14, identical
## conditions to the `most_visible` A/B: fresh pristine `valve_baseline`
## copy, `local_termination="decoupled_vertex"`, `n_layers=2`,
## `first_height=1e-5`, `growth_rate=1.2`)

| metric | area_weighted (baseline) | most_visible | smoothed |
|---|---|---|---|
| total local_excluded_faces | 7054 | 6612 (-6.3%) | 7048 (-0.08%) |
| n_cells_after | 589113 | 589997 | 589125 |

**Result: prediction NOT confirmed.** `smoothed` is essentially a no-op
on the valve, far short of even matching `most_visible`, let alone
exceeding it as hoped. Round-1 exclusion breakdown was IDENTICAL to
`area_weighted`'s down to the exact cell counts (232 negative-volume
prisms, 24 core cells, 2384 pyramid-violating faces, 2898 excluded) --
i.e. the smoothed field had ZERO measurable effect on the vertices that
actually fail.

## Root cause (found, not guessed): the blend-weight signal is flat here

Instrumented `_build_precompute` directly on a fresh valve copy and
printed the `angle_fade` distribution used for the blend weight
(`scratchpad/check_angle_fade_dist.py`):

```
angle_fade stats: min=1.0000 mean=1.0000 median=1.0000 max=1.0000
  fraction with angle_fade <= 0.95: 0.00%
  fraction with angle_fade <= 1.0: 100.00%
```

`angle_fade` is EXACTLY 1.0 on every single wall vertex of the valve, so
`blend_w = 1 - angle_fade = 0.0` everywhere -- the smoothed field is
NEVER blended in, by construction, regardless of how well the Laplacian
smoothing itself works. This is not a new discovery about the geometry:
it is the SAME fact already documented earlier this session in `_build_
precompute`'s own docstring for `concavity_criterion`: "concavity_
criterion='dual_convexity' ... fires on the concave-feature dual cells
of the valve where the normal fade is flat (measured >= 0.8 on all valve
wall vertices)". The median-dual wall faces are smooth quads even at
CAD concave features (the concavity lives in the DUAL CELL's convexity,
not in the wall-face-normal angular span), so `angle_fade`'s premise
("wide angular span between incident wall-face normals = concave ridge")
simply does not fire on this geometry's boundary representation. Choosing
`(1 - angle_fade)` as the blend weight was therefore the wrong signal for
THIS specific geometry from the start -- a scope error in the design, not
a failure of Laplacian normal smoothing itself, which the hand-computed
star confirmed works correctly as a primitive.

**Not fixed tonight (honest stop)**: the correct blend weight for the
valve would need to key off `dual_convexity` (the signal that DOES fire
there) instead of `angle_fade`, but `normal_method="smoothed"` currently
only supports `concavity_criterion="angle_fade"` (same restriction as
`most_visible`, for the same reason: `dual_convexity` mode skips the
per-vertex face-normal-fan loop entirely, see `bl_poly.py`). Extending
`smoothed` to blend by `(1 - dual_fade)` when `concavity_criterion=
"dual_convexity"` is a small, well-scoped follow-up but was not
implemented this session -- reported here as the concrete next step
rather than rushed in without time to re-verify and re-measure.

**Conclusion**: keep `normal_method="smoothed"` as documented, opt-in,
default-off (byte-identical `area_weighted` unaffected). Its near-zero
effect on the valve is a genuine, understood, root-caused negative
result for THIS geometry under `concavity_criterion="angle_fade"`, not a
general verdict on the technique -- the dual_convexity-blended variant
remains untested and is the natural next experiment.
