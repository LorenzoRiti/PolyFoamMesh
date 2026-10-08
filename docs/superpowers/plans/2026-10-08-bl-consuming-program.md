# BL Multi-Layer Consuming Program — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** make the in-house boundary-layer engine build the FULL requested stack on real geometries (tube, venturi, Parte2) instead of capping to the first dual cell's depth — without compressing the first layer (y+ stays true), without breaking any existing validation, and without changing the default until the acceptance below is green.

**Architecture:** bounded vertex relocation ("core reflow"). The wall extrudes to the full stack depth by relocating the interior dual vertices that bound the consumed region outward along the wall normal, merging 1..k cell layers into the prism stack. Whole-cell absorption is explicitly rejected (proved impossible without transition cells). Every relocation is capped per vertex (0.9 * local cell height) and the whole build must pass the existing `_validate`; anything else falls back to today's behavior.

**Tech Stack:** Python + numpy inside `core/bl_poly.py` (+ a small helper module `core/bl_reflow.py` to keep `bl_poly.py` manageable); validation via `_validate` and REAL checkMesh through WSL.

**Spec:** this document is the spec+plan. Primary evidence (all measured in this project's sessions):
- Layer counts: tube `gmsh_direct_20261007_115127` = 30,139 prisms / 6,095 pre-BL wall faces (~4.9 *compressed* layers); tube `gmsh_direct_20261008_103202` = 11,285 prisms / 9,772 wall faces (~1.2 honest layers); venturi `gmsh_direct_20261008_103936` = 1 layer.
- Physics: tube y+30/water/1 m/s → h1 ≈ 0.625 mm, 6-7 layer stack ≈ 6 mm; venturi δ99 ≈ 7 mm. The dual's first cell is ~1-2 mm → today's cap.
- The budget: `max_h = angle_fade * min(clamp*d, 0.5*edge_cap)`, `d` = distance to the first non-wall vertex (`support_d`).
- Lane-B topology proof: deleting the wall-adjacent cell breaks the identity "prism top == translated wall face"; the prism top cannot attach to the next cell's far boundary without transition cells (naive morph prototype: 55 non-positive cells, rejected).
- Lane-A finding: GMSH cannot bake 3D boundary layers on imported CAD (2D-only field; verified empirically) — so the prisms must come from our engine.

## Design decisions (locked)

1. **Approach**: bounded per-vertex relocation of the consumed region's far-boundary vertices (NOT cell deletion, NOT transition remeshing). Rationale: it preserves the existing build's topology invariants (the prism top stays a translated copy of a polygonal surface; no new face types) while extending the depth across whole cell layers.
2. **Consumed-region rule**: a vertex is relocated only if EVERY cell it belongs to lies inside the swept prism region; otherwise that vertex keeps today's cap (partial consumption never happens in v1).
3. **Displacement cap**: per vertex, displacement ≤ 0.9 × (its smallest incident cell height along the normal); vertices that would invert any incident cell are left at today's cap.
4. **Gate**: everything behind `POLYFOAMMESH_BL_CONSUME=1`; OFF = byte-identical output (SHA256 proof required).
5. **Keep-best**: any failed attempt (validation) falls back to the current chain; the feature can never produce a mesh that `_validate` rejects.
6. **No touching** `gmsh_wrapper.py`, `tet_poly_dual.py`, `openfoam_runner.py`, GUI files (recently stabilized; other work depends on them).

## Global Constraints

- Python: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"` (bare `python` lacks deps).
- checkMesh MUST be run as: `wsl -d Ubuntu -- bash -lc "source /usr/lib/openfoam/openfoam2512/etc/bashrc 2>/dev/null; cd '<case>' && checkMesh"` (a bare `wsl checkMesh` fails — a previous lane got this wrong).
- Determinism: same input → same output (no randomness, no thread-order dependence).
- Every task ends with: ruff clean on touched files + the listed tests green + a commit. Default-path invariance is re-proven in T5.
- The degenerate-prism guard (first layer < 5% of request → reject) and every existing guard stay in force.

## File Structure

- `src/polyfoammesh/core/bl_reflow.py` (NEW): pure functions for the support walk and the relocation math — no I/O, no engine state, unit-testable in isolation.
- `src/polyfoammesh/core/bl_poly.py` (MODIFY): wire `bl_reflow` into `_build_precompute` (support walk) and `_build` (relocation, gated), keep the existing fallback chain and logging contract.
- `tests/test_bl_reflow.py` (NEW): unit tests for the pure math.
- `tests/test_bl_consume.py` (NEW): engine-level integration tests (synthetic graded dual box) + the gated/no-op contract.
- Scratch probes: `.superpowers/sdd/2026-10-05-poly-beat-snappy-program/` (deleted after use).

---

### Task 1: Support-layer walk (pure function)

**Files:**
- Create: `src/polyfoammesh/core/bl_reflow.py`
- Test: `tests/test_bl_reflow.py`

**Interfaces:**
- Produces: `walk_support_layers(points, faces, owner, neighbour, n_int, wall_verts, normals, max_layers, cum) -> dict[int, list[float]]` mapping each wall vertex to the cumulative depths reached along its normal through successive cells (entry i = depth at which the walk crosses the far side of the i-th cell layer). The first entry must equal today's `support_d` for the same vertex (regression contract).

- [ ] **Step 1: Write the failing test**

```python
def test_walk_matches_first_cell_depth_and_extends():
    # synthetic graded dual box: 3x3x7 cells, first layer 0.08, second 0.3
    pre = build_graded_box(first=0.08, second=0.30, rest=0.3, nz=7)
    layers = walk_support_layers(**pre, max_layers=3, cum=np.array([0.1, 0.22, 0.36]))
    w = next(iter(pre["wall_verts"]))
    assert layers[w][0] == pytest.approx(pre["support_d"][w], rel=1e-9)
    assert len(layers[w]) >= 2
    assert layers[w][1] > layers[w][0]
```

- [ ] **Step 2: Run it, verify it fails** (`pytest tests/test_bl_reflow.py -v` → ImportError).
- [ ] **Step 3: Implement the walk** — from the wall vertex along `normals[w]`, find the incident cell's far face along the normal (reuse the same face-walk idea as `support_d`: the incident boundary face's owner cell, then its far face in the normal direction), record the crossing depth, continue into the next cell; stop when the depth exceeds `cum[-1]`, the walk leaves the mesh, or `max_layers` entries exist. Pure numpy/dicts, no engine state.
- [ ] **Step 4: Run the tests → PASS.** Also run `pytest tests/test_bl_poly.py -q` to prove nothing changed (the function is not wired yet).
- [ ] **Step 5: Commit** — `feat(bl): support-layer walk helper (pure, tested)`.

