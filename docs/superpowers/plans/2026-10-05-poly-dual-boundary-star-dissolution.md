# Poly Dual Boundary-Star Dissolution (Lane NON-ORTHO) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the opt-in walk-level dissolution of tiny boundary vertex stars in our tet→poly dual (`core/tet_poly_dual.py`), so the boundary layer no longer extrudes sliver prisms from sub-cell boundary polygons, and measure it A/B. No default changes.

**Architecture:** A planning pass computes boundary-star areas and assigns sub-threshold vertices to a neighbouring survivor (smooth edge, same patch, largest star). The survivor's boundary polygon is built as the loop union of both stars (splice at the shared chain); the dissolved vertex's dual cell is collapsed into the survivor (`base_l` remap, shared internal face dropped); unused points are dropped and cells renumbered. Validation + keep-best against the unmerged dual; flag off → byte-identical output.

**Tech Stack:** Python 3.11, numpy, pytest, WSL2 + OpenFOAM v2512, `benchmarks/` harness.

**Spec (binding authority):** `notes/poly_nonortho_reasoning.md` (prediction + probe evidence, committed `1b8057d`).
**Gap rows:** `benchmarks/results/GAP_ANALYSIS.md` rows 8, 10, 16.
**Spike (throwaway, in the SDD workspace):** `probe_sliver_merge_spike.py` — proved post-hoc merge is NOT viable (merged dual 96.73 vs 74.00); its loop-splice logic is the seed for Task 2's helper.

## Global Constraints

- Interpreter: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"` (bare `python` is an unrelated venv).
- Repo root for all commands. **Local only — never push** (the human decides integration).
- **No default changes.** `dissolve_tiny_boundary_stars=0.0` must leave the dual byte-identical (assert in tests).
- `ruff check src/ tests/ benchmarks/` green; `pytest -m "not slow and not wsl"` green (baseline: 1234 passed / 2 skipped / 1 xfailed).
- Benchmark hard cap `RUN_TIMEOUT_S = 1500`; never raise it.
- Kill criterion (from the note): if cube_duct does not PASS (<70 non-ortho, skew <4, aspect <1000), or venturi improves by <5° from 74.00 dual-only, or any healthy fast-tier geometry regresses (>2° primary metric, or a passing row starts failing) → lane killed and documented.
- Any dissolution that fails validation is refused; the mesh must never be written worse than the unmerged dual.
- Commit hook: prepend `$env:LOCALAPPDATA\Programs\Python\Python311` to PATH before `git commit`; never `--no-verify`. `benchmarks/results/` is git-ignored → `git add -f` for artefacts.

---

### Task 1: Boundary-star areas and dissolution planning

**Files:**
- Modify: `src/polyfoammesh/core/tet_poly_dual.py` (module-level helpers, after `_detect_defects` ~line 2148)
- Test: `tests/test_poly_star_dissolution.py` (create)

**Interfaces:**
- Consumes: nothing (pure helpers over the primal arrays)
- Produces:
  - `_boundary_star_areas(P) -> np.ndarray` — indexed by primal vertex id; sum of the areas of the boundary triangles incident to each boundary vertex (0 for non-boundary vertices).
  - `_plan_star_dissolution(P, threshold_fraction: float) -> dict[int, int]` — mapping `{dissolved_vertex: survivor_vertex}`; a vertex `v` is dissolved when `star_area[v] < threshold_fraction * median(nonzero star areas)`; the survivor `u` is a neighbour across a boundary edge with `star_area[u] >= star_area[v]`, the largest such candidate; chains resolve to the largest star in the connected group; empty dict when the fraction is <= 0 or nothing is below threshold.
  - `_smooth_boundary_neighbours(P) -> dict[int, list[int]]` — boundary-vertex adjacency across non-feature boundary edges, same patch.

- [ ] **Step 1: Write the failing tests**

