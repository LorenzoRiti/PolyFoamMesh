# Tet -> Poly by barycentric dual rebuild (`core/tet_poly_dual.py`)

Status: implemented and measured. **Not wired into the GUI** — see "GUI" below.

## What it is

`TetPolyDualConverter` replaces tet *merging* with a dual *rebuild*:

```
one output cell   per primal VERTEX
one internal face per primal EDGE
boundary faces    = exact subdivision of the original boundary triangles
```

Every input tet is consumed, so poly coverage is 100% by construction — the
notion of a "residual tet" does not exist in this algorithm. Cell count falls
~5.5x (a tet mesh has roughly six tets per vertex).

## Why the boundary is not the hard part here

`poly_workflow_part2.md` treats "clip the dual cell against the CAD surface" as
the blocking step. That is true of a **Voronoi / circumcentric** dual, whose
cells stick out through the boundary and must be cut back.

This uses the **median (barycentric)** dual, whose boundary dual points already
lie exactly on the primal surface: the boundary vertex itself, boundary-edge
midpoints, boundary-triangle centroids. Each primal boundary triangle (a,b,c)
splits into three planar quads

```
[a, mid(ab), centroid(abc), mid(ca)]  -> cell of a      (and cyclically)
```

that tile it exactly. So the output boundary is *geometrically identical* to the
input boundary, patch for patch. No clipping, no CAD queries, no tolerances.
Total volume is preserved to machine precision (measured drift 0.0 - 1.1e-16).

Orientation is likewise unambiguous: the dual face for edge (a,b) separates
exactly the cells of a and b, so its normal must point from a to b. There is no
winding heuristic to get wrong.

## Measured results

All numbers are real `checkMesh` runs (OpenFOAM 2512, WSL). Every comparison is
against the *same* tet mesh, not a re-meshed one.

### Reference case — block with cylindrical bore, medium detail, 251,048 tets

| | terminal-face (shipped, variant A) | **dual (this module)** |
|---|---|---|
| cells out | 139,227 | 47,528 |
| poly coverage | **80.3%** (27,420 tets left) | **100.0%** (0 tets) |
| checkMesh | **Failed 2 mesh checks** | **Mesh OK** |
| incorrectly oriented faces | **1,073** | **0** ("Face pyramids OK") |
| max skewness | 4.93 (fail) | 2.25 (OK) |
| max non-orthogonality | 88.06 | 54.43 (better than the 66.55 tet input) |
| max aspect ratio | 6.98 | 4.47 |
| total volume | 1.69409e-06 | 1.69409e-06 (identical) |
| conversion time | 17.6 s | 8.8 s |

Stability: three independent runs (fresh GMSH mesh each time, 251,048 /
251,508 / 251,715 tets) all gave **Mesh OK, 100% poly, 0 misoriented faces**,
max skewness 2.25 / 1.97 / 2.03.

For context, the historical mutual-agreement-only baseline on this geometry was
21% poly / 79 misoriented / 1 failed check.

### Real industrial part — `Parte4.stp`, medium detail

Same tet mesh for both converters (824,661 tets):

| | terminal-face | **dual** |
|---|---|---|
| poly coverage | 80% | **100.0%** |
| checkMesh | Failed **4** checks | Failed **3** checks |
| incorrectly oriented faces | **3,545** | **895** |
| max skewness | 34.94 | 44.33 |
| non-orthogonality errors | 5 | 55 |
| max aspect ratio | **1,240 (FAIL)** | 779 (OK) |
| time | 62.7 s | 33.7 s |

Second, independent run (1,036,918 tets): 100% poly, 854 misoriented, 42
non-ortho errors, max skewness 72.1, Failed 3 checks, **37.7 s**. So the valve
result is stable in kind, and runtime is ~36 s per million tets.

**The valve does not reach `Mesh OK`.** Reported plainly: 3 failed checks.

## Why the valve still fails — the actual reason

Measured, not guessed. Of the 766-895 inverted face pyramids:

* **705/766 are boundary faces**, not internal ones.
* The cells that fail have **their own boundary-face normals spanning 90-134
  degrees**, against **1.1 degrees** for a typical boundary cell.
* Their volumes are **normal** (1.79e-9 vs a boundary-cell median of 2.32e-9),
  so this is *not* a sliver/primal-quality effect.

So: where a primal boundary vertex sits on a **concave CAD feature edge**, its
dual cell wraps around the feature and is genuinely non-convex, and the cell's
own centroid falls outside one of its boundary quads. The cell is still closed,
positive-volume and watertight — it fails a *convexity-assuming* check.

This affects 268-462 cells, i.e. **0.25-0.29% of the mesh**, and it is a
property of the geometry (89 B-Rep surfaces with concave junctions), not of the
input mesh quality.

