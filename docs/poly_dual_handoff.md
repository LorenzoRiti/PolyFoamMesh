# HANDOFF — tet->poly dual converter (state at end of this session)

Repo: `C:\Users\Davide Valoroso\cfmesh-autogui`, branch `master`, base commit
`1513a39`. WSL2 Ubuntu + OpenFOAM 2512 at `/usr/lib/openfoam/openfoam2512`.
Python with all deps (cadquery, gmsh, PySide6) is
`%LOCALAPPDATA%\Programs\Python\Python311\python.exe` — the default `python` on
PATH has none of them.

**Nothing is committed.** Two new untracked files:
- `src/cfmesh_autogui/core/tet_poly_dual.py` (1,191 lines) — the converter
- `docs/poly_dual_converter.md` — full measured results

The tet pipeline (`gmsh_wrapper.py`, `openfoam_runner.py`, `main_window.py`) and
`terminal_face.py` are **untouched**. The GUI is **not** wired to the new
converter.

## PARALLEL WORK — READ THIS FIRST

Another agent worked on the same goal during this session and left, untracked:
- `src/cfmesh_autogui/commercial/tet_poly_volume.py` (~31 KB)
- `tests/bench_tetpoly_dual.py` — a bench harness whose `--mode dual` is not
  actually wired to a dual converter; it only drives `TerminalFaceConverter`.
  It reads its reference tet mesh from `C:/polybench/ref1/constant/polyMesh_tet_backup`,
  which is a directory **this** session created.
- `src/cfmesh_autogui/core/solution_adaptive.py`, `docs/solution_adaptive_handoff.md`,
  `tools/venturi_amr_validation.py` — unrelated (solution-adaptive refinement).

Nothing imports `tet_poly_dual`, so there is no collision today, but
**before doing anything else, diff `commercial/tet_poly_volume.py` against
`core/tet_poly_dual.py` and decide which one survives.** Two dual converters in
one repo is exactly the "silent swap" confusion that cost the previous session a
day. Do not leave both.

## WHAT WAS BUILT

A **barycentric (median) dual rebuild**, not a merge algorithm.

```
one output cell   per primal VERTEX
one internal face per primal EDGE
boundary faces    = exact subdivision of the original boundary triangles
```

Every tet is consumed, so **poly coverage is 100% by construction** — "residual
tet" is not a concept that exists in this algorithm.

### The key insight (why the "unsolvable" boundary clipping vanished)

`docs/poly_workflow_part2.md` treats "clip the dual cell against the CAD
surface" as blocking. That is true **only of a Voronoi / circumcentric dual**,
whose cells poke out through the boundary.

The **median** dual's boundary points already lie exactly on the primal surface
(the boundary vertex itself, boundary-edge midpoints, boundary-triangle
centroids), so each primal boundary triangle (a,b,c) splits into three planar
quads `[a, mid(ab), centroid(abc), mid(ca)]` (and cyclically) that tile it
exactly. Output boundary == input boundary, patch for patch, bit for bit.
No clipping, no CAD queries, no tolerances. Measured volume drift: 0 to 1.1e-16.

Orientation is geometric and unambiguous: the dual face for edge (a,b) separates
exactly the cells of a and b, so its normal must point a->b. No winding
heuristic exists to get wrong.

## MEASURED RESULTS (real checkMesh, same tet mesh for both converters)

### Reference: block + cylindrical bore, medium, 251,048 tets

| | terminal-face (shipped) | **dual** |
|---|---|---|
| poly coverage | 80.3% (27,420 tets left) | **100.0%** |
| checkMesh | Failed **2** checks | **Mesh OK** |
| misoriented faces | **1,073** | **0** ("Face pyramids OK") |
| max skewness | 4.93 (fail) | 2.25 |
| max non-orthogonality | 88.06 | 54.43 (better than the 66.55 tet input) |
| max aspect ratio | 6.98 | 4.47 |
| total volume | 1.69409e-06 | 1.69409e-06 |
| time | 17.6 s | 8.8 s |

Stability: **3/3 independent runs** (fresh GMSH each: 251,048 / 251,508 /
251,715 tets) -> Mesh OK, 100% poly, 0 misoriented, skew 2.25 / 1.97 / 2.03.
Historical mutual-agreement baseline for reference: 21% poly, 79 misoriented,
1 failed check.

### Real part `Parte4.stp`, medium, same 824,661-tet mesh for both

| | terminal-face | **dual** |
|---|---|---|
| poly coverage | 80% | **100.0%** |
| checkMesh | Failed **4** | Failed **3** |
| misoriented faces | **3,545** | **895** |
| max skewness | 34.94 | 44.33 |
| non-ortho errors | 5 | 55 |
| max aspect ratio | **1,240 (FAIL)** | 779 (OK) |
| time | 62.7 s | 33.7 s |

Second independent run, 1,036,918 tets: 100% poly, 854 misoriented, 42
non-ortho errors, skew 72.1, Failed 3, **37.7 s** (~36 s per million tets).

**The valve does NOT reach Mesh OK. 3 failed checks. Stated plainly.**

## WHY THE VALVE STILL FAILS — measured, not guessed

Of the 766-895 inverted face pyramids:
- **705/766 are boundary faces**, not internal.
- Failing cells have **their own boundary-face normals spanning 90-134 deg**,
  vs **1.1 deg** for a typical boundary cell.
- Their volumes are **normal** (1.79e-9 vs boundary median 2.32e-9) — so this is
  **not** a sliver / primal-quality effect.