```python
"""Boundary-star dissolution helpers for the poly dual (opt-in, default off)."""
from __future__ import annotations

import numpy as np

import polyfoammesh.core.tet_poly_dual as tpd


class _StarP:
    """Primal stub for `_boundary_star_areas` (points + bnd_tri only)."""

    def __init__(self):
        self.points = np.array([
            [0.0, 0.0, 0.0],    # 0 tiny star centre
            [0.01, 0.0, 0.0],   # 1
            [1.0, 0.0, 0.0],    # 2 big star centre
            [1.01, 0.0, 0.0],   # 3
            [1.0, 1.0, 0.0],    # 4
            [0.0, 1.0, 0.0],    # 5
        ])
        # t0/t3 tiny (~5e-3), t1/t2 big (~0.5)
        self.bnd_tri = np.array([
            [0, 1, 5],
            [1, 2, 5],
            [2, 4, 5],
            [2, 3, 4],
        ])
        self.n_bnd = 4


class _EdgeP:
    """Primal stub for `_smooth_boundary_neighbours` (edge fields only)."""

    def __init__(self, feature: bool = False):
        self.n_pi = 100
        self.bnd_e_faces = {10: [0, 1], 11: [0, 2]}
        self.uniq_key = {10: 0 * 100 + 1, 11: 0 * 100 + 5}
        self.feat_edge = {10: feature, 11: False}
        self.patch_of_bnd = np.array([0, 0, 0])


def test_star_areas_flag_the_tiny_star():
    areas = tpd._boundary_star_areas(_StarP())
    assert 0.0 < areas[0] < areas[2]
    assert areas[2] > 0.5


def test_plan_dissolves_tiny_into_largest_neighbour(monkeypatch):
    areas = np.zeros(6)
    areas[0], areas[1], areas[2] = 0.005, 0.505, 1.005
    areas[3], areas[4], areas[5] = 0.005, 0.505, 1.005
    monkeypatch.setattr(tpd, "_boundary_star_areas", lambda P: areas)
    monkeypatch.setattr(
        tpd, "_smooth_boundary_neighbours",
        lambda P: {0: [1, 5], 3: [2, 4], 1: [0, 5], 5: [0, 1, 2],
                   2: [1, 5, 3, 4], 4: [3, 2]},
    )
    plan = tpd._plan_star_dissolution(object(), threshold_fraction=0.5)
    assert plan == {0: 5, 3: 2}


def test_plan_is_empty_when_disabled(monkeypatch):
    monkeypatch.setattr(tpd, "_boundary_star_areas", lambda P: np.array([1.0, 1.0]))
    assert tpd._plan_star_dissolution(object(), threshold_fraction=0.0) == {}


def test_smooth_neighbours_exclude_feature_edges():
    assert tpd._smooth_boundary_neighbours(_EdgeP()) == {0: [1, 5], 1: [0], 5: [0]}
    assert tpd._smooth_boundary_neighbours(_EdgeP(feature=True)) == {0: [5], 5: [0]}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_poly_star_dissolution.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name '_boundary_star_areas'`.

- [ ] **Step 3: Implement the helpers**

Read `_read_primal` (tet_poly_dual.py:599-796) first to use the REAL field names and the `uniq_key` encoding; then implement:

```python
def _boundary_star_areas(P) -> np.ndarray:
    """Sum of incident boundary-triangle areas per primal vertex."""
    n_pts = len(P.points)
    areas = np.zeros(n_pts, dtype=np.float64)
    pts = np.asarray(P.points, dtype=np.float64)
    for bi in range(P.n_bnd):
        vs = P.bnd_tri[bi]
        p0, p1, p2 = pts[vs[0]], pts[vs[1]], pts[vs[2]]
        a = 0.5 * float(np.linalg.norm(np.cross(p1 - p0, p2 - p0)))
        areas[vs[0]] += a
        areas[vs[1]] += a
        areas[vs[2]] += a
    return areas


def _smooth_boundary_neighbours(P) -> dict[int, list[int]]:
    """Boundary-vertex adjacency across non-feature, same-patch edges."""
    n_pi = P.n_pi
    out: dict[int, list[int]] = {}
    for ei, bis in P.bnd_e_faces.items():
        if P.feat_edge[ei]:
            continue
        key = int(P.uniq_key[ei])
        a, b = key // n_pi, key % n_pi
        if a == b or len(bis) != 2:
            continue
        if P.patch_of_bnd[bis[0]] != P.patch_of_bnd[bis[1]]:
            continue
        out.setdefault(a, []).append(b)
        out.setdefault(b, []).append(a)
    return out


def _plan_star_dissolution(P, threshold_fraction: float) -> dict[int, int]:
    """Assign sub-threshold boundary stars to their largest smooth neighbour."""
    if threshold_fraction <= 0.0:
        return {}
    areas = _boundary_star_areas(P)
    nz = areas[areas > 0.0]
    if len(nz) == 0:
        return {}
    thr = float(np.median(nz)) * threshold_fraction
    nb = _smooth_boundary_neighbours(P)
    small = {v for v in nb if areas[v] < thr}
    if not small:
        return {}
    plan: dict[int, int] = {}
    for v in sorted(small, key=lambda x: areas[x]):
        cands = [u for u in nb[v] if u not in small and areas[u] >= areas[v]]
        if not cands:
            continue
        plan[v] = max(cands, key=lambda u: areas[u])
    return plan
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_poly_star_dissolution.py -q -p no:cacheprovider`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python311;$env:PATH"
git add src/polyfoammesh/core/tet_poly_dual.py tests/test_poly_star_dissolution.py
git commit -m "feat(poly): boundary-star area/dissolution planning helpers (unused yet)"
```

---

### Task 2: Boundary loop splice (union) helper

**Files:**
- Modify: `src/polyfoammesh/core/tet_poly_dual.py` (module-level helper)
- Test: `tests/test_poly_star_dissolution.py` (append)

**Interfaces:**
- Consumes: nothing
- Produces: `_splice_boundary_loops(a: list[int], b: list[int], xyz: np.ndarray) -> list[int] | None` — the union of two outward-oriented boundary polygons sharing a consecutive chain (2 or more points), traversed in opposite directions; returns `None` when there is no shared chain, when the union repeats a vertex, or when the union's Newell area does not equal `area(a) + area(b)` within 1e-6 relative (bowtie guard). Seed logic: the fixed splice validated in the SDD spike (`probe_sliver_merge_spike.py::merge_polygons`, single-pair check showed 0 crossing edges).

- [ ] **Step 1: Write the failing tests**

```python
from polyfoammesh.core.tet_poly_dual import _newell_area, _splice_boundary_loops


def test_splice_two_squares_sharing_an_edge():
    # left square 0-1-2-3, right square sharing edge (1,2) traversed reversed
    xyz = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [2.0, 0.0, 0.0], [2.0, 1.0, 0.0],
    ], dtype=float)
    a = [0, 1, 2, 3]        # left square (area 1)
    b = [1, 4, 5, 2]        # right square (area 1), shared chain (2,1)
    merged = _splice_boundary_loops(a, b, xyz)
    assert merged is not None
    assert len(set(merged)) == 6
    assert abs(_newell_area(xyz, merged) - 2.0) < 1e-9


def test_splice_rejects_disjoint_loops():
    xyz = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [5.0, 5.0, 0.0], [6.0, 5.0, 0.0], [6.0, 6.0, 0.0], [5.0, 6.0, 0.0],
    ], dtype=float)
    assert _splice_boundary_loops([0, 1, 2, 3], [4, 5, 6, 7], xyz) is None


def test_splice_rejects_identical_loops():
    xyz = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
    ], dtype=float)
    assert _splice_boundary_loops([0, 1, 2, 3], [0, 1, 2, 3], xyz) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_poly_star_dissolution.py -q -p no:cacheprovider -k splice`
Expected: FAIL — import error.

- [ ] **Step 3: Implement the helper**

Port the fixed splice from the spike (it validated a real venturi pair with 0 crossing edges) and add the area guard:

```python
def _newell_area(xyz, ids) -> float:
    p = np.asarray([xyz[i] for i in ids], dtype=np.float64)
    n = np.zeros(3)
    for k in range(len(p)):
        j = (k + 1) % len(p)
        n += np.cross(p[k], p[j])
    return 0.5 * float(np.linalg.norm(n))


