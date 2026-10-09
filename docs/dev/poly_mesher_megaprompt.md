# MEGA PROMPT — OUR POLY MESHER: where to restart, what to do, how to continue

> This is a complete work prompt for an EXTERNAL AGENT who does not know the
> project. Read it all before touching a file. At the end there is a CODE BLOCK
> that you absolutely must fill in with YOUR contribution (section 10).

---

## 1. Operations — read first (a mistake here costs an hour)

- **Repo**: `C:\Users\Davide Valoroso\cfmesh-autogui` (Windows 11, WSL2 Ubuntu,
  OpenFOAM 2512 in `/usr/lib/openfoam/openfoam2512`), branch `master`.
- **Python interpreter with the dependencies** (gmsh, numpy, cadquery, PySide6):
  ```
  C:\Users\Davide Valoroso\AppData\Local\Programs\Python\Python311\python.exe
  ```
  The `python` on PATH is a broken venv without pytest. ALWAYS use the one above.
- **The working tree is DIRTY and must NOT be committed** (previous sessions not
  committed). Before touching any file: `git status` and `git diff` to
  separate the others' work from yours.
- **Work dir without spaces**: `OFConfig.validate_case_path()` rejects spaces.
  WSL cases already prepared in `C:\polybench\` (`ref1`, `valve1`, `smoke`, ...).
  Real geometry: `C:\Users\Davide Valoroso\Desktop\Report\Parte4.stp`
  (valve, 89 surfaces, 3.0×0.255×0.255 m).
- **Non-negotiable rule**: every claim about mesh quality must be
  supported by a real `checkMesh` with real numbers. Never "it should work".
  Compare only on the SAME tet mesh (GMSH is not deterministic: regenerating it
  invalidates the comparison). Every result must be reproduced 2–3 times.

---

## 2. The work: "our poly mesher"

The user (Lorenzo/Davide, CFD engineer — he likes NUMBERS, not
reassurances) wants the app's **polyhedral** meshing path to become
**concrete, serious, reliable, robust, lean, mathematically
perfect and reviewable as a product of ours** — not a set of modules
left by different sessions.

The current poly path (all inside this repo):

```
GUI "CFD Poly" (gmsh_direct_poly)
  └─ main_window._start_gmsh_volume_worker  (src/cfmesh_autogui/gui/main_window.py:2432)
       └─ GmshVolumeWorker → gmsh_wrapper.generate_volume_mesh  (core/gmsh_wrapper.py:1509)
            GMSH tet on Windows (HXT, MSH 2.2), adaptive sizing from CAD
       └─ convert_to_foam → gmshToFoam in WSL  (core/openfoam_runner.py / config.py:298)
       └─ checkMesh (tet) → if poly requested:
       └─ DualPolyWorker → TetPolyDualConverter  (core/tet_poly_dual.py, OUR converter)
            barycentric/median dual: 1 cell per primal vertex, 100% poly
            by construction, boundary = exact subdivision of the boundary triangles
       └─ checkMesh (poly) → keep-with-warning (Lorenzo's decision, see §5)
```

Parallel headless path (QuickMesh/one_click_run): `commercial/mesh_engine.py`
(TETRAHEDRAL / POLY_AGGREGATED) that uses `core/mesh_converter.py` (meshio MSH→OF
converter) and `commercial/poly_aggregator.py`.

---

## 3. The REAL state now (measured on 2026-08-03) — restart from here

### Done and working (do not redo it)
- `core/tet_poly_dual.py` (1,567 lines) is the **ONLY** tet→poly converter.
  Barycentric/median dual: guaranteed invariants (closure, positive volume,
  owner<neighbour, 3 quads per boundary triangle, atomic rollback), in-process
  detector of the checkMesh metrics that coincides EXACTLY with checkMesh
  on the valve (895 pyramids predicted / 895 reported).
  Measured numbers (same tet mesh):
  - Block with hole (251k tet): **Mesh OK, 100% poly, 0 misoriented faces,
    skew 2.25, non-ortho 54.4, 8.8 s.**
  - Valve Parte4.stp (824k tet): **Failed 3 checks, 895 inverted face
    pyramids (705/766 are BOUNDARY faces), 0.25–0.29% of the cells** — cells
    closed, positive volume, watertight; they fail a check that assumes
    convexity. Cause: primal boundary vertices on **CONCAVE feature
    edges of the CAD** → non-convex dual cell (boundary normals 90–134° vs
    1.1° typical). It is a property of the geometry, not of the tet.
- **SIGNED-concavity split** implemented in tet_poly_dual.py
  (`_plan_splits` ~914, `_concave_boundary_edges` ~1210, `_split_vertex_star`
  ~1264, default `split_rounds=0` = OFF). Sign test verified on a cube
  (0 concave edges) and an L-shaped groove (concave detected). **NEVER measured
  on the valve** — it is the main lead of Phase 2.
- `core/foam_mesh_io.py`: polyMesh read/write (ASCII), used by
  tet_poly_dual and its tests.
- `core/poly_smoother.py`: keep-best Laplacian smoothing (pinned boundary).
- GUI: `_launch_gmsh_poly_dual` (main_window.py:3614) → DualPolyWorker.
- `poly_dual_risk` warning (gmsh_wrapper.py:811, main_window.py:2629).
- Tests: `tests/test_tet_poly_dual.py` (invariants + 895/895 regression),
  `tests/test_dual_poly_options.py`, `tests/test_poly_smoother.py`,
  `tests/test_mesh_converter.py`, `tests/test_poly_aggregator.py`.
  Fixture: `tests/fixtures/valve_dual.npz` + `valve_dual_checkmesh.txt`.
- Bench: `tests/bench_tet_poly.py` (machine-readable JSON row),
  `tools/tet_poly_dual_cli.py`, `tools/poly_fixture_builder.py`,
  `tools/poly_solver_validation.py` (potentialFoam tet vs poly).

### DONE AT THE END OF THE 2026-08-03 SESSION (Phase 1 consolidation, lane B — VERIFIED in the working tree)
- `core/terminal_face.py` **DELETED** (1559 lines, retired merge
  converter: rejected with numbers on every axis — "DO NOT REDO").
- `commercial/polyhedral_preprocessor.py` **DELETED** (1126 lines, duplicated
  circumcentric dual with real bugs: `vert_to_cells[ci]` line 859, dead
  code 861, smoothing that also moves the boundary vertices).
- `tests/test_polyhedral_preprocessor.py` **DELETED** (639 lines).
- `commercial/__init__.py` cleaned of dead re-exports.
- `tests/bench_tet_poly.py` updated (references to the retired converter).
- **GUI**: callbacks `_on_terminal_face_finished/_failed` RENAMED to
  `_on_gmsh_polydual_finished/_failed` (main_window.py:3658-3673) and the
  result now reads the CORRECT `DualPolyResult` attributes
  (`result.n_cells_after`, `self._cells_before_poly` — before it read
  `n_tets_before/n_polyhedra` which do not exist).
- `tests/test_tet_poly_dual.py:246` **monkeypatch leak FIXED**
  (`test_write_failure_rollback` restored `fio.write_faces` by re-reading
  the attribute it had just replaced → `boom` stayed installed and broke
  test_poly_smoother when run in sequence. It now saves the reference
  before the patch).

### IN PROGRESS OR NOT DONE (ALWAYS verify with git status before assuming)
- **LANE A (core consolidation) — PARTIAL/IN PROGRESS by another session**:
  at the moment the following have NOT yet appeared in the working tree:
  - `core/quality_thresholds.py` (single source of the quality thresholds:
    mesh_engine.ADAPTIVE_THRESHOLDS {skew 0.9, non-ortho 70, aspect 1000} at
    mesh_engine.py:57, quality_engine.THRESHOLDS likewise at quality_engine.py:32,
    optimizer.THRESHOLDS {skew **4.0**, non-ortho 65, aspect 1000} at
    optimizer.py:136 — the discrepancy 0.9 vs 4.0 must be centralized WITHOUT
    changing the values, flagging it as a decision to review).
  - Unification of the duplicated polyMesh writers: `core/mesh_converter.py`
    (its own writer ~313-400) and `commercial/poly_aggregator.py`
    (writer ~217-241 and `_write_poly_mesh` ~944-1051) must delegate to
    `foam_mesh_io.write_polymesh`.
  - Unification of the checkMesh parser: `quality_engine._parse_metrics`
    (quality_engine.py:237) → reuse `openfoam_runner.parse_checkmesh_output`
    (openfoam_runner.py:789).
  - `mesh_converter.py` ~250: silent fallback
    `face_cell_map.get(tuple(sorted(fv)), (0, 0))[0]` (owner 0 if the key is not
    present) → determinism (assert/descriptive error).
  - `np.fromstring` deprecated in mesh_converter/poly_aggregator.
  - `MeshQualityReport` (openfoam_runner.py:684): add `summary()`
    on one line (idea collected from polyhedral_preprocessor, now deleted).
- **The valve's tet→poly is NOT closed** (895 pyramids) — Phase 2.
- **Docs to consolidate**: `docs/poly_converter.md` +
  `docs/poly_dual_converter.md` (duplicates), `docs/poly_dual_handoff.md`,
  `docs/poly_workflow_part2.md` → a SINGLE definitive `docs/poly_converter.md`
  (Phase 4). `docs/poly_converter_plan.md` stays as the plan reference.
- `.slim/deepwork/poly-mesher.md` — progress file of the deepwork session
  (git-ignored, but readable).

---

## 4. The phased plan (in order; each phase has a review gate)

### PHASE 1 — Consolidation (lean): ONE writer, ONE parser, ZERO dead code
Much is already done (see §3). Complete only what turns out to be missing
after `git status`:
1. `core/quality_thresholds.py` (centralized thresholds, values UNCHANGED).
2. mesh_converter + poly_aggregator → delegate to `foam_mesh_io`.
3. quality_engine → unified parser.
4. Fix silent owner-0 + `np.fromstring`.
5. `MeshQualityReport.summary()`.
6. Do not modify: gmsh_wrapper.py, tet_poly_dual.py (algorithm),
   docs/ (phase 4).
**Verification**: `pytest tests/test_mesh_converter.py tests/test_poly_aggregator.py
tests/test_mesh_engine.py tests/test_quality_engine.py tests/test_openfoam_runner.py
tests/test_checkmesh_parsing.py` + package import smoke test.
**Gate**: Oracle review.

### PHASE 2 — Mathematical correctness of the dual (the heart)
Goal: close the 895 pyramids on the valve, or stop with honest numbers.
1. **Measure the signed split** (`split_rounds=1`, `_split_boundary_layer_only`
   if present) on the valve with a real bench:
   `C:\polybench\valve1` (already prepared with `polyMesh_tet_backup`) →
   `tests/bench_tet_poly.py <case> --conv dual --split ...` → checkMesh.
   A/B comparison: `split_rounds=0` vs `1`, combined with `median_faces=True`
   (A/B measured in the past: False → 895 faces/skew 44.3; True → 766/skew 470;
   neither dominates alone — it could change in combination).
2. Verify NUMERICALLY the signed concavity test before trusting it
   (cube = all convex; groove = known concave). The wrong sign has
   already made a right idea look failed (895→1,215 with unsigned angle).
3. **Stop rule**: if after the signed split + variants the valve does not pass,
   STOP and report honestly: "100% coverage, better quality than the
   previous converter on every axis, N non-convex cells remain on
   concave junctions of the CAD for reason Y". No fourth algorithm.
4. Do not touch the upstream tet pipeline to "help" the conversion (it is a
   separate, reviewed cross-cutting change).
**Gate**: Oracle review.

### PHASE 3 — Pipeline robustness (reliable)
1. `mesh_engine._execute_algorithm` POLYHEDRAL (mesh_engine.py:448-457):
   if polyDualMesh fails today it keeps the hex **silently** → explicit log +
   visible decision (keep-hex-with-warning), never silent.
2. `quality_engine.auto_fix` / `optimizer` relaunch `cartesianMesh` on
   ANY existing mesh → if the case is GMSH/poly, today it would silently
   replace it with a cfMesh hex of different dimensions → explicit guard
   (refuse or require confirmation; never silent replacement).
3. `main_window._on_gmsh_volume_failed` (main_window.py:2652): the retry without
   BL re-reads the spinboxes instead of reusing the first attempt's parameters →
   reuse the real values used.
4. `GmshVolumeWorker` (openfoam_runner.py:1814): 10 positional arguments +
   env var side channel → contract to keyword args, externally unchanged.
5. Explicit poly quality gate in the log: cells before→after, checkMesh verdict,
   converter name always present (already partly there — complete it).
**Gate**: Oracle review.

### PHASE 4 — Documentation and closure (reviewable)
1. A SINGLE document `docs/poly_converter.md`: algorithm and WHY the boundary
   requires no clipping (median dual, not Voronoi); table of measured numbers;
   rejected repairs WITH their numbers; residual limit and geometric cause;
   how to reproduce everything. Replace `poly_dual_converter.md`,
   `poly_dual_handoff.md`, `poly_workflow_part2.md` with cross-references.
2. **Mathematical spec** of the dual (formal invariants, for the
   "mathematically perfect" requirement).
3. If WSL is available: `tools/poly_solver_validation.py` (potentialFoam tet vs
   poly) — record residuals/iterations/flow rate; conservativeness comparison.
4. Full suite: `pytest tests/` green (or declared regressions).
5. Update `.opencode/skills/cfmesh-autogui/SKILL.md` with the poly section.
**Gate**: final Oracle review.

---

## 5. Product decisions already made (do not re-litigate them)

- **Dead code**: collect the useful ideas (quality report, thresholds, pipeline
  shape) and delete — DONE (Phase 1 lane B).
- **Poly quality gate**: if the poly mesh fails checkMesh → **keep-with-
  warning**: the mesh is kept, a visible warning in the log, tet backup in
  `constant/polyMesh_tet_backup` already existing. No automatic rollback.
- **Real WSL benchmarks on the valve**: AUTHORIZED (10-40 min/run), with an
  honest stop rule.
- The tet→poly cell drop (~5.5x) must ALWAYS be stated in the log (already implemented).

## 6. What NOT to do (measured, not opinions)

- **No fourth conversion algorithm.** If Phase 2 fails, the problem
  is upstream of the converter (defeaturing / feature-aware tet — a
  separate, reviewed change).
- **DO NOT redo the defect-driven agglomeration** (worsens monotonically:
  766→839→1,225→1,835→2,929; code removed).
- **DO NOT redo the split with unsigned dihedral angle** (895→1,215, it split the
  convex edges that were fine too).
- **DO NOT leave two implementations** "for safety" — it is the failure that
  this work exists to close. One converter, one writer, one parser.
- **DO NOT touch the tet pipeline** (gmsh_wrapper.py, the tet flow of
  main_window.py) to help the poly conversion.
- **DO NOT declare success from a single run** (GMSH is not deterministic).
- **DO NOT silently change** which converter produced a mesh.

## 7. Verification commands (from repo root)

```powershell
# Fast offline tests (no WSL/GMSH)
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_tet_poly_dual.py tests/test_poly_smoother.py -q --tb=short
# NOTE: run them IN THE SAME command to catch leak regressions.

# Full suite
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/ -q --tb=short

# Bench with real checkMesh (WSL) on the prepared case
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" tests/bench_tet_poly.py C:\polybench\valve1 --conv dual --reuse
```

## 8. Useful references

- `docs/poly_converter_plan.md` — the consolidation plan (Phase 0-5) of the
  previous session; much of Phase 0-1 is already executed (see §3).
- `docs/poly_converter.md` / `docs/poly_dual_converter.md` — measured numbers
  (to be unified in Phase 4).
- `docs/poly_workflow_part2.md` — quality gate and failure/rollback matrix
  of the poly path.
- `docs/residual_risks.md` — §Polyhedral Conversion, §Sizing and Estimates.
- `.opencode/skills/cfmesh-autogui/SKILL.md` — project knowledge (known
  bugs, patterns, configurations).
- `.slim/deepwork/poly-mesher.md` — log of the deepwork session.

## 9. Expected delivery

When done: show the NUMBERS (coverage, checkMesh verdict, misoriented
faces, skew, non-ortho, aspect, volume, time), the files touched, the green
tests, and every residual limit with its cause. No "it should work".

---

## 10. YOUR CONTRIBUTION — fill in this MANDATORY block

You must add something of YOUR own to the work: do not limit yourself to executing the plan. In
this code block write (1) what you understood, (2) your personal
technical proposal (improvement, mathematical idea, refactor, test), (3) your
first real code contribution already ready to apply, with motivation and
estimated risk.

```python
# ============================================================================
# CONTRIBUTION OF THE NEW AGENT — fill in everything
# ============================================================================
CONTRIBUTO = {
    "autore": "Reasonix (autonomous agent, session 2026-08-03)",
    "data": "2026-08-03",

    "comprensione": """
    The converter is the barycentric dual (tet_poly_dual.py): one poly cell
    per primal vertex, internal faces passing through the tet centroids
    (or also through the vertices with median_faces=True). The valve's defect is
    topological, not geometric: on a CONCAVE edge of the CAD the dual cell
    of the vertex wraps the corner and its pyramid test fails (895 in
    replica, 852 per checkMesh). I verified that split_rounds (352 concave
    vertices) and wedge_cells (614 edges) ARE implemented and fire, but
    keep-best discards them because they worsen the total count; median_faces
    moves the defect from pyramids (852) to catastrophic skewness (470). Phase 1
    lane A (centralized thresholds, single foam_mesh_io writer, single checkMesh
    parser, deterministic owner, np.fromstring removed, summary()) is complete
    and green (140 tests). The remaining work: Phase 3 (pipeline guards) and Phase 4
    (documentation) — the residual valve defect remains open and must be pinned.
    """,

    "proposta_tecnica": """
    Two ideas, both measured on the valve's real numbers:

    1) PINNED BASELINE as a regression guard. The valve defect
       (852 wrong_oriented / 1027 residual_defects on A_default) is the number
       to beat, but today it lives only in human memory. tools/
       valve_defect_baseline.py reloads the intact tet backup, reconverts
       with A_default and FAILS if the numbers change — every future attempt
       (split, wedge, median, new algorithm) starts from an automatic comparison,
       not from a memory. Cost: ~2 min/run, authorized by the
       constraint "real WSL benchmarks 10-40 min".

    2) DIAGNOSIS OF THE REJECTION in the result. My measurement shows that split and
       wedge are discarded by keep-best without leaving a trace of why
       (log only). Proposal: enrich DualPolyResult with a field
       ``rejected_rebuilds`` that records {round/wedge: total_before,
       total_after, counts} — so the next attempt knows EXACTLY which
       avenue has already been closed and with which numbers, without re-running 50s of
       conversion. File: src/cfmesh_autogui/core/tet_poly_dual.py
       (run(), lines ~319-364 where the rebuilds are evaluated).

       Measured recommendation: do NOT chase median_faces (skew 17->470) nor
       the signed split (inert); the direction the numbers indicate is
       topological (add cells that separate the concave corner, like the
       wedges — but with a construction that does not double the defects).
    """,

    "primo_contributo_codice": """
    Two real files, already written and verified in this session:

    tools/valve_defect_baseline.py  (regression guard, ~130 lines)
    tools/concavity_verify.py       (numerical verification of the signed test)

    The heart of the first — reload the backup, reconvert, compare:

        from cfmesh_autogui.core.tet_poly_dual import TetPolyDualConverter
        PINNED = {"residual_defects": 1027, "wrong_oriented": 852,
                  "negative_cells": 0, "mesh_ok": False}
        # ... prepare_case from the C:/polybench/valve1 backup ...
        res = TetPolyDualConverter(case, log=lambda m: None).run()
        measured = {"residual_defects": int(res.residual_defects)}
        # + checkMesh via WSL (see tools/valve_defect_baseline.py)
        assert all(measured.get(k) == v for k, v in PINNED.items()), \\
            "BASELINE SHIFT: the valve defect has changed — verify"

    tools/concavity_verify.py verifies the signed test on real meshes
    (cube1: 0/0 concave; groove1: 7 vertices/6 arcs) before trusting it,
    as required by Phase 2 point 2.
    """,

    "motivazione": "The numbers measured in Phase 2 become a reproducible regression constraint, and the diagnosis of the rejection eliminates the blind cost of retrying already-closed avenues: it advances the work without touching the algorithm (constraint: the working converter does not change).",
    "rischio": "Low: no modification to the converter nor the pipeline; the tools read the intact backup in a temporary case and do not touch C:\\polybench; the only dependency is WSL+checkMesh (already authorized).",
    "verifica_proposta": "python tools/valve_defect_baseline.py && python tools/concavity_verify.py  (both PASS in this session)",
}

# End of block. Do not modify anything else in the prompt: the plan remains the constraint.
```

---

## PHASE 1 — NATIVE MESHER: PROOF OF CONCEPT (gate PASS, 2026-08-03)

New path (our algorithm, pure Python, 100% poly): hex-dominant (or cut-cell)
primal → **median dual** (1 cell per vertex, 1 face per edge, boundary =
exact subdivision into quads) → checkMesh. No CAD clipping, no intermediate tet.

### Done
- `core/hex_poly_dual.py` — generic dual engine (works on ANY valid polyhedral
  cell, not just hex): fan walk and geometric orientation ported from
  `tet_poly_dual`, I/O via `foam_mesh_io`, invariants (closure/volume/owner<neighbour),
  checkMesh-replica defect detector, non-destructive write to `constant/polyMesh_dual`.
  Verified on regular grids (dual cells == vertices, quads==boundary faces,
  volume conserved, corner cells = 1/8, closure ~1e-17) and on 2 real venturi cases.
- `tools/bench_hex_dual_ab.py` — A/B on the SAME hex mesh: polyDualMesh (oracle)
  vs our dual → checkMesh on both.

### A/B numbers (same hex mesh, real WSL checkMesh)
| | base_VENTURI-bc (31,832 hex) | base_VENTURI-nobc (15,809 hex) |
|---|---|---|
| oracle / our cells | 33,892 / 33,892 | 16,396 / 16,396 |
| checkMesh oracle / ours | Mesh OK / Mesh OK | Mesh OK / Mesh OK |
| max skew oracle / ours | 1.294 / 2.016 | 1.295 / 2.028 |
| non-ortho max oracle / ours | 52.70 / 52.70 | 52.66 / 52.66 |
| non-ortho avg oracle / ours | 11.59 / 11.59 | 13.24 / 13.03 |
| max aspect oracle / ours | 19.12 / 18.40 | 19.12 / 18.39 |
| inverted pyramids | 0 / 0 | 0 / 0 |

**PHASE 1 GATE: PASS** — same checkMesh verdict, defects of the same order of
magnitude, every metric below checkMesh's fail threshold.

### Phase 2 — seam collapse (closed, 2026-08-03)
`core/hex_poly_dual.py` (promoted from hex_dual_spike) now implements the collapse
of smooth-edge seams **exactly like polyDualMesh** (faithful port of the
OpenFOAM 2512 source: `dualPatch`/`collectPatchInternalFace`/`splitFace` +
`calcFeatures`):
- feature edge = non-manifold / cross-patch / dihedral ≥ featureAngle (90°);
  feature point = vertex with >2 feature edges;
- boundary = ONE dual face per primal vertex, polygon through the centroids
  of the incident boundary faces in rotational order (walk), midpoints only
  on feature edges, fan split at feature points;
- smooth-edge seam face: kept but WITHOUT the midpoint vertex
  (the 2.02 vs 1.29 gap of Phase 1 is CLOSED).

### A/B numbers post-collapse (same hex mesh, real WSL checkMesh)
| | base_VENTURI-bc (31,832 hex) | base_VENTURI-nobc (15,809 hex) |
|---|---|---|
| oracle / our cells | 33,892 / 33,892 | 16,396 / 16,396 |
| checkMesh oracle / ours | Mesh OK / Mesh OK | Mesh OK / Mesh OK |
| max skew oracle / ours | **1.294 / 1.294** | **1.295 / 1.295** |
| non-ortho max oracle / ours | 52.70 / 52.70 | 52.66 / 52.66 |
| non-ortho avg oracle / ours | 11.59 / 11.59 | 13.24 / 13.24 |
| max aspect oracle / ours | 19.12 / 19.12 | 19.12 / 19.12 |
| inverted pyramids | 0 / 0 | 0 / 0 |

**IDENTICAL metric for metric to the oracle** on both cases. Dual boundary
faces = 4,048 (one per boundary vertex, exactly like polyDualMesh);
boundary-cell face counts identical to the oracle; dual volume with surface offset
(-0.19% bc, -0.46% nobc, same behavior as polyDualMesh).

### Residual / notes
- In collapse mode the dual volume is no longer an exact partition of the
  primal (boundary through the face centroids) — invariant = closed +
  positive volume, like polyDualMesh. Exact tiling (volume conserved) remains
  available with `collapse_smooth_edges=False`.
- Cell numbering compacted (one dual cell per USED vertex; orphan
  points of meshes with holes do not become zero-face cells).
- Tests: `tests/test_hex_poly_dual.py` (11 tests, fast, no WSL): cube/bore/L
  concave (valve defect class), invariants of both modes, regression
  with frozen counts, rejection of an invalid primal. Suite: green.
- Phase 3 lead: native generator (background grid + cut-cell in Python).
- Next commands: `python tools/bench_hex_dual_ab.py C:/cfmesh_poly_bench/base_VENTURI-bc`

---

## PHASE 3 — NATIVE GENERATOR (background + cut-cell), first release (2026-08-03)

`core/native_mesher.py` + `tests/test_native_mesher.py` (7 tests, fast, no WSL).

### Construction (our algorithm, pure Python)
1. closed trimesh surface (CAD tessellation) → uniform Cartesian grid (bbox + margin);
2. containment per corner (`trimesh.contains`), hex cells (8/8 inside) / cut (mixed) / outside (skipped);
3. **cut-cell**: the surface triangles that intersect the cell are CLIPPED
   (Sutherland–Hodgman against the box) → the clipped polygons ARE the cap (boundary on the
   exact surface, not chords!) and their vertices on the box planes feed the 2D rule
   for the face parts → adjacent cells compute the SAME shared face;
4. global dedup of points/faces (rounded coords), owner/neighbour, patch per input mesh;
5. invariants: closure per cell, positive volumes, drop sliver (< 5e-5·cell³) and,
   in `clean_cells=True` mode, drop cells with non-manifold edges (dual requirement).

### Numbers (real WSL checkMesh)
| case | cells | volume | closure | max skew | non-ortho max |
|---|---|---|---|---|---|
| cube 1³ c0.5 | 27 (1 hex+26 cut) | 1.000000 exact | 5.6e-17 | — | — |
| sphere r0.5 c0.2 | 117 | 0.5058 (99.98%) | 1.1e-16 | — | — |
| venturi STL c0.06 | 5,775 (3,671 hex+70 prism+1,962 poly) | 1.02332 (99.9999%) | 3e-7 | **43.1** (403/22,987 faces, 1.75%) | 48.6 OK |
| cfMesh cartesianMesh reference (15,809 cells) | — | — | — | 1.29 | 52.7 |

EXACT volume at every resolution (the caps are the triangles clipped on the true surface);
watertight; non-ortho/aspect/pyramids OK. **Only failed check: skewness** — an intrinsic
artefact of the castellated approach (wedge cells where the surface is nearly parallel to the grid);
cfMesh/snappy avoid it with SNAPPING (a documented future phase, not a bug).

### Native → dual → 100% poly pipeline (demonstrated, with a quality gap)
`icosphere sphere subdiv2 c0.15 (clean_cells=True)` → 260 native cells → `hex_poly_dual` →
**1,198 cells 100% poly, watertight (closure 1.6e-16), positive volumes, aspect 13.9 OK**,
volume 99.2% conserved. checkMesh: 9 inverted pyramid faces + skew 4.85 + non-ortho 77
(quality gap vs the dual of the cfMesh-hex — snapping/quality phase needed).
Documented limit: the dual requires clean primal cells; the wedge cells of walls
parallel to the grid (venturi) block the dual → snapping needed (Phase 3 continues).

### Bugs found and fixed during Phase 3 (do not repeat)
- lattice `meshgrid().reshape(-1)` k-fastest vs `vid()` i-fastest → transposed matrix
  (SILENT) → wrong classification → 25% volume erosion: fix explicit construction;
- vectorized classification `.reshape(-1)` on an array (nx,ny,nz) → `.T.reshape(-1)`
  needed (i-fastest cell index) — same transposition bug, 13% of volume;
- `zip(poly, ids)` after dedup truncates the final vertices → points missing from the boundary faces;
- clip vertices on box edges assigned to only ONE face → add to both;
- wrong `_FACE_EDGES` (4/6 faces) → corrected edge→faces map for the OpenFOAM hex model;
- boundary faces with patch=-1 (neighboring cells skipped) DROPPED in the assembly → assigned to patch 0;
- volume calculation for the sliver drop on NON-oriented faces → orient before summing.

---

## PHASE 4 (partial) — GUI INTEGRATION: experimental Native Poly mesher (2026-08-03)

User request: "implement it in the GUI as an experimental mesher, do not touch the others".

### Done (additive, isolated — no existing flow modified)
- `MeshingAlgorithm.NATIVE_POLY = "NativePoly"` + `ALGORITHM_INFO` (experimental, no WSL
  to generate; WSL only for the checkMesh validation). NOT in `ESCALATION_LADDER` /
  `ALGORITHM_ROBUSTNESS` → never auto-selected.
- `_run_native_poly` in `mesh_engine.py`: `load_geometry`/`meshes` → `NativeMesher`
  (clean_cells=True, cell size from the detail) → `HexPolyDualConverter` → the 100%
  poly mesh becomes `constant/polyMesh`, the castellation is preserved in
  `constant/polyMesh_hex_native` (never lost).
- **Escalation disabled for NATIVE_POLY** (`max_steps=0`): a failure is REPORTED,
  never silently replaced with GMSH/cfMesh.
- GUI: entry "Tools → Experimental: Native Poly (100% poly, native)" + help; "NativePoly"
  added to the wizard combo and the preset map; mapping in `verification.py`.
- Tests: `tests/test_mesh_engine.py` enum count 9→10 (new member).

### Verification (headless engine, WSL checkMesh via quality)
- sphere (`C:/polybench/test_sphere.stl`, coarse): **success=True, 13,676 cells 100% poly,
  escalation=0**, native hex preserved; quality_passed=False (known dual gap: skew 4.85).
- venturi: **fails CLEARLY** (non-manifold boundary of the castellation on the straight
  walls — a known limitation, snapping needed), no escalation.

### Still to do (Phase 4 continues, not touched)
- Native BL on the dual (bl_poly adapted), checkMesh-backed quality already active via the engine.
- Snapping (closes skewness 43 and unblocks the venturi) remains the quality prerequisite.
