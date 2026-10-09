# Hex Thin-Feature Refinement (Lane NON-ORTHO) Implementation Plan

> **SUPERSEDED (2026-10-05, human decision):** the lane target moved from the
> cfMesh hex path (external library) to OUR tet→poly engine. Do not execute
> this plan; see `docs/superpowers/plans/2026-10-05-poly-dual-boundary-star-dissolution.md`
> and `notes/poly_nonortho_reasoning.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the opt-in `thin_feature_refinement` fix predicted in `notes/non-ortho_reasoning.md` — feature-keyed `objectRefinements` boxes that resolve sub-cell thin features in the hex/cfMesh path — and measure it A/B against the recorded baseline. No default changes.

**Architecture:** A robust thin-feature detector (opposing-wall nearest-point query, NOT ray casting — the existing sampler is blind to open slots) lives in `core/geometry.py`; `write_meshdict(..., thin_feature_refinement=False)` turns detections into `objectRefinements` boxes with `cellSize ≈ feature_width / 2`; the benchmark harness surfaces the flag as `--hex-thin-refine` (parent + child). Detection is opt-in and additive: flag off → byte-identical meshDict; no thin features → no boxes.

**Tech Stack:** Python 3.11, numpy, trimesh (`nearest.on_surface` via rtree), pytest, WSL2 + OpenFOAM v2512, `benchmarks/` harness.

**Spec (binding authority):**
- `notes/non-ortho_reasoning.md` — prediction, A-side table, kill criterion (written before this plan)
- `benchmarks/results/GAP_ANALYSIS.md` — lane rows 11-14 (hex `non_ortho_max` misses)
- `benchmarks/results/comparison_20261005T113621.json` — A-side full baseline

## Global Constraints

- Interpreter: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"` (bare `python` is an unrelated venv).
- Repo root for all commands. **Local only — never push** (spec D8; the human decides integration).
- **No default changes.** `thin_feature_refinement=False` must leave `meshDict` byte-identical to today (assert in tests).
- `ruff check src/ tests/ benchmarks/` green; `pytest -m "not slow and not wsl"` green (baseline: 1234 passed / 2 skipped / 1 xfailed).
- Benchmark hard cap `RUN_TIMEOUT_S = 1500`; never raise it. Stage budgets unchanged.
- Kill criterion (from the note, verbatim): if the B-side does not bring groove1/hex `max_non_ortho` below 70 — or improves it by <5° from 75.98 — or any fast-tier geometry regresses (cube_duct 64.86 → >70, any healthy primary metric worse by >2°, or a currently-passing row starts failing), the lane is killed and documented as a negative result. Do not promote a dead lane.
- A meshing failure or timeout is terrain data, not a harness bug. Harness bugs get a fix + regression test.
- Commit hook: prepend `$env:LOCALAPPDATA\Programs\Python\Python311` to PATH before `git commit`; never `--no-verify`. `benchmarks/results/` is git-ignored → `git add -f` for artefacts.
- Detection must never abort a meshing run: any exception → log + continue without the refinement block (repo pattern).

---

### Task 1: Thin-feature detector and box builder

**Files:**
- Modify: `src/polyfoammesh/core/geometry.py` (append after `build_graded_refinement_boxes`, ~line 830)
- Test: `tests/test_thin_feature_refinement.py` (create)

**Interfaces:**
- Consumes: nothing (pure geometry helpers)
- Produces:
  - `detect_thin_features(meshes: list[trimesh.Trimesh], max_width: float, samples: int = 2048, seed: int = 0xC0FFEE, max_points: int = 200) -> tuple[np.ndarray, np.ndarray]` — returns `(points (N,3), widths (N,))` of midpoints between opposing, non-adjacent surfaces closer than `max_width`; empty arrays when none or on failure.
  - `build_thin_feature_boxes(meshes: list[trimesh.Trimesh], global_max_cell: float, max_boxes: int = 64, seed: int = 0xC0FFEE) -> list[dict]` — box dicts in `meshdict_gen.build_object_refinements`' box schema (`{type:"box", xmin..zmax, cell_size}`), `cell_size = clip(width/2, global_max_cell/32, global_max_cell*0.9)`, one box per `global_max_cell`-sized voxel, capped at `max_boxes`; `[]` when nothing detected or on failure.