def _splice_boundary_loops(a, b, xyz):
    """Union of two outward polygons sharing a consecutive, reversed chain."""
    # find the longest consecutive run of a that appears reversed in b
    set_b = set(b)
    runs, cur = [], []
    for x in a:
        if x in set_b:
            cur.append(x)
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    if not runs:
        return None
    chain = max(runs, key=len)
    na, nb_ = len(a), len(b)
    ia = a.index(chain[0])
    rev = list(reversed(chain))
    ib = None
    for start in range(nb_):
        if all(b[(start + k) % nb_] == rev[k] for k in range(len(rev))):
            ib = start
            break
    if ib is None:
        return None
    pos_a = [(ia + k) % na for k in range(len(chain))]
    if len(pos_a) == na:
        return None
    comp = []
    k = pos_a[-1]
    while True:
        comp.append(a[k])
        if k == pos_a[0]:
            break
        k = (k + 1) % na
    pos_b = [(ib + k) % nb_ for k in range(len(chain))]
    start_b, end_b = pos_b[-1], pos_b[0]
    out = list(comp)
    k = (start_b + 1) % nb_
    while k != end_b:
        out.append(b[k])
        k = (k + 1) % nb_
    clean = []
    for x in out:
        if not clean or clean[-1] != x:
            clean.append(x)
    if clean and clean[0] == clean[-1]:
        clean.pop()
    if len(clean) < 3 or len(set(clean)) != len(clean):
        return None
    if abs(_newell_area(xyz, clean) - (_newell_area(xyz, a) + _newell_area(xyz, b))) > 1e-6 * max(
        _newell_area(xyz, a) + _newell_area(xyz, b), 1e-30
    ):
        return None
    return clean
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_poly_star_dissolution.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python311;$env:PATH"
git add src/polyfoammesh/core/tet_poly_dual.py tests/test_poly_star_dissolution.py
git commit -m "feat(poly): validated boundary-loop splice for star dissolution"
```

---

### Task 3: Wire dissolution into the converter (opt-in, keep-best)

**Files:**
- Modify: `src/polyfoammesh/core/tet_poly_dual.py` (`__init__`, `_build_dual`, `_build_boundary_collapsed`, `_run_inner`)
- Test: `tests/test_poly_star_dissolution.py` (append) + reuse the existing dual fixtures from `tests/test_tet_poly_dual.py`

**Interfaces:**
- Consumes: `_plan_star_dissolution`, `_splice_boundary_loops` (Tasks 1-2)
- Produces: `TetPolyDualConverter(..., dissolve_tiny_boundary_stars: float = 0.0)`; when `> 0`:
  - `_build_dual` plans the mapping, remaps `base_l` for dissolved vertices, passes the mapping to `_build_boundary_collapsed`, skips collapsed internal faces (owner==neigh after remap), drops unused points, compacts cell ids, and stores `res.n_dissolved_stars` / `res.dissolve_fallbacks`;
  - `_build_boundary_collapsed` walks each survivor once and splices the dissolved neighbours' loops; a splice that fails leaves that vertex un-dissolved (no tiny face is ever removed without a valid union);
  - `_run_inner` keep-best: if the dissolution pass fails validation (closure/positive volumes/pyramid non-regression), the unmerged build is used and `res.warnings` records it.

- [ ] **Step 1: Write the failing tests**

```python
def test_converter_rejects_negative_threshold():
    import pytest
    from pathlib import Path
    from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter

    with pytest.raises(ValueError):
        TetPolyDualConverter(Path("."), dissolve_tiny_boundary_stars=-0.1)


def test_default_is_byte_identical_on_fixture(tmp_path):
    # uses the same small primal fixture as tests/test_tet_poly_dual.py;
    # build twice (flag 0.0 and flag absent) and compare the written files
    ...
```

(The fixture harness and the exact byte-compare live in `tests/test_tet_poly_dual.py` — reuse its helper functions; the new test asserts default-off byte identity, which is the Global Constraint.)

- [ ] **Step 2: Run to verify they fail**

Run: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_poly_star_dissolution.py -q -p no:cacheprovider -k converter`
Expected: FAIL — unexpected keyword argument.

- [ ] **Step 3: Implement the wiring**

In `__init__` (after `collapse_volume_tolerance`):

```python
        dissolve_tiny_boundary_stars: float = 0.0,
```
```python
        if dissolve_tiny_boundary_stars < 0.0:
            raise ValueError(
                "dissolve_tiny_boundary_stars must be >= 0, got "
                f"{dissolve_tiny_boundary_stars!r}"
            )
        self._dissolve_stars = float(dissolve_tiny_boundary_stars)
```

In `_build_dual`, right after `base_l = base[:n_pi].tolist()` (line ~832):

