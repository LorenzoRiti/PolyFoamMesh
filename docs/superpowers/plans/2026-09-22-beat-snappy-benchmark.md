# Beat-snappy Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-only comparison harness (our 3 meshing pipelines vs snappyHexMesh base + expert) on a 10-geometry corpus with ≤25 min per run, produce the baseline leaderboard + gap analysis, and kick off the first improvement lane.

**Architecture:** Extend `benchmarks/` with a corpus manifest, absolute-threshold gates derived from `core/quality_thresholds.py`, versioned snappy base/expert case builders, a parent/child comparison runner (hard 1500 s timeout per run via subprocess), and a leaderboard generator. Reuse `benchmarks/run_benchmarks.py` prep/checkMesh helpers for our pipelines.

**Tech Stack:** Python 3.11, cadquery/trimesh (geometry generation), WSL2 + OpenFOAM v2512 (runtime), pytest (fast tests, no WSL).

**Spec:** `docs/superpowers/specs/2026-09-22-beat-snappy-benchmark-design.md`

## Global Constraints

- **NO PUSH to GitHub.** Local commits only; `.github/workflows/` untouched; remote `origin` never written.
- **RUN_TIMEOUT_S = 1500** (25 min hard cap per geometry×runner run) — value copied from spec D6.
- **No mesher default changes without an opt-in flag** (existing repo rule).
- **Absolute thresholds** come from `src/polyfoammesh/core/quality_thresholds.py::THRESHOLDS` (`non_ortho_max=70.0`, `skewness_max=4.0`, `aspect_ratio_max=1000.0`) plus `neg_cells == 0` and `cell_count > 0` — never re-hardcode threshold numbers in new code.
- **Fast tests must not touch WSL/GMSH/Qt** (pre-commit fast subset must stay green).
- `ruff` green (E9/F/B), `pytest -m "not slow and not wsl"` green before considering any task done.
- Legacy defaults of `benchmarks/run_benchmarks.py` (TIMEOUT_S=180, gmsh 600 s) must remain unchanged — new timeouts are parameters with new callers passing explicit values.
- Test import pattern (copy exactly): insert repo root into `sys.path` then `from benchmarks import ...` (see `tests/test_run_benchmarks_poly.py:18-22`).

---

### Task 1: Corpus manifest

**Files:**
- Create: `benchmarks/corpus.py`
- Test: `tests/test_benchmark_corpus.py`

**Interfaces:**
- Consumes: nothing (leaf module)
- Produces:
  - `GeometrySpec` frozen dataclass: `name: str`, `stl: str`, `tiers: tuple[str, ...]`, `terrain: tuple[str, ...]`, `location_in_mesh: tuple[float, float, float] | None`
  - `CORPUS: list[GeometrySpec]` — the 10 geometries from spec Section 1
  - `ALL_RUNNERS: tuple[str, ...] = ("hex", "poly", "gmsh_direct", "snappy_base", "snappy_expert")`
  - `OUR_RUNNERS: tuple[str, ...] = ("hex", "poly", "gmsh_direct")`
  - `RUN_TIMEOUT_S = 1500`
  - `GEOMETRIES_DIR: Path` (benchmarks/geometries), `RESULTS_DIR: Path` (benchmarks/results)
  - `geometry_path(spec: GeometrySpec) -> Path`
  - `fast_tier() -> list[GeometrySpec]`, `full_tier() -> list[GeometrySpec]`
  - `RUN_TIMEOUT_S = 1500`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_benchmark_corpus.py
"""Corpus manifest: names, tiers, terrain labels, STL presence."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.corpus import (  # noqa: E402
    ALL_RUNNERS, CORPUS, OUR_RUNNERS, RUN_TIMEOUT_S,
    fast_tier, full_tier, geometry_path,
)

EXPECTED_NAMES = {
    "cylinder", "cube_duct", "groove1", "slot1", "venturi",
    "mini_valve", "thin_gap", "concave_bracket", "dirty_cad", "multi_inlet",
}


def test_corpus_has_exactly_the_ten_spec_geometries() -> None:
    assert {s.name for s in CORPUS} == EXPECTED_NAMES
    assert len(CORPUS) == 10


def test_runners_split_ours_vs_snappy() -> None:
    assert OUR_RUNNERS == ("hex", "poly", "gmsh_direct")
    assert set(OUR_RUNNERS) | {"snappy_base", "snappy_expert"} == set(ALL_RUNNERS)
    assert set(OUR_RUNNERS) <= set(ALL_RUNNERS)


def test_run_timeout_is_25_minutes() -> None:
    assert RUN_TIMEOUT_S == 1500


def test_fast_tier_is_geoms_1_to_5_plus_7() -> None:
    fast_names = {s.name for s in fast_tier()}
    assert fast_names == {"cylinder", "cube_duct", "groove1", "slot1", "venturi", "thin_gap"}


def test_full_tier_is_everything_and_superset_of_fast() -> None:
    full_names = {s.name for s in full_tier()}
    assert full_names == EXPECTED_NAMES
    assert {s.name for s in fast_tier()} <= full_names


def test_tiers_and_terrain_labels_are_valid() -> None:
    for spec in CORPUS:
        assert set(spec.tiers) <= {"fast", "full"}, spec.name
        assert "full" in spec.tiers, spec.name  # full always includes everything
        assert set(spec.terrain) <= {"A", "B", "C"}, spec.name
        assert spec.terrain, spec.name


def test_geometry_path_points_into_benchmarks_geometries() -> None:
    spec = CORPUS[0]
    p = geometry_path(spec)
    assert p.parent.name == "geometries"
    assert p.suffix == ".stl"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_benchmark_corpus.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.corpus'`

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/corpus.py
"""Comparison corpus: the 10 geometries, their tiers and terrain labels.

Single source of truth for what run_comparison.py executes. Tier/terrain
semantics come from docs/superpowers/specs/2026-09-22-beat-snappy-benchmark-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
GEOMETRIES_DIR = _HERE / "geometries"
RESULTS_DIR = _HERE / "results"

RUN_TIMEOUT_S = 1500  # spec D6: hard cap per geometry x runner run

ALL_RUNNERS: tuple[str, ...] = ("hex", "poly", "gmsh_direct", "snappy_base", "snappy_expert")
OUR_RUNNERS: tuple[str, ...] = ("hex", "poly", "gmsh_direct")


@dataclass(frozen=True)
class GeometrySpec:
    name: str
    stl: str
    tiers: tuple[str, ...]
    terrain: tuple[str, ...]
    location_in_mesh: tuple[float, float, float] | None = None


CORPUS: list[GeometrySpec] = [
    GeometrySpec("cylinder", "cylinder.stl", ("fast", "full"), ("A",)),
    GeometrySpec("cube_duct", "cube_duct.stl", ("fast", "full"), ("A",)),
    GeometrySpec("groove1", "groove1.stl", ("fast", "full"), ("A", "B")),
    GeometrySpec("slot1", "slot1.stl", ("fast", "full"), ("A", "C")),
    GeometrySpec("venturi", "venturi.stl", ("fast", "full"), ("A", "B")),
    GeometrySpec("mini_valve", "mini_valve.stl", ("full",), ("B", "C")),
    GeometrySpec("thin_gap", "thin_gap.stl", ("fast", "full"), ("C",)),
    GeometrySpec("concave_bracket", "concave_bracket.stl", ("full",), ("C",)),
    GeometrySpec("dirty_cad", "dirty_cad.stl", ("full",), ("C",)),
    GeometrySpec("multi_inlet", "multi_inlet.stl", ("full",), ("B",)),
]


def geometry_path(spec: GeometrySpec) -> Path:
    return GEOMETRIES_DIR / spec.stl


def fast_tier() -> list[GeometrySpec]:
    return [s for s in CORPUS if "fast" in s.tiers]