**Why nearest-point, not ray casting:** `_sample_thickness_one_mesh` casts rays along the wall normal; in an open slot the ray exits the open end, so the sampler reports the slot's length (or nothing), not its width — measured: p10 = 0.020 m on slot1 against a true 0.0015 m. `mesh.nearest.on_surface` finds the opposing wall regardless of openness; opposing normals (`dot < -0.5`) reject same-wall nearest points.

- [ ] **Step 1: Write the failing tests**

```python
"""Thin-feature detection for the hex NON-ORTHO lane (opt-in refinement)."""
from __future__ import annotations

import numpy as np
import trimesh

from polyfoammesh.core.geometry import build_thin_feature_boxes, detect_thin_features


def _two_plates(gap: float = 0.004, thickness: float = 0.001) -> list[trimesh.Trimesh]:
    a = trimesh.creation.box(extents=[0.02, 0.02, thickness])
    b = trimesh.creation.box(extents=[0.02, 0.02, thickness])
    b.apply_translation([0.0, 0.0, thickness + gap])
    return [a, b]


def test_detects_thin_slot_width():
    pts, widths = detect_thin_features(_two_plates(gap=0.004), max_width=0.01)
    assert len(pts) > 0
    assert 0.003 <= float(np.min(widths)) <= 0.005


def test_ignores_slab_thicker_than_max_width():
    slab = [trimesh.creation.box(extents=[0.05, 0.05, 0.05])]
    pts, widths = detect_thin_features(slab, max_width=0.01)
    assert len(pts) == 0


def test_build_boxes_targets_the_gap():
    boxes = build_thin_feature_boxes(_two_plates(gap=0.004), global_max_cell=0.01)
    assert boxes
    assert all(b["type"] == "box" for b in boxes)
    assert min(b["cell_size"] for b in boxes) <= 0.0025
    # at least one box covers the gap region (z between the plates)
    assert any(b["zmin"] <= 0.0 and b["zmax"] >= 0.004 for b in boxes)


def test_build_boxes_empty_on_healthy_geometry():
    slab = [trimesh.creation.box(extents=[0.05, 0.05, 0.05])]
    assert build_thin_feature_boxes(slab, global_max_cell=0.01) == []


def test_build_boxes_never_raises_on_empty_input():
    assert build_thin_feature_boxes([], global_max_cell=0.01) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_thin_feature_refinement.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'detect_thin_features'`.

- [ ] **Step 3: Implement the detector and builder**

Append to `src/polyfoammesh/core/geometry.py`:

```python
def detect_thin_features(
    meshes: list[trimesh.Trimesh],
    max_width: float,
    samples: int = 2048,
    seed: int = 0xC0FFEE,
    max_points: int = 200,
) -> tuple[np.ndarray, np.ndarray]:
    """Locate thin passages by OPPOSING-WALL nearest-point proximity.

    Ray casting (``_sample_thickness_one_mesh``) measures a wall's inward
    normal ray; in an OPEN slot that ray exits the slot end, so the slot
    width is never measured (measured live: p10 = 0.020 m on slot1 against
    a true 0.0015 m slot). Nearest-point queries do not care about openness.
    A hit counts only when the two normals oppose (dot < -0.5), which
    rejects same-wall local proximity, and when the distance clears the
    grazing-edge plausibility floor used by ``sample_thickness_field``.

    Returns ``(midpoints (N,3), widths (N,))``, deduplicated per
    ``max_width``-sized voxel (min width kept), sorted by width, capped at
    ``max_points``. Empty arrays when nothing is detected or on any failure
    (never raises — a meshing run must not die on a detection bug).
    """
    if not meshes or max_width <= 0:
        return np.zeros((0, 3)), np.zeros(0)
    try:
        mesh = meshes[0] if len(meshes) == 1 else trimesh.util.concatenate(meshes)
        if len(mesh.faces) == 0 or mesh.area <= 0:
            return np.zeros((0, 3)), np.zeros(0)
        pts, face_idx = trimesh.sample.sample_surface(mesh, samples, seed=seed)
        closest, dist, hit_idx = mesh.nearest.on_surface(pts)
        normals_a = np.asarray(mesh.face_normals[face_idx], dtype=np.float64)
        normals_b = np.asarray(mesh.face_normals[hit_idx], dtype=np.float64)
        opposing = np.einsum("ij,ij->i", normals_a, normals_b) < -0.5
        all_verts = np.vstack([np.asarray(m.vertices) for m in meshes])
        bbox_max = float(max(all_verts.max(axis=0) - all_verts.min(axis=0)))
        eps = max(bbox_max * 1e-6, 1e-12)
        face_scale = np.sqrt(np.maximum(mesh.area_faces[face_idx], 1e-300))
        valid = (
            opposing
            & (dist > eps)
            & (dist < max_width)
            & (dist >= face_scale * 0.05)
        )
        if not valid.any():
            return np.zeros((0, 3)), np.zeros(0)
        centers = (np.asarray(pts[valid]) + np.asarray(closest[valid])) / 2.0
        widths = np.asarray(dist[valid], dtype=np.float64)
        keys = np.floor(centers / max(max_width, 1e-12)).astype(np.int64)
        uniq, inv = np.unique(keys, axis=0, return_inverse=True)
        min_w = np.full(len(uniq), np.inf)
        np.minimum.at(min_w, inv, widths)
        rep = np.zeros((len(uniq), 3), dtype=np.float64)
        counts = np.zeros(len(uniq), dtype=np.float64)
        np.add.at(rep, inv, centers)
        np.add.at(counts, inv, 1.0)
        rep /= np.maximum(counts[:, None], 1.0)
        order = np.argsort(min_w)[:max_points]
        return rep[order], min_w[order]
    except Exception:
        logger.exception("detect_thin_features failed")
        return np.zeros((0, 3)), np.zeros(0)


def build_thin_feature_boxes(
    meshes: list[trimesh.Trimesh],
    global_max_cell: float,
    max_boxes: int = 64,
    seed: int = 0xC0FFEE,
) -> list[dict]:
    """Turn detected thin features into ``objectRefinements`` boxes.

    A feature thinner than ``global_max_cell`` is unresolved: one octree
    cell spans both walls and their projection produces the >70° internal
    faces checkMesh reports. Each box asks cfMesh for
    ``cellSize = width / 2`` (two cells across the feature), clamped to
    ``[global_max_cell/32, global_max_cell*0.9]``, one box per
    ``global_max_cell``-sized voxel so both walls of a slot collapse into
    the same box. Box schema matches ``build_object_refinements``' box
    form. Returns ``[]`` (never raises) when nothing is detected.
    """
    if not meshes or global_max_cell <= 0:
        return []
    try:
        pts, widths = detect_thin_features(
            meshes, max_width=global_max_cell, seed=seed,
        )
        if len(pts) == 0:
            return []
        voxel = float(global_max_cell)
        keys = np.floor(pts / voxel).astype(np.int64)
        uniq, inv = np.unique(keys, axis=0, return_inverse=True)
        min_w = np.full(len(uniq), np.inf)
        np.minimum.at(min_w, inv, widths)
        pad = voxel * 0.1
        boxes: list[dict] = []
        for k, width in zip(uniq, min_w):
            if not np.isfinite(width):
                continue
            cell_size = float(np.clip(
                width / 2.0, global_max_cell / 32.0, global_max_cell * 0.9,
            ))
            lo = k * voxel - pad
            hi = (k + 1) * voxel + pad
            boxes.append({
                "type": "box",
                "xmin": float(lo[0]), "xmax": float(hi[0]),
                "ymin": float(lo[1]), "ymax": float(hi[1]),
                "zmin": float(lo[2]), "zmax": float(hi[2]),
                "cell_size": cell_size,
            })
        boxes.sort(key=lambda b: b["cell_size"])
        return boxes[:max_boxes]
    except Exception:
        logger.exception("build_thin_feature_boxes failed")
        return []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_thin_feature_refinement.py -q -p no:cacheprovider`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python311;$env:PATH"
git add src/polyfoammesh/core/geometry.py tests/test_thin_feature_refinement.py
git commit -m "feat(geometry): thin-feature detector + refinement boxes (opt-in, unused yet)"
```

---

### Task 2: `write_meshdict(thin_feature_refinement=False)` integration

**Files:**
- Modify: `src/polyfoammesh/core/meshdict_gen.py` (`write_meshdict` signature + body, ~lines 427-511)
- Test: `tests/test_thin_feature_refinement.py` (append)

**Interfaces:**
- Consumes: `build_thin_feature_boxes(meshes, global_max_cell)` from Task 1
- Produces: `write_meshdict(..., thin_feature_refinement: bool = False) -> Path`; when `True`, detected boxes are appended to `object_refinements` before writing. Default `False` → output byte-identical to current behavior.

- [ ] **Step 1: Write the failing tests**

```python
import tempfile
from pathlib import Path

