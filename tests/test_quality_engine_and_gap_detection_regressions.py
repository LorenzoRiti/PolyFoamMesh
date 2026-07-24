"""Regressions found while auditing the FullAutoPipeline (one_click_run.py)
dependency chain before wiring it up as a CLI entry point.

- quality_engine.py's QualityEngine.auto_fix() had the identical bug already
  fixed in commercial/optimizer.py's MeshOptimizer: it rewrote meshDict text
  via _apply_fix() but never re-ran cartesianMesh before re-analysing, so the
  loop re-checked the same unchanged mesh every iteration.
- geometry_pipeline.py's _extract_features() computed
  `mesh.edges[~mesh.face_adjacency_edges]` to find boundary edges, but
  face_adjacency_edges holds vertex-index pairs, not a boolean mask — `~`
  produced garbage indices, always caught by a bare except, so gap detection
  silently reported zero gaps for every non-watertight mesh.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _test_helpers import load_commercial_module


def test_quality_engine_auto_fix_reruns_cartesianmesh(monkeypatch, tmp_path):
    mod = load_commercial_module("quality_engine")

    case_dir = tmp_path / "case"
    (case_dir / "system").mkdir(parents=True)
    (case_dir / "system" / "meshDict").write_text(
        "maxCellSize 0.1;\nminCellSize 0.02;\n"
    )

    engine = mod.QualityEngine()

    calls = {"cartesian_mesh": 0}
    monkeypatch.setattr(engine, "_run_cartesian_mesh", lambda cd: (calls.__setitem__(
        "cartesian_mesh", calls["cartesian_mesh"] + 1) or True))

    # Re-analyse after the fix + remesh: now passes, loop should stop.
    reports = iter([
        mod.QualityReport(metrics=mod.QualityMetrics(max_skewness=0.1, cells=10), passed=True),
    ])
    monkeypatch.setattr(engine, "analyse", lambda cd: next(reports))

    initial = mod.QualityReport(metrics=mod.QualityMetrics(max_skewness=5.0, cells=10), passed=False)
    result = engine.auto_fix(case_dir, report=initial, max_iterations=3)

    assert calls["cartesian_mesh"] == 1, (
        "auto_fix() must actually re-run cartesianMesh once a fix is applied, "
        "not just rewrite meshDict and re-check the stale mesh"
    )
    assert result.passed is True


def test_quality_engine_auto_fix_stops_if_remesh_fails(monkeypatch, tmp_path):
    mod = load_commercial_module("quality_engine")
    case_dir = tmp_path / "case"
    (case_dir / "system").mkdir(parents=True)
    (case_dir / "system" / "meshDict").write_text("maxCellSize 0.1;\nminCellSize 0.02;\n")

    engine = mod.QualityEngine()
    monkeypatch.setattr(engine, "_run_cartesian_mesh", lambda cd: False)

    analyse_calls = {"n": 0}
    def _analyse(cd):
        analyse_calls["n"] += 1
        return mod.QualityReport(metrics=mod.QualityMetrics(max_skewness=0.1, cells=10), passed=True)
    monkeypatch.setattr(engine, "analyse", _analyse)

    initial = mod.QualityReport(metrics=mod.QualityMetrics(max_skewness=5.0, cells=10), passed=False)
    engine.auto_fix(case_dir, report=initial, max_iterations=3)

    assert analyse_calls["n"] == 0, (
        "must not re-analyse after a failed remesh — that would report on "
        "the pre-fix mesh as if it reflected the fix"
    )


def test_geometry_pipeline_gap_detection_finds_real_boundary_edges():
    mod = load_commercial_module("geometry_pipeline")
    import numpy as np
    import trimesh

    box = trimesh.creation.box()
    mask = np.ones(len(box.faces), dtype=bool)
    mask[0] = False  # remove one face -> non-watertight, real boundary edges
    box.update_faces(mask)
    assert not box.is_watertight

    gp = mod.GeometryPipeline()
    report = gp._extract_features([box])

    assert report.n_gap_regions >= 1
    assert report.min_gap > 0.0
