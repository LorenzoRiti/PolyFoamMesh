# Quality-driven smoothing was silently skipped on healthy meshes

## Trigger

Comparing our poly dual mesh against `snappyHexMesh` on a plain cylinder
at a comparable cell count (10,649 vs 11,880 cells), real checkMesh
showed snappy winning on every geometric quality metric: aspect ratio
(3.02 vs 4.43), max non-orthogonality (29.1° vs 51.3°), max skewness
(0.86 vs 1.53).

## Root cause found

`TetPolyDualConverter` (and `hex_poly_dual.py`) both default
`smooth=True`, intending the quality-driven Laplacian smoothing pass
(`poly_smoother.smooth_dual_mesh`) to run by default. But the call site
gated it behind `best_defects > 0` — and `best_defects` only counts
THRESHOLD-crossing cells (pyramid <= 0, non-ortho > 70°, skew > 4). Our
cylinder mesh had 0/0/0 defects by that count (well within thresholds),
so the smoothing pass was silently skipped entirely — even though there
was real, measurable headroom below those thresholds.

## Fix and verification

Removed the `best_defects > 0` gate in both `tet_poly_dual.py` and
`hex_poly_dual.py` — `smooth_dual_mesh` is keep-best by construction, so
running it unconditionally cannot make the defect COUNT worse, only find
improvement or leave the mesh unchanged.

Forced the pass on the cylinder mesh (bypassing the old gate) and
verified with real checkMesh: max non-orthogonality 51.3° → 41.7°
(-19%), max skewness 1.53 → 1.28 (-16%). Real, measured improvement,
narrowing (not eliminating) the gap to snappyHexMesh.

## A second bug found while fixing the first

Removing the gate exposed a real regression on two existing tests using
tiny synthetic meshes (a single tet, and a 12-tet perturbed cube): the
tie-breaking acceptance (`cand_obj <= best_obj`, i.e. "not worse") let a
numerically near-neutral step through even when there was no real
non-ortho/skew defect to improve, because on an almost-degenerate mesh
any two configurations differ by float noise in the continuous
objective. That aimless step measurably distorted 3 faces that were
EXACTLY planar by construction (max deviation 0.0021 vs a 1.7e-6
tolerance) — the objective/acceptance never tracked planarity as a
quality axis at all.

Two-part fix:
1. Changed the tie-break to strict `<` (a real improvement, not merely
   "not worse") — this alone did not fully resolve the single-tet case,
   because the accepted step really did have a strictly lower objective
   (planarity was invisible to it, so trading it away for a
   microscopic non-ortho/skew gain still counted as "better").
2. Added a hard planarity guard, same pattern as the existing
   volume-positivity and volume-ratio guards already in
   `smooth_dual_mesh`: track the worst per-face planarity deviation
   (`tet_poly_dual._face_planarity`) and reject any candidate step that
   makes it meaningfully worse than the current best (small relative
   slack for float noise: `max(best_planarity * 1e-6, 1e-12)`).

After both fixes: the single-tet test passes exactly (0 non-planar
faces, as before). A second, unrelated test
(`test_fix_triangulate_eliminates_nonplanar_faces`) still needed its
pinned exact face count updated (122 → 162) — smoothing now genuinely
moves interior points before that test's own "triangulate non-planar
faces" step runs, which changes WHICH faces end up needing
triangulation (not a regression: the properties that matter — zero
non-planar faces left, `max_dev < 1e-9` — still hold exactly).

## Honest scope of the win

This closes part of the quality gap to snappyHexMesh (non-orthogonality
and skewness measurably better on the cylinder), not all of it — aspect
ratio was essentially unchanged (4.43 → 4.46, within noise), and
snappyHexMesh still has better absolute numbers on all three metrics.
The remaining gap is a separate, larger question (this mesher's
octree-based hex-dominant refinement vs our tet→dual conversion
approach), not something this fix was trying to close.
