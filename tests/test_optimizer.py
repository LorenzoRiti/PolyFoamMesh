"""Tests for the mesh quality optimizer module."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("optimizer")
QualitySnapshot = _mod.QualitySnapshot
QualityReport = _mod.QualityReport
MeshOptimizer = _mod.MeshOptimizer


def test_quality_snapshot_defaults():
    s = QualitySnapshot()
    assert s.metrics == {}
    assert not s.passed
    assert s.fix_attempt == 0


def test_quality_report_defaults():
    r = QualityReport()
    assert r.initial is None
    assert r.final is None
    assert r.history == []
    assert r.iterations == 0
    assert not r.converged
    assert not r.improved()


def test_quality_report_improved():
    r = QualityReport()
    r.initial = QualitySnapshot(metrics={"max_skewness": 8.0}, passed=False)
    r.final = QualitySnapshot(metrics={"max_skewness": 3.0}, passed=True)
    assert r.improved()


def test_quality_report_not_improved():
    r = QualityReport()
    r.initial = QualitySnapshot(metrics={"max_skewness": 3.0}, passed=True)
    r.final = QualitySnapshot(metrics={"max_skewness": 8.0}, passed=False)
    assert not r.improved()


def test_quality_report_metrics_improvement():
    r = QualityReport()
    r.initial = QualitySnapshot(metrics={"max_skewness": 8.0, "max_non_ortho": 70.0})
    r.final = QualitySnapshot(metrics={"max_skewness": 3.0, "max_non_ortho": 50.0})
    impr = r.metrics_improvement()
    assert impr["max_skewness"] == 5.0
    assert impr["max_non_ortho"] == 20.0


def test_quality_report_no_initial():
    r = QualityReport()
    r.final = QualitySnapshot(metrics={"max_skewness": 3.0})
    assert r.metrics_improvement() == {}


def test_quality_report_to_dict():
    r = QualityReport(iterations=2, converged=False)
    d = r.to_dict()
    assert d["iterations"] == 2
    assert not d["converged"]


def test_mesh_optimizer_init():
    opt = MeshOptimizer()
    assert opt._of_config is not None
    assert opt.THRESHOLDS["skewness_max"] == 4.0


def test_mesh_optimizer_thresholds():
    opt = MeshOptimizer()
    assert "skewness_max" in opt.THRESHOLDS
    assert "non_ortho_max" in opt.THRESHOLDS
    assert "aspect_ratio_max" in opt.THRESHOLDS


def test_optimizer_refuses_tet_mesh_remesh(tmp_path):
    """Fase 3 P3.2: the optimiser must refuse to re-run cartesianMesh over a
    GMSH tet mesh (pure-tet, no backup) — a silent swap would replace the
    user's tet mesh with an unrelated cfMesh hex at different dimensions."""
    import numpy as np
    from polyfoammesh.core import foam_mesh_io

    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    faces = [[0, 1, 2], [0, 1, 3], [1, 2, 3], [0, 3, 2]]
    foam_mesh_io.write_polymesh(
        poly, pts, faces,
        np.array([0, 0, 0, 0], dtype=np.int64),
        np.array([-1, -1, -1, -1], dtype=np.int64),
        [{"name": "walls", "type": "patch", "nFaces": 4, "startFace": 0}],
    )
    opt = MeshOptimizer()
    # Guard short-circuits before any WSL call — no OpenFOAM needed.
    assert opt._run_cartesian_mesh(tmp_path) is False


def test_optimizer_refuses_tet_backup_remesh(tmp_path):
    """Fase 3 P3.2: same guard via the polyMesh_tet_backup marker."""
    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "owner").write_text("", encoding="ascii")  # a mesh is present
    (tmp_path / "constant" / "polyMesh_tet_backup").mkdir()
    opt = MeshOptimizer()
    assert opt._run_cartesian_mesh(tmp_path) is False


def test_quality_snapshot_from_report():
    """Test creating a snapshot from a dict (avoiding MeshQualityReport import)."""
    snap = QualitySnapshot(
        metrics={"cells": 5000, "max_skewness": 4.5},
        passed=False, fix_attempt=1,
    )
    assert snap.fix_attempt == 1
    assert snap.metrics.get("cells", 0) == 5000


if __name__ == "__main__":
    test_quality_snapshot_defaults()
    test_quality_report_defaults()
    test_quality_report_improved()
    test_quality_report_not_improved()
    test_quality_report_metrics_improvement()
    test_quality_report_no_initial()
    test_quality_report_to_dict()
    test_mesh_optimizer_init()
    test_mesh_optimizer_thresholds()
    test_quality_snapshot_from_report()
    print("ALL PASS")