```python
        dissolve: dict[int, int] = {}
        if self._dissolve_stars > 0.0 and self._collapse:
            dissolve = _plan_star_dissolution(P, self._dissolve_stars)
            for v, u in dissolve.items():
                base_l[v] = base_l[u]
```

(Only meaningful in collapse mode; log and skip otherwise.) Pass `dissolve` to
`_build_boundary_collapsed`. In the internal-face loop, where owner/neighbour
are taken from `base_l[va]`/`base_l[vb]` (lines ~944-976), skip the face when
the two are equal. In `_build_boundary_collapsed`, skip vertices present in
`dissolve`, and for a survivor `u` with assigned `v`s, walk `v` with the same
walk code (extract the walk into `_walk_boundary_vertex(P, v, ...) -> list[int]`
so it can be reused) and `_splice_boundary_loops` it into `u`'s loop; emit ONE
polygon owned by `base_l[u]`. Record `res.n_dissolved_stars = len(dissolve)`.

At the end of `_build_dual` (after boundary emission, before returning M):
drop unused points, remap faces, compact cell ids to `0..n_cells-1`, and
re-sort owner/neigh (swap + reverse the loop when needed). Refuse the whole
pass (return the unmerged M) if `_validate`-style invariants fail; log the
fallback in `res.warnings`.

- [ ] **Step 4: Run the tests**

Run: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_tet_poly_dual.py tests/test_poly_star_dissolution.py -q -p no:cacheprovider`
Expected: all pass, default-off byte identity included.

- [ ] **Step 5: Commit**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python311;$env:PATH"
git add src/polyfoammesh/core/tet_poly_dual.py tests/test_poly_star_dissolution.py
git commit -m "feat(poly): opt-in walk-level tiny-star dissolution (default off, keep-best)"
```

---

### Task 4: Harness flag `--poly-sliver-dissolve`

**Files:**
- Modify: `benchmarks/run_benchmarks.py` (`_benchmark_poly_one` signature + converter call, ~line 718)
- Modify: `benchmarks/run_comparison.py` (`run_single`, `run_parent`, CLI, `main`)
- Test: `tests/test_run_comparison.py` (append)

**Interfaces:**
- Consumes: `TetPolyDualConverter(dissolve_tiny_boundary_stars=...)`
- Produces: `--poly-sliver-dissolve FLOAT` (parent + child, default `0.0`), threaded as `_benchmark_poly_one(..., sliver_dissolve=0.0)` and `run_single(..., poly_sliver_dissolve=0.0)` / `run_parent(..., poly_sliver_dissolve=0.0)`.

- [ ] **Step 1: Failing tests**

```python
def test_poly_sliver_dissolve_defaults_off():
    opts = rc._build_parser().parse_args(["--tier", "fast"])
    assert opts.poly_sliver_dissolve == 0.0


def test_poly_sliver_dissolve_forwarded_to_child(tmp_path, monkeypatch):
    # same capture pattern as test_hex_thin_refine_flag_forwarded_to_child:
    # run_parent(..., poly_sliver_dissolve=0.25) must put
    # ["--poly-sliver-dissolve", "0.25"] in every child cmd
    ...


def test_run_single_passes_dissolve_to_poly(tmp_path, monkeypatch):
    # monkeypatch rb._benchmark_poly_one, capture kwargs, assert
    # sliver_dissolve == 0.25
    ...
```

- [ ] **Step 2: Run to verify they fail** (`-k sliver` → AttributeError)

- [ ] **Step 3: Implement** — parser `type=float, default=0.0`; parent appends
  `["--poly-sliver-dissolve", str(poly_sliver_dissolve)]` when `> 0.0`; child
  passes to `run_single`; `_benchmark_poly_one` passes
  `dissolve_tiny_boundary_stars=sliver_dissolve` to the converter.

- [ ] **Step 4: Run** `pytest tests/test_run_comparison.py tests/test_run_comparison_parent.py -q -p no:cacheprovider` → all pass.

