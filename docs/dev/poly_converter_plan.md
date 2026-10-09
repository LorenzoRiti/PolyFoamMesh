# PROMPT — Consolidate tet→poly into ONE well-made converter

This is the work prompt. Read it all before touching a file.

---

## 0. Operating context

Repo: `C:\Users\Davide Valoroso\cfmesh-autogui`, branch `master`, base `1513a39`.

- **Python with the dependencies**: `%LOCALAPPDATA%\Programs\Python\Python311\python.exe`.
  The default `python` on PATH **does not have** cadquery/gmsh/PySide6. Getting the
  interpreter wrong is the first way to lose an hour.
- **OpenFOAM 2512** in WSL2 Ubuntu: `source /usr/lib/openfoam/openfoam2512/etc/bashrc`.
  Warning: `cd` to a path with spaces must be **quoted**, otherwise bash silently gives
  "too many arguments".
- **Cases already prepared** in `C:\polybench\` (`ref1..ref3`, `valve1`, `valve2`,
  `smoke`), each with `constant/polyMesh_tet_backup`: you restart from the identical
  tet mesh without redoing GMSH. **Use them** — regenerating the mesh invalidates every
  comparison, because GMSH is not deterministic.
- Real geometry: `C:\Users\Davide Valoroso\Desktop\Report\Parte4.stp`
  (89 surfaces, 3.0 × 0.255 × 0.255 m, 750k–2.2M tet depending on the detail).

**Non-negotiable rule**: every claim about mesh quality must be
supported by a real `checkMesh` with real numbers. Never "it should work".
Compare only on the **same** tet mesh. Every result must be reproduced 2–3 times
before calling it stable.

---

## 1. Where we start (measured state, not opinions)

There are **three** tet→poly implementations in the repo. That is the main problem.

| file | what it is | state |
|---|---|---|
| `core/tet_poly_dual.py` | barycentric dual, ~1,200 lines | works, it is the best measured, wired into the GUI |
| `core/terminal_face.py` | tet merge (Salinas 2023), 3 variants | legacy, worse on every axis |
| `commercial/tet_poly_volume.py` | parallel attempt by another agent, ~31 KB | **nobody imports it**, never measured |

### Numbers, same tet mesh for both converters

**Block with cylindrical bore, 251,048 tet**

| | terminal-face | **dual** |
|---|---|---|
| poly coverage | 80.3% (27,420 residual tets) | **100.0%** |
| checkMesh | Failed **2** | **Mesh OK** |
| misoriented faces | **1,073** | **0** |
| max skewness | 4.93 (fail) | 2.25 |
| max non-orthogonality | 88.06 | 54.43 |
| max aspect ratio | 6.98 | 4.47 |
| cells out | 139,227 | 47,528 |
| time | 17.6 s | 8.8 s |

3 independent runs of the dual (fresh GMSH mesh each time: 251,048 / 251,508 /
251,715 tet) → **Mesh OK, 100%, 0 misoriented faces** all three.

**Valve `Parte4.stp`, 824,661 tet**

| | terminal-face | **dual** |
|---|---|---|
| poly coverage | 80% | **100.0%** |
| checkMesh | Failed **4** | Failed **3** |
| misoriented faces | **3,545** | **895** |
| max skewness | 34.94 | 44.33 |
| non-orthogonality errors | 5 | 55 |
| max aspect ratio | **1,240 (FAIL)** | 779 (OK) |
| time | 62.7 s | 33.7 s |

Second independent run (1,036,918 tet): 100%, 854 faces, 42 non-orthogonality
errors, skew 72.1, Failed 3, **37.7 s** (≈36 s per million tet).

### Why the dual is the right algorithm

It does not merge tet. It builds the **dual complex**: one cell per primal
**vertex**, one internal face per primal **edge**, and on the boundary an
**exact** subdivision of the original surface triangles. 100%
coverage is structural, not obtained by force of thresholds.

The "boundary clipping against the CAD" that `poly_workflow_part2.md` treated as
nearly unsolvable **does not exist** in this construction: it holds only for a
**Voronoi/circumcentric** dual. The **median** dual uses points that already lie
exactly on the primal surface, so every boundary triangle (a,b,c) splits
into the three planar quads `[a, mid(ab), centroid(abc), mid(ca)]` that tile it
exactly. Output boundary identical to input boundary, patch
by patch. Volume conserved to machine precision (measured drift 0 – 1.1e-16).
Orientation is also geometric and unambiguous: the dual face of edge
(a,b) separates exactly the cells of a and b, so the normal **must** point
from a to b — there is no winding heuristic to get wrong.

### Why the valve does not pass — measured diagnosis

Of the 766–895 inverted face pyramids:

- **705/766 are boundary faces**, not internal;
- the failing cells have **their own boundary normals spanning
  90–134°**, against **1.1°** for a typical boundary cell;
- their **volume is normal** (1.79e-9 vs a median of 2.32e-9) → it is **not** a
  sliver or tet-quality problem.

Conclusion: where a primal boundary vertex sits on a **concave feature
edge of the CAD**, its dual cell wraps around the feature and is genuinely
**non-convex**, so the cell's barycenter falls outside one of its own
boundary quads. The cell stays closed, positive-volume and sealed: it fails
a check that **assumes convexity**. It concerns 268–462 cells = **0.25–0.29%**.

### Two repairs already tried and rejected — do NOT redo them

1. **Defect-driven agglomeration** (merge the defective cell into the healthiest
   neighbor, disjoint pairs, iterate): worsens monotonically,
   766 → 839 → 1,225 → 1,835 → 2,929. Merging two dual cells produces a larger
   non-convex cell. It independently reproduces the wall of the terminal-face
   session: **merging does not fix shape.** Code removed.
2. **Vertex-star split** (`split_rounds`, still in the file, default
   0): 895 → 1,215 → 1,218. The construction is correct (sealed, preserves the
   volume, all invariants pass) but it does not pay: breaking a wrapping cell
   into wedges swaps one non-convex cell for several thin ones that
   invert on their own. **It does however have a known, never-fixed bug**: it uses
   the **unsigned** dihedral angle, so it also splits the **convex** 90°
   edges that were perfectly fine. See Phase 3.

### The infrastructure that is worth more than the code

`tet_poly_dual.py` contains an in-process replica of the checkMesh metrics
(`_cell_centres`, `_detect_defects`) that **coincides exactly** with checkMesh
on the inverted-pyramid count (895 predicted / 895 reported). It is this
that makes the measurements above trustworthy without a WSL round trip, and it is to be
preserved and tested.

---

## 2. The decision to make

**One converter: the dual.** Not a flag, not two paths, not a fallback
to terminal-face. The reasons are measured, not aesthetic: on the same mesh the
dual dominates on coverage, misoriented faces, aspect ratio, time, and on both
geometries. There is no scenario in which terminal-face is the right choice.

Keeping two converters is exactly the class of confusion ("which one produced
this mesh?") that has already cost this project a day.

---

## 3. The plan

### Phase 0 — Consolidation (half a day)

**Goal: a single implementation, zero dead code, a test bench in the repo.**

1. **Decide on `commercial/tet_poly_volume.py`.** First really read it, then
   measure it with the same bench on the same mesh as `C:\polybench\ref1`. If it does not
   beat the dual (prediction: no), **delete it**. If it has a better idea on
   some aspect, bring it into the dual and then delete it. Do not leave two duals.
2. **Extract the I/O into `core/foam_mesh_io.py`.** Today `tet_poly_dual.py` imports
   `TerminalFaceConverter` only to use its static readers — an architecturally
   wrong dependency. Move there: reading points/faces/owner/
   neighbour/boundary (ASCII **and binary**, `faceList` **and** `faceCompactList`,
   including the `_binary_header_parse` fix of commit `1513a39`) and the bulk
   writing. Point both converters there.
3. **Retire terminal-face from the poly path.** Remove the menu entry
   `Tools → Polyhedral Converter` and the double-worker dispatch. The module may
   stay in the repo marked legacy, but it must no longer be reachable from the GUI.
   Delete the already-dead variants inside it (`_merge_leftover_tets`,
   `_merge_leftovers_safe`, `_augment_disjoint_pair_matching`): their numbers
   are documented, the code is no longer needed.
4. **Bring the test bench into the repo** as `tests/bench_tet_poly.py`.
   Today it lives in a session scratchpad and will disappear. It must: build the case from
   STEP, `gmshToFoam`, save/restore `polyMesh_tet_backup`, convert,
   `checkMesh`, and emit a **machine-readable result row** (JSON) with
   coverage, verdict, misoriented faces, skewness, non-orthogonality, aspect
   ratio, volume, time. Unify with `tests/bench_tetpoly_dual.py` of the other
   agent, do not keep both.

**Exit criterion**: a single importable `TetPolyConverter`; `ref1` and `valve1`
reproduced with the numbers of the table above through the bench in the repo.

### Phase 1 — Safety net before touching the algorithm (half a day)

Needed **before** modifying the geometry, otherwise you do not know whether a change
improved or broke it.

1. **Unit tests** on tiny hand-built meshes (no GMSH, no WSL):
   - a single tet → one dual cell per vertex, closed, positive volume;
   - two tet sharing one face;
   - a tetrahedralized cube: verify closure (Σ area vectors = 0 per cell),
     total volume conserved, upper-triangular ordering, `owner < neighbour`,
     no unused point, every boundary triangle → exactly 3 quads
     in the right patch;
   - rejection of a non-tet input (prisms/boundary layer) with a clear message;
   - rollback: if the write fails, the original `polyMesh` stays intact.
2. **Metric regression tests**: verify that
   `_detect_defects` keeps coinciding with checkMesh on the pyramid
   count (today 895/895 on the valve). If this correspondence breaks,
   every subsequent measurement becomes unreliable.

**Exit criterion**: the tests run in < 30 s without WSL and without GMSH.

### Phase 2 — The validation that really matters: run a solver (half a day)

**This is the piece that is completely missing today and is worth more than closing the 895
faces.** `checkMesh: Mesh OK` is geometry, it does not say you can compute on it.

1. On the block case, poly mesh: set up a real case (`potentialFoam` as a
   smoke test, then laminar `simpleFoam` or `scalarTransportFoam`) and **make it
   converge**. Record residuals and iteration count.
2. Do the same on the **tet** mesh of the same case and compare: flow rate,
   pressure drop, total volume. They must coincide within physical tolerance.
   It is this that proves the conversion is conservative in practice, not only
   in `checkMesh`.
3. Repeat on the valve. If it converges despite the 3 failed checks, it is
   **enormous** information: it means the correct bar was not `Mesh OK`.
   If it diverges, you know exactly why Phase 3 matters.

**Exit criterion**: a poly case that converges, with numbers compared against
the tet, saved in the documentation.

### Phase 3 — Close the gap on the valve (1–2 days, with a stop rule)

The defect is non-convexity on **concave** features. In order of increasing
cost. **Stop at the first one that works.**

1. **Split only on concave vertices** (the cheap lead never tested).
   The existing attempt uses the unsigned dihedral angle and therefore splits also
   the convex edges, which were fine — the probable cause of the worsening
   895→1,215. A **signed** concavity test is needed: for a boundary edge
   (a,b) shared by triangles T1 (outward normal n1, opposite vertex p1) and T2
   (n2, p2), the edge is concave if `(p2 − centroid(T1)) · n1 > 0`.
   **Verify this test numerically on a cube (all convex) and on a
   groove (known concave) before using it** — getting the sign wrong here makes
   the right idea look failed, which is exactly what has already happened.
   Then split only the vertices that are (a) incident to at least one concave
   edge **and** (b) currently defective.
2. **If the wedges remain too thin**: partition only the tet layer in
   contact with the boundary instead of propagating the cut through the whole
   star. The resulting cells are more compact.
3. **If this also fails**: the problem is not in the converter, it is upstream.
   Consider defeaturing or a tet mesh aware of the feature edges —
   and this must be opened as a **separate, reviewed change**, not hidden in the
   converter's commit.

**Stop rule**: if after point 2 the valve does not pass, **stop and report
honestly**. Do not invent a fourth conversion algorithm. "100% coverage,
better quality than the previous converter on every axis, N non-convex cells
remain on concave junctions of the CAD for reason Y" is an acceptable and valuable
result. Continuing to invent heuristics is repeating yesterday's wall with another name.

Try in combination, not alone: `median_faces=True`. A/B on the valve:
`False` → 895 faces / skew 44.3; `True` → 766 faces / skew 470. Neither
dominates; it could change in combination with the correct split.

### Phase 4 — Product: make it honest in the GUI (half a day)

1. **A single poly path**, no toggle, with the log line that always names
   the converter and the outcome (already implemented, keep it).
2. **State the cell count change.** It goes from 824k tet to 152k poly cells.
   It is the normal and desirable gain of the polyhedral approach, but without explaining it
   the first reaction is "it threw away 80% of my mesh". The log must state
   cells before → after, poly coverage, and that for a given target resolution
   you must **mesh finer upstream**.
3. **Explicit quality gate.** Decide and implement what happens when
   `checkMesh` fails on the poly mesh: keep it with a visible warning, or
   roll back to the tet. **It is a product choice, to be asked of Lorenzo, not
   decided by the code.** Whatever it is, it must be visible in the log and reversible
   (the tet backup already exists).
4. Check that `_auto_quality_fix()` (~line 3133 of `main_window.py`) resets
   its own state between attempts: that class of bug has bitten twice
   in a single session.

### Phase 5 — Documentation and closure

A single document, `docs/poly_converter.md`, that replaces
`poly_workflow_part2.md`, `poly_dual_converter.md` and `poly_dual_handoff.md`.
It must contain: the algorithm and **why** the boundary needs no clipping; the
table of measured numbers; the rejected repairs **with their numbers**; the
residual limit and its geometric cause; how to reproduce everything.
Update `poly_workflow_part2.md` with a cross-reference, do not let it contradict
the real state.

---

## 4. Final acceptance criteria

Do not consider it finished until, on **both** block and valve, you can
show:

1. **Poly coverage**: real percentage. Not rounded up, not the
   best of several runs.
2. **checkMesh**: the **complete** `Failed N mesh checks` line, or `Mesh OK`.
3. **Misoriented faces**: exact count, compared with terminal-face
   on the **same** tet mesh (1,073 on the block, 3,545 on the valve).
4. **Max skewness and max non-orthogonality**: exact values, flagging
   explicitly whether they are **new** failure categories.
5. **A solver that converges** on poly mesh, with results compared against
   the tet mesh (Phase 2).
6. **Time** at real scale (1–2M tet). Current reference: ≈36 s per million.
   Do not regress by an order of magnitude without declaring it as a trade-off.
7. **Stability**: 3 runs on the same input, numbers reported for all three.
8. **A single converter** reachable from the GUI, and no orphan dual module
   in the repo.

---

## 5. What NOT to do

- Do not add a fourth conversion algorithm. If Phase 3 points 1–2
  fail, the problem is upstream of the converter.
- Do not redo agglomeration or the unsigned split: both measured, both
  worsening, numbers above.
- Do not leave two implementations "for safety". It is the failure this
  plan exists to close.
- Do not touch the tet generation pipeline (`gmsh_wrapper.py`,
  `openfoam_runner.py` on the GMSH side, the tet flow of `main_window.py`) to
  "help" the poly conversion. If needed, it is a separate, cross-cutting change
  reviewed apart.
- Do not declare success from a single run: GMSH is not deterministic.
- Do not silently change which converter produced a result.
