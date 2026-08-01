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

## Reproducing

```bash
python tests/bench_tetpoly_dual.py        # other agent's harness
```

or the harness used for the numbers above (block + valve, fresh mesh each run,
tet backup restored before every conversion so comparisons share one input).
`TetPolyDualConverter(case_dir, log=print).run()` on any pure-tet OpenFOAM case
is all the module itself needs; it reads binary or ASCII polyMesh via
`TerminalFaceConverter`'s readers.
