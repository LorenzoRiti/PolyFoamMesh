"""Tests for the quality engine."""
from __future__ import annotations
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("quality_engine")
QualityMetrics = _mod.QualityMetrics
AutoFixAction = _mod.AutoFixAction
QualityReport = _mod.QualityReport
QualityEngine = _mod.QualityEngine
THRESHOLDS = _mod.THRESHOLDS


CHECKMESH_OK = """cells: 5000; Max non-orthogonality = 45.2 average = 12.1; Max skewness = 2.3 average = 0.8; Max aspect ratio = 450; Min volume = 1.2e-08; Mesh OK."""

CHECKMESH_FAIL = """cells: 5000; Max non-orthogonality = 85.0 average = 18.3; Max skewness = 8.5 average = 1.5; Max aspect ratio = 2000; There are 3 cells with negative volume. Min volume = -5.3e-10; Mesh NOT OK."""


def test_thresholds():
    assert THRESHOLDS["skewness_max"] == 0.9
    assert THRESHOLDS["non_ortho_max"] == 70.0
    assert THRESHOLDS["aspect_ratio_max"] == 1000.0


def test_quality_metrics_defaults():
    m = QualityMetrics()
    assert m.max_skewness == 0.0
    assert m.neg_cells == 0
    assert not m.has_fatal


def test_quality_metrics_passed():
    m = QualityMetrics(max_skewness=0.5, max_non_orthogonality=40.0, max_aspect_ratio=500)
    assert m.passed


def test_quality_metrics_failed_skew():
    m = QualityMetrics(max_skewness=2.0, max_non_orthogonality=40.0, max_aspect_ratio=500)
    assert not m.passed


def test_quality_metrics_failed_neg():
    m = QualityMetrics(neg_cells=2)
    assert not m.passed


def test_auto_fix_action():
    a = AutoFixAction(action="relax", target_metric="skewness", current_value=2.0)
    assert a.action == "relax"
    assert a.target_metric == "skewness"


def test_quality_report_defaults():
    r = QualityReport()
    assert not r.passed
    assert r.fixes_applied == []
    assert r.warnings == []


def test_quality_report_summary_pass():
    m = QualityMetrics(cells=5000, max_skewness=0.5)
    r = QualityReport(metrics=m, passed=True)
    s = r.summary()
    assert "PASS" in s
    assert "5,000" in s


def test_quality_report_summary_fail():
    m = QualityMetrics(cells=5000, max_skewness=5.0, neg_cells=2)
    r = QualityReport(metrics=m, passed=False)
    s = r.summary()
    assert "FAIL" in s


def test_engine_init():
    qe = QualityEngine()
    assert qe is not None


def test_parse_metrics_ok():
    qe = QualityEngine()
    m = qe._parse_metrics(CHECKMESH_OK)
    assert m.cells == 5000
    assert m.max_skewness == 2.3
    assert m.max_non_orthogonality == 45.2
    assert m.max_aspect_ratio == 450
    assert m.neg_cells == 0
    assert not m.has_fatal


def test_parse_metrics_fail():
    qe = QualityEngine()
    m = qe._parse_metrics(CHECKMESH_FAIL)
    assert m.max_skewness == 8.5
    assert m.neg_cells == 3
    assert m.max_aspect_ratio == 2000


def test_status_text_pass():
    m = QualityMetrics(max_skewness=0.5, max_non_orthogonality=40, max_aspect_ratio=500)
    assert QualityEngine._status_text(m) == "PASS"


def test_status_text_fail():
    m = QualityMetrics(max_skewness=5.0, max_non_orthogonality=80, max_aspect_ratio=500)
    txt = QualityEngine._status_text(m)
    assert "FAIL" not in txt  # No neg cells, so it's a WARN
    assert "skew" in txt


def test_decide_fixes_none():
    qe = QualityEngine()
    m = QualityMetrics(max_skewness=0.5, max_non_orthogonality=40, max_aspect_ratio=500)
    fixes = qe._decide_fixes(m)
    assert len(fixes) == 0


def test_decide_fixes_skew():
    qe = QualityEngine()
    m = QualityMetrics(max_skewness=2.0, max_non_orthogonality=40, max_aspect_ratio=500)
    fixes = qe._decide_fixes(m)
    assert any(f.action == "relax" for f in fixes)


def test_decide_fixes_non_ortho():
    qe = QualityEngine()
    m = QualityMetrics(max_skewness=0.5, max_non_orthogonality=80, max_aspect_ratio=500)
    fixes = qe._decide_fixes(m)
    assert any(f.action == "reduce_bl" for f in fixes)


def test_decide_fixes_neg_vol():
    qe = QualityEngine()
    m = QualityMetrics(neg_cells=5)
    fixes = qe._decide_fixes(m)
    assert any(f.action == "remesh" for f in fixes)


def test_decide_fixes_aspect():
    qe = QualityEngine()
    m = QualityMetrics(max_aspect_ratio=2000)
    fixes = qe._decide_fixes(m)
    assert any(f.action == "split" for f in fixes)


def test_relax_cell_sizes():
    text = "maxCellSize 0.05;\nminCellSize 0.01;\n"
    result = QualityEngine._relax_cell_sizes(text)
    assert "0.060000" in result  # 0.05 * 1.2
    assert "0.008333" in result  # 0.01 / 1.2


def test_reduce_boundary_layers():
    text = "maxCellSize 0.05;\nboundaryLayers\n{\n    nLayers 20;\n    thicknessRatio 1.2;\n}\n"
    result = QualityEngine._reduce_boundary_layers(text)
    assert "nLayers                 10;" in result
    assert "thicknessRatio          1.0100;" in result


def test_disable_boundary_layers():
    text = "maxCellSize 0.05;\nboundaryLayers\n{\n    nLayers 3;\n}\nminCellSize 0.01;\n"
    result = QualityEngine._disable_boundary_layers(text)
    assert "boundaryLayers" not in result


def test_reduce_max_cell():
    text = "maxCellSize 0.05;\n"
    result = QualityEngine._reduce_max_cell(text)
    assert "0.035000" in result  # 0.05 * 0.7


def test_export_json():
    import tempfile as _tf
    qe = QualityEngine()
    m = QualityMetrics(cells=5000, max_skewness=2.3)
    r = QualityReport(metrics=m, passed=True)
    out = Path(_tf.gettempdir()) / "test_quality_report.json"
    qe.export_json(r, out)
    data = json.loads(out.read_text())
    assert data["metrics"]["cells"] == 5000
    assert data["passed"]
    out.unlink(missing_ok=True)


if __name__ == "__main__":
    test_thresholds()
    test_quality_metrics_defaults()
    test_quality_metrics_passed()
    test_quality_metrics_failed_skew()
    test_quality_metrics_failed_neg()
    test_auto_fix_action()
    test_quality_report_defaults()
    test_quality_report_summary_pass()
    test_quality_report_summary_fail()
    test_engine_init()
    test_parse_metrics_ok()
    test_parse_metrics_fail()
    test_status_text_pass()
    test_status_text_fail()
    test_decide_fixes_none()
    test_decide_fixes_skew()
    test_decide_fixes_non_ortho()
    test_decide_fixes_neg_vol()
    test_decide_fixes_aspect()
    test_relax_cell_sizes()
    test_disable_boundary_layers()
    test_reduce_max_cell()
    test_export_json()
    print("ALL PASS")
