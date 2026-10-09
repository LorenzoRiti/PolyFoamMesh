# MEGAPROMPT — Closing the polyhedral mesher + boundary layer

> Complete handoff for an external agent who does NOT know the project.
> Read everything before touching a file. At the end (§10) there is a block you must
> fill in with YOUR contribution.

---

## 1. Operations — a mistake here costs an hour

- **Repo**: `C:\Users\Davide Valoroso\cfmesh-autogui`, branch `master`, Windows 11.
- **Python interpreter with the dependencies** (numpy, gmsh, cadquery, PySide6, pytest):
  ```
  C:\Users\Davide Valoroso\AppData\Local\Programs\Python\Python311\python.exe
  ```
  The `python` on PATH is a broken venv **without pytest**. ALWAYS use the one above.
  In the commands below it is abbreviated `PY`.
- **WSL2 Ubuntu + OpenFOAM v2512** in `/usr/lib/openfoam/openfoam2512`. Needed only
  for `checkMesh` / `gmshToFoam` / the solvers — never to generate the mesh.
- **Work paths WITHOUT spaces**: `OFConfig.validate_case_path()` rejects
  spaces and the WSL conversion eats the space in `C:\Users\Davide Valoroso\...`.
  Use `C:\polybench\` and `C:\polybench2\`.
- **Bench cases already prepared** (with `constant/polyMesh_tet_backup` intact, instant
  reuse): `C:\polybench\ref1` (block with a hole), `C:\polybench\valve1`
  (real valve), `cube1`, `groove1`, `smoke`. Real CAD geometry:
  `C:\Users\Davide Valoroso\Desktop\Report\Parte4.stp` (valve, 89 B-Rep surfaces).
- **Non-negotiable rule**: every claim about mesh quality must be
  supported by a REAL `checkMesh` with real numbers. Never "it should work".
  Compare only on the SAME tet mesh (GMSH is not deterministic: regenerating it
  invalidates the comparison). Reproduce every result 2-3 times before calling it
  stable.
- **Working tree**: do `git status` and `git diff` BEFORE touching anything.
  Commit in phases, with messages stating the measured numbers.

---

## 2. What is already done and works (do NOT redo it)

Production path, all inside this repo, no external tool:

```
STL/STEP → GMSH tet (Windows, native)
         → gmshToFoam (WSL)
         → TetPolyDualConverter   (core/tet_poly_dual.py)   → 100% poly
         → PolyBoundaryLayerEngine (core/bl_poly.py)        → wall prisms
         → checkMesh (WSL)