### Task 2: Relocation math (pure function)

**Files:**
- Modify: `src/polyfoammesh/core/bl_reflow.py`
- Test: `tests/test_bl_reflow.py`

**Interfaces:**
- Consumes: Task 1's `layers`.
- Produces: `reflow_points(points, faces, owner, neighbour, n_int, wall_verts, normals, layers, target_depth, cell_height, cap=0.9) -> tuple[np.ndarray, dict[int, float], int]` returning (new points array, per-vertex applied displacement, number of vertices relocated). Rule: relocate the vertices of the far boundary of the consumed region by `min(target_depth - first_cell_depth, cap * cell_height)`; vertices whose ALL incident cells are not fully inside the swept region keep displacement 0.

- [ ] **Step 1: Write the failing tests**

```python
def test_reflow_moves_only_consumable_vertices():
    pre = build_graded_box(...)
    new_pts, disp, n = reflow_points(**pre, target_depth=0.30, ...)
    assert n > 0
    assert all(d <= 0.9 * pre["cell_height"][v] + 1e-12 for v, d in disp.items())

def test_reflow_is_identity_when_no_consumption_needed():
    new_pts, disp, n = reflow_points(**pre, target_depth=pre["support_d"][w], ...)
    assert n == 0 and np.allclose(new_pts, pre["points"])
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** the two rules (all-incident-cells-inside; per-vertex cap), returning the new array (copy, never mutate the input).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(bl): bounded core-reflow relocation (pure, tested)`.

### Task 3: Wire into the engine (gated)

**Files:**
- Modify: `src/polyfoammesh/core/bl_poly.py`
- Test: `tests/test_bl_consume.py` (new)

**Interfaces:**
- Consumes: `walk_support_layers` (T1), `reflow_points` (T2).
- Produces: with `POLYFOAMMESH_BL_CONSUME=1`, `_build` uses the reflowed points before the extrusion and the per-vertex budget uses the extended depth; `built["consumed_layers_max"]` set; log line `[bl] stack consumes up to N interior cell layers (X mm of Y mm requested)`. With the env OFF: no calls into `bl_reflow`, byte-identical output.