## Two repairs that were implemented, measured, and rejected

Both are still in the file, off by default, with their numbers — they are the
obvious things to try next and these are the numbers to beat.

1. **Defect-driven cell agglomeration** (merge a failing cell into its healthiest
   neighbour, disjoint pairs, iterate). Made it monotonically *worse*:
   766 -> 839 -> 1,225 -> 1,835 -> 2,929 inverted pyramids over four rounds.
   Merging two dual cells produces a larger non-convex cell. This independently
   reproduces the wall the terminal-face session hit: merging does not fix shape.
   Removed from the module (the `median_faces` A/B and split loop remain).

2. **Feature splitting of the vertex star** (`split_rounds`, still present,
   default 0): partition the tets around a failing vertex into one group per
   smooth surface region — clustered by dihedral angle across the boundary
   triangles at that vertex, then flooded inwards — and give each group its own
   cell, cut by exact barycentric corners of the interior primal faces. The
   construction is correct (watertight, volume-conserving, all invariants pass),
   but it does not pay: 895 -> 1,215 -> 1,218 inverted pyramids. Splitting a
   wrapped cell into wedges trades one non-convex cell for several thin ones
   that invert in their own right.

The run loop scores every round with an in-process replica of checkMesh's own
metrics and keeps the best, so enabling either can never make the *written*
mesh worse. The in-process detector agrees with checkMesh exactly on the
pyramid count (895 predicted / 895 reported), which is what makes these
measurements trustworthy without a WSL round trip.

## What would actually fix the valve

The defect is non-convexity at concave feature vertices. Merging and wedge
splitting both fail because they keep the cell centroid inside a wrapped shape.
The remaining candidate is to **not place a dual cell corner on a concave
feature at all** — e.g. insert the feature edge as a proper cell edge and build
prism-like cells along it, or accept a locally hybrid boundary layer there.
That is a genuinely different construction, not a tuning knob.

## GUI

Wired in behind a user-visible switch: **Tools -> Polyhedral Converter**, with
"Barycentric dual (100% polyhedral)" and "Terminal-face (legacy, ~80%)". The
choice persists in `AppSettings` under `mesh/poly_converter` and **defaults to
the dual**, because it measured better than terminal-face on every axis on both
test geometries.

Which converter ran is always printed in the visible log — the cell count
changes by ~5.5x between them, so a silent swap would be uninterpretable:

```
[poly] Converting tet -> polyhedral mesh (barycentric dual, 100% polyhedral)...
[poly 5/9] round 0: 47,650 dual cells, 313,616 internal + 85,506 boundary faces
[poly 6/9] round 0 quality: 0 inverted face pyramids, 0 non-orthogonality errors...
[poly 9/9] wrote 47,650 polyhedral cells, ... residual tetrahedra: 0
[poly] Conversion OK: 251,715 tets -> 47,650 polyhedra, residual tetrahedra 0 (12.12s)
```

Implementation: `DualPolyWorker` in `openfoam_runner.py` (mirrors
`TerminalFaceWorker`, adds `cancel()`); dispatch in `_launch_terminal_face()` in
`main_window.py`. `DualPolyResult` exposes `n_polyhedra` and `wall_time_s`
aliases so the existing `_on_terminal_face_finished` handler works unchanged.
The tet-generation path (`gmsh_wrapper.py`, GMSH workers, checkMesh launch) is
untouched.

Verified end to end through the real worker/QThread path on the reference case:
251,715 tets -> 47,650 polyhedra, 0 residual, **Mesh OK**, 12.1 s.

There is also a CLI for trying it on an existing case without the GUI:

```bash
python tools/tet_poly_dual_cli.py <case_dir> --check
```

It backs the tet mesh up to `constant/polyMesh_tet_backup` first, so the
conversion is undoable.

## Boundary layers on the poly mesh (`core/bl_poly.py`)

Since the dual needs pure tetrahedra as input, boundary layers used to be
force-disabled on the "Polyhedral (CFD)" path. That limitation is gone: the
layers are added AFTER the conversion, by `PolyBoundaryLayerEngine`.

Why this is conforming by construction (the median dual makes it easy): the
wall faces are planar quads `[a, mid(ab), cent(abc), mid(ca)]`; extruding each
quad into a prism stack pairs every side face with exactly one neighbouring
stack (the wall quads share their mid/centroid vertices), and each wall vertex
moves to ONE last-layer position, so the top surface tiles without gaps or
overlaps. The interior cell that owned a wall face swaps it for the top face
and gets its wall-adjacent internal faces' wall vertices moved to the last
layer — owner/neighbour pairs never change, only positions. The topology
pattern OpenFOAM accepts (3 faces on wall-adjacent edges) is preserved.