```

- `core/tet_poly_dual.py` — barycentric/median dual. One cell per primal vertex,
  one internal face per primal edge, boundary = EXACT subdivision
  of the boundary triangles (3 planar quads per triangle). 100% poly by
  construction. No CAD clipping (it is a MEDIAN dual, not Voronoi: the boundary
  points already lie on the surface). It has an **in-process detector of the checkMesh
  metrics that coincides EXACTLY with checkMesh** (895 pyramids predicted /
  895 reported on the valve) — it is the most useful infrastructure in the repo, use
  it to iterate without a WSL round trip.
- `core/bl_poly.py` — OUR advancing-layer BL engine, pure numpy. Extrudes
  anisotropic prisms from the wall quads of the already-built poly mesh. Conforming
  by construction. Internal validation with checkMesh's criteria + keep-best
  fallback (mesh never left half done).
- `core/foam_mesh_io.py` — the single polyMesh reader/writer (ASCII/binary,
  faceList/faceCompactList). Every write goes through here.
- `core/poly_smoother.py` — keep-best Laplacian smoothing, pinned boundary.

### Measured numbers (real checkMesh, to beat)

| Case | Result |
|---|---|
| `ref1` block+hole, 251k tet | **Mesh OK**, 100% poly, 0 misoriented faces, skew 2.25, non-ortho 54.4, 8.8 s |
| venturi coarse | GMSH 39.792 tet → **Mesh OK** → dual 8.074 poly cells → **Mesh OK** |
| cylinder + BL (tools/bench_bl_poly_dual.py) | **505,950 prism cells + 72,483 polyhedra, Mesh OK** |
| `valve1` valve, 824k tet | **Failed 3 checks**, 852 misoriented faces, 1027 residual defects (in-process replica), skew 17.12, non-ortho 95.72, volume conserved ~1e-15 |

The valve baseline is **pinned** in `tools/valve_defect_baseline.py`: any of
your changes must be compared against those numbers, not against a memory.

---

## 3. Avenues ALREADY TRIED AND REJECTED — do not go down them again

The valve's defect: where a primal boundary vertex sits on a **CONCAVE
feature edge** of the CAD, its dual cell wraps around the corner and is
genuinely **non-convex** → the centroid falls outside one of its own
boundary quads → it fails checkMesh's "face pyramids" criterion. The cells are
closed, positive-volume and watertight: it fails a check that ASSUMES convexity.
It concerns 268-462 cells = **0.25-0.29% of the mesh**. It is a property of the
geometry (89 surfaces with concave junctions), not of the tet.

| Attempt | MEASURED outcome | Status |
|---|---|---|
| Defect-driven agglomeration (merge the defective cell into the healthiest neighbor) | Worsens monotonically: 766 → 839 → 1,225 → 1,835 → 2,929 over 4 rounds | Code removed. **DO NOT redo** |
| Vertex-star split with UNSIGNED dihedral angle | 895 → 1,215 (it split the healthy convex edges too) | **DO NOT redo** |
| Vertex-star split with SIGNED concavity test (`split_rounds`) | Implemented and numerically verified (cube: 0 concave; L-shaped groove: concave detected). On the valve it fires on 352 vertices but keep-best discards it: it does not improve | Stays in the file, `split_rounds=0` by default |
| Wedge cells "Direction A" (a wedge cell that OWNS the concave edge, 614 edges) | Implemented, fires, discarded by keep-best | `wedge_cells=True` is enabled by the runner but does not solve it |
| `median_faces=True` | Moves the defect: 852 pyramids → 766, but skew 17 → **470**. Neither dominates | Enabled by default, not the lever |

**An idea that looks good and is dead a priori**: triangulating the defective
boundary faces. The boundary quads are **planar by construction**, so every
sub-triangle lies in the same plane and has the same sign in the pyramids
test. It cannot work. Do not try it.

---

## 4. What is REALLY missing — the three holes, in order of value/cost

### G1 — The BL is applied to the ENTIRE boundary, inlet and outlet included
`core/openfoam_runner.py` line ~1179 calls the BL engine with `apply_to_all=True`
**hardcoded**. For CFD this is wrong: very thin cells against a pressure/velocity
BC, a skewed inlet profile, wasted cells. **It affects every
case, not only pathological geometries.**

Why it is so: `bl_poly._select_faces` (line ~441) auto-closes the selection
across edges, because a partial BL would leave non-manifold seams, and
`_build` (line ~675) raises `ValueError` if an edge has ≠2 selected faces.
It is an implementation limit, not a geometric necessity.

### G2 — The BL gives up GLOBALLY if one local zone is ugly
`bl_poly.run` tries 5 decreasing scales `(1.0, 0.5) … (0.1, 0.05)` on the ENTIRE
mesh; if none passes validation, **no BL anywhere**. A handful
of vertices on concave features zeroes out the work on all the rest (this is the reason
the valve stays without BL). There is already `angle_fade` (line ~563) that fades
the HEIGHT on features, but the NUMBER of layers is uniform, and a layer faded to
5% remains a very thin layer that fails the pyramids criterion instead
of disappearing.

### G3 — The dual's concave defect (0.25% cells, checkMesh non-OK)
See §3. All converter-level avenues are closed with numbers.

---

## 5. THE PLAN — four phases, each with a gate

### PHASE 0 — Decide the acceptance criterion (DO IT FIRST)
**Question**: does that 0.25% of cells make the mesh unusable, or is it just a check
that assumes convexity on cells the solver digests very well?

`tools/poly_solver_validation.py` already does exactly this comparison
(potentialFoam on tet mesh vs poly mesh, same geometry, same BCs: volume,
flow rate, max|U|, pressure statistics) but it is pointed at `ref1`, which already passes.
**Point it at the valve** (`C:\polybench\valve1`, tet backup already present).

- If the valve's poly solves, converges and conserves the flow rate within a few
  % → G3 **is not a bug**, it is a documented limit. The whole budget goes to G1/G2.
- If it diverges or does not conserve → G3 returns to priority, do Phase 3.

**Cost**: hours, not days — the infrastructure is written.
**Gate**: solver numbers reported (residuals, iterations, in/out flow rate,
conservation) + decision written in `docs/`. **Do not skip this phase**: it can
save you the most expensive phase of the plan.

### PHASE 1 — Selective BL per patch (closes G1)
The technical piece: on the boundary edge between BL-zone and non-BL zone, the prism's
side face has only ONE incident prism. Instead of widening the selection
(which is what happens today), that face must become a **BOUNDARY face
assigned to the adjacent patch** (the one without BL). It is exactly what
snappy/cfMesh do at the edge of a layer zone: topologically clean, no non-manifold
seam.

Accounting consequence: `nFaces`/`startFace` of the non-BL patch grow by the
new side faces, and the ordering of the boundary faces must be redone
accordingly. It is the annoying part, but it is bookkeeping, not geometry.

Then the right behavior is exposed: **BL by default only on patches of type
`wall`**, never on inlet/outlet, with an explicit override in the GUI.

- **Verification**: cylinder with named inlet/outlet → BL only on the wall,
  `checkMesh` = **Mesh OK**, and `n_prism_cells == n_layers × wall_faces`
  (NOT × all boundary faces).
- **Gate**: Mesh OK + correct prism count on 2 different geometries.

### PHASE 2 — Local layer termination (closes G2)
Move from variable-height to **variable-count**: where the layers do not
fit, put fewer locally (n → n−1 → … → 0), do not thin everything
everywhere. It is the standard treatment of production BL codes.

**Careful, this is a topological change**: two adjacent wall faces with different n
share side faces of different heights → a transition face is needed that closes
the step. It is the real algorithmic piece of this phase.

Side value: it also improves geometries that pass today, because it stops
thinning the entire BL because of one zone.

- **Gate**: valve with a VALID partial BL (checkMesh) + cylinder unchanged
  compared to the 505,950 prisms / Mesh OK (regression).

### PHASE 3 — The concave defect (G3) — ONLY if Phase 0 justifies it
The converter-level avenues are closed (§3). The one the numbers
leave open is **the local planar cut**: cut the wrapped cell with the
bisector plane of the concave edge, obtaining two convex halves whose
centroids fall inside. It is classic convex decomposition.

The difficulty is not the cut, it is **conformity**: neighboring cells that
share faces with the cut one must be cut along the same plane trace.
It is bounded (only the cells touching the concave edge), but it is a new
algorithm with a WSL regression campaign behind it.

- **High cost, high risk**: it is the only phase that might produce nothing.
- **STOP RULE**: if the planar cut does not beat **852 misoriented faces /
  1027 residual defects** on `tools/valve_defect_baseline.py`, **STOP** and
  document honestly: "100% coverage, better quality on every axis of the
  previous converter, N non-convex cells remain on concave junctions of the
  CAD for reason Y". **No fifth algorithm.**

---

## 6. What NOT to do (measured, not opinions)

- **DO NOT modify the converter to please the BL**, nor the upstream tet pipeline
  (`gmsh_wrapper.py`) to please the converter. Every time this
  constraint was violated it cost a session.
- **DO NOT reopen** `median_faces` / `split_rounds` / agglomeration / unsigned
  split: A/B already done, numbers in §3.
- **DO NOT triangulate the boundary faces** for the pyramids defect: planar, dead a
  priori (§3).
- **DO NOT chase checkMesh-green on the valve before Phase 0.**
- **DO NOT leave two implementations "for safety"**: one converter, one
  writer (`foam_mesh_io`), one parser. It is the failure this work exists
  to close.
- **DO NOT declare success from a single run** (GMSH is not deterministic).
- **DO NOT silently change** which engine produced a mesh: always an explicit
  log line.
- **DO NOT silently replace** a failed mesh with a fallback: the
  product decision is **keep-with-warning** (the mesh is kept, a visible warning,
  tet backup in `constant/polyMesh_tet_backup`).

---

## 7. Verification commands (from repo root)

```powershell
# Fast offline tests (no WSL, no GMSH) — run the two IN THE SAME command:
# intercepts regressions from monkeypatch leaks
& $PY -m pytest tests/test_tet_poly_dual.py tests/test_poly_smoother.py -q --tb=short

