"""Tests for the real-time quality dashboard module."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("monitor")
QualityMetric = _mod.QualityMetric
QualitySnapshot = _mod.QualitySnapshot
WorstCell = _mod.WorstCell
QualityMonitor = _mod.QualityMonitor


CHECKMESH_OUTPUT_OK = """
Mesh stats
    cells:     5000
    faces:     15000
    points:    6000
Max non-orthogonality = 45.2  average = 12.1
Max skewness = 2.3  average = 0.8
Max aspect ratio = 450
Min volume = 1.2e-08
Mesh OK.
"""

CHECKMESH_OUTPUT_FAIL = """
Mesh stats
    cells:     5000
    faces:     15000
    points:    6000
Max non-orthogonality = 78.5  average = 18.3
Max skewness = 6.2  average = 1.5
Max aspect ratio = 1200
There are 3 cells with negative volume.
Min volume = -5.3e-10
Mesh NOT OK.
"""

CHECKMESH_OUTPUT_WARN = """
Mesh stats
    cells:     5000
Max non-orthogonality = 72.0  average = 15.0
Max skewness = 5.1  average = 1.2
Max aspect ratio = 800
Min volume = 8.5e-09
"""


def test_quality_metric_defaults():
    m = QualityMetric()
    assert m.name == ""
    assert m.value == 0.0
    assert m.status == "pass"


def test_quality_metric_status_pass():
    m = QualityMetric(value=3.0, warn_threshold=4.0, fail_threshold=10.0)
    assert m.status == "pass"


def test_quality_metric_status_warn():
    m = QualityMetric(value=5.0, warn_threshold=4.0, fail_threshold=10.0)
    assert m.status == "warn"


def test_quality_metric_status_fail():
    m = QualityMetric(value=12.0, warn_threshold=4.0, fail_threshold=10.0)
    assert m.status == "fail"


def test_quality_snapshot_defaults():
    s = QualitySnapshot()
    assert s.metrics == {}
    assert not s.passed
    assert s.iteration == 0


def test_worst_cell_defaults():
    c = WorstCell()
    assert c.index == 0
    assert c.metric == ""
    assert c.value == 0.0
    assert c.location == (0.0, 0.0, 0.0)


def test_monitor_init():
    m = QualityMonitor()
    assert m._cell_count == 0
    assert m._neg_cells == 0
    assert not m._has_fatal


def test_monitor_ingest_ok():
    m = QualityMonitor()
    m.ingest_checkmesh(CHECKMESH_OUTPUT_OK)
    assert m._cell_count == 5000
    assert m.passed
    assert m.metric("max_skewness") is not None


def test_monitor_ingest_fail():
    m = QualityMonitor()
    m.ingest_checkmesh(CHECKMESH_OUTPUT_FAIL)
    assert not m.passed
    assert m._neg_cells == 3
    assert m.summary()["neg_cells"] == 3


def test_monitor_ingest_warn():
    m = QualityMonitor()
    m.ingest_checkmesh(CHECKMESH_OUTPUT_WARN)
    assert m.passed, "Warn-only should still pass"
    assert m.metric("max_non_orthogonality").status == "warn"


def test_monitor_summary():
    m = QualityMonitor()
    m.ingest_checkmesh(CHECKMESH_OUTPUT_OK)
    s = m.summary()
    assert s["cells"] == 5000
    assert s["passed"]


def test_monitor_heatmap_data():
    m = QualityMonitor()
    m.ingest_checkmesh(CHECKMESH_OUTPUT_OK)
    h = m.heatmap_data()
    assert "skewness" in h
    assert "non_orthogonality" in h


def test_monitor_heatmap_data_fallback():
    m = QualityMonitor()
    m.ingest_checkmesh("cells: 100\nMax skewness = 3.0")
    h = m.heatmap_data()
    assert len(h["skewness"]) > 0, "Should have fallback data"


def test_monitor_worst_cells():
    m = QualityMonitor()
    m.ingest_checkmesh(CHECKMESH_OUTPUT_WARN)
    worst = m.worst_cells(n=5)
    assert isinstance(worst, list)


def test_monitor_worst_cells_empty():
    m = QualityMonitor()
    worst = m.worst_cells(n=5)
    assert worst == []


def test_monitor_histogram():
    m = QualityMonitor()
    m.ingest_checkmesh(CHECKMESH_OUTPUT_OK)
    hist = m.histogram("skewness", bins=10)
    assert "counts" in hist
    assert "mean" in hist
    assert "max" in hist


def test_monitor_histogram_empty():
    m = QualityMonitor()
    hist = m.histogram("skewness")
    assert hist["counts"] == []


def test_monitor_convergence_history():
    m = QualityMonitor()
    m.ingest_checkmesh(CHECKMESH_OUTPUT_OK)
    m.ingest_checkmesh(CHECKMESH_OUTPUT_WARN)
    hist = m.convergence_history()
    assert len(hist) == 2
    assert hist[0]["iteration"] == 0
    assert hist[1]["iteration"] == 1


def test_monitor_export_json():
    m = QualityMonitor()
    m.ingest_checkmesh(CHECKMESH_OUTPUT_OK)
    import os, json
    out = Path(os.environ.get("TEMP", "/tmp")) / "test_quality_monitor.json"
    m.export_json(out)
    data = json.loads(Path(out).read_text())
    assert "summary" in data
    assert data["summary"]["cells"] == 5000
    out.unlink(missing_ok=True)


def test_monitor_custom_thresholds():
    m = QualityMonitor(thresholds={"skewness": (2.0, 5.0)})
    m.ingest_checkmesh(CHECKMESH_OUTPUT_OK)
    assert m._thresholds["skewness"] == (2.0, 5.0)


if __name__ == "__main__":
    import json, os
    test_quality_metric_defaults()
    test_quality_metric_status_pass()
    test_quality_metric_status_warn()
    test_quality_metric_status_fail()
    test_quality_snapshot_defaults()
    test_worst_cell_defaults()
    test_monitor_init()
    test_monitor_ingest_ok()
    test_monitor_ingest_fail()
    test_monitor_ingest_warn()
    test_monitor_summary()
    test_monitor_heatmap_data()
    test_monitor_heatmap_data_fallback()
    test_monitor_worst_cells()
    test_monitor_worst_cells_empty()
    test_monitor_histogram()
    test_monitor_histogram_empty()
    test_monitor_convergence_history()
    test_monitor_export_json()
    test_monitor_custom_thresholds()
    print("ALL PASS")
