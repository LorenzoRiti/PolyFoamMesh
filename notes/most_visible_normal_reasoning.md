# Wall-vertex normal computation — "most visible normal" (prediction before testing)

## Literature grounding (Alauzet, closed-advancing-layer BL paper, already fetched)

The paper directly compares three ways to compute the per-vertex extrusion
normal at a wall vertex shared by several boundary faces, and measures them
on concave-ridge test cases (F117, projectile):

1. simple averaging of incident face normals — the paper's own baseline,
   measured WORST at concave ridges (BL growth terminates earliest there).
2. "most visible normal" (Aubry et al.): the normal that minimizes the
   MAXIMUM angle to any incident face normal — i.e. it sits as close as
   possible to every incident face normal in the worst case, instead of
   being pulled toward whichever faces dominate by area. "Optimal by
   construction" per the paper.
3. "normal smoothing": Laplacian smoothing of the normal field across the
   wall topology (~20 iterations) blended with the true per-vertex normal
   by a distance-based weight — the most invasive of the three (touches
   neighbouring vertices' normals, not just the local face fan).

## Why this maps onto OUR measured problem

Our own diagnostic (`notes/bl_local_height_fallback_reasoning.md`,
`diagnose_out.txt`) found the valve's ~4,162 excluded wall faces are
overwhelmingly caused by the FRESHLY EXTRUDED PRISM being invalid (negative
volume or new pyramid violation) at ~230-330 root wall vertices — i.e. a
LOCAL extrusion-direction/height problem, not an inherited core-mesh defect.
Our current normal (`bl_poly.py:1157-1182`, "per-vertex normals (area-weighted
face normals)") is EXACTLY method (1) above — simple sum-then-normalize of
incident face normals, weighted implicitly by face area since `_newell`
returns an area-scaled normal vector. At a concave ridge, incident face
normals point in divergent directions; the area-weighted average is pulled
toward whichever faces are larger, which can point the extrusion direction
AWAY from the direction that keeps the new prism convex/positive-volume at
the vertex's SMALLEST incident face — precisely a plausible mechanism for
"freshly extruded prism invalid".

## The idea (method 2, NOT method 3)

Implement "most visible normal" only — method 3 (cross-vertex Laplacian
smoothing) touches the topology of the normal FIELD (neighbouring vertices
influence each other), which is a strictly bigger, harder-to-bound change.
Method 2 is a PURELY LOCAL replacement: for each wall vertex, given its set
of incident (already unit-normalized) face normals `f_1..f_k`, compute the
unit vector `n` that minimizes `max_i arccos(n . f_i)`, i.e. the Chebyshev
centre of the spherical cap containing all `f_i` — a bounded, per-vertex,
embarrassingly-parallel computation with no cross-vertex coupling, matching
the safety profile of the height-fade computation it sits next to.

Implementation: iterative min-max search (no closed form for k>2 in general).
Start from the area-weighted average (today's `n`), then repeatedly find the
incident face normal `f_worst` currently farthest (max angle) from `n`, and
rotate `n` a decaying step toward `f_worst` (spherical interpolation). This
is the standard fixed-point iteration for the 1-centre problem on a sphere
and converges in a handful of iterations for the small `k` (2-6) we see per
wall vertex. Verify against a brute-force check (sample many candidate `n`
on the unit sphere near the average, confirm none has a smaller max-angle
than the iterative result) before trusting the formula, exactly like the
GETMe gradient was checked against finite differences earlier this session.

## Prediction

I predict this REDUCES (not necessarily eliminates) the freshly-extruded-
prism-invalid exclusion count at concave wall vertices on the valve,
because it removes exactly the area-weighting bias identified above as a
plausible root cause, matching the paper's own measured direction of effect
(more BL growth survives at concave ridges). I do NOT predict it closes the
gap entirely — the "most visible normal" is a better DIRECTION choice, not
a magic fix for vertices whose smallest incident face is so tight that NO
extrusion direction keeps a real, positive-volume prism at the tested
heights; those need the (still not implemented) per-vertex height retry
from the earlier note.

**Risk**: purely local, per-vertex, opt-in via new `normal_method` parameter
mirroring the existing `concavity_criterion` opt-in pattern
(`bl_poly.py` `_build_precompute`/`_build`/`run`). Default behaviour
(`normal_method="area_weighted"`) stays byte-identical to today. Validated
with real checkMesh on the valve (primary target) before considering it
more than experimental; if it does not help, or regresses any of the 5
reference geometries, that is reported here honestly.

## Implementation

`_most_visible_normal(f_norms, iters=200)` in `bl_poly.py`: iterative
min-max (1-centre-on-sphere) fixed point, starting from the area-weighted
average and repeatedly slerping a decaying step toward the currently-worst
(largest-angle) incident face normal. Verified against a brute-force
sampled search (30 random concave-ridge-like fans, spread 20-160deg,
k=2..6 incident faces, `scratchpad/verify_most_visible_normal.py`): always
at least as good as the plain average, within ~1.4deg of the sampled
optimum. Wired as `normal_method` opt-in through `run()` ->
`_run_local_termination()`/direct path -> `_build_precompute()`, only
implemented for `concavity_criterion="angle_fade"` (raises otherwise).
3 new unit tests (`tests/test_bl_poly.py`): unknown-value rejection,
unsupported-combo rejection, and a healthy-cube structural-equivalence
check. Full `test_bl_poly.py` suite: 21/21 pass (no regression).

## Real measurement on the production valve (2026-09-14, after this
## session's other quality fixes -- current codebase state, not the
## older diagnostic table above)

Identical run (`local_termination="decoupled_vertex"`, `n_layers=2`,
`first_height=1e-5`, `growth_rate=1.2`, `apply_to_all=True`) on a FRESH
pristine copy of `valve_baseline` (the earlier diagnostic's `measure_concave`
case had been silently mutated in place by its own prior BL write, so this
re-measurement uses two clean copies for a fair A/B), converging at
scale=1.0 for both (note: the current codebase's dual mesh differs from
the one behind the OLDER exclusion table above, due to the smoothing-gate
fix, skewness-formula fix, and GETMe compactness/axis-balance smoothing
landed earlier this session -- so absolute counts are NOT comparable to
that table, only area_weighted vs most_visible under IDENTICAL current code):

| metric | area_weighted (baseline) | most_visible | change |
|---|---|---|---|
| negative/zero-volume prisms (round 1) | 232 | 112 | -52% |
| pyramid-violating faces touching a prism (round 1) | 1223 | 584 | -52% |
| total local_excluded_faces | 7054 | 6612 | -6.3% |
| n_cells_after | 589113 | 589997 | +884 cells kept |
| min_cell_volume | 3.56e-13 | 1.29e-13 | smaller (still positive) |

**Result: prediction CONFIRMED, directionally.** The freshly-extruded-
prism-invalid failure mode -- the dominant driver identified by the
earlier diagnostic -- is cut roughly in HALF by the better normal
direction alone (232->112 negative-volume prisms, 1223->584 prism-side
pyramid violations), exactly the mechanism the paper describes. The net
wall-coverage gain is more modest (6.3% fewer excluded faces) because a
face is only rescued if ALL of its vertices survive, and some vertices
still fail even with the better normal (matching the prediction's own
caveat: a better direction is not a magic fix for vertices whose
smallest incident face admits no valid extrusion at the tested heights --
those still need the not-yet-implemented per-vertex height retry from
`notes/bl_local_height_fallback_reasoning.md`). `min_cell_volume` is
smaller under most_visible (still comfortably positive) -- worth watching
under real checkMesh, not just the in-process validator, per this
project's standing discipline.

## Real checkMesh (WSL, `-allGeometry -allTopology`), same two meshes

| metric | area_weighted | most_visible | change |
|---|---|---|---|
| non-ortho average | 18.5196 | 18.5655 | worse (+0.05) |
| severely non-orthogonal faces (>70deg) | 9614 | 10117 | worse (+503, +5.2%) |
| face pyramid errors (wrong orientation) | 841 | 847 | worse (+6) |
| max skewness | 16.245 | 16.805 | worse |
| highly skew faces | 86 | 93 | worse (+7) |
| face-tet errors (low quality/negative decomposition) | 2375 | 2315 | better (-60) |
| high aspect ratio cells | 1000 | 982 | better (-18) |
| concave cells (face-plane test) | 57479 | 57473 | ~same |
| min cell volume (still positive) | 3.568e-13 | 1.286e-13 | smaller |
| mesh checks failed (of the checkMesh categories) | 9 | 9 | same |

**Result: MIXED, net honest verdict is NOT a win on real checkMesh.**
The build-time story (halved negative-volume-prism and prism-side-pyramid
FAILURES, 6.3% fewer excluded faces) does NOT translate into better real
mesh quality -- non-orthogonality, severely-non-orthogonal face count, and
skewness are all measurably WORSE under most_visible, with only minor
gains in face-tet quality and aspect ratio. This is the same pattern this
session already hit once before with `aspect_lam=10`: an in-process
diagnostic/objective improving does not guarantee real checkMesh agrees,
because "most visible" optimises a DIFFERENT thing (angular equidistance
to the incident face fan) than what checkMesh's non-orthogonality/skewness
metrics measure (alignment between the face-normal and the
owner-to-neighbour cell-centre vector) -- the two are correlated but not
identical, and at the exact vertices where "most visible" pulls the
normal away from the area-weighted average to rescue a marginal prism,
it can point further from the direction checkMesh actually rewards.

**Conclusion**: keep `normal_method="most_visible"` as an opt-in
EXPERIMENTAL parameter (never the default -- `area_weighted` stays
default, byte-identical, per the existing discipline), documented here
honestly as a build-time-robustness/real-quality TRADEOFF, not a clear
improvement. Do NOT promote it, do NOT wire it into the GUI's default
concave-closure path. The paper's OTHER method ("normal smoothing",
cross-vertex Laplacian smoothing of the normal field) was explicitly
NOT attempted this session (see "The idea" section above -- ruled out as
strictly bigger/harder-to-bound than the local per-vertex method tried
here); it remains a candidate for a future session with more validation
time, since it is the paper's method that specifically targets exactly
this checkMesh-vs-buildtime tension via smoothing rather than a per-vertex
worst-case optimum.