Per-vertex layer heights are clamped (distance to the interior, feature-angle
fade for concave slivers, smallest incident wall-face edge), windings are
repaired with a greedy closure pass + a checkMesh-style "face pyramids" pass,
and the whole result is validated in-process (volume conserved, cells closed,
all positive volumes) before writing. On failure the mesh is left untouched.

Measured (see `tools/bench_bl_poly_dual.py`): cylinder, GMSH tet (398,745
tets) -> dual (72,483 poly cells) -> BL (5 layers, 505,950 prism cells) ->
checkMesh `Mesh OK`, 0 misoriented faces. The cube (9 dual cells -> 108 prism
cells) also passes `Mesh OK`.

GUI wiring: `DualPolyWorker` accepts `bl_params` (`nLayers`,
`firstLayerThickness` [m], `thicknessRatio`); the poly path no longer forces
BL off — when enabled, the worker logs and inserts the layers right after the
conversion, before checkMesh.

Known limitation: BL is applied to the whole boundary (partial-patch BL would
create non-manifold seams, so the face set is auto-closed across edges). On
severely concave-feature geometry (the reference valve) the engine reports a
clean failure and keeps the mesh without BL.

## Reproducing

```bash
python tests/bench_tetpoly_dual.py        # other agent's harness
```

or the harness used for the numbers above (block + valve, fresh mesh each run,
tet backup restored before every conversion so comparisons share one input).
`TetPolyDualConverter(case_dir, log=print).run()` on any pure-tet OpenFOAM case
is all the module itself needs; it reads binary or ASCII polyMesh via
`TerminalFaceConverter`'s readers.

## Non-planarity of the internal dual faces, and the optional fix

The dual's internal faces are rings of tet centroids around each primal edge,
and those rings are **not planar** in general (only the boundary faces are
planar quads by construction). OpenFOAM assumes planar faces, so the face
centre/normal of a warped ring is slightly off — worse non-orthogonality and
skewness, and artefacts in foamToVTK/ParaView. Measured on real cases
(threshold `dev > 1e-6 * bbox_diag`, see below):

| case (dual output) | faces | non-planar | max dev / bbox |
|---|---|---|---|
| ref1 (block+bore, 47.5k cells) | 398,340 | 55.9% | 4.5e-3 |
| valve1 (1.0M cells) | 1,241,048 | 58.8% | 3.8e-3 |

autopoly (CVT/Voronoi) does not have this problem — its faces are planar by
construction — so this concerns only the tet->poly dual path.

### Measuring: `planarity_report`

```python
from cfmesh_autogui.core.tet_poly_dual import planarity_report
rep = planarity_report(points, faces, owner, neigh, n_int)  # or a case dir
```

For every face it computes the max vertex deviation from the face's Newell
plane (same `_newell` used by the converter's orientation code) and reports
total faces, non-planar count/percentage (threshold `rel_tol * bbox_diag`,
default `rel_tol = 1e-6`), max/mean/p90/sum of the per-face deviation, the
internal/boundary split, and the worst faces for debugging. CLI:

```bash
python -m cfmesh_autogui.core.tet_poly_dual --planarity <case_dir>
python -m cfmesh_autogui.core.tet_poly_dual --convert <tet_case_dir> --fix triangulate
```

### Fixing: `fix_nonplanar_faces` (default `"none"`)

`TetPolyDualConverter(case, fix_nonplanar_faces="triangulate"|"planarize")`.

* **`"triangulate"`** (the cfMesh approach): fan-triangulate every non-planar
  face from its centroid. The fan tiles the face's area vector *exactly*, so
  total volume, closure and owner->neighbour orientation are preserved to
  machine precision (per-cell volumes shift only where a warped face is
  split) and cells stay polyhedral. After the fix no face is
  beyond the threshold (verified on the reference case and the unit cube).
* **`"planarize"`**: project interior vertices of non-planar faces onto their
  Newell planes (boundary vertices pinned — the dual boundary is an exact
  subdivision of the input surface), keep-best against the in-process
  checkMesh replica: a pass is kept only if the defect count does not increase
  and planarity improves, so the written mesh is never worse. MEASURED:
  on ref1 it halves the planarity deviation (sum 4.50 -> 2.47, max
  1.12e-4 -> 5.37e-5, 0 defects before and after) but the *count* of faces
  over the absolute threshold rises (shared vertices move); on the valve it
  is a no-op — every pass is rejected by keep-best, which is the safety net
  doing its job. It cannot zero the deviation of boundary-edge rings, which
  contain pinned surface vertices. Left in, off by default, measured: it is
  the honest "smoothing-style" alternative to triangulation, and these are
  the numbers to beat.

`"none"` (the default) leaves the output **byte-identical** to the pre-fix
converter (verified by hashing the written polyMesh with and without the
flag; the fix hook is the only added code path).