from polyfoammesh.core.meshdict_gen import write_meshdict


def _write_slot_case(case_dir: Path) -> None:
    tri = Path(case_dir) / "constant" / "triSurface"
    tri.mkdir(parents=True, exist_ok=True)
    combined = trimesh.util.concatenate(_two_plates(gap=0.004))
    combined.export(tri / "surface.stl")


def test_write_meshdict_flag_off_is_unchanged(tmp_path):
    _write_slot_case(tmp_path)
    out = write_meshdict(
        tmp_path, max_cell_size=0.01, min_cell_size=0.002,
        surface_file="constant/triSurface/surface.stl",
    )
    assert "objectRefinements" not in out.read_text()


def test_write_meshdict_thin_feature_refinement_adds_boxes(tmp_path):
    _write_slot_case(tmp_path)
    out = write_meshdict(
        tmp_path, max_cell_size=0.01, min_cell_size=0.002,
        surface_file="constant/triSurface/surface.stl",
        thin_feature_refinement=True,
    )
    content = out.read_text()
    assert "objectRefinements" in content
    assert "cellSize" in content
    assert "refinementBox_0" in content


def test_write_meshdict_resolves_stl_when_surface_is_fms(tmp_path):
    _write_slot_case(tmp_path)
    (Path(tmp_path) / "constant" / "triSurface" / "surface.fms").write_text("fms")
    out = write_meshdict(
        tmp_path, max_cell_size=0.01, min_cell_size=0.002,
        surface_file="constant/triSurface/surface.fms",
        thin_feature_refinement=True,
    )
    assert "objectRefinements" in out.read_text()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_thin_feature_refinement.py -q -p no:cacheprovider -k write_meshdict`
Expected: FAIL — `TypeError: write_meshdict() got an unexpected keyword argument 'thin_feature_refinement'`.

- [ ] **Step 3: Implement**

In `src/polyfoammesh/core/meshdict_gen.py`:

Add the parameter to `write_meshdict` (after `workflow_stop_after`, before `max_cells_override`):

```python
    thin_feature_refinement: bool = False,
```

Add a module-level helper near `write_meshdict`:

```python
def _resolve_detection_surface(case_dir: Path, surface_file: str) -> Path:
    """Surface used for thin-feature detection.

    Prefer a sibling ``.stl`` when the configured surfaceFile is the
    generated ``.fms`` (trimesh cannot read .fms; the STL is exported
    alongside it by every caller).
    """
    p = Path(surface_file)
    if p.suffix.lower() == ".fms":
        p = p.with_suffix(".stl")
    return p if p.is_absolute() else Path(case_dir) / p
```

In the body, immediately before `lines = build_meshdict_lines(...)`:

```python
    if thin_feature_refinement:
        try:
            import trimesh as _trimesh

            from polyfoammesh.core.geometry import build_thin_feature_boxes

            det_path = _resolve_detection_surface(case_dir, surface_file)
            if det_path.exists():
                surf = _trimesh.load(str(det_path), force="mesh", process=False)
                boxes = build_thin_feature_boxes([surf], global_max_cell=max_cell_size)
                if boxes:
                    object_refinements = list(object_refinements or []) + boxes
                    logger.info(
                        "meshDict: thin_feature_refinement added %d box(es) "
                        "(max_cell=%.6g)", len(boxes), max_cell_size,
                    )
            else:
                logger.warning(
                    "meshDict: thin_feature_refinement skipped — surface not "
                    "found at %s", det_path,
                )
        except Exception:
            logger.exception(
                "meshDict: thin_feature_refinement failed — writing meshDict "
                "without it"
            )
