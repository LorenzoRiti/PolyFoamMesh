"""Tests for the poly-mesh remediation loop (commercial/poly_remediation.py).

Hermetic: builds tiny dual meshes in memory (no WSL, no OpenFOAM, no network)
and asserts the strictly-non-regressive contract:
- an action that does not strictly help leaves the mesh byte-identical;
- the iteration cap is respected even when quality never converges;
- the result dataclass reports accepted/rejected actions;
- concave-feature defects are detected and skipped, never "fixed";
- the tet fallback is reported as an opt-in option, never applied automatically.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

from _test_helpers import load_commercial_module
from polyfoammesh.core import foam_mesh_io as fio
from polyfoammesh.core.poly_smoother import _boundary_vertex_mask
from polyfoammesh.core.tet_poly_dual import (
    TetPolyDualConverter,
    _cell_centres,
    _detect_defects,
    _face_geometry,
)
from test_tet_poly_dual import build_tet_case  # noqa: E402

_mod = load_commercial_module("poly_remediation")
remediate_poly_mesh = _mod.remediate_poly_mesh
restore_tet_mesh = _mod.restore_tet_mesh
PolyRemediationResult = _mod.PolyRemediationResult
PolyRemediationAction = _mod.PolyRemediationAction


# ---------------------------------------------------------------------------
# helpers: build tiny dual meshes in memory
# ---------------------------------------------------------------------------

def _cube_tets_pts() -> tuple[list, np.ndarray]:
    """Unit cube [0,1]^3 split into 6 tets, apex at the centre."""
    pts = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0], [1.0, 0.0, 1.0], [1.0, 1.0, 1.0], [0.0, 1.0, 1.0],
    ], dtype=np.float64)
    c = pts.mean(axis=0)
    faces_of_cube = [
        [0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4],
        [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7],
    ]
    tets = []
    for f in faces_of_cube:
        tets.append([f[0], f[1], f[2], 8])
        tets.append([f[0], f[2], f[3], 8])
    pts = np.vstack([pts, c])
    return tets, pts


def _run_dual(tmp_path: Path) -> tuple:
    """Build the cube tet case, run the plain dual, read back the polyMesh."""
    tets, pts = _cube_tets_pts()
    case = build_tet_case(tmp_path, pts, tets)
    conv = TetPolyDualConverter(case, smooth=False)
    res = conv.run()
    assert res.success, res.errors
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neighbour, patches = fio.read_polymesh(poly)
    n_int = len(neighbour)
    n_cells = int(max(owner.max(), neighbour.max())) + 1
    return case, points, faces, owner, neighbour, patches, n_int, n_cells


def _inject_defects(points, faces, n_int, seed: int = 3) -> np.ndarray:
    """Displace interior vertices to create fixable (non-concave) defects."""
    boundary = _boundary_vertex_mask(points, faces, n_int)
    rng = np.random.default_rng(seed)
    injected = points.copy()
    for v in np.flatnonzero(~boundary):
        injected[v] += rng.normal(0, 0.15, 3)
    return injected


def _counts_of(points, faces, owner, neigh, n_int, n_cells) -> dict:
    sf, cf = _face_geometry(points, faces)
    ctr, vol = _cell_centres(sf, cf, owner, neigh, n_int, n_cells)
    _, counts = _detect_defects(points, faces, sf, cf, ctr, owner, neigh, n_int, n_cells)
    return counts


def _defect_total(points, faces, owner, neigh, n_int, n_cells) -> int:
    counts = _counts_of(points, faces, owner, neigh, n_int, n_cells)
    return counts["pyramid"] + counts["non_ortho"] + counts["skew"]


def _mesh_bytes(case: Path) -> dict:
    poly = case / "constant" / "polyMesh"
    return {name: (poly / name).read_bytes()
            for name in ("points", "faces", "owner", "neighbour", "boundary")}


# ---------------------------------------------------------------------------
# strictly non-regressive contract
# ---------------------------------------------------------------------------

def test_rollback_when_action_does_not_help(tmp_path, monkeypatch):
    case, points, faces, owner, neigh, patches, n_int, n_cells = _run_dual(tmp_path)
    injected = _inject_defects(points, faces, n_int)
    fio.write_polymesh(
        case / "constant" / "polyMesh", injected, faces, owner, neigh, patches,
    )
    before = _mesh_bytes(case)

    # A non-helping action: smoothing returns the input unchanged (with its
    # real defect count) — the strict rule must reject it and leave the mesh
    # byte-identical.
    import polyfoammesh.core.poly_smoother as ps

    def noop_smooth(points, faces, owner, neigh, n_int, n_cells, *a, **k):
        return points, _counts_of(points, faces, owner, neigh, n_int, n_cells)

    monkeypatch.setattr(ps, "smooth_dual_mesh", noop_smooth)

    result = remediate_poly_mesh(case, max_iterations=3)
    assert not result.improved
    assert result.unchanged
    assert result.defects_after == result.defects_before
    assert len(result.actions) == 1 and not result.actions[0].accepted
    for name, data in before.items():
        assert (case / "constant" / "polyMesh" / name).read_bytes() == data, name


def test_iteration_cap_respected_when_never_converges(tmp_path, monkeypatch):
    case, points, faces, owner, neigh, patches, n_int, n_cells = _run_dual(tmp_path)
    injected = _inject_defects(points, faces, n_int)
    fio.write_polymesh(
        case / "constant" / "polyMesh", injected, faces, owner, neigh, patches,
    )

    # Each smoothing call claims one fewer defect but never reaches zero:
    # the loop must stop at the iteration cap, not run forever.
    import polyfoammesh.core.poly_smoother as ps

    state: dict = {"cur": None}

    def improving_never_converges(points, faces, owner, neigh, n_int, n_cells, *a, **k):
        if state["cur"] is None:
            state["cur"] = _defect_total(points, faces, owner, neigh, n_int, n_cells)
        state["cur"] = max(1, state["cur"] - 1)
        return points, {"pyramid": state["cur"], "non_ortho": 0, "skew": 0}

    monkeypatch.setattr(ps, "smooth_dual_mesh", improving_never_converges)

    result = remediate_poly_mesh(case, max_iterations=5)
    assert result.iterations == 5
    assert len(result.actions) == 5
    assert all(a.accepted for a in result.actions)
    assert result.defects_after > 0  # never converged


def test_result_reports_accepted_and_rejected_actions(tmp_path, monkeypatch):
    case, points, faces, owner, neigh, patches, n_int, n_cells = _run_dual(tmp_path)
    injected = _inject_defects(points, faces, n_int)
    fio.write_polymesh(
        case / "constant" / "polyMesh", injected, faces, owner, neigh, patches,
    )

    import polyfoammesh.core.poly_smoother as ps

    state: dict = {"cur": None, "n": 0}

    def improve_then_plateau(points, faces, owner, neigh, n_int, n_cells, *a, **k):
        state["n"] += 1
        if state["cur"] is None:
            state["cur"] = _defect_total(points, faces, owner, neigh, n_int, n_cells)
        if state["n"] == 1:
            state["cur"] = max(1, state["cur"] - 1)
        return points, {"pyramid": state["cur"], "non_ortho": 0, "skew": 0}

    monkeypatch.setattr(ps, "smooth_dual_mesh", improve_then_plateau)

    result = remediate_poly_mesh(case, max_iterations=3)
    assert len(result.actions) == 2
    assert result.actions[0].accepted
    assert not result.actions[1].accepted
    assert result.actions[0].defects_after < result.actions[0].defects_before
    assert result.actions[1].defects_after == result.actions[1].defects_before
    assert result.improved
    assert result.defects_after < result.defects_before


def test_remediation_improves_fixable_mesh(tmp_path):
    case, points, faces, owner, neigh, patches, n_int, n_cells = _run_dual(tmp_path)
    injected = _inject_defects(points, faces, n_int)
    fio.write_polymesh(
        case / "constant" / "polyMesh", injected, faces, owner, neigh, patches,
    )
    injected_bytes = (case / "constant" / "polyMesh" / "points").read_bytes()

    result = remediate_poly_mesh(case, max_iterations=3)
    assert result.improved
    assert result.defects_after < result.defects_before
    assert any(a.accepted for a in result.actions)
    # the improved mesh was actually written
    assert (case / "constant" / "polyMesh" / "points").read_bytes() != injected_bytes


# ---------------------------------------------------------------------------
# concave-feature defects: detected and skipped, never "fixed"
# ---------------------------------------------------------------------------

def test_concave_feature_defects_are_skipped(tmp_path):
    # Single-tet dual with one primal vertex displaced so its dual cell wraps
    # the corner: exactly one boundary-face pyramid failure, all volumes
    # positive — the documented concave-feature defect class.
    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    case = build_tet_case(tmp_path, pts, [[0, 1, 2, 3]])
    conv = TetPolyDualConverter(case, smooth=False)
    res = conv.run()
    assert res.success, res.errors
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    n_int = len(neigh)
    n_cells = int(max(owner.max(), neigh.max())) + 1

    points[2] += np.array([-1.0, -1.0, -1.0]) * 0.5
    sf, cf = _face_geometry(points, faces)
    ctr, vol = _cell_centres(sf, cf, owner, neigh, n_int, n_cells)
    _, counts = _detect_defects(points, faces, sf, cf, ctr, owner, neigh, n_int, n_cells)
    assert counts == {"pyramid": 1, "non_ortho": 0, "skew": 0}
    assert (vol > 0.0).all()
    fio.write_polymesh(poly, points, faces, owner, neigh, patches)
    before = (poly / "points").read_bytes()

    result = remediate_poly_mesh(case, max_iterations=3)
    assert result.concave_defects_skipped
    assert result.n_concave_defect_cells == 1
    assert not result.improved
    assert result.unchanged
    assert result.actions == []
    assert (poly / "points").read_bytes() == before


# ---------------------------------------------------------------------------
# last-resort degrade: reported as opt-in, never automatic
# ---------------------------------------------------------------------------

def test_tet_fallback_reported_when_repair_fails(tmp_path, monkeypatch):
    case, points, faces, owner, neigh, patches, n_int, n_cells = _run_dual(tmp_path)
    injected = _inject_defects(points, faces, n_int)
    fio.write_polymesh(
        case / "constant" / "polyMesh", injected, faces, owner, neigh, patches,
    )
    backup = case / "constant" / "polyMesh_tet_backup"
    backup.mkdir()
    (backup / "points").write_text("tet backup", encoding="ascii")

    import polyfoammesh.core.poly_smoother as ps

    def noop_smooth(points, faces, owner, neigh, n_int, n_cells, *a, **k):
        return points, _counts_of(points, faces, owner, neigh, n_int, n_cells)

    monkeypatch.setattr(ps, "smooth_dual_mesh", noop_smooth)

    result = remediate_poly_mesh(case, max_iterations=3)
    assert result.tet_fallback_available
    assert not result.improved
    assert not result.degraded_to_tet
    degrade = [a for a in result.actions if a.strategy == "degrade_to_tet"]
    assert len(degrade) == 1 and not degrade[0].accepted


def test_restore_tet_mesh_is_opt_in(tmp_path):
    case, points, faces, owner, neigh, patches, n_int, n_cells = _run_dual(tmp_path)
    backup = case / "constant" / "polyMesh_tet_backup"
    backup.mkdir()
    (backup / "points").write_text("tet points", encoding="ascii")
    assert restore_tet_mesh(case)
    assert (case / "constant" / "polyMesh" / "points").read_text(
        encoding="ascii",
    ) == "tet points"
    # no backup -> False, nothing happens
    assert not restore_tet_mesh(tmp_path / "nope")


# ---------------------------------------------------------------------------
# quality_engine.auto_fix wiring
# ---------------------------------------------------------------------------

def test_auto_fix_branches_to_poly_remediation_when_tet_backup_exists(
    tmp_path, monkeypatch,
):
    mod = load_commercial_module("quality_engine")
    case_dir = tmp_path / "case"
    (case_dir / "constant" / "polyMesh_tet_backup").mkdir(parents=True)
    engine = mod.QualityEngine()
    called = {"n": 0}

    def fake_poly(case_dir, report, max_iterations):
        called["n"] += 1
        report.warnings.append("Poly remediation: branch hit")
        return report

    monkeypatch.setattr(engine, "_auto_fix_poly", fake_poly)
    initial = mod.QualityReport(
        metrics=mod.QualityMetrics(max_skewness=5.0, cells=10), passed=False,
    )
    result = engine.auto_fix(case_dir, report=initial, max_iterations=3)
    assert called["n"] == 1
    assert any("Poly remediation" in w for w in result.warnings)


def test_auto_fix_poly_branch_skipped_when_report_passes(tmp_path, monkeypatch):
    mod = load_commercial_module("quality_engine")
    case_dir = tmp_path / "case"
    (case_dir / "constant" / "polyMesh_tet_backup").mkdir(parents=True)
    engine = mod.QualityEngine()
    called = {"n": 0}

    def fake_poly(case_dir, report, max_iterations):
        called["n"] += 1
        return report

    monkeypatch.setattr(engine, "_auto_fix_poly", fake_poly)
    initial = mod.QualityReport(
        metrics=mod.QualityMetrics(max_skewness=0.5, cells=10), passed=True,
    )
    result = engine.auto_fix(case_dir, report=initial, max_iterations=3)
    assert called["n"] == 0
    assert result is initial


def test_auto_fix_poly_records_remediation_outcome(tmp_path, monkeypatch):
    mod = load_commercial_module("quality_engine")
    # Keep the lazy `from polyfoammesh.commercial.poly_remediation import ...`
    # inside _auto_fix_poly from triggering the heavy commercial/__init__.py:
    # the submodule is already in sys.modules (loaded above), so a stub parent
    # package is enough for the import machinery.
    monkeypatch.setitem(
        sys.modules, "polyfoammesh.commercial",
        types.ModuleType("polyfoammesh.commercial"),
    )

    case, points, faces, owner, neigh, patches, n_int, n_cells = _run_dual(tmp_path)
    injected = _inject_defects(points, faces, n_int)
    fio.write_polymesh(
        case / "constant" / "polyMesh", injected, faces, owner, neigh, patches,
    )

    engine = mod.QualityEngine()
    monkeypatch.setattr(
        engine, "analyse",
        lambda cd: mod.QualityReport(
            metrics=mod.QualityMetrics(max_skewness=0.1, cells=10), passed=True,
        ),
    )
    initial = mod.QualityReport(
        metrics=mod.QualityMetrics(max_skewness=5.0, cells=10), passed=False,
    )
    result = engine._auto_fix_poly(case, initial, 3)
    assert result.passed
    assert any("Poly remediation" in w for w in result.warnings)
    assert any(f.action == "smooth" for f in result.fixes_applied)