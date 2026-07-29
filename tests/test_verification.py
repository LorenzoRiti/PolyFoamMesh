"""Tests for verification framework."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("verification")
AlgorithmMetrics = _mod.AlgorithmMetrics
VerificationReport = _mod.VerificationReport
VerificationSuite = _mod.VerificationSuite


def test_algorithm_metrics_defaults():
    m = AlgorithmMetrics()
    assert m.algorithm == ""
    assert m.cell_count == 0
    assert m.max_skewness == 0.0
    assert not m.passed


def test_algorithm_metrics_summary():
    m = AlgorithmMetrics(
        algorithm="CartesianHex",
        cell_count=10000,
        max_skewness=0.5,
        max_non_ortho=45.0,
        max_aspect_ratio=300.0,
        wall_time_s=30.0,
        passed=True,
    )
    s = m.summary()
    assert "CartesianHex" in s
    assert "10,000" in s
    assert "PASS" in s


def test_algorithm_metrics_score_perfect():
    m = AlgorithmMetrics(passed=True, cell_count=5000)
    score = m.quality_score()
    assert score == 0.0


def test_algorithm_metrics_score_imperfect():
    m = AlgorithmMetrics(
        max_skewness=0.9,
        max_non_ortho=70.0,
        max_aspect_ratio=1000.0,
        neg_cells=1,
    )
    score = m.quality_score()
    # skew_factor = 1.0 (0.9/0.9), northo_factor = 1.0, aspect_factor = 1.0
    # score = 0.3*1.0 + 0.3*1.0 + 0.2*1.0 + 0.2*1 = 1.0
    assert 0.9 <= score <= 1.1


def test_verification_report_defaults():
    r = VerificationReport()
    assert r.geometry_name == ""
    assert r.algorithms_tested == []
    assert r.results == []
    assert r.best_algorithm == ""


def test_verification_report_best_algorithm():
    m1 = AlgorithmMetrics(algorithm="A", passed=False, max_skewness=0.8, max_non_ortho=60.0, max_aspect_ratio=400.0)
    m2 = AlgorithmMetrics(algorithm="B", passed=True, max_skewness=0.3, max_non_ortho=25.0, max_aspect_ratio=100.0)

    r = VerificationReport(
        geometry_name="test",
        algorithms_tested=["A", "B"],
        results=[m1, m2],
        best_algorithm="B",
        best_score=m2.quality_score(),
    )
    assert r.best_algorithm == "B"
    assert r.best_score < m1.quality_score()


def test_verification_report_table():
    m = AlgorithmMetrics(algorithm="Test", cell_count=1000, passed=True)
    r = VerificationReport(
        geometry_name="duct",
        algorithms_tested=["Test"],
        results=[m],
        best_algorithm="Test",
        best_score=m.quality_score(),
    )
    table = r.print_table()
    assert "VERIFICATION REPORT" in table
    assert "Test" in table


def test_verification_suite_init():
    class MockConfig:
        pass

    suite = VerificationSuite(MockConfig())
    assert suite is not None


def test_generate_test_geometries(tmp_path):
    class MockConfig:
        pass

    suite = VerificationSuite(MockConfig())
    geoms = suite.generate_test_geometries(tmp_path)

    assert "duct" in geoms
    assert "sharp_box" in geoms
    assert "complex_pipe" in geoms

    for name, path in geoms.items():
        assert path.exists(), f"Missing: {name}"
        assert path.suffix == ".stl"
        assert path.stat().st_size > 0, f"Empty STL: {name}"


def test_export_report(tmp_path):
    r = VerificationReport(
        geometry_name="duct",
        best_algorithm="CartesianHex",
        best_score=0.5,
    )
    out = tmp_path / "report.json"

    class MockConfig:
        pass
    suite = VerificationSuite(MockConfig())
    suite.export_report(r, out)

    assert out.exists()
    import json
    data = json.loads(out.read_text())
    assert data["geometry"] == "duct"
    assert data["best_algorithm"] == "CartesianHex"


if __name__ == "__main__":
    test_algorithm_metrics_defaults()
    test_algorithm_metrics_summary()
    test_algorithm_metrics_score_perfect()
    test_algorithm_metrics_score_imperfect()
    test_verification_report_defaults()
    test_verification_report_best_algorithm()
    test_verification_report_table()
    test_verification_suite_init()
    test_generate_test_geometries(None)
    test_export_report(None)
    print("ALL PASS")