# Full suite
& $PY -m pytest tests/ -q --tb=short

# Pinned valve baseline (regression guard, requires WSL)
& $PY tools/valve_defect_baseline.py
& $PY tools/valve_defect_baseline.py --conv-only   # converter only, no checkMesh

# Numerical verification of the signed concavity test (no WSL)
& $PY tools/concavity_verify.py

# BL on our dual, end-to-end with checkMesh (WSL)
& $PY tools/bench_bl_poly_dual.py

# Converter bench on a prepared case, reusing the tet backup (WSL)
& $PY tests/bench_tet_poly.py C:\polybench\valve1 --conv dual --reuse

# Solver validation (Phase 0)
& $PY tools/poly_solver_validation.py
```

---

## 8. Files you will touch (and those you must NOT)

**You will touch**:
- `src/cfmesh_autogui/core/bl_poly.py` — Phases 1 and 2 (`_select_faces` ~441,
  `_build` ~511, edge check ~675, `angle_fade` ~563, scale fallback ~383)
- `src/cfmesh_autogui/core/openfoam_runner.py` ~1159-1199 — passing of the
  BL parameters, hardcoded `apply_to_all`
- `src/cfmesh_autogui/gui/main_window.py` — exposure of the patch choice
- `tools/poly_solver_validation.py` — Phase 0, point it at the valve
- `tests/` — new tests for every phase
- `docs/residual_risks.md` + `docs/dev/poly_mesher_STATUS.md` — update at the end of the work

**Do NOT touch without declaring it as a separate, reviewed change**:
- `src/cfmesh_autogui/core/gmsh_wrapper.py` (upstream tet pipeline)
- `src/cfmesh_autogui/core/tet_poly_dual.py` (converter algorithm) —
  except Phase 3, if and only if Phase 0 justifies it
- `src/cfmesh_autogui/core/foam_mesh_io.py` (single writer, stable contract)

**Context documents to read**:
- `docs/poly_mesher_megaprompt.md` — the complete history and numbers of every
  attempt (it is the source of §3)
- `docs/residual_risks.md` §Polyhedral Conversion — accepted limits
- `docs/dev/poly_mesher_STATUS.md` — careful: it discusses the NATIVE CARTESIAN
  mesher, which is a SEPARATE experimental branch (Tools menu), not the production path
  described here. Do not confuse them.
- `docs/poly_dual_handoff.md` — **partially OBSOLETE**: it says the signed
  concavity test and the wedge cells are to be done, but they are already implemented and already
  measured. Trust §3 of this document.

---

## 9. Using subagents — how to parallelize without hurting yourself

**The phases are sequential by dependency** (Phase 2 modifies the same
structures as Phase 1): do NOT launch subagents that write to `bl_poly.py` in
parallel. What parallelizes well:

**Wave 1 — reconnaissance (parallel, read-only, before writing code)**
- SA-1: exact map of the BL data flow — who calls `PolyBoundaryLayerEngine`,
  with which parameters, where they come from (GUI → worker → engine). Output: call
  chain with file:line.
- SA-2: map of the patch bookkeeping in `bl_poly._build` — where
  `nFaces`/`startFace` are recomputed, which invariants the face
  ordering must respect (internal sorted by owner/neighbour, boundary in patch
  order). Output: the list of invariants not to break in Phase 1.
- SA-3: inventory of existing tests covering BL and dual — which will fail
  if I change the face selection. Output: list of tests + what they assert.
- SA-4: reads `docs/poly_mesher_megaprompt.md` and confirms/disproves every number
  cited in §3 of this document. Output: discrepancies found.

**Wave 2 — Phase 0 (parallel, independent)**
- SA-5: adapts `poly_solver_validation.py` to the valve and runs it.
- SA-6: in parallel runs `valve_defect_baseline.py` to confirm that the
  pinned baseline is still valid on the current working tree.

**Wave 3 — implementation (SEQUENTIAL, a single agent writes)**
Phase 1, then Phase 2. One subagent at a time on `bl_poly.py`. You can however keep
in parallel an **adversarial verifier** subagent that, given the diff, tries to
build a geometric counterexample that breaks the new face selection
(patches that touch on an edge, patches with a single face, a disconnected
patch). It does not write code: it produces test cases.

**Rule for every subagent**: it must return NUMBERS and file:line, not
reassurances. If a subagent says "it should work", its output is
worthless — relaunch it asking for the measurement.

---

## 10. YOUR CONTRIBUTION — mandatory block to fill in

Do not limit yourself to executing the plan: add something of your own. Fill in and deliver:

```python
CONTRIBUTO = {
    "autore": "<model/agent, date>",

    "comprensione": """
    <What the poly+BL path is, where the residual defect is, why the avenues in
    §3 are closed. Demonstrate that you understood the difference between the
    production path (GMSH tet -> dual -> BL) and the native Cartesian mesher.>
    """,

    "fase_0_esito": """
    <The solver numbers on the valve: residuals, iterations, in/out flow rate,
    volume conservation. And the DECISION that follows: is G3 a bug or a
    documented limit?>
    """,

    "cosa_ho_implementato": """
    <For each phase: files touched, algorithm, and the NUMBERS measured before/after
    (checkMesh: verdict, misoriented faces, skew, non-ortho, aspect, volume,
    time; BL: n. prism cells, total thickness).>
    """,

    "proposta_personale": """
    <An idea of your own, technical and measurable, that was not in the plan. With the number that
    supports it or the experiment that would verify it.>
    """,

    "limiti_residui": """
    <What remains open and WHY, with the geometric/algorithmic cause. If you
    stopped for Phase 3's stop rule, say it with the numbers.>
    """,

    "regressioni": "<green/red tests, and every declared regression>",
}
```

## 11. Expected delivery

At the end show: the NUMBERS (checkMesh verdict, misoriented faces, skew,
non-ortho, aspect, volume, time, prism cells), the files touched, the green tests, and
every residual limit with its cause. **No "it should work".**
