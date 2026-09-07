"""Tests for the one-click full auto pipeline."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("one_click_run")
FullAutoResult = _mod.FullAutoResult
FullAutoPipeline = _mod.FullAutoPipeline


def test_full_auto_result_defaults():
    r = FullAutoResult()
    assert not r.success
    assert r.cell_count == 0
    assert r.steps_completed == []
    assert r.errors == []
    assert r.warnings == []


def test_full_auto_result_with_data():
    r = FullAutoResult(
        success=True, case_dir="/tmp/test", algorithm_used="CartesianHex",
        cell_count=50000, quality_passed=True,
        steps_completed=["import", "mesh", "quality", "bc", "solver", "report"],
    )
    assert r.success
    assert r.cell_count == 50000
    assert r.quality_passed
    assert len(r.steps_completed) == 6


def test_summary_success():
    r = FullAutoResult(
        success=True, cell_count=50000, n_patches=8,
        quality_passed=True, algorithm_used="CartesianHex",
        steps_completed=["a", "b", "c"], wall_time_s=120.5,
    )
    s = r.summary()
    assert "✅" in s
    assert "50,000" in s
    assert "120s" in s


def test_summary_failure():
    r = FullAutoResult(success=False, wall_time_s=5.0)
    s = r.summary()
    assert "❌" in s


def test_pipeline_init():
    p = FullAutoPipeline()
    assert p._of_config is not None


def test_pipeline_run_nonexistent():
    p = FullAutoPipeline()
    result = p.run("Z:\\nonexistent.step")
    assert not result.success
    assert len(result.errors) > 0


def test_pipeline_result_steps_on_failure():
    p = FullAutoPipeline()
    result = p.run("Z:\\nonexistent.step")
    # Should have 0 steps completed (import fails first)
    assert len(result.steps_completed) <= 1


def test_pipeline_result_quality_target():
    p = FullAutoPipeline()
    r = p.run("Z:\\nonexistent.stl", quality_target="high")
    assert r.quality_target == "high"


def test_pipeline_result_preserves_geometry():
    p = FullAutoPipeline()
    r = p.run("Z:\\nonexistent.step", quality_target="draft")
    assert r.geometry_file == "Z:\\nonexistent.step"


def test_full_auto_result_default_quality():
    r = FullAutoResult()
    assert r.quality_target == "medium"


if __name__ == "__main__":
    test_full_auto_result_defaults()
    test_full_auto_result_with_data()
    test_summary_success()
    test_summary_failure()
    test_pipeline_init()
    test_pipeline_run_nonexistent()
    test_pipeline_result_steps_on_failure()
    test_pipeline_result_quality_target()
    test_pipeline_result_preserves_geometry()
    test_full_auto_result_default_quality()
    print("ALL PASS")