Conclusion: where a primal boundary vertex sits on a **concave CAD feature
edge**, its dual cell wraps around the feature and is genuinely **non-convex**,
so the cell centroid falls outside one of its own boundary quads. The cell is
still closed, positive-volume and watertight — it fails a *convexity-assuming*
check. Affects 268-462 cells = **0.25-0.29% of the mesh**. It is a property of
the geometry (89 B-Rep surfaces with concave junctions), not of the tet mesh.

Diagnostic scripts that produced this are in the scratchpad:
`.../scratchpad/diag.py` (classify checkMesh's face sets) and `diag2.py`
(concavity vs sliver vs numerical-noise breakdown).

## TWO REPAIRS TRIED, MEASURED, REJECTED — DO NOT REDO THESE

1. **Defect-driven cell agglomeration** (merge each failing cell into its
   healthiest neighbour, disjoint pairs, iterate). Monotonically **worse**:
   766 -> 839 -> 1,225 -> 1,835 -> 2,929 inverted pyramids over four rounds.
   Merging two dual cells makes a bigger non-convex cell. This independently
   reproduces the previous session's wall: **merging does not fix shape.**
   Code removed.

2. **Feature splitting of the vertex star** (`split_rounds=`, still in the file,
   **default 0**). Partitions the tets around a failing vertex into one group per
   smooth surface region (dihedral-angle clustering of the boundary triangles at
   that vertex, flooded inwards over the star), each group becoming its own cell,
   cut by exact barycentric corners of the interior primal faces. The
   construction is **correct** — watertight, volume-conserving, all invariants
   pass — but it does not pay: **895 -> 1,215 -> 1,218**. Splitting a wrapped
   cell into wedges trades one non-convex cell for several thin ones that invert
   in their own right. Kept, off, with the numbers in the docstring.

The run loop scores every round with an in-process replica of checkMesh's own
metrics and keeps the best round, so enabling either can never make the
*written* mesh worse. **The in-process detector agrees with checkMesh exactly on
the pyramid count (895 predicted / 895 reported)** — that is what makes these
measurements trustworthy without a WSL round trip, and it is the single most
useful piece of infrastructure here.

## WHAT TO DO NEXT (in priority order)

1. **Resolve the duplicate converter** (see PARALLEL WORK above). Decide between
   `core/tet_poly_dual.py` and `commercial/tet_poly_volume.py`. Benchmark both on
   `C:/polybench/ref1` and the valve with the *same* tet input. Delete the loser.

2. **Attack the concave-feature non-convexity — the only thing between the valve
   and `Mesh OK`.** Merging and wedge-splitting are both dead ends (measured).
   The remaining idea: **stop putting a dual cell corner on a concave feature at
   all.** Candidates:
   - detect concave feature edges on the primal surface up front (dihedral angle
     of the *solid*, signed — my current split used unsigned angle, which is why
     it also split harmless convex 90-degree edges and made things worse);
   - along those edges, build prism-like cells that own the feature, and let the
     dual fill the rest;
   - or locally revert to a small hybrid boundary treatment there.
   Note the signed-vs-unsigned angle bug in my `_split_vertex_star` is a real,
   cheap thing to fix first — splitting *only* concave vertices might already
   change the 895 -> 1,215 result. **This was not tested.**

3. **Try `median_faces=True`** in combination with anything above. A/B on the
   valve: `median_faces=False` -> 895 misoriented / skew 44.3;
   `median_faces=True` -> 766 misoriented / skew 470. Neither dominates.

4. Only after the valve reaches `Mesh OK`: **wire the GUI**. Follow
   `TerminalFaceWorker` in `openfoam_runner.py` exactly (cancel via proc_holder,
   `Qt.QueuedConnection`). The converter already accepts `log=` and `cancel=`
   callbacks and emits staged `[poly n/9]` lines for the visible log. Check
   `_auto_quality_fix()` (~line 3133 of `main_window.py`) resets its state
   between retries — that bug class bit the previous session twice. Never swap
   which converter produced a mesh without an explicit visible log line.

5. Consider whether ~5.5x cell reduction (824,661 tets -> 152,086 poly cells) is
   what Lorenzo wants. It is standard and desirable for CFD, but it is a big
   change in cell count and should be surfaced in the GUI, not silent.

## HOW TO REPRODUCE ANYTHING ABOVE

Bench harness used for every number in this document:
`<scratchpad>/polybench.py` (session scratchpad). It does STEP -> gmsh tet ->
`gmshToFoam` -> converter -> `checkMesh`, keeps a `polyMesh_tet_backup` and
restores it before every conversion so both converters see one identical input.

```
python polybench.py ref1  --geom block --detail medium --conv dual
python polybench.py ref1  --reuse --conv terminal     # same mesh, other converter
python polybench.py valve1 --geom valve --detail medium --conv dual
```

Work dir is `C:\polybench` (deliberately space-free; WSL `cd` needs the path
quoted otherwise — that bit me). Cases `ref1`..`ref3`, `valve1`, `valve2`,
`smoke` already exist there with their tet backups, so `--reuse` is instant.

## RULES THAT STILL APPLY

- Every claim about mesh quality needs a real `checkMesh` run. No "should work".
- Compare converters on the **same** tet mesh. GMSH is non-deterministic; a
  re-mesh invalidates the comparison.
- Run 2-3 times before calling a result stable.
- Do not modify the tet-generation pipeline to make poly conversion easier
  without flagging it as a separate, reviewed change.
