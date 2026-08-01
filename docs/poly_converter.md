# Tet -> Poly Converter (`core/tet_poly_dual.py`)

Status: **the one and only tet->poly converter in this project.** Consolidates
and supersedes `poly_workflow_part2.md`, `poly_dual_converter.md` and
`poly_dual_handoff.md` (which are kept only as historical pointers).

## What it is

`TetPolyDualConverter` converts a pure-tetrahedral OpenFOAM mesh into a
polyhedral mesh by building the **barycentric (median) dual complex**:

```
one output cell   per primal VERTEX
one internal face per primal EDGE
boundary faces    = exact subdivision of the original boundary triangles
```

Every input tet is consumed, so **poly coverage is 100% by construction** —
"residual tet" is not a concept that exists in this algorithm. Cell count
drops ~5.5x (a tet mesh has roughly six tets per vertex), which is the normal
and desirable tet->poly reduction.

The converter is wired into the GUI as the single poly path (no toggle), runs
in a background QThread (`DualPolyWorker`), and always names itself in the
visible log. The tet-generation pipeline is untouched.

## Why the boundary is not the hard part here

`poly_workflow_part2.md` treated "clip the dual cell against the CAD surface"
as the blocking step. That is true of a **Voronoi / circumcentric** dual,
whose cells stick out through the boundary and must be cut back.

This uses the **median (barycentric)** dual, whose boundary dual points already
lie exactly on the primal surface: the boundary vertex itself, boundary-edge
midpoints, boundary-triangle centroids. Each primal boundary triangle (a,b,c)
splits into three planar quads

```
[a, mid(ab), centroid(abc), mid(ca)]  -> cell of a      (and cyclically)
```

that tile it exactly. So the output boundary is *geometrically identical* to
the input boundary, patch for patch. No clipping, no CAD queries, no
tolerances. Total volume is preserved to machine precision (measured drift
0.0 - 1.1e-16).

Orientation is geometric and unambiguous: the dual face for edge (a,b)
separates exactly the cells of a and b, so its normal must point from a to b.
There is no winding heuristic to get wrong.

## Measured results

All numbers are real `checkMesh` runs (OpenFOAM 2512, WSL). Every comparison
is against the *same* tet mesh, not a re-meshed one. Reproduce with
`python tests/bench_tet_poly.py <case> --conv dual --reuse`.

### Reference case — block with cylindrical bore, medium, 251,048 tets

| | terminal-face (retired) | **dual (this module)** |
|---|---|---|
| cells out | 139,227 | 47,528 |
| poly coverage | 80.3% (27,420 tets left) | **100.0%** (0 tets) |
| checkMesh | Failed 2 mesh checks | **Mesh OK** |
| incorrectly oriented faces | 1,073 | **0** ("Face pyramids OK") |
| max skewness | 4.93 (fail) | 2.25 (OK) |
| max non-orthogonality | 88.06 | 54.43 (better than the 66.55 tet input) |
| max aspect ratio | 6.98 | 4.47 |
| total volume | 1.69409e-06 | 1.69409e-06 (identical) |
| conversion time | 17.6 s | 8.8 s |

Stability: three independent runs (fresh GMSH mesh each time, 251,048 /
251,508 / 251,715 tets) all gave **Mesh OK, 100% poly, 0 misoriented faces**,
max skewness 2.25 / 1.97 / 2.03. The historical mutual-agreement-only
baseline on this geometry was 21% poly / 79 misoriented / 1 failed check.

### Real industrial part — `Parte4.stp`, medium, same 824,661-tet mesh

| | terminal-face | **dual** |
|---|---|---|
| poly coverage | 80% | **100.0%** |
| checkMesh | Failed 4 checks | Failed **3** checks |
| incorrectly oriented faces | 3,545 | **895** |
| max skewness | 34.94 | 44.33 |
| non-orthogonality errors | 5 | 55 |
| max aspect ratio | 1,240 (FAIL) | 779 (OK) |
| time | 62.7 s | 33.7 s |

Second, independent run (1,036,918 tets): 100% poly, 854 misoriented, 42
non-ortho errors, max skewness 72.1, Failed 3 checks, 37.7 s (~36 s per
million tets).

**The valve does not reach `Mesh OK`. Reported plainly: 3 failed checks.**

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
positive-volume and watertight — it fails a *convexity-assuming* check. This
affects 268-462 cells = **0.25-0.29% of the mesh**, and it is a property of
the geometry (89 B-Rep surfaces with concave junctions), not of the input mesh
quality. All 446 defective cells on the valve sit on concave feature edges
(verified with the signed concavity test).

## Repairs that were implemented, measured, and rejected

Do not redo these. All are off by default; the numbers are the reason.

1. **Defect-driven cell agglomeration** (merge a failing cell into its
   healthiest neighbour, disjoint pairs, iterate). Monotonically *worse*:
   766 -> 839 -> 1,225 -> 1,835 -> 2,929 inverted pyramids over four rounds.
   Merging two dual cells produces a larger non-convex cell. This
   independently reproduces the wall the terminal-face session hit: **merging
   does not fix shape.** Code removed.