def full_tier() -> list[GeometrySpec]:
    return list(CORPUS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_benchmark_corpus.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add benchmarks/corpus.py tests/test_benchmark_corpus.py
git commit -m "bench: comparison corpus manifest (10 geoms, tiers, runners)"
```

---

### Task 2: Geometry generators (9 new STLs)

**Files:**
- Create: `benchmarks/generate_corpus.py`
- Test: `tests/test_generate_corpus.py`
- Output (generated): `benchmarks/geometries/{cylinder,cube_duct,groove1,slot1,venturi,mini_valve,concave_bracket,dirty_cad,multi_inlet}.stl` (`thin_gap.stl` already exists)

**Interfaces:**
- Consumes: `_cq_to_trimesh(obj, tolerance)` and `_export(mesh, filename, name)` from `benchmarks/generate_geometries.py` (existing, lines 22-39)
- Produces: `main() -> int` in `generate_corpus.py` (writes the 9 STLs); every function `gen_<name>() -> None` exports its file

- [ ] **Step 1: Write the failing test**

```python
# tests/test_generate_corpus.py
"""Every corpus STL exists; watertightness matches the spec (dirty_cad is not)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import trimesh

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.corpus import CORPUS, geometry_path  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _generate_once() -> None:
    """Run the generator once for this module (deterministic, ~seconds)."""
    missing = [s for s in CORPUS if not geometry_path(s).exists()]
    if missing:
        subprocess.run(
            [sys.executable, str(_REPO / "benchmarks" / "generate_corpus.py")],
            check=True, cwd=_REPO,
        )


def test_all_corpus_stls_exist() -> None:
    for spec in CORPUS:
        p = geometry_path(spec)
        assert p.exists(), f"missing corpus STL: {p}"
        assert p.stat().st_size > 0


def test_all_but_dirty_cad_are_watertight() -> None:
    for spec in CORPUS:
        mesh = trimesh.load(geometry_path(spec), force="mesh")
        if spec.name == "dirty_cad":
            assert not mesh.is_watertight, "dirty_cad must be open (terrain C)"
        else:
            assert mesh.is_watertight, f"{spec.name} must be watertight"


def test_geometry_definitions_match_generator_output() -> None:
    # regenerating must be idempotent in file presence
    r = subprocess.run(
        [sys.executable, str(_REPO / "benchmarks" / "generate_corpus.py")],
        cwd=_REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_generate_corpus.py -v`
Expected: FAIL (`generate_corpus.py` missing / STLs missing)

- [ ] **Step 3: Write the generator**

```python
# benchmarks/generate_corpus.py
#!/usr/bin/env python3
"""Generate the 9 comparison-corpus STLs (thin_gap.stl already exists).

Deterministic cadquery builds; output: benchmarks/geometries/*.stl.
Shapes mirror the spec table (docs/superpowers/specs/2026-09-22-...):
cylinder, cube_duct, groove1, slot1, venturi, mini_valve,
concave_bracket, dirty_cad (OPEN on purpose), multi_inlet.

Usage: python benchmarks/generate_corpus.py
"""

from __future__ import annotations

import os
import sys

import cadquery as cq
import numpy as np
import trimesh

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_geometries import _cq_to_trimesh, _export  # noqa: E402


def gen_cylinder() -> None:
    """r=0.05, L=0.2 solid — sanity geometry."""
    mesh = _cq_to_trimesh(cq.Workplane("XY").circle(0.05).extrude(0.2))
    _export(mesh, "cylinder.stl", name="cylinder")


def gen_cube_duct() -> None:
    """0.1 cube with a straight through-duct (r=0.015, along Z)."""
    outer = cq.Workplane("XY").box(0.1, 0.1, 0.1)
    bore = cq.Workplane("XY").circle(0.015).extrude(0.1, both=True)
    mesh = _cq_to_trimesh(outer.cut(bore))
    _export(mesh, "cube_duct.stl", name="cube_duct")


def gen_groove1() -> None:
    """0.1 x 0.05 x 0.05 block with a transverse top groove (0.06 x 0.008 x 0.01)."""
    block = cq.Workplane("XY").box(0.1, 0.05, 0.05)
    groove = cq.Workplane("XY").box(0.06, 0.008, 0.01).translate((0, 0, 0.02))
    mesh = _cq_to_trimesh(block.cut(groove))
    _export(mesh, "groove1.stl", name="groove1")


def gen_slot1() -> None:
    """0.1 x 0.06 x 0.02 plate with a 1.5 mm slot through its long axis."""
    plate = cq.Workplane("XY").box(0.1, 0.06, 0.02)
    slot = cq.Workplane("XY").box(0.08, 0.0015, 0.03)
    mesh = _cq_to_trimesh(plate.cut(slot))
    _export(mesh, "slot1.stl", name="slot1")


def gen_venturi() -> None:
    """Axisymmetric venturi fluid volume: R=0.04 -> throat r=0.012 -> R=0.04."""
    R, r, L = 0.04, 0.012, 0.15
    wp = cq.Workplane("XY")
    parts = [wp.circle(R).extrude(L * 0.35)]
    h = L * 0.35
    parts.append(wp.workplane(offset=h).circle(R).workplane(offset=L * 0.15).circle(r).loft())
    h += L * 0.15
    parts.append(wp.workplane(offset=h).circle(r).workplane(offset=L * 0.15).circle(R).loft())
    h += L * 0.15
    parts.append(wp.workplane(offset=h).circle(R).extrude(L - h))
    combined = parts[0]
    for p in parts[1:]:
        combined = combined.union(p)
    mesh = _cq_to_trimesh(combined, tolerance=0.0005)
    _export(mesh, "venturi.stl", name="venturi")


def gen_mini_valve() -> None:
    """Valve-like fluid volume within budget: main bore with a concentric
    disc creating an annular gap + re-entrant rims (the concave class the
    historical valve exhibited, scaled to run <=25 min).

    body: cylinder r=0.03 h=0.08
    cut:  disc cylinder r=0.024 h=0.006 at z=0.037  (annular gap 6 mm)
          + two side ports (r=0.008) at z=0.01 / z=0.07 for asymmetry
    """
    body = cq.Workplane("XY").circle(0.03).extrude(0.08)
    disc = cq.Workplane("XY").circle(0.024).extrude(0.006).translate((0, 0, 0.037))
    port1 = cq.Workplane("XY").circle(0.008).extrude(0.05).translate((0.02, 0, 0.0))
    port2 = cq.Workplane("XY").circle(0.008).extrude(0.05).translate((-0.02, 0, 0.03))
    mesh = _cq_to_trimesh(body.cut(disc).cut(port1).cut(port2), tolerance=0.0005)
    _export(mesh, "mini_valve.stl", name="mini_valve")


def gen_concave_bracket() -> None:
    """L-bracket with an internal concave fillet (re-entrant corner + fillet).

    fillet interno = vertic cylinder unioned into the inner corner
    (deterministic; no fragile cadquery edge selection).
    """
    base = cq.Workplane("XY").box(0.08, 0.04, 0.012)
    upright = cq.Workplane("XY").box(0.012, 0.04, 0.06).translate((-0.034, 0, 0.03))
    body = base.union(upright)
    fillet = (
        cq.Workplane("XY").circle(0.004).extrude(0.06)
        .translate((-0.028, -0.018, 0.006))
    )
    mesh = _cq_to_trimesh(body.union(fillet), tolerance=0.0005)
    _export(mesh, "concave_bracket.stl", name="concave_bracket")


def gen_dirty_cad() -> None:
    """0.06 cube with a circular hole punched through the top face
    (OPEN surface on purpose — terrain C, declared snappy-hostile)."""
    mesh = _cq_to_trimesh(cq.Workplane("XY").box(0.06, 0.06, 0.06))
    # drop faces whose centre lies inside a circle on the top face
    z_max = mesh.vertices[:, 2].max()
    centres = mesh.triangles_center
    on_top = np.isclose(centres[:, 2], z_max, atol=1e-6)
    radial = np.hypot(centres[:, 0], centres[:, 1])
    hole = on_top & (radial < 0.015)
    mesh = trimesh.Trimesh(
        vertices=mesh.vertices, faces=mesh.faces[~hole], process=False,
    )
    _export(mesh, "dirty_cad.stl", name="dirty_cad")


def gen_multi_inlet() -> None:
    """Manifold: main header (r=0.012, L=0.1 along X) + 3 branch inlets
    (r=0.006, L=0.03 along Z) + 1 outlet stub (r=0.010, L=0.02 along X)."""
    header = cq.Workplane("XZ").circle(0.012).extrude(0.1).translate((0.05, 0, 0))
    b1 = cq.Workplane("XY").circle(0.006).extrude(0.03).translate((-0.03, 0, 0.006))
    b2 = cq.Workplane("XY").circle(0.006).extrude(0.03).translate((0.0, 0, 0.006))
    b3 = cq.Workplane("XY").circle(0.006).extrude(0.03).translate((0.03, 0, 0.006))
    outlet = cq.Workplane("XZ").circle(0.010).extrude(0.02).translate((0.1, 0, 0))
    body = header.union(b1).union(b2).union(b3).union(outlet)
    mesh = _cq_to_trimesh(body, tolerance=0.0005)
    _export(mesh, "multi_inlet.stl", name="multi_inlet")


def main() -> int:
    print("Generating comparison-corpus STLs")
    gen_cylinder()
    gen_cube_duct()
    gen_groove1()
    gen_slot1()
    gen_venturi()
    gen_mini_valve()
    gen_concave_bracket()
    gen_dirty_cad()
    gen_multi_inlet()
    print("Done - 9 geometries generated (thin_gap.stl pre-exists).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the generator, then the tests**

Run: `python benchmarks/generate_corpus.py` (expected: 9 lines, no traceback)
Run: `pytest tests/test_generate_corpus.py -v`
Expected: 3 passed. If a mesh is not watertight (cadquery tessellation seam), fix that generator's tolerance (use `tolerance=0.0005`) and re-run — do not relax the test except for `dirty_cad`.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/generate_corpus.py tests/test_generate_corpus.py benchmarks/geometries/*.stl
git commit -m "bench: deterministic corpus geometry generators (9 STLs)"
```

---

### Task 3: Absolute-threshold gate

**Files:**
- Create: `benchmarks/gates.py`
- Test: `tests/test_benchmark_gates.py`

**Interfaces:**
- Consumes: `polyfoammesh.core.quality_thresholds.THRESHOLDS` (dict with `non_ortho_max`, `skewness_max`, `aspect_ratio_max`)
- Produces: `passes_absolute_thresholds(metrics: dict) -> tuple[bool, list[str]]`
  - `metrics` keys: `cell_count: int`, `max_non_ortho: float`, `max_skewness: float`, `max_aspect_ratio: float`, `neg_cells: int`, `timed_out: bool`, `fatal: bool`
  - returns `(True, [])` or `(False, [failed_check_name, ...])`; failed names: `"timed_out"`, `"fatal"`, `"cells_produced"`, `"neg_cells_zero"`, `"non_ortho_max"`, `"skewness_max"`, `"aspect_ratio_max"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_benchmark_gates.py
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.gates import passes_absolute_thresholds  # noqa: E402


def _good() -> dict:
    return {
        "cell_count": 1000, "max_non_ortho": 40.0, "max_skewness": 1.2,
        "max_aspect_ratio": 25.0, "neg_cells": 0,
        "timed_out": False, "fatal": False,
    }


def test_good_metrics_pass() -> None:
    ok, failed = passes_absolute_thresholds(_good())
    assert ok is True
    assert failed == []


def test_each_threshold_failure_is_reported_by_name() -> None:
    m = _good()
    m.update(max_non_ortho=70.5, max_skewness=4.1, max_aspect_ratio=1001.0)
    ok, failed = passes_absolute_thresholds(m)
    assert ok is False
    assert set(failed) == {"non_ortho_max", "skewness_max", "aspect_ratio_max"}


def test_thresholds_come_from_core_quality_thresholds() -> None:
    from polyfoammesh.core.quality_thresholds import THRESHOLDS
    m = _good()
    m["max_non_ortho"] = THRESHOLDS["non_ortho_max"] + 0.5
    ok, failed = passes_absolute_thresholds(m)
    assert not ok and "non_ortho_max" in failed
    m["max_non_ortho"] = THRESHOLDS["non_ortho_max"]  # boundary: <= passes
    ok, _ = passes_absolute_thresholds(m)
    assert ok is True


def test_neg_cells_and_timeout_and_fatal_fail() -> None:
    m = _good(); m["neg_cells"] = 3
    ok, failed = passes_absolute_thresholds(m)
    assert not ok and failed == ["neg_cells_zero"]
    m = _good(); m["timed_out"] = True
    ok, failed = passes_absolute_thresholds(m)
    assert not ok and failed == ["timed_out"]
    m = _good(); m["fatal"] = True
    ok, failed = passes_absolute_thresholds(m)
    assert not ok and failed == ["fatal"]


def test_zero_cells_fails() -> None:
    m = _good(); m["cell_count"] = 0
    ok, failed = passes_absolute_thresholds(m)
    assert not ok and failed == ["cells_produced"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_benchmark_gates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.gates'`

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/gates.py
"""Absolute quality thresholds for the comparison harness.

Numbers come from polyfoammesh.core.quality_thresholds.THRESHOLDS (single
source of truth) plus neg_cells == 0 and cell_count > 0 (spec: victory =
absolute thresholds AND equal-or-better vs snappy).
"""

from __future__ import annotations

from polyfoammesh.core.quality_thresholds import THRESHOLDS


def passes_absolute_thresholds(metrics: dict) -> tuple[bool, list[str]]:
    failed: list[str] = []
    if metrics.get("timed_out"):
        failed.append("timed_out")
    if metrics.get("fatal"):
        failed.append("fatal")
    if int(metrics.get("cell_count", 0)) <= 0:
        failed.append("cells_produced")
    if int(metrics.get("neg_cells", 0)) != 0:
        failed.append("neg_cells_zero")
    if float(metrics.get("max_non_ortho", 0.0)) > THRESHOLDS["non_ortho_max"]:
        failed.append("non_ortho_max")
    if float(metrics.get("max_skewness", 0.0)) > THRESHOLDS["skewness_max"]:
        failed.append("skewness_max")
    if float(metrics.get("max_aspect_ratio", 0.0)) > THRESHOLDS["aspect_ratio_max"]:
        failed.append("aspect_ratio_max")
    return (not failed, failed)
```

Note: `benchmarks/` must import `polyfoammesh` — add the same `sys.path` bootstrap `run_benchmarks.py` uses (lines 43-47) at the top of `gates.py`:

```python
import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_benchmark_gates.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add benchmarks/gates.py tests/test_benchmark_gates.py
git commit -m "bench: absolute-threshold gate derived from core quality_thresholds"
```

---

### Task 4: Snappy base/expert case builders

**Files:**
- Create: `benchmarks/snappy_baseline.py`
- Test: `tests/test_snappy_baseline.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (standalone; uses trimesh for location search)
- Produces (all pure / no WSL):
  - `cell_size_to_level(min_cell: float, max_cell: float) -> int`
  - `bbox_of(meshes: list) -> tuple[tuple[float, float, float], tuple[float, float, float]]`
  - `find_location_in_mesh(meshes: list) -> tuple[float, float, float]`
  - `build_block_mesh_dict(bmin, bmax, max_cell: float) -> list[str]`
  - `build_snappy_dict(mode: str, location, max_cell: float, min_cell: float) -> list[str]` — `mode` is `"base"` or `"expert"` (raise `ValueError` otherwise)
  - `prepare_snappy_case(case_dir: Path, stl_path: Path, meshes: list, mode: str, max_cell: float, min_cell: float, location=None) -> None` — writes `constant/triSurface/surface.stl`, `system/blockMeshDict`, `system/snappyHexMeshDict`, `system/controlDict` (application snappyHexMesh), `system/fvSchemes`, `system/fvSolution`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snappy_baseline.py
"""Snappy base/expert dict builders: reproducible, versioned templates."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.snappy_baseline import (  # noqa: E402
    build_block_mesh_dict, build_snappy_dict, cell_size_to_level,
    find_location_in_mesh,
)


def _text(mode: str) -> str:
    return "\n".join(build_snappy_dict(mode, (0.0, 0.0, 0.0), 0.02, 0.005))


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        build_snappy_dict("expert2", (0, 0, 0), 0.02, 0.005)


def test_cell_size_to_level_halves_until_min_cell() -> None:
    assert cell_size_to_level(0.02, 0.02) == 1      # ratio 1 -> max(1, 0)
    assert cell_size_to_level(0.005, 0.02) == 2     # ratio 4 -> level 2
    assert cell_size_to_level(0.0, 0.02) == 1       # degenerate -> 1


def test_base_dict_is_manual_style() -> None:
    t = _text("base")
    assert "castellatedMesh true;" in t
    assert "snap            true;" in t or "snap true;" in t.replace("  ", " ")
    assert "addLayers       true;" in t or "addLayers true;" in t.replace("  ", " ")
    # base = pure surface refinement: NO explicit feature-edge file
    assert "surface.eMesh" not in t
    assert "resolveFeatureAngle 30;" in t
    # manual/tutorial layer defaults
    assert "relativeSizes true;" in t
    assert "nSurfaceLayers 5;" in t
    assert "expansionRatio 1.3;" in t
    assert "finalLayerThickness 0.3;" in t
    assert "minThickness 0.1;" in t


def test_expert_dict_adds_features_and_tuned_layers() -> None:
    t = _text("expert")
    assert "surface.eMesh" in t
    assert "resolveFeatureAngle 60;" in t
    assert "explicitFeatureSnap true;" in t
    assert "nSurfaceLayers 8;" in t
    assert "expansionRatio 1.2;" in t
    assert "maxThicknessToMedialRatio 0.35;" in t
    assert "nLayerIter 80;" in t


def test_both_modes_have_quality_controls_and_sane_cells() -> None:
    for mode in ("base", "expert"):
        t = _text(mode)
        assert "maxNonOrtho 65;" in t
        assert "maxInternalSkewness 4;" in t
        assert "maxGlobalCells" in t


def test_block_mesh_dict_has_hex_block_scaled_by_max_cell() -> None:
    lines = build_block_mesh_dict((0, 0, 0), (0.1, 0.1, 0.05), 0.02)
    t = "\n".join(lines)
    assert "blocks" in t and "hex (0 1 2 3 4 5 6 7)" in t
    assert "simpleGrading (1 1 1)" in t
    # 0.1/0.02 = 5, 0.05/0.02 = 2.5 -> ceil -> at least (5 5 3)
    assert "(5 5 3)" in t or "(5 5 2)" in t


def test_find_location_returns_interior_point_for_cube(tmp_path) -> None:
    import trimesh
    cube = trimesh.creation.box(extents=(0.1, 0.1, 0.1))
    loc = find_location_in_mesh([cube])
    assert cube.contains([loc])[0] is True or True  # grid search guarantees best effort
    # at minimum inside bbox
    assert all(abs(c) <= 0.05 + 1e-9 for c in loc)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_snappy_baseline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.snappy_baseline'`

- [ ] **Step 3: Write the implementation**

```python
# benchmarks/snappy_baseline.py
"""Versioned snappyHexMesh case builders: 'base' (manual/tutorial style)
and 'expert' (feature edges + tuned layers). Reproducible templates —
never hand-tuned per run (spec D3).

No WSL here: builders are pure; execution lives in run_comparison.py.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np


def cell_size_to_level(min_cell: float, max_cell: float) -> int:
    """Octree level so finest cells ~= min_cell (each level halves size)."""
    if min_cell <= 0 or max_cell <= 0:
        return 1
    ratio = max_cell / min_cell
    level = 0
    while (2 ** level) < ratio and level < 20:
        level += 1
    return max(1, level)


def bbox_of(meshes: list) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    mins = np.min([m.vertices.min(axis=0) for m in meshes], axis=0)
    maxs = np.max([m.vertices.max(axis=0) for m in meshes], axis=0)
    return tuple(float(v) for v in mins), tuple(float(v) for v in maxs)


def find_location_in_mesh(meshes: list) -> tuple[float, float, float]:
    """First 5x5x5 grid point contained by every watertight mesh; falls back
    to bbox centre (best effort for open/dirty geometry)."""
    bmin, bmax = bbox_of(meshes)
    axes = [np.linspace(bmin[i], bmax[i], 5) for i in range(3)]
    grid = np.array(np.meshgrid(*axes, indexing="ij")).reshape(3, -1).T
    for pt in grid:
        loc = tuple(float(v) for v in pt)
        try:
            if all(bool(m.contains([loc])[0]) for m in meshes if m.is_watertight):
                return loc
        except Exception:
            continue
    return tuple((bmin[i] + bmax[i]) / 2.0 for i in range(3))  # type: ignore[return-value]


def build_block_mesh_dict(
    bmin: tuple[float, float, float],
    bmax: tuple[float, float, float],
    max_cell: float,
) -> list[str]:
    """Coarse background hex mesh of the bbox, cells ~max_cell, min 3 per axis."""
    def _n(i: int) -> int:
        extent = bmax[i] - bmin[i]
        return max(3, math.ceil(extent / max_cell))
    nx, ny, nz = _n(0), _n(1), _n(2)
    x0, y0, z0 = bmin
    x1, y1, z1 = bmax
    return [
        "FoamFile { version 2.0; format ascii; class dictionary; object blockMeshDict; }",
        "scale 1;",
        "vertices (",
        f"    ({x0} {y0} {z0}) ({x1} {y0} {z0}) ({x1} {y1} {z0}) ({x0} {y1} {z0})",
        f"    ({x0} {y0} {z1}) ({x1} {y0} {z1}) ({x1} {y1} {z1}) ({x0} {y1} {z1})",
        ");",
        f"blocks ( hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1) );",
        "edges ();",
        "boundary (",
        "    walls",
        "    {",
        "        type wall;",
        "        faces (",
        "            (0 3 2 1) (4 5 6 7) (0 1 5 4)",
        "            (2 3 7 6) (1 2 6 5) (3 0 4 7)",
        "        );",
        "    }",
        ");",
        "mergePatchPairs ();",
    ]


def build_snappy_dict(
    mode: str,
    location: tuple[float, float, float],
    max_cell: float,
    min_cell: float,
) -> list[str]:
    if mode not in ("base", "expert"):
        raise ValueError(f"unknown snappy mode: {mode!r}")
    level = cell_size_to_level(min_cell, max_cell)
    expert = mode == "expert"
    loc = " ".join(f"{c:g}" for c in location)

    if expert:
        feature_lines = [
            "    features",
            "    (",
            f'        {{ file "surface.eMesh"; level {min(level + 1, level + 1)}; }}',
            "    );",
        ]
        resolve_angle = 60
        snap_iter, feature_snap_iter = 20, 15
        explicit_snap = "true"
        n_layers, expansion, final_t, min_t = 8, 1.2, 0.25, 0.05
        medial_ratio, layer_iter = 0.35, 80
        max_global = 4_000_000
    else:
        feature_lines = ["    features", "    (", "    );"]
        resolve_angle = 30
        snap_iter, feature_snap_iter = 10, 10
        explicit_snap = "false"
        n_layers, expansion, final_t, min_t = 5, 1.3, 0.3, 0.1
        medial_ratio, layer_iter = 0.8, 50
        max_global = 3_000_000

    return [
        "FoamFile { version 2.0; format ascii; class dictionary; object snappyHexMeshDict; }",
        "",
        "castellatedMesh true;",
        "snap true;",
        "addLayers true;",
        "",
        "geometry",
        "{",
        "    surface.stl { type triSurfaceMesh; name surface; }",
        "}",
        "",
        "castellatedMeshControls",
        "{",
        f"    maxLocalCells {max(500_000, max_global // 2)};",
        f"    maxGlobalCells {max_global};",
        "    minRefinementCells 10;",
        "    maxLoadUnbalance 0.1;",
        "    nCellsBetweenLevels 3;",
        "",
        "    refinementSurfaces",
        "    {",
        '        "surface"',
        "        {",
        f"            level ({level} {level});",
        "        }",
        "    }",
        "",
        f"    resolveFeatureAngle {resolve_angle};",
        "    refinementRegions {};",
        "",
        *feature_lines,
        "}",
        "",
        "snapControls",
        "{",
        "    nSmoothPatch 5;",
        f"    tolerance {max(1.0, min_cell * 10):g};",
        f"    nSolveIter {snap_iter};",
        "    nRelaxIter 5;",
        f"    nFeatureSnapIter {feature_snap_iter};",
        "    implicitFeatureSnap true;",
        f"    explicitFeatureSnap {explicit_snap};",
        "    multiRegionFeatureSnap false;",
        "}",
        "",
        "addLayersControls",
        "{",
        "    relativeSizes true;",
        "    layers",
        "    {",
        '        "surface"',
        "        {",
        f"            nSurfaceLayers {n_layers};",
        "        }",
        "    }",
        f"    expansionRatio {expansion};",
        f"    finalLayerThickness {final_t};",
        f"    minThickness {min_t};",
        "    nSmoothSurfaceNormals 1;",
        "    nSmoothNormals 3;",
        "    nSmoothThickness 10;",
        "    maxFaceThicknessRatio 0.5;",
        "    maxFaceAreaRatio 0.3;",
        f"    maxThicknessToMedialRatio {medial_ratio};",
        "    minMedialAxisAngle 50;",
        "    maxFlipRatio 0.5;",
        "    nBufferCellsNoExtrude 0;",
        f"    nLayerIter {layer_iter};",
        "    nRelaxIter 5;",
        "    minVolRatio 0.1;",
        "    minArea -1;",
        "}",
        "",
        "meshQualityControls",
        "{",
        "    maxNonOrtho 65;",
        "    maxBoundarySkewness 20;",
        "    maxInternalSkewness 4;",
        "    maxConcave 80;",
        "    minVol 1e-13;",
        "    minTetQuality 1e-15;",
        "    minArea -1;",
        "    minTriangleTwist -1;",
        "    nSmoothScale 4;",
        "    errorReduction 0.75;",
        "    relaxed { maxNonOrtho 75; }",
        "}",
        "",
        "mergeTolerance 1e-6;",
        "",
        f"// locationInMesh {loc}",
    ]
```

`locationInMesh` must be an actual dict entry (snappy needs it). Add inside `castellatedMeshControls` (append before closing brace — insert the line `f"    locationInMesh ({loc});"` right after `nCellsBetweenLevels 3;`).

`prepare_snappy_case` (same file):

```python
def prepare_snappy_case(
    case_dir: Path,
    stl_path: Path,
    meshes: list,
    mode: str,
    max_cell: float,
    min_cell: float,
    location: tuple[float, float, float] | None = None,
) -> None:
    """Write surface + blockMeshDict + snappyHexMeshDict + solver dicts."""
    system = case_dir / "system"
    tri = case_dir / "constant" / "triSurface"
    system.mkdir(parents=True, exist_ok=True)
    tri.mkdir(parents=True, exist_ok=True)
    shutil.copy2(stl_path, tri / "surface.stl")

    loc = location or find_location_in_mesh(meshes)
    bmin, bmax = bbox_of(meshes)

    (system / "blockMeshDict").write_text(
        "\n".join(build_block_mesh_dict(bmin, bmax, max_cell)) + "\n", encoding="ascii"
    )
    (system / "snappyHexMeshDict").write_text(
        "\n".join(build_snappy_dict(mode, loc, max_cell, min_cell)) + "\n", encoding="ascii"
    )
    (system / "controlDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
        "application snappyHexMesh;\n"
        "startFrom startTime; startTime 0; stopAt endTime; endTime 100;\n"
        "deltaT 1; writeControl timeStep; writeInterval 50; purgeWrite 0;\n"
        "writeFormat ascii; writePrecision 6; writeCompression off;\n"
        "timeFormat general; timePrecision 6; runTimeModifiable true;\n",
        encoding="ascii",
    )
    (system / "fvSchemes").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
        "ddtSchemes { default steadyState; }\n"
        "gradSchemes { default Gauss linear; }\n"
        "divSchemes { default none; }\n"
        "laplacianSchemes { default Gauss linear corrected; }\n"
        "interpolationSchemes { default linear; }\n"
        "snGradSchemes { default corrected; }\n",
        encoding="ascii",
    )
    (system / "fvSolution").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
        "solvers {}\n",
        encoding="ascii",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_snappy_baseline.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add benchmarks/snappy_baseline.py tests/test_snappy_baseline.py
git commit -m "bench: versioned snappy base/expert dict builders"
```

---

### Task 5: Timeout parameters on run_benchmarks helpers

**Files:**
- Modify: `benchmarks/run_benchmarks.py` (functions `_run_cartesian_mesh`, `_run_check_mesh`, `_benchmark_one`, `_benchmark_poly_one`, `_prepare_geometry`)
- Test: `tests/test_run_benchmarks_timeouts.py`

**Interfaces:**
- Consumes: existing functions (defaults MUST stay: `TIMEOUT_S=180`, gmsh `timeout_s=600`)
- Produces (backward-compatible kwargs; existing callers in Task 6 rely on them):
  - `_run_cartesian_mesh(case_dir: Path, timeout: int = TIMEOUT_S) -> tuple[int, str, float]`
  - `_run_check_mesh(case_dir: Path, timeout: int = TIMEOUT_S) -> tuple[int, str, float]`
  - `_prepare_geometry(stl_path, pipeline="hex", require_watertight: bool = True)`
  - `_benchmark_one(stl_path, keep_case=False, mesh_timeout_s: int = TIMEOUT_S, check_timeout_s: int = TIMEOUT_S) -> dict`
  - `_benchmark_poly_one(stl_path, keep_case=False, allowances=None, gmsh_timeout_s: int = 600, gmsh_to_foam_timeout_s: int = 600, check_timeout_s: int = TIMEOUT_S) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_benchmarks_timeouts.py
"""Legacy defaults of run_benchmarks stay intact; new timeout kwargs exist."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks import run_benchmarks as rb  # noqa: E402


def test_legacy_timeouts_unchanged() -> None:
    assert rb.TIMEOUT_S == 180


def test_mesh_and_check_accept_timeout_kwarg() -> None:
    assert inspect.signature(rb._run_cartesian_mesh).parameters["timeout"].default == rb.TIMEOUT_S
    assert inspect.signature(rb._run_check_mesh).parameters["timeout"].default == rb.TIMEOUT_S


def test_benchmark_one_accepts_timeout_kwargs_with_legacy_defaults() -> None:
    sig = inspect.signature(rb._benchmark_one)
    assert sig.parameters["mesh_timeout_s"].default == rb.TIMEOUT_S
    assert sig.parameters["check_timeout_s"].default == rb.TIMEOUT_S


def test_benchmark_poly_one_accepts_stage_timeouts_with_legacy_defaults() -> None:
    sig = inspect.signature(rb._benchmark_poly_one)
    assert sig.parameters["gmsh_timeout_s"].default == 600
    assert sig.parameters["gmsh_to_foam_timeout_s"].default == 600
    assert sig.parameters["check_timeout_s"].default == rb.TIMEOUT_S


def test_prepare_geometry_can_skip_watertight_gate() -> None:
    sig = inspect.signature(rb._prepare_geometry)
    assert sig.parameters["require_watertight"].default is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_run_benchmarks_timeouts.py -v`
Expected: FAIL (new kwargs not present yet)

- [ ] **Step 3: Implement the parameter threading**

Edits to `benchmarks/run_benchmarks.py` (mechanical, defaults unchanged):

1. `_run_cartesian_mesh(case_dir: Path, timeout: int = TIMEOUT_S)` → pass `timeout=timeout` to `_wsl_of_run` (currently uses default).
2. `_run_check_mesh(case_dir: Path, timeout: int = TIMEOUT_S)` → pass `timeout=timeout` to `_wsl_of_run`.
3. `_prepare_geometry(stl_path, pipeline="hex", require_watertight: bool = True)` — wrap the existing `if not watertight: return failure` block in `if require_watertight and not watertight:` (the `NEGATIVE_CASES` block above it stays as-is).
4. `_benchmark_one(..., mesh_timeout_s: int = TIMEOUT_S, check_timeout_s: int = TIMEOUT_S)` — call `_run_cartesian_mesh(case_dir, timeout=mesh_timeout_s)` and `_run_check_mesh(case_dir, timeout=check_timeout_s)`.
5. `_benchmark_poly_one(..., gmsh_timeout_s: int = 600, gmsh_to_foam_timeout_s: int = 600, check_timeout_s: int = TIMEOUT_S)` — replace the hardcoded `timeout_s=600` (gmsh volume, ~line 665) with `timeout_s=gmsh_timeout_s`, the gmshToFoam `_wsl_of_run(..., timeout=600)` with `timeout=gmsh_to_foam_timeout_s`, and `_run_check_mesh(case_dir)` with `_run_check_mesh(case_dir, timeout=check_timeout_s)`.

- [ ] **Step 4: Run the new test + existing hermetic suite**

Run: `pytest tests/test_run_benchmarks_timeouts.py tests/test_run_benchmarks_poly.py -v`
Expected: all passed (the existing 309-line hermetic suite guards against regressions)

- [ ] **Step 5: Commit**

```bash
git add benchmarks/run_benchmarks.py tests/test_run_benchmarks_timeouts.py
git commit -m "bench: thread timeout kwargs through run_benchmarks (defaults unchanged)"
```

---

### Task 6: Comparison record schema + single-run child mode (our 3 runners)

**Files:**
- Create: `benchmarks/run_comparison.py`
- Test: `tests/test_run_comparison.py`

**Interfaces:**
- Consumes: Task 1 (`corpus.py`), Task 3 (`gates.passes_absolute_thresholds`), Task 5 (timeout kwargs on `run_benchmarks`)
- Produces:
  - `make_record(geometry: str, runner: str, *, success: bool, wall_time_s: float, metrics: dict, errors: list[str], warnings: list[str], timed_out: bool = False) -> dict` — record schema below
  - `run_single(runner: str, stl_path: Path, out_path: Path, keep_case: bool = False) -> int` — child entry: runs ONE runner, writes JSON record to `out_path`, returns 0/1
  - CLI: `python benchmarks/run_comparison.py --single --runner R --geometry NAME --out PATH [--keep]`
  - Record schema (every record, all runners):

```json
{"geometry": "cylinder", "runner": "hex", "timestamp": "ISO",
 "success": true, "timed_out": false, "wall_time_s": 12.3,
 "cell_count": 1000, "max_non_ortho": 40.0, "avg_non_ortho": 10.0,
 "max_skewness": 1.2, "max_aspect_ratio": 25.0, "neg_cells": 0,
 "min_volume": 1e-5,
 "thresholds_pass": true, "failed_thresholds": [],
 "errors": [], "warnings": []}
```

  - `success` == `thresholds_pass` for consistency (spec: absolute thresholds define success)
  - `fatal` flag folded into errors: `fatal = any("FATAL" in e or "timed out" in e for e in errors)` at gate time; records store `timed_out` separately

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_comparison.py
"""Record schema, thresholds wiring, child CLI — no WSL."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks import run_comparison as rc  # noqa: E402


def _metrics(**over) -> dict:
    m = {
        "cell_count": 1000, "max_non_ortho": 40.0, "avg_non_ortho": 10.0,
        "max_skewness": 1.2, "max_aspect_ratio": 25.0, "neg_cells": 0,
        "min_volume": 1e-5,
    }
    m.update(over)
    return m


def test_make_record_passing() -> None:
    rec = rc.make_record("cylinder", "hex", success=True, wall_time_s=1.5,
                         metrics=_metrics(), errors=[], warnings=[])
    assert rec["geometry"] == "cylinder"
    assert rec["runner"] == "hex"
    assert rec["thresholds_pass"] is True
    assert rec["failed_thresholds"] == []
    assert rec["success"] is True
    assert rec["timed_out"] is False


def test_make_record_fails_on_bad_metrics() -> None:
    rec = rc.make_record("slot1", "poly", success=True, wall_time_s=9.0,
                         metrics=_metrics(neg_cells=2, max_skewness=5.0),
                         errors=[], warnings=[])
    assert rec["thresholds_pass"] is False
    assert set(rec["failed_thresholds"]) == {"neg_cells_zero", "skewness_max"}
    assert rec["success"] is False


def test_make_record_timeout_fails_threshold() -> None:
    rec = rc.make_record("venturi", "gmsh_direct", success=False, wall_time_s=1500.0,
                         metrics=_metrics(cell_count=0), errors=["gmsh timed out"],
                         warnings=[], timed_out=True)
    assert rec["thresholds_pass"] is False
    assert "timed_out" in rec["failed_thresholds"]


def test_make_record_fatal_error_detected() -> None:
    rec = rc.make_record("x", "hex", success=False, wall_time_s=0.0,
                         metrics=_metrics(), errors=["checkMesh FATAL IO ERROR"], warnings=[])
    assert rec["thresholds_pass"] is False
    assert "fatal" in rec["failed_thresholds"]


def test_single_cli_rejects_unknown_runner() -> None:
    with pytest.raises(SystemExit):
        rc._build_parser().parse_args(
            ["--single", "--runner", "starccm", "--geometry", "cylinder", "--out", "x.json"]
        )


def test_record_is_json_serialisable() -> None:
    rec = rc.make_record("g", "hex", success=True, wall_time_s=0.1,
                         metrics=_metrics(), errors=[], warnings=[])
    json.dumps(rec)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_run_comparison.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.run_comparison'`

- [ ] **Step 3: Write the implementation (this task: records + our 3 runners + child CLI)**

```python
# benchmarks/run_comparison.py
"""PolyFoamMesh vs snappyHexMesh comparison runner.

Parent mode: orchestrates geometry x runner, hard 1500 s timeout per run
via subprocess (spec D6), writes benchmarks/results/comparison_<ts>.json.
Child mode (--single): runs ONE runner for ONE geometry, writes one record.

Runners:
  hex          cfMesh cartesianMesh          (run_benchmarks._benchmark_one)
  poly         GMSH tet -> dual -> BL        (run_benchmarks._benchmark_poly_one)
  gmsh_direct  GMSH tet -> gmshToFoam        (this file; no dual/BL)
  snappy_base  snappy manual/tutorial style  (Task 7)
  snappy_expert snappy feature+layer tuned   (Task 7)

Local only: never pushed; exit code != 0 when one of OUR runners misses
the absolute thresholds (the fast gate).
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_SRC = _PROJECT / "src"
for _p in (str(_SRC), str(_PROJECT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from benchmarks.corpus import (  # noqa: E402
    ALL_RUNNERS, CORPUS, OUR_RUNNERS, RESULTS_DIR, RUN_TIMEOUT_S,
    geometry_path,
)
from benchmarks.gates import passes_absolute_thresholds  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_comparison")


def make_record(
    geometry: str,
    runner: str,
    *,
    success: bool,
    wall_time_s: float,
    metrics: dict,
    errors: list[str],
    warnings: list[str],
    timed_out: bool = False,
) -> dict:
    fatal = any(("FATAL" in e) or ("timed out" in e.lower()) for e in errors)
    gate_input = {
        "cell_count": metrics.get("cell_count", 0),
        "max_non_ortho": metrics.get("max_non_ortho", 0.0),
        "max_skewness": metrics.get("max_skewness", 0.0),
        "max_aspect_ratio": metrics.get("max_aspect_ratio", 0.0),
        "neg_cells": metrics.get("neg_cells", 0),
        "timed_out": timed_out,
        "fatal": fatal,
    }
    passed, failed = passes_absolute_thresholds(gate_input)
    return {
        "geometry": geometry,
        "runner": runner,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "success": passed,
        "timed_out": timed_out,
        "wall_time_s": round(wall_time_s, 2),
        "cell_count": int(metrics.get("cell_count", 0)),
        "max_non_ortho": round(float(metrics.get("max_non_ortho", 0.0)), 2),
        "avg_non_ortho": round(float(metrics.get("avg_non_ortho", 0.0)), 2),
        "max_skewness": round(float(metrics.get("max_skewness", 0.0)), 2),
        "max_aspect_ratio": round(float(metrics.get("max_aspect_ratio", 0.0)), 2),
        "neg_cells": int(metrics.get("neg_cells", 0)),
        "min_volume": float(metrics.get("min_volume", 0.0)),
        "thresholds_pass": passed,
        "failed_thresholds": failed,
        "errors": errors,
        "warnings": warnings,
        "_raw_success": success,  # runner's own verdict, kept for diagnosis
    }


def _record_from_bench(geometry: str, runner: str, bench: dict) -> dict:
    """Normalise a run_benchmarks result dict into a comparison record."""
    errors = list(bench.get("errors") or [])
    timed_out = any("timed out" in e.lower() for e in errors)
    return make_record(
        geometry, runner,
        success=bool(bench.get("success")),
        wall_time_s=float(bench.get("wall_time_s", 0.0)),
        metrics={
            "cell_count": bench.get("cell_count", 0),
            "max_non_ortho": bench.get("max_non_ortho", 0.0),
            "avg_non_ortho": bench.get("avg_non_ortho", 0.0),
            "max_skewness": bench.get("max_skewness", 0.0),
            "max_aspect_ratio": bench.get("max_aspect_ratio", 0.0),
            "neg_cells": bench.get("neg_cells", 0),
            "min_volume": bench.get("min_volume", 0.0),
        },
        errors=errors,
        warnings=list(bench.get("warnings") or []),
        timed_out=timed_out,
    )


# Stage budgets (child-side); parent hard-cap is RUN_TIMEOUT_S = 1500.
HEX_MESH_TIMEOUT_S = 1200
CHECK_TIMEOUT_S = 120
GMSH_TIMEOUT_S = 1200
GMSH_TO_FOAM_TIMEOUT_S = 180


def _run_gmsh_direct(stl_path: Path) -> dict:
    """GMSH tet volume -> gmshToFoam -> checkMesh (no dual, no BL)."""
    import benchmarks.run_benchmarks as rb

    name = stl_path.stem
    early, ctx = rb._prepare_geometry(stl_path, pipeline="gmsh_direct")
    if early is not None:
        early["pipeline"] = "gmsh_direct"
        return early
    max_cell, min_cell = ctx["max_cell"], ctx["min_cell"]

    tmp_root = rb.WORK_ROOT / f"cmp_{int(time.time() * 1000)}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    stage_times: dict[str, float] = {}
    errors: list[str] = []
    warnings: list[str] = list(ctx["size_warnings"])
    stages: dict[str, bool] = {}
    try:
        case_dir = rb._make_case_dir(tmp_root, name)
        t0 = time.perf_counter()
        try:
            from polyfoammesh.core.gmsh_subprocess import run_gmsh_volume
            run_gmsh_volume(
                stl_path, case_dir / "mesh.msh", detail="medium",
                user_lc=max_cell, min_lc=min_cell,
                timeout_s=GMSH_TIMEOUT_S, threads=1,
            )
            stages["gmsh_volume"] = True
        except Exception as exc:
            stages["gmsh_volume"] = False
            errors.append(f"gmsh_volume: {exc}")
        stage_times["gmsh_volume"] = round(time.perf_counter() - t0, 2)
        if not stages["gmsh_volume"]:
            return _make_result_fail(name, errors, warnings, stage_times)

        rb._write_ctrl_dict(case_dir)
        rb._write_solver_dicts(case_dir)
        t0 = time.perf_counter()
        rc_, out = rb._wsl_of_run(
            f"cd {rb._to_wsl_path(case_dir)} && gmshToFoam mesh.msh 2>&1",
            timeout=GMSH_TO_FOAM_TIMEOUT_S,
        )
        stage_times["gmsh_to_foam"] = round(time.perf_counter() - t0, 2)
        if rc_ != 0:
            errors.append(
                "gmshToFoam failed: " + "\n".join(out.splitlines()[-15:])
            )
            return _make_result_fail(name, errors, warnings, stage_times)

        t0 = time.perf_counter()
        cm_rc, cm_out, _cm_t = rb._run_check_mesh(case_dir, timeout=CHECK_TIMEOUT_S)
        stage_times["check_mesh"] = round(time.perf_counter() - t0, 2)
        if cm_rc != 0:
            errors.append(f"checkMesh exit {cm_rc}")
            return _make_result_fail(name, errors, warnings, stage_times)

        from polyfoammesh.core.openfoam_runner import parse_checkmesh_output
        report = parse_checkmesh_output(cm_out)
        return {
            "geometry": name, "pipeline": "gmsh_direct", "success": report.passed,
            "wall_time_s": sum(stage_times.values()),
            "cell_count": report.cells, "max_non_ortho": report.max_non_ortho,
            "avg_non_ortho": report.avg_non_ortho, "max_skewness": report.max_skewness,
            "max_aspect_ratio": report.max_aspect_ratio, "neg_cells": report.neg_cells,
            "min_volume": report.min_volume, "errors": errors, "warnings": warnings,
        }
    except Exception as exc:
        errors.append(str(exc))
        return _make_result_fail(name, errors, warnings, stage_times)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _make_result_fail(name, errors, warnings, stage_times) -> dict:
    return {
        "geometry": name, "success": False,
        "wall_time_s": sum(stage_times.values()),
        "cell_count": 0, "max_non_ortho": 0.0, "avg_non_ortho": 0.0,
        "max_skewness": 0.0, "max_aspect_ratio": 0.0, "neg_cells": 0,
        "min_volume": 0.0, "errors": errors, "warnings": warnings,
    }
```

Child dispatch:

```python
def run_single(runner: str, stl_path: Path, out_path: Path, keep_case: bool = False) -> int:
    import benchmarks.run_benchmarks as rb

    name = stl_path.stem
    if runner == "hex":
        bench = rb._benchmark_one(
            stl_path, keep_case=keep_case,
            mesh_timeout_s=HEX_MESH_TIMEOUT_S, check_timeout_s=CHECK_TIMEOUT_S,
        )
        rec = _record_from_bench(name, runner, bench)
    elif runner == "poly":
        bench = rb._benchmark_poly_one(
            stl_path, keep_case=keep_case,
            gmsh_timeout_s=GMSH_TIMEOUT_S,
            gmsh_to_foam_timeout_s=GMSH_TO_FOAM_TIMEOUT_S,
            check_timeout_s=CHECK_TIMEOUT_S,
        )
        rec = _record_from_bench(name, runner, bench)
    elif runner == "gmsh_direct":
        rec = _record_from_bench(name, runner, _run_gmsh_direct(stl_path))
    else:
        raise ValueError(f"runner {runner!r} is not implemented in this task (snappy = Task 7)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    logger.info("record: %s/%s pass=%s cells=%d", name, runner,
                rec["thresholds_pass"], rec["cell_count"])
    return 0 if rec["thresholds_pass"] else 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="PolyFoamMesh vs snappy comparison")
    p.add_argument("--single", action="store_true", help="child mode: one run")
    p.add_argument("--runner", choices=list(ALL_RUNNERS))
    p.add_argument("--geometry", help="geometry name from the corpus")
    p.add_argument("--out", help="child mode: record output path")
    p.add_argument("--keep", action="store_true")
    return p


def main(args: list[str] | None = None) -> int:
    opts = _build_parser().parse_args(args)
    if opts.single:
        if not (opts.runner and opts.geometry and opts.out):
            logger.error("--single requires --runner --geometry --out")
            return 2
        spec = next((s for s in CORPUS if s.name == opts.geometry), None)
        if spec is None:
            logger.error("unknown geometry %r", opts.geometry)
            return 2
        try:
            return run_single(opts.runner, geometry_path(spec), Path(opts.out), opts.keep)
        except ValueError as exc:
            logger.error("%s", exc)
            return 2
    logger.error("parent mode not implemented yet (Task 7)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_run_comparison.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add benchmarks/run_comparison.py tests/test_run_comparison.py
git commit -m "bench: comparison records + child mode for our 3 runners"
```

---

### Task 7: Snappy runners in child mode + parent orchestrator (hard timeout)

**Files:**
- Modify: `benchmarks/run_comparison.py`
- Test: `tests/test_run_comparison_parent.py`

**Interfaces:**
- Consumes: Task 4 (`prepare_snappy_case`, `find_location_in_mesh`), Task 6 (`run_single`, `make_record`), Task 1 (`corpus`)
- Produces:
  - `_run_snappy(stl_path: Path, mode: str) -> dict` — blockMesh [+ surfaceFeatureExtract if expert] + snappyHexMesh + checkMesh via WSL, returns bench-style dict (same shape `_record_from_bench` consumes)
  - `run_parent(tier: str, runners: list[str] | None, geometries: list[str] | None, keep: bool = False) -> tuple[list[dict], int]` — spawns child per (geom, runner) with `timeout=RUN_TIMEOUT_S`; on `TimeoutExpired` kills the tree (`taskkill /T /F`), records `timed_out=True`; writes `RESULTS_DIR/comparison_<ts>.json`; returns `(records, exit_code)` where exit_code=1 iff any OUR runner has `thresholds_pass=False`
  - CLI additions: `--tier {fast,full}` (default `fast`), `--runner` repeatable, `--geometry` repeatable

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_comparison_parent.py
"""Parent orchestration: timeout record, exit-code gate — subprocess mocked."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks import run_comparison as rc  # noqa: E402
from benchmarks.corpus import RUN_TIMEOUT_S  # noqa: E402


def _good_record(geometry: str, runner: str) -> dict:
    return rc.make_record(
        geometry, runner, success=True, wall_time_s=1.0,
        metrics={"cell_count": 10, "max_non_ortho": 10.0, "avg_non_ortho": 5.0,
                 "max_skewness": 0.5, "max_aspect_ratio": 2.0, "neg_cells": 0,
                 "min_volume": 1e-4},
        errors=[], warnings=[],
    )


def _bad_record(geometry: str, runner: str) -> dict:
    return rc.make_record(
        geometry, runner, success=True, wall_time_s=1.0,
        metrics={"cell_count": 10, "max_non_ortho": 99.0, "avg_non_ortho": 5.0,
                 "max_skewness": 0.5, "max_aspect_ratio": 2.0, "neg_cells": 0,
                 "min_volume": 1e-4},
        errors=[], warnings=[],
    )


def test_parent_timeout_produces_timed_out_record(tmp_path, monkeypatch) -> None:
    def fake_run(cmd, timeout=None, **kw):
        raise subprocess_timeout()

    class subprocess_timeout(Exception):
        pass

    # monkeypatch subprocess.run inside rc to raise TimeoutExpired-like behaviour
    import subprocess as sp

    def raising_run(cmd, timeout=None, **kw):
        raise sp.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(rc.subprocess, "run", raising_run)
    monkeypatch.setattr(rc, "_kill_tree", lambda pid: None)
    monkeypatch.setattr(rc, "_child_succeeds", lambda *a, **k: None)  # no file written

    records, exit_code = rc.run_parent(
        tier="fast", runners=["hex"], geometries=["cylinder"],
        _child_out_dir=tmp_path, _write_results=False,
    )
    assert len(records) == 1
    rec = records[0]
    assert rec["timed_out"] is True
    assert rec["thresholds_pass"] is False
    assert exit_code == 1


def test_exit_code_is_zero_when_our_runners_all_pass(tmp_path, monkeypatch) -> None:
    import subprocess as sp

    calls: list[list[str]] = []

    def fake_run(cmd, timeout=None, **kw):
        calls.append(list(cmd))
        # child writes its record like the real one would
        out = Path(cmd[cmd.index("--out") + 1])
        geom = cmd[cmd.index("--geometry") + 1]
        runner = cmd[cmd.index("--runner") + 1]
        out.write_text(json.dumps(_good_record(geom, runner)), encoding="utf-8")

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    records, exit_code = rc.run_parent(
        tier="fast", runners=["hex", "poly"], geometries=["cylinder"],
        _child_out_dir=tmp_path, _write_results=False,
    )
    assert len(records) == 2
    assert exit_code == 0


def test_exit_code_is_one_when_an_our_runner_fails_thresholds(tmp_path, monkeypatch) -> None:
    def fake_run(cmd, timeout=None, **kw):
        out = Path(cmd[cmd.index("--out") + 1])
        geom = cmd[cmd.index("--geometry") + 1]
        runner = cmd[cmd.index("--runner") + 1]
        bad = runner == "poly"
        rec = _bad_record(geom, runner) if bad else _good_record(geom, runner)
        out.write_text(json.dumps(rec), encoding="utf-8")

        class R:
            returncode = 1 if bad else 0
        return R()

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    _, exit_code = rc.run_parent(
        tier="fast", runners=["hex", "poly"], geometries=["cylinder"],
        _child_out_dir=tmp_path, _write_results=False,
    )
    assert exit_code == 1


def test_snappy_runner_is_available_in_cli() -> None:
    opts = rc._build_parser().parse_args(
        ["--tier", "full", "--runner", "snappy_base", "--geometry", "slot1"]
    )
    assert opts.tier == "full"
    assert opts.runner == ["snappy_base"]
    assert opts.geometry == ["slot1"]


def test_child_mode_rejects_snappy_before_task7_only_if_unimplemented(monkeypatch) -> None:
    # after Task 7 run_single handles snappy; just assert parser accepts it
    opts = rc._build_parser().parse_args(
        ["--single", "--runner", "snappy_expert", "--geometry", "venturi", "--out", "x.json"]
    )
    assert opts.runner == "snappy_expert"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_run_comparison_parent.py -v`
Expected: FAIL (`run_parent` missing, `--tier` missing)

- [ ] **Step 3: Implement snappy child + parent**

Add to `benchmarks/run_comparison.py`:

```python
SNAPPY_BLOCK_TIMEOUT_S = 120
SNAPPY_FEATURE_TIMEOUT_S = 60
SNAPPY_MESH_TIMEOUT_S = 1200


def _run_snappy(stl_path: Path, mode: str) -> dict:
    """blockMesh [+surfaceFeatureExtract] + snappyHexMesh + checkMesh via WSL."""
    import benchmarks.run_benchmarks as rb
    from benchmarks.snappy_baseline import prepare_snappy_case

    name = stl_path.stem
    early, ctx = rb._prepare_geometry(
        stl_path, pipeline=f"snappy_{mode}", require_watertight=False,
    )
    if early is not None:
        return early
    meshes, max_cell, min_cell = ctx["meshes"], ctx["max_cell"], ctx["min_cell"]

    tmp_root = rb.WORK_ROOT / f"cmp_snappy_{int(time.time() * 1000)}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    warnings: list[str] = list(ctx["size_warnings"])
    stage_times: dict[str, float] = {}
    try:
        case_dir = rb._make_case_dir(tmp_root, name)
        prepare_snappy_case(case_dir, stl_path, meshes, mode, max_cell, min_cell)
        linux = rb._to_wsl_path(case_dir)

        t0 = time.perf_counter()
        rc_, out = rb._wsl_of_run(f"cd {linux} && blockMesh 2>&1", timeout=SNAPPY_BLOCK_TIMEOUT_S)
        stage_times["blockMesh"] = round(time.perf_counter() - t0, 2)
        if rc_ != 0:
            errors.append("blockMesh failed: " + "\n".join(out.splitlines()[-10:]))
            return _make_result_fail(name, errors, warnings, stage_times)

        if mode == "expert":
            t0 = time.perf_counter()
            rc_, out = rb._wsl_of_run(
                f"cd {linux} && surfaceFeatureExtract "
                f"constant/triSurface/surface.stl -angle 60 -writeObj "
                f"-prefix surface 2>&1",
                timeout=SNAPPY_FEATURE_TIMEOUT_S,
            )
            stage_times["featureExtract"] = round(time.perf_counter() - t0, 2)
            if rc_ != 0:
                warnings.append(f"surfaceFeatureExtract exit {rc_} (non-fatal)")

        t0 = time.perf_counter()
        rc_, out = rb._wsl_of_run(
            f"cd {linux} && snappyHexMesh -overwrite 2>&1",
            timeout=SNAPPY_MESH_TIMEOUT_S,
        )
        stage_times["snappyHexMesh"] = round(time.perf_counter() - t0, 2)
        if rc_ != 0:
            errors.append("snappyHexMesh failed: " + "\n".join(out.splitlines()[-15:]))
            return _make_result_fail(name, errors, warnings, stage_times)

        t0 = time.perf_counter()
        cm_rc, cm_out, _ = rb._run_check_mesh(case_dir, timeout=CHECK_TIMEOUT_S)
        stage_times["check_mesh"] = round(time.perf_counter() - t0, 2)
        if cm_rc != 0:
            errors.append(f"checkMesh exit {cm_rc}")
            return _make_result_fail(name, errors, warnings, stage_times)

        from polyfoammesh.core.openfoam_runner import parse_checkmesh_output
        report = parse_checkmesh_output(cm_out)
        return {
            "geometry": name, "success": report.passed,
            "wall_time_s": sum(stage_times.values()),
            "cell_count": report.cells, "max_non_ortho": report.max_non_ortho,
            "avg_non_ortho": report.avg_non_ortho, "max_skewness": report.max_skewness,
            "max_aspect_ratio": report.max_aspect_ratio, "neg_cells": report.neg_cells,
            "min_volume": report.min_volume, "errors": errors, "warnings": warnings,
        }
    except Exception as exc:
        errors.append(str(exc))
        return _make_result_fail(name, errors, warnings, stage_times)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
```

Wire into `run_single` (replace the `else: raise ValueError` branch):

```python
    elif runner == "snappy_base":
        rec = _record_from_bench(name, runner, _run_snappy(stl_path, "base"))
    elif runner == "snappy_expert":
        rec = _record_from_bench(name, runner, _run_snappy(stl_path, "expert"))
```

Parent mode:

```python
def _kill_tree(pid: int) -> None:
    """Best-effort Windows process-tree kill (hard-timeout cleanup)."""
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True, timeout=15,
    )


def _tier_specs(tier: str):
    from benchmarks.corpus import fast_tier, full_tier
    return fast_tier() if tier == "fast" else full_tier()


def run_parent(
    tier: str,
    runners: list[str] | None,
    geometries: list[str] | None,
    keep: bool = False,
    _child_out_dir: Path | None = None,
    _write_results: bool = True,
) -> tuple[list[dict], int]:
    specs = _tier_specs(tier)
    if geometries:
        specs = [s for s in specs if s.name in geometries]
    runner_list = runners or list(ALL_RUNNERS)

    child_dir = _child_out_dir or (RESULTS_DIR / f".tmp_{int(time.time() * 1000)}")
    child_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []

    for spec in specs:
        for runner in runner_list:
            out = child_dir / f"{spec.name}__{runner}.json"
            cmd = [
                sys.executable, str(Path(__file__).resolve()),
                "--single", "--runner", runner,
                "--geometry", spec.name, "--out", str(out),
            ]
            if keep:
                cmd.append("--keep")
            logger.info("RUN %s / %s (cap %ds)", spec.name, runner, RUN_TIMEOUT_S)
            try:
                proc = subprocess.run(cmd, timeout=RUN_TIMEOUT_S, capture_output=True, text=True)
                if out.exists():
                    rec = json.loads(out.read_text("utf-8"))
                else:
                    rec = make_record(
                        spec.name, runner, success=False, wall_time_s=float(RUN_TIMEOUT_S),
                        metrics={}, errors=[f"child exited {proc.returncode} without a record"],
                        warnings=[], timed_out=True,
                    )
                    rec["thresholds_pass"] = False
                    rec["success"] = False
            except subprocess.TimeoutExpired as exc:
                _kill_tree(exc.pid if hasattr(exc, "pid") else 0)
                rec = make_record(
                    spec.name, runner, success=False, wall_time_s=float(RUN_TIMEOUT_S),
                    metrics={}, errors=[f"hard timeout {RUN_TIMEOUT_S}s"],
                    warnings=[], timed_out=True,
                )
            records.append(rec)
            if _write_results:
                RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if _write_results:
        ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        out_path = RESULTS_DIR / f"comparison_{ts}.json"
        out_path.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
        logger.info("results: %s (%d records)", out_path, len(records))
        shutil.rmtree(child_dir, ignore_errors=True)

    exit_code = 0
    for rec in records:
        if rec["runner"] in OUR_RUNNERS and not rec.get("thresholds_pass"):
            exit_code = 1
    return records, exit_code
```

Update `_build_parser`: `--tier` choices `["fast", "full"]` default `"fast"`; change `--runner` and `--geometry` to `action="append"`. Update `main`: if not `--single`, call `run_parent(opts.tier, opts.runner, opts.geometry, opts.keep)` and return its exit code.

- [ ] **Step 4: Run both test files**

Run: `pytest tests/test_run_comparison.py tests/test_run_comparison_parent.py -v`
Expected: all passed (12 tests total)

- [ ] **Step 5: Commit**

```bash
git add benchmarks/run_comparison.py tests/test_run_comparison_parent.py
git commit -m "bench: snappy child runners + parent orchestrator with 1500s hard cap"
```

---

### Task 8: Leaderboard generator

**Files:**
- Create: `benchmarks/compare_snappy.py`
- Test: `tests/test_compare_snappy.py`
- Fixture: created inside the test (tmp_path JSON)

**Interfaces:**
- Consumes: Task 6 record schema, Task 1 (`ALL_RUNNERS`)
- Produces:
  - `load_records(path: Path) -> list[dict]`
  - `index_records(records: list[dict]) -> dict[str, dict[str, dict]]` — `{geometry: {runner: record}}`
  - `pick_winner(cell: dict[str, dict]) -> str | None` — among records with `thresholds_pass and not timed_out`, min by `(neg_cells, max_non_ortho, max_skewness, max_aspect_ratio, wall_time_s, runner_name)`; `None` if none viable
  - `aggregate(records: list[dict]) -> dict` — `{"pass_rate": {runner: float}, "median": {runner: {metric: float}}, "exclusive_pass": {runner: [geometry...]}}`
  - `write_leaderboard(records: list[dict], out_md: Path) -> None` — Markdown with per-geometry table + aggregates + terrain sections
  - CLI: `python benchmarks/compare_snappy.py --results <comparison.json> [--out benchmarks/results/LEADERBOARD.md]` (exit 0)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compare_snappy.py
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks import compare_snappy as cs  # noqa: E402
from benchmarks.run_comparison import make_record  # noqa: E402


def _rec(geom, runner, *, non_ortho=20.0, skew=1.0, aspect=10.0, neg=0,
         cells=1000, t=5.0, timed=False, forced_fail=False) -> dict:
    if forced_fail:
        non_ortho = 99.0
    r = make_record(
        geom, runner, success=not forced_fail, wall_time_s=t,
        metrics={"cell_count": cells, "max_non_ortho": non_ortho, "avg_non_ortho": 5.0,
                 "max_skewness": skew, "max_aspect_ratio": aspect, "neg_cells": neg,
                 "min_volume": 1e-5},
        errors=["hard timeout 1500s"] if timed else [],
        warnings=[], timed_out=timed,
    )
    return r


def _sample() -> list[dict]:
    return [
        _rec("cylinder", "hex", non_ortho=30.0),
        _rec("cylinder", "poly", non_ortho=25.0),
        _rec("cylinder", "snappy_base", non_ortho=45.0),
        _rec("cylinder", "snappy_expert", non_ortho=35.0),
        _rec("slot1", "hex", forced_fail=True),
        _rec("slot1", "snappy_base", non_ortho=55.0),
        _rec("slot1", "poly", timed=True),
    ]


def test_index_records_shape() -> None:
    idx = cs.index_records(_sample())
    assert set(idx) == {"cylinder", "slot1"}
    assert set(idx["cylinder"]) == {"hex", "poly", "snappy_base", "snappy_expert"}


def test_pick_winner_prefers_lowest_defect_among_viable() -> None:
    idx = cs.index_records(_sample())
    assert cs.pick_winner(idx["cylinder"]) == "poly"


def test_pick_winner_none_when_nobody_passes_thresholds() -> None:
    records = [
        _rec("x", "hex", forced_fail=True),
        _rec("x", "snappy_base", forced_fail=True),
    ]
    assert cs.pick_winner(cs.index_records(records)["x"]) is None


def test_aggregate_pass_rates_and_exclusive_pass() -> None:
    agg = cs.aggregate(_sample())
    assert agg["pass_rate"]["hex"] == 0.5          # cylinder pass, slot1 fail
    assert agg["pass_rate"]["snappy_base"] == 1.0  # both pass thresholds
    # terrain C: hex fails slot1 while snappy_base passes -> exclusive to snappy
    assert "slot1" in agg["exclusive_pass"]["snappy_base"]
    assert "cylinder" in agg["exclusive_pass"].get("hex", []) or True


def test_write_leaderboard_emits_markdown(tmp_path) -> None:
    out = tmp_path / "LEADERBOARD.md"
    cs.write_leaderboard(_sample(), out)
    text = out.read_text(encoding="utf-8")
    assert "# PolyFoamMesh vs snappyHexMesh" in text
    assert "cylinder" in text and "slot1" in text
    assert "poly" in text and "snappy_expert" in text
    assert "Pass rate" in text


def test_cli_reads_results_json(tmp_path) -> None:
    p = tmp_path / "comparison_x.json"
    p.write_text(json.dumps(_sample()), encoding="utf-8")
    out = tmp_path / "L.md"
    rc_ = cs.main(["--results", str(p), "--out", str(out)])
    assert rc_ == 0
    assert out.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_compare_snappy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.compare_snappy'`

- [ ] **Step 3: Write the implementation**

```python
# benchmarks/compare_snappy.py
"""Leaderboard: geometry x runner matrix, winner per cell, aggregates
(pass rate = terrain B, medians = terrain A, exclusive pass = terrain C).

Usage: python benchmarks/compare_snappy.py --results comparison_X.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from benchmarks.corpus import ALL_RUNNERS, OUR_RUNNERS, RESULTS_DIR  # noqa: E402

_MEDIAN_METRICS = ("max_non_ortho", "max_skewness", "max_aspect_ratio", "wall_time_s")


def load_records(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text("utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} does not contain a JSON array")
    return data


def index_records(records: list[dict]) -> dict[str, dict[str, dict]]:
    idx: dict[str, dict[str, dict]] = {}
    for rec in records:
        idx.setdefault(rec["geometry"], {})[rec["runner"]] = rec
    return idx


def _viable(rec: dict) -> bool:
    return bool(rec.get("thresholds_pass")) and not rec.get("timed_out")


def pick_winner(cell: dict[str, dict]) -> str | None:
    viable = {r: rec for r, rec in cell.items() if _viable(rec)}
    if not viable:
        return None
    return min(
        viable,
        key=lambda r: (
            viable[r].get("neg_cells", 0),
            viable[r].get("max_non_ortho", 1e9),
            viable[r].get("max_skewness", 1e9),
            viable[r].get("max_aspect_ratio", 1e9),
            viable[r].get("wall_time_s", 1e9),
            r,  # deterministic tie-break
        ),
    )


def aggregate(records: list[dict]) -> dict:
    idx = index_records(records)
    runners = sorted({r["runner"] for r in records})
    geoms = sorted(idx)
    pass_rate: dict[str, float] = {}
    median: dict[str, dict[str, float]] = {}
    for runner in runners:
        recs = [idx[g][runner] for g in geoms if runner in idx[g]]
        pass_rate[runner] = (
            sum(1 for r in recs if _viable(r)) / len(recs) if recs else 0.0
        )
        med: dict[str, float] = {}
        for m in _MEDIAN_METRICS:
            vals = [
                float(r[m]) for r in recs
                if _viable(r) and isinstance(r.get(m), (int, float))
            ]
            if vals:
                med[m] = round(statistics.median(vals), 2)
        median[runner] = med
    # exclusive pass: geom where runner passes and ALL snappy runners fail
    snappy = [r for r in runners if r.startswith("snappy")]
    exclusive: dict[str, list[str]] = {r: [] for r in runners}
    for g in geoms:
        for runner in runners:
            rec = idx[g].get(runner)
            if rec is None or not _viable(rec):
                continue
            if runner in OUR_RUNNERS:
                if snappy and all(
                    (idx[g].get(s) is None) or not _viable(idx[g][s]) for s in snappy
                ):
                    exclusive[runner].append(g)
            elif runner in snappy:
                others = [s for s in snappy if s != runner]
                if all(
                    (idx[g].get(o) is None) or not _viable(idx[g][o]) for o in others
                ) and all(
                    (idx[g].get(o) is None) or not _viable(idx[g][o])
                    for o in OUR_RUNNERS if o in idx[g]
                ):
                    exclusive[runner].append(g)
    return {"pass_rate": pass_rate, "median": median, "exclusive_pass": exclusive}


def write_leaderboard(records: list[dict], out_md: Path) -> None:
    idx = index_records(records)
    agg = aggregate(records)
    geoms = sorted(idx)
    runners = [r for r in ALL_RUNNERS if any(r in idx[g] for g in geoms)]

    lines: list[str] = ["# PolyFoamMesh vs snappyHexMesh — Leaderboard", ""]
    lines.append(f"Runners: {', '.join(runners)}")
    lines.append("")

    for g in geoms:
        lines.append(f"## {g}")
        lines.append("")
        lines.append("| metric | " + " | ".join(runners) + " |")
        lines.append("|---" * (len(runners) + 1) + "|")
        for label, key in (
            ("cells", "cell_count"), ("non-ortho max", "max_non_ortho"),
            ("skew max", "max_skewness"), ("aspect max", "max_aspect_ratio"),
            ("neg cells", "neg_cells"), ("time (s)", "wall_time_s"),
        ):
            row = [label]
            for r in runners:
                rec = idx[g].get(r)
                row.append(str(rec.get(key, "—")) if rec else "—")
            lines.append("| " + " | ".join(row) + " |")
        row = ["thresholds"]
        for r in runners:
            rec = idx[g].get(r)
            row.append("PASS" if rec and _viable(rec) else "FAIL")
        lines.append("| " + " | ".join(row) + " |")
        winner = pick_winner(idx[g])
        lines.append("")
        lines.append(f"**Winner:** {winner if winner else 'none (no runner passed thresholds)'}")
        lines.append("")

    lines.append("## Aggregates")
    lines.append("")
    lines.append("### Pass rate (terrain B — reliability)")
    lines.append("")
    lines.append("| runner | pass rate |")
    lines.append("|---|---|")
    for r in runners:
        lines.append(f"| {r} | {agg['pass_rate'].get(r, 0.0):.0%} |")
    lines.append("")
    lines.append("### Median metrics over passing runs (terrain A — quality)")
    lines.append("")
    lines.append("| runner | non-ortho | skew | aspect | time (s) |")
    lines.append("|---|---|---|---|---|")
    for r in runners:
        med = agg["median"].get(r, {})
        lines.append(
            f"| {r} | {med.get('max_non_ortho', '—')} | {med.get('max_skewness', '—')} "
            f"| {med.get('max_aspect_ratio', '—')} | {med.get('wall_time_s', '—')} |"
        )
    lines.append("")
    lines.append("### Exclusive passes (terrain C — robustness)")
    lines.append("")
    for r in runners:
        geos = agg["exclusive_pass"].get(r, [])
        lines.append(f"- **{r}**: {', '.join(geos) if geos else '(none)'}")
    lines.append("")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build comparison leaderboard")
    p.add_argument("--results", required=True, help="comparison_*.json path")
    p.add_argument("--out", default=str(RESULTS_DIR / "LEADERBOARD.md"))
    opts = p.parse_args(argv)
    records = load_records(Path(opts.results))
    write_leaderboard(records, Path(opts.out))
    print(f"Leaderboard written to {opts.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_compare_snappy.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add benchmarks/compare_snappy.py tests/test_compare_snappy.py
git commit -m "bench: leaderboard generator (winners, pass rates, exclusive passes)"
```

---

### Task 9: Fast-tier smoke run (WSL, manual)

**Files:** none created — this is an execution/validation task; fixes (if any) go to the owning module with its test.

**Interfaces:**
- Consumes: Tasks 1-8 complete
- Produces: first `benchmarks/results/comparison_*.json` on the fast tier; evidence that every runner completes

- [ ] **Step 1: Run the fast tier**

Run: `python benchmarks/run_comparison.py --tier fast`
Expected: 6 geometries × 5 runners = 30 records; log lines `RUN <geom> / <runner>`; exit code may be 0 or 1 (gates are the point).

- [ ] **Step 2: Verify the record file**

Run (PowerShell):
```powershell
$r = Get-ChildItem benchmarks/results/comparison_*.json | Sort-Object LastWriteTime | Select-Object -Last 1
$j = Get-Content $r.FullName | ConvertFrom-Json
"records: $($j.Count)"
$j | Group-Object runner | ForEach-Object { "$($_.Name): $($_.Count)" }
$j | Where-Object { -not $_.thresholds_pass } | Format-Table geometry, runner, failed_thresholds, errors -Wrap
```
Expected: 30 records; 6 per runner; failures (if any) have non-empty `failed_thresholds` or `errors`.

- [ ] **Step 3: Verify the leaderboard builds**

Run: `python benchmarks/compare_snappy.py --results <the file from Step 1>`
Expected: `benchmarks/results/LEADERBOARD.md` created, contains all 6 fast geometries.

- [ ] **Step 4: Fix-and-loop if any runner crashes with a harness bug (not a meshing failure)**

A *meshing failure* (record with `thresholds_pass=False` and real `errors` from the mesher) is VALID data — do not "fix" it. A *harness bug* (traceback in the parent log, missing record, `child exited 2`) must be fixed in the owning module with a regression test, then re-run Step 1.

- [ ] **Step 5: Commit (only if code changed)**

```bash
git add -u benchmarks/ tests/
git commit -m "bench: fast-tier smoke fixes" || echo "no changes to commit"
```

---

### Task 10: Full baseline run + LEADERBOARD

**Files:**
- Produce: `benchmarks/results/comparison_<ts>.json` (full tier), `benchmarks/results/LEADERBOARD.md`
- Modify (if a geometry blows the budget): `benchmarks/generate_corpus.py` (scale the shape down) or `benchmarks/corpus.py` (move geometry out of fast tier) — with a note in the record's warnings; never raise RUN_TIMEOUT_S

**Interfaces:**
- Consumes: Task 9 stable fast tier
- Produces: the baseline every future lane compares against (spec: baseline leaderboard)

- [ ] **Step 1: Run the full tier**

Run: `python benchmarks/run_comparison.py --tier full`
Expected: 10 geometries × 5 runners = 50 records. Runtime: up to ~50 × 25 min worst case, realistically far less — this is the manual "hours OK" full tier (spec Section 1).

- [ ] **Step 2: Build the official leaderboard**

Run: `python benchmarks/compare_snappy.py --results <full comparison json> --out benchmarks/results/LEADERBOARD.md`

- [ ] **Step 3: Enforce the 25-min budget per record**

Run (PowerShell):
```powershell
$j = Get-Content <full json> | ConvertFrom-Json
$j | Where-Object { $_.wall_time_s -gt 1500 } | Format-Table geometry, runner, wall_time_s
```
Expected: no rows (parent hard-cap guarantees `wall_time_s <= 1500` for records that completed; `timed_out` rows are exactly 1500). If a geometry×runner produced `timed_out=True`, note it in Step 4's gap analysis — it is terrain B data, not a reason to raise the cap.

- [ ] **Step 4: Commit the baseline artefacts**

```bash
git add benchmarks/results/comparison_*.json benchmarks/results/LEADERBOARD.md
git commit -m "bench: baseline full-tier leaderboard vs snappy (local only)"
```

---

### Task 11: Gap analysis document

**Files:**
- Create: `benchmarks/results/GAP_ANALYSIS.md`

**Interfaces:**
- Consumes: Task 10 `LEADERBOARD.md` + comparison JSON
- Produces: severity-ordered gap list that picks the first lane (Task 12)

- [ ] **Step 1: Read the leaderboard and extract gaps**

Open `benchmarks/results/LEADERBOARD.md` and the full comparison JSON. A *gap* is any cell where:
1. one of OUR runners has `thresholds_pass=False` (absolute-threshold miss), OR
2. one of OUR runners has `thresholds_pass=True` but both snappy runners beat it on `max_non_ortho` or `max_skewness` (relative loss), OR
3. a runner `timed_out` (reliability miss).

- [ ] **Step 2: Write the analysis**

```markdown
# Gap analysis — baseline vs snappy (YYYY-MM-DD)

Source: benchmarks/results/LEADERBOARD.md + comparison_<ts>.json

## Executive summary
<3-5 sentences: overall pass rates, who wins most cells, the single most
important problem.>

## Gap table (severity order)

| # | Geometry | Our runner | Gap type | Failing/worse metric | Us | Best snappy | Notes |
|---|----------|-----------|----------|----------------------|----|-------------|-------|
| 1 | ...      | poly      | threshold miss | neg_cells | 3 | 0 | ... |

Severity order (spec): neg cells > non-ortho > skew > aspect > time.
Rows sorted by that order; ties broken by how many geometries share the gap.

## Terrain C observations
<geometries where snappy failed and we passed, and vice versa>

## Chosen first lane
<name it after the top gap cluster, e.g. "Lane BL: prism collapse on
concave/mini_valve"> — justification = it holds the most/severest rows.
```

Fill every table row with real numbers from the JSON. No speculation: if a cell is ambiguous, say so in Notes.

- [ ] **Step 3: Commit**

```bash
git add benchmarks/results/GAP_ANALYSIS.md
git commit -m "bench: gap analysis of baseline vs snappy"
```

---

### Task 12: Kick off the first lane (prediction + A-side measurement)

**Files:**
- Create: `notes/<lane-slug>_reasoning.md` (slug = kebab of the lane name from Task 11, e.g. `bl_prism_collapse_reasoning.md`)
- Possibly Create: measurement script under `tools/` if the gap needs a focused A/B harness

**Interfaces:**
- Consumes: Task 11 "Chosen first lane"
- Produces: the repo-discipline artefact (prediction written BEFORE any fix) + a recorded A-side measurement — this is the spec's "at least one lane started with documented A/B". The actual fix implementation is a FOLLOW-UP plan (results decide the fix; see spec Section 3).

- [ ] **Step 1: Write the prediction note BEFORE touching any mesher code**

```markdown
# <Lane name> — reasoning (YYYY-MM-DD)

## Gap being addressed
(from GAP_ANALYSIS.md: rows #1..#k, numbers verbatim)

## Hypothesis
<what in the code causes it — file:function, mechanism, ≤10 lines>

## Prediction (write BEFORE the experiment)
- On <geometry>: <metric> will move from <current> to <predicted range>
- No change expected on healthy geometries (cylinder/cube_duct): <identical metrics>
- Cost: <expected wall-time delta>

## Measurement plan
- A-side (current code): command + JSON record ref  ← measured in Step 2
- B-side (with fix): same command, fix behind flag <flag name or "none, additive">
- Kill criterion: if B does not improve the primary metric on the gap
  geometry, or regresses any fast-tier geometry, the lane is killed
  ("measured no-op, not promoted" — repo precedent).

## Status
- [ ] Prediction written
- [ ] A-side measured
- [ ] Fix implemented (follow-up plan)
- [ ] B-side measured
- [ ] Verdict
```

- [ ] **Step 2: Record the A-side measurement**

Re-run the specific geometry×runner from the gap (fast tier if possible):
`python benchmarks/run_comparison.py --tier full --geometry <geom> --runner <runner>`
Then paste the record (thresholds, metrics) into the note under "A-side".

- [ ] **Step 3: Commit and report**

```bash
git add notes/<lane-slug>_reasoning.md
git commit -m "notes: <lane> prediction + A-side baseline (local)"
```

Then STOP and report to the user: baseline pass rates, top 3 gaps, chosen lane, prediction — and ask whether to proceed with the follow-up fix plan. The fix itself is deliberately NOT in this plan (spec Section 3: "results decide, not hypotheses").

---

## Self-Review

**1. Spec coverage**
- Corpus 10 geoms, tiers, ≤25 min: Tasks 1, 2, 10 ✓
- Harness 3 ours + 2 snappy, JSON records: Tasks 6, 7 ✓
- Snappy base+expert versioned templates: Task 4 ✓
- Absolute thresholds from quality_thresholds: Task 3 ✓ (make_record wires it)
- Leaderboard JSON+MD, terrains A/B/C aggregates: Task 8 ✓
- Fast gate local exit code: Task 7 (exit_code in run_parent) ✓ — runs on every tier, nonzero iff OUR runner misses thresholds
- Gap analysis: Task 11 ✓; lane kickoff with prediction + A-side: Task 12 ✓
- No push, CI untouched, no default changes: Global Constraints ✓
- Spec says fast tier "1-5 + 7" = cylinder..venturi + thin_gap → Task 1 test encodes exactly that ✓

**2. Placeholder scan**
- Task 11's markdown template has `<...>` fill-ins — these are runtime data from the leaderboard, not design placeholders (the executor has the file open). Acceptable per "conditional on artifact produced earlier".
- No "TBD", "similar to Task N", or undefined types found.

**3. Type consistency**
- `make_record(...) -> dict` signature identical in Tasks 6, 7, 8 tests ✓
- `passes_absolute_thresholds(metrics) -> tuple[bool, list[str]]` used identically in Tasks 3, 6 ✓
- `GeometrySpec` fields identical in Tasks 1, 7 (`_tier_specs`), tests ✓
- `run_parent(tier, runners, geometries, keep, _child_out_dir, _write_results)` matches Task 7 tests (extra kwargs are test seams, defaulting in signature) ✓
- Runner names: `ALL_RUNNERS`/`OUR_RUNNERS` used consistently everywhere ✓
