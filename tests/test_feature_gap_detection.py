"""Feature detector gap detection must find real thin passages.

The old implementation was a placeholder that always returned an empty
gap list, so thin walls/passages between non-adjacent surfaces were
invisible to feature-based cell sizing. The real implementation queries
closest points between non-adjacent surface pairs via GMSH; this test
builds a thin-walled pipe (4 mm wall on a 200 mm OD) and checks the
wall is detected as a gap of the right width.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

gmsh = pytest.importorskip("gmsh")
cq = pytest.importorskip("cadquery")

from polyfoammesh.core.feature_detector import FeatureDetector  # noqa: E402


def _thin_pipe_step(tmp_path: Path) -> str:
    """200 mm OD pipe with a 4 mm wall, authored in cadquery millimetres."""
    outer = cq.Workplane("XY").circle(100).extrude(300)
    core = cq.Workplane("XY").circle(96).extrude(300)
    pipe = outer.cut(core)
    step = tmp_path / "thin_pipe.step"
    cq.exporters.export(pipe, str(step))
    return str(step)


def test_thin_wall_is_detected_as_gap(tmp_path):
    step = _thin_pipe_step(tmp_path)
    fm = FeatureDetector().analyze_step(step, detail="medium", scale=1.0)
    assert fm.gap_regions, "thin-walled pipe must produce gap regions"
    widths = sorted(g.gap_width for g in fm.gap_regions)
    # 4 mm wall, expressed in metres after GMSH's unit conversion
    assert 0.003 < widths[0] < 0.005, f"expected ~0.004 m gap, got {widths[0]}"


def test_no_gap_regions_for_a_solid_block(tmp_path):
    """A plain solid (no passages) must not produce phantom gaps."""
    block = cq.Workplane("XY").box(200, 200, 200)
    step = tmp_path / "block.step"
    cq.exporters.export(block, str(step))
    fm = FeatureDetector().analyze_step(step, detail="medium", scale=1.0)
    assert fm.gap_regions == []


def test_cache_hits_on_second_analysis(tmp_path):
    step = _thin_pipe_step(tmp_path)
    fd = FeatureDetector()
    fd.analyze_step(step, detail="medium", scale=1.0)
    fm2 = fd.analyze_step(step, detail="medium", scale=1.0)
    assert fm2.gap_regions, "cached result must still carry gap regions"