- [ ] **Step 1: Write the failing test** — synthetic graded box, n_layers=4, h1 so the stack needs 2 cell layers, env ON → assert median layers == 4, `_validate` True, min exact volume > 0; same run env OFF → median layers <= 2 (today's cap).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement the wiring** — in `_build_precompute`, when the env is set, call the walk and store `pre["support_layers"]`; in `_build`, when set, compute the target depth per vertex (from `cum` and the walk) and call `reflow_points` before the existing extrusion; keep every existing guard and the fallback chain untouched; the env check must be a single `os.environ` read so OFF is a no-op.
- [ ] **Step 4: Run the new tests + the whole BL suite** (`tests/test_bl_poly.py tests/test_bl_engine.py tests/test_bl_on_collapsed_boundary.py tests/test_bl_collapsed_valve.py tests/test_bl_first_layer_absolute.py`) → all green (OFF behavior unchanged).
- [ ] **Step 5: Commit** — `feat(bl): consuming reflow wired in (gated POLYFOAMMESH_BL_CONSUME)`.

### Task 4: Real-geometry acceptance (measurement only, no code)

**Files:** probe under `.superpowers/sdd/2026-10-05-poly-beat-snappy-program/` (delete after).

- [ ] **Step 1:** Rebuild the pre-BL dual for the tube (`C:\cfmesh_cases\gmsh_direct_20261008_103202\constant\polyMesh_tet_backup`, production converter kwargs) and run the engine env ON with the panel config (h1 ≈ 0.625 mm, 6 layers, growth 1.2, robust ON).
- [ ] **Step 2:** Report: layer histogram (target: median >= 4, coverage 100%), first-layer ratio (must be ~1.0: y+ stays true), and REAL checkMesh on the written case (the bashrc command above). Acceptance: checkMesh PASS (every metric reported).
- [ ] **Step 3:** Same for the venturi tet backup (`gmsh_direct_20261008_103936`) — report layers + checkMesh.
- [ ] **Step 4:** If either fails: stop, report the exact metric and the failure mode; do NOT tune the acceptance.
- [ ] **Step 5: Commit** the probe results into `notes/bl_consuming_reasoning.md`.

### Task 5: Regression + invariance proof

- [ ] **Step 1:** Feature OFF: SHA256 of points/faces/owner/neighbour/boundary on the tube dual must equal the HEAD value (record both).
- [ ] **Step 2:** `benchmarks/run_benchmarks.py --pipeline poly` env ON vs the recorded baseline entries (external_aero / pipe / pipe_constriction / thin_gap / box_obstacle): no regression beyond the allowance.
- [ ] **Step 3:** Full fast suite green + ruff clean. Commit the evidence.

### Task 6: Promotion decision (only if T4+T5 are green)

- [ ] **Step 1:** Flip the default ON (drop the env gate), re-run T4 acceptance + T5 invariance (OFF no longer exists; the new behavior IS the default) + the full BL suite.
- [ ] **Step 2:** If anything regresses: revert to gated, document the blocker in `notes/bl_consuming_reasoning.md`, and return to design. Never ship a partial.

---

## Risk register

| Risk | Mitigation |
|---|---|
| Cell inversion at the relocated interface | per-vertex cap 0.9 × cell height + `_validate` positivity gate + fallback |
| Non-conformity at the consumed interface | only far-boundary vertices of fully-consumed cells move; topology untouched |
| Default regression | env gate + SHA256 invariance proof + corpus run before promotion |
| Performance (extra walk per vertex) | walk bounded to `max_layers`; measured in T4 (report stage times) |
| Non-determinism | pure functions, fixed iteration order, no randomness |

## Rollback

Feature OFF (`POLYFOAMMESH_BL_CONSUME` unset) restores today's behavior exactly; the commits are additive and revertible individually.

## Self-review (run against the spec)

- Spec coverage: the goal (full stack, true y+, no default change) maps to T1-T6; the acceptance (real checkMesh on tube+venturi) is T4; the invariance/regression protocol is T5. ✔
- Placeholders: none — every step names files, functions, assertions and commands. ✔
- Type consistency: `walk_support_layers` → `layers: dict[int, list[float]]` consumed by `reflow_points(... layers ...)`; `reflow_points` → `(points, disp, n)` consumed by `_build`; names used consistently. ✔

## Execution protocol

- One lane per task; the orchestrator reviews the gate evidence (tests + numbers) between tasks and reconciles before the next dispatch.
- The lane already in flight (dispatched before this plan) must be reconciled against T1/T2: accept only code that matches this design and passes these tests; otherwise discard it.