2. **Feature splitting of the vertex star** (`split_rounds`, still present,
   default 0). Partition the tets around a failing vertex into one group per
   smooth surface region and give each group its own cell. The construction is
   correct (watertight, volume-conserving, all invariants pass) but it does
   not pay:
   - full-star split: 895 -> 1,215 -> 1,218 inverted pyramids;
   - **concave-only split** (signed concavity gate, so only genuinely concave
     vertices are split): 895 -> 1,215 — the gate does not help because all
     defective cells were already on concave edges;
   - **boundary-layer-only split** (only the tets touching the boundary are
     partitioned, the rest of the star stays one core cell): 895 -> 1,103 —
     still worse than the baseline.
   Splitting a wrapped cell into wedges trades one non-convex cell for several
   thin ones that invert in their own right. The run loop scores every round
   with an in-process replica of checkMesh's own metrics and keeps the best,
   so enabling any of these can never make the *written* mesh worse.

The signed concavity test itself is verified: a cube has 0 concave vertices,
an L-shaped groove has 7, a narrow-slot part has 86.

## What would actually fix the valve

The defect is non-convexity at concave feature vertices. Merging and wedge
splitting both fail because they keep the cell centroid inside a wrapped
shape. The remaining candidate is to **not place a dual cell corner on a
concave feature at all** — e.g. insert the feature edge as a proper cell edge
and build prism-like cells along it, or accept a locally hybrid boundary layer
there. That is a genuinely different construction, not a tuning knob, and it
is out of scope for the converter itself. The alternative is upstream:
defeaturing the CAD or a feature-aware tet mesh — a separate, reviewed change,
not something to fold silently into the converter.

## Solver validation (the mesh computes)

`checkMesh: Mesh OK` is geometry; it does not prove you can solve on the mesh.
`tools/poly_solver_validation.py` runs `potentialFoam` on the SAME
block-with-bore geometry meshed two ways (tet and dual poly) with identical
boundary conditions and compares the solved fields:

| metric | tet | poly | diff% |
|---|---|---|---|
| total volume | 1.69409e-06 | 1.69409e-06 | 0.00% |
| flow rate through inlet | -9.60e-05 | -9.56e-05 | 0.45% |
| mean \|U\| | 1.177 | 1.175 | 0.19% |
| kinetic energy | 2.572e-06 | 2.560e-06 | 0.49% |
| max \|U\| | 4.41 | 2.57 | 41.7% (resolution: the tet mesh has tiny cells near the bore) |

The poly mesh **converges** (potentialFoam residual -> ~1e-6, continuity error
small) and matches the tet solution within ~0.5% on the conserved quantities
(flow rate, mean velocity, kinetic energy) and exactly on volume. The max-U
difference is a resolution effect of the ~5.5x coarser poly mesh, not a
conversion error. This is the evidence that the conversion is conservative in
practice, not just topologically valid.

## In-process defect detector (the measurement guard)

`_detect_defects` in `tet_poly_dual.py` replicates checkMesh's face-pyramid
test and agrees with it **exactly** on the pyramid count (895 predicted /
895 reported on the valve). This is what makes every number above trustworthy
without a WSL round trip. `tests/test_tet_poly_dual.py` guards this
correspondence offline against a recorded real-valve fixture
(`tests/fixtures/valve_dual.npz` + `valve_dual_checkmesh.txt`), so a change to
the detector that silently drifts from checkMesh is caught.

## Module layout

- `core/tet_poly_dual.py` — the converter (`TetPolyDualConverter`,
  `DualPolyResult`).
- `core/foam_mesh_io.py` — the single home for OpenFOAM polyMesh I/O (ASCII +
  binary, `faceList` + `faceCompactList`, atomic writes). Both converters read
  and write through it.
- `core/terminal_face.py` — the retired merge-based converter. Kept as legacy
  reference only; **not reachable from the GUI** (the Tools -> Polyhedral
  Converter menu and the dual-worker dispatch were removed; the GUI runs the
  dual only).
- `core/openfoam_runner.py` — `DualPolyWorker` (QThread wrapper, backs the tet
  mesh up to `constant/polyMesh_tet_backup` before converting).
- `tests/bench_tet_poly.py` — the unified benchmark (build/reuse case,
  convert, checkMesh, machine-readable JSON row).
- `tests/test_tet_poly_dual.py` — unit + regression tests (no WSL/GMSH,
  < 30 s).
- `tools/tet_poly_dual_cli.py` — CLI for converting an existing case.
- `tools/poly_solver_validation.py` — potentialFoam tet-vs-poly comparison.
- `tools/poly_fixture_builder.py` — builds the cube/groove/slot fixtures and
  the valve regression fixture.

## Reproducing

```bash
# benchmark a case (reuses C:\polybench\<case>\constant\polyMesh_tet_backup)
python tests/bench_tet_poly.py ref1 --conv dual --reuse
python tests/bench_tet_poly.py valve1 --conv dual --reuse

# unit + regression tests (offline, < 30 s)
python -m pytest tests/test_tet_poly_dual.py -q

# solver validation (tet vs poly potentialFoam)
python tools/poly_solver_validation.py

# CLI conversion of an existing case
python tools/tet_poly_dual_cli.py <case_dir> --check
```

## Rules that still apply

- Every claim about mesh quality needs a real `checkMesh` run. No "should
  work".
- Compare converters on the **same** tet mesh. GMSH is non-deterministic; a
  re-mesh invalidates the comparison.
- Run 2-3 times before calling a result stable.
- Do not modify the tet-generation pipeline to make poly conversion easier
  without flagging it as a separate, reviewed change.
- Never swap which converter produced a mesh without an explicit visible log
  line.