```

Also log the enabled flag next to the existing `boundary_cell_size` info log:

```python
    if thin_feature_refinement:
        logger.info("meshDict: thin_feature_refinement enabled.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_thin_feature_refinement.py tests/test_meshdict_gen.py -q -p no:cacheprovider`
Expected: all pass (5 + 3 new + existing meshDict tests).

- [ ] **Step 5: Verify no default drift**

Run: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_meshdict_gen.py -q -p no:cacheprovider`
Expected: green — proves the default path is unchanged.

- [ ] **Step 6: Commit**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python311;$env:PATH"
git add src/polyfoammesh/core/meshdict_gen.py tests/test_thin_feature_refinement.py
git commit -m "feat(meshdict): opt-in thin_feature_refinement boxes (default off)"
```

---

### Task 3: Harness flag `--hex-thin-refine`

**Files:**
- Modify: `benchmarks/run_benchmarks.py` (`_benchmark_one` signature + write_meshdict call, ~lines 351-412)
- Modify: `benchmarks/run_comparison.py` (`run_single`, `run_parent`, CLI, `main`, ~lines 317-459)
- Test: `tests/test_run_comparison.py` (append)

**Interfaces:**
- Consumes: `write_meshdict(..., thin_feature_refinement=...)` from Task 2
- Produces: `--hex-thin-refine` CLI flag (parent + child); `run_parent(..., hex_thin_refine=False)`, `run_single(..., hex_thin_refine=False)`, `_benchmark_one(..., thin_refine=False)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_hex_thin_refine_defaults_off():
    opts = rc._build_parser().parse_args(["--tier", "fast"])
    assert opts.hex_thin_refine is False


def test_hex_thin_refine_flag_forwarded_to_child(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, timeout=None, **kw):
        seen.append(cmd)
        out = Path(cmd[cmd.index("--out") + 1])
        geom = cmd[cmd.index("--geometry") + 1]
        runner = cmd[cmd.index("--runner") + 1]
        out.write_text(json.dumps(rc.make_record(
            geom, runner, success=True, wall_time_s=1.0,
            metrics={"cell_count": 10, "max_non_ortho": 10.0,
                     "avg_non_ortho": 5.0, "max_skewness": 0.5,
                     "max_aspect_ratio": 2.0, "neg_cells": 0,
                     "min_volume": 1e-4},
            errors=[], warnings=[],
        )), encoding="utf-8")

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    rc.run_parent(
        tier="fast", runners=["hex"], geometries=["cylinder"],
        hex_thin_refine=True, _child_out_dir=tmp_path, _write_results=False,
    )
    assert seen and all("--hex-thin-refine" in c for c in seen)


def test_run_single_passes_thin_refine_to_hex(tmp_path, monkeypatch):
    import benchmarks.run_benchmarks as rb

    captured = {}

    def fake_bench(stl_path, **kw):
        captured.update(kw)
        return {"geometry": Path(stl_path).stem, "success": True,
                "wall_time_s": 1.0, "cell_count": 10, "max_non_ortho": 10.0,
                "avg_non_ortho": 5.0, "max_skewness": 0.5,
                "max_aspect_ratio": 2.0, "neg_cells": 0, "min_volume": 1e-4,
                "errors": [], "warnings": []}

    monkeypatch.setattr(rb, "_benchmark_one", fake_bench)
    rc.run_single(
        "hex", Path("benchmarks/geometries/cylinder.stl"),
        tmp_path / "out.json", hex_thin_refine=True,
    )
    assert captured.get("thin_refine") is True
```

(Import `json`, `Path`, `rc` at the top of the file as the existing tests do.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_run_comparison.py -q -p no:cacheprovider -k thin`
Expected: FAIL — `AttributeError: 'Namespace' object has no attribute 'hex_thin_refine'` (and `run_parent`/`run_single` TypeError).

- [ ] **Step 3: Implement**

`benchmarks/run_benchmarks.py` — signature and call:

```python
def _benchmark_one(
    stl_path: Path, keep_case: bool = False,
    mesh_timeout_s: int = TIMEOUT_S, check_timeout_s: int = TIMEOUT_S,
    thin_refine: bool = False,
) -> dict:
```

and in its `write_meshdict(...)` call add `thin_feature_refinement=thin_refine,`.

`benchmarks/run_comparison.py`:

```python
def run_single(
    runner: str, stl_path: Path, out_path: Path, keep_case: bool = False,
    hex_thin_refine: bool = False,
) -> int:
```

hex branch:

```python
        bench = rb._benchmark_one(
            stl_path, keep_case=keep_case,
            mesh_timeout_s=HEX_MESH_TIMEOUT_S, check_timeout_s=CHECK_TIMEOUT_S,
            thin_refine=hex_thin_refine,
        )
```

`run_parent` signature gains `hex_thin_refine: bool = False,` and the child command gains:

```python
            if hex_thin_refine:
                cmd.append("--hex-thin-refine")
```

CLI parser gains:

```python
    p.add_argument(
        "--hex-thin-refine", action="store_true",
        help="opt-in: feature-keyed objectRefinements for sub-cell thin "
             "features on the hex runner (default off)",
    )
```

`main` passes it through:

```python
            return run_single(
                opts.runner[0], geometry_path(spec), Path(opts.out),
                opts.keep, hex_thin_refine=opts.hex_thin_refine,
            )
...
    _, exit_code = run_parent(
        opts.tier, opts.runner, opts.geometry, opts.keep,
        hex_thin_refine=opts.hex_thin_refine,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_run_comparison.py tests/test_run_comparison_parent.py tests/test_run_benchmarks_timeouts.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python311;$env:PATH"
git add benchmarks/run_benchmarks.py benchmarks/run_comparison.py tests/test_run_comparison.py
git commit -m "bench: --hex-thin-refine opt-in flag through parent/child (default off)"
```

---

### Task 4: B-side targeted measurement + kill/promote gate

**Files:**
- Produce (gitignored, `git add -f`): `benchmarks/results/comparison_<ts>.json`
- No source changes.

**Interfaces:**
- Consumes: Tasks 1-3
- Produces: B-side records for the four gap rows; gate decision for Task 5.

- [ ] **Step 1: Run the B-side on the fast-tier gap rows**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier fast --geometry groove1 --geometry slot1 --geometry thin_gap --runner hex --hex-thin-refine
```

Use a bash timeout of at least 3 600 000 ms. Expected: 3 records (exit 0 if all pass, 1 otherwise — both are data).

- [ ] **Step 2: Run the B-side on mini_valve (full tier)**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier full --geometry mini_valve --runner hex --hex-thin-refine
```

- [ ] **Step 3: Compare against the A-side table and apply the kill criterion**

A-side (from `notes/non-ortho_reasoning.md`): groove1 75.98, slot1 77.18, thin_gap 73.76, mini_valve 72.58; all `failed_thresholds == ["non_ortho_max"]`.

Record the B-side `max_non_ortho`, `cell_count`, `wall_time_s` per geometry and decide:
- **PROMOTE (continue to Task 5)** iff: groove1 < 70 **and** improvement ≥5° (≤70.98 is not enough) **and** no row got worse by >2° **and** `cell_count` ≤3× A-side (≤1.5× for thin_gap) **and** `thresholds_pass == true` on groove1/slot1/thin_gap.
- **KILL** otherwise. On kill: skip Task 5, go to Task 6 and document the negative result honestly (numbers, mechanism, why the prediction failed).

- [ ] **Step 4: Commit the B-side artefacts**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python311;$env:PATH"
git add -f benchmarks/results/comparison_<ts>.json
git commit -m "bench: B-side hex thin-feature refinement on gap rows (local)"
```

---

### Task 5: B-side regression runs (only if Task 4 promoted)

**Files:**
- Produce (gitignored, `git add -f`): `benchmarks/results/comparison_<ts>.json` ×2

**Interfaces:**
- Consumes: Task 4 PROMOTE decision
- Produces: fast-tier and full-tier hex records with the flag on; regression evidence on healthy geometries.

- [ ] **Step 1: Fast tier, hex only, flag on**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier fast --runner hex --hex-thin-refine
```

Verify against the valid fast baseline (`comparison_20261005T135839.json`):
- groove1/slot1/thin_gap: now `thresholds_pass == true`.
- cylinder 52.83, cube_duct 64.86, venturi 53.33: `max_non_ortho` unchanged (directive does not fire — features are ≥1.76 cells across) or worse by ≤2°; `cell_count` not inflated beyond 1.2×.

- [ ] **Step 2: Full tier, hex only, flag on**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier full --runner hex --hex-thin-refine
```

Verify: mini_valve passes (<70); all 10 hex rows compared to the baseline `comparison_20261005T113621.json` — no currently-passing row starts failing; hex pass rate target 9/10 (dirty_cad stays 0). Use a bash timeout of at least 3 600 000 ms.

- [ ] **Step 3: If any regression appears**

The kill criterion governs: any healthy geometry worse by >2°, cube_duct >70, or a passing row failing → KILL the lane (skip to Task 6, document). Do not tune the flag to rescue a regressing run — that is a new experiment requiring a new prediction.

- [ ] **Step 4: Commit**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python311;$env:PATH"
git add -f benchmarks/results/comparison_<ts>.json benchmarks/results/comparison_<ts2>.json
git commit -m "bench: B-side hex regression runs with thin-feature refinement (local)"
```

---

### Task 6: Verdict, documentation, handoff

**Files:**
- Modify: `notes/non-ortho_reasoning.md` (append B-side section; tick Status boxes)
- Modify: `.opencode/GOAL.md` (append `## Avanzamento 2026-10-05 — Lane NON-ORTHO fix`)
- No source changes.

**Interfaces:**
- Consumes: Task 4/5 records
- Produces: the repo-discipline verdict artefact; STOP for the human's promotion decision.

- [ ] **Step 1: Append the B-side results to the note**

Add under the existing A-side section, keeping the prediction text untouched:

```markdown
### B-side (with `thin_feature_refinement` / `--hex-thin-refine`)

| geometry | record ref | cells | max_non_ortho | A-side | Δ | wall s | thresholds_pass |
|---|---|---|---|---|---|---|---|
| ... (fill from Task 4/5 JSONs) |

Verdict: PROMOTED-OPT-IN | KILLED — <one paragraph: which criterion decided,
what the mechanism did, what remains unmeasured (e.g. production path)>.
```

Tick the Status boxes that are now true (`Fix implemented`, `B-side measured`,
`Verdict`) and leave any box that is genuinely not done unticked.

- [ ] **Step 2: Append the GOAL entry**

`.opencode/GOAL.md`, Italian style consistent with the file: what was fixed, the
A/B numbers verbatim, whether the lane was promoted or killed, and that
production/GUI wiring is a separate human decision (flag remains opt-in, no
default changed).

- [ ] **Step 3: Full verification**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m ruff check src/ tests/ benchmarks/
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/ -q -m "not slow and not wsl" -p no:cacheprovider --tb=line
```

Expected: ruff clean; suite green (≥1234 passed + the new tests; 2 skipped; 1 xfailed).

- [ ] **Step 4: Commit and STOP**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python311;$env:PATH"
git add notes/non-ortho_reasoning.md .opencode/GOAL.md
git commit -m "notes: hex thin-feature refinement B-side verdict (local)"
```

Report to the human: B-side numbers, verdict, and the explicit question —
promote to opt-in production wiring (GUI/`openfoam_runner`/`mesh_engine`) or leave
it benchmark-only. Do not wire it without that decision.

---

## Self-Review

**Spec coverage:**
- "feature-keyed refinement directive in the hex meshDict" → Tasks 1-2 (`objectRefinements` boxes keyed to detected features) ✓
- "behind the flag `thin_feature_refinement` (opt-in param on `write_meshdict`)" → Task 2 ✓
- "surfaced as `--hex-thin-refine` in the harness" → Task 3 ✓
- "default off — no default changed, additive only" → Task 2 Step 5 asserts it ✓
- B-side commands from the note's Measurement plan → Tasks 4-5 ✓
- Kill criterion from the note → Task 4 Step 3 / Task 5 Step 3 ✓
- Verdict recorded, fix not wired without approval → Task 6 ✓

**Deviation from the note, declared:** the note's prediction says the directive "also fires" on multi_inlet (1.76 cells across). This plan's threshold is `width < global_max_cell`, so multi_inlet (0.012 > 0.0068) does **not** fire; its prediction ("stays PASS") still holds trivially. Recorded here so the executor does not chase it.

**Placeholder scan:** the only `<...>` tokens are runtime artefact names/tables filled from generated JSONs; no TBD, no "similar to Task N".

**Type consistency:** `detect_thin_features` → `build_thin_feature_boxes` → `object_refinements` box schema (`build_object_refinements` consumes it) → `write_meshdict` → `_benchmark_one(thin_refine=...)` → `run_single(hex_thin_refine=...)` / `run_parent(hex_thin_refine=...)` → `--hex-thin-refine`; names match across all tasks.