- [ ] **Step 5: Commit**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python311;$env:PATH"
git add benchmarks/run_benchmarks.py benchmarks/run_comparison.py tests/test_run_comparison.py
git commit -m "bench: --poly-sliver-dissolve opt-in flag through parent/child"
```

---

### Task 5: B-side measurement + kill/promote gate

**Files:**
- Produce (gitignored, `git add -f`): `benchmarks/results/comparison_<ts>.json`

**Interfaces:**
- Consumes: Tasks 1-4
- Produces: B-side records for the poly gap rows; gate decision for Task 6.

- [ ] **Step 1: B-side targeted runs**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier fast --geometry cube_duct --geometry venturi --runner poly --poly-sliver-dissolve 0.5
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier full --geometry concave_bracket --runner poly --poly-sliver-dissolve 0.5
```

(Bash timeout ≥1 800 000 ms.) Record `max_non_ortho`, `max_skewness`,
`max_aspect_ratio`, `cell_count`, `wall_time_s`, `thresholds_pass` per row.

- [ ] **Step 2: Compare against the prediction and the A-side**

A-side (note): cube_duct 80.64 / skew 4.92 / aspect 554.2; venturi 146.41;
concave_bracket 105.41. Dual-only: 51.87 / 74.00 / 62.66+36 pyramids.

Apply the kill criterion (Global Constraints). If the fraction 0.5 is too
aggressive or too timid, ONE retry at a different fraction is allowed
(e.g. 0.25) — record both, do not sweep further without a new prediction.

- [ ] **Step 3: Regression runs (only if the gate passed)**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier fast --runner poly --poly-sliver-dissolve 0.5
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier full --runner poly --poly-sliver-dissolve 0.5
```

Verify healthy geometries (cylinder, groove1, slot1, thin_gap, multi_inlet)
unchanged, no passing row starts failing.

- [ ] **Step 4: Commit the artefacts**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python311;$env:PATH"
git add -f benchmarks/results/comparison_<ts>.json
git commit -m "bench: B-side poly star dissolution on gap rows (locale)"
```

---

### Task 6: Verdict, documentation, handoff

**Files:**
- Modify: `notes/poly_nonortho_reasoning.md` (append B-side section, tick Status)
- Modify: `.opencode/GOAL.md` (append `## Avanzamento 2026-10-05 — Lane NON-ORTHO poly`)
- No source changes.

- [ ] **Step 1: Append the B-side results** under the note's A-side section
  (prediction text untouched), with the verdict PROMOTED-OPT-IN or KILLED and
  the numbers; tick the Status boxes that are true.
- [ ] **Step 2: GOAL entry** in the file's Italian style: what was fixed, A/B
  numbers verbatim, promoted/killed, production wiring still a human decision.
- [ ] **Step 3: Full verification**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m ruff check src/ tests/ benchmarks/
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/ -q -m "not slow and not wsl" -p no:cacheprovider --tb=line
```

- [ ] **Step 4: Commit and STOP** — report to the human; do not wire the flag
  into production paths without their decision.

---

## Self-Review

**Spec coverage:**
- Walk-level dissolution (no tiny polygon left) → Tasks 1-3 ✓
- Opt-in, default off, byte-identical default → Task 3 tests ✓
- Keep-best + refusal on invalid union/merge → Tasks 2-3 ✓
- Harness flag → Task 4 ✓
- A/B + kill criterion from the note → Task 5 ✓
- Honest verdict + human gate for wiring → Task 6 ✓

**Known design constraints carried from the probes:**
- Post-hoc merge is dead (spike 96.73 > 74.00) — the plan never merges a star without removing its polygon (splice is mandatory; a failed splice leaves the vertex un-dissolved).
- `poly_cell_merge`'s pre-BL warning is respected: this is not a cell merge of arbitrary cells, only star dissolution with the polygon union built in the same walk.
- The exact-path boundary (collapse off) is untouched (`dissolve` only applies when `_collapse` is on).

**Placeholder scan:** `<ts>`/`<...>` are runtime artefact names; no TBD, no "similar to Task N".

**Type consistency:** `_plan_star_dissolution(P, float) -> dict[int,int]` → consumed in `_build_dual`; `_splice_boundary_loops(list,int, xyz)` → consumed in `_build_boundary_collapsed`; `dissolve_tiny_boundary_stars: float` → `_benchmark_poly_one(sliver_dissolve=)` → `--poly-sliver-dissolve` → `run_single(poly_sliver_dissolve=)` / `run_parent(poly_sliver_dissolve=)`; names consistent across tasks.
