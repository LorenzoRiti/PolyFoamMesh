"""Tests for the one-click full auto pipeline."""
from __future__ import annotations
import sys
import types
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


# ---------------------------------------------------------------------------
# Algorithm substitution transparency
# ---------------------------------------------------------------------------
def test_full_auto_result_substitution_fields():
    r = FullAutoResult()
    assert not r.algorithm_substituted
    assert r.original_algorithm == ""
    assert r.escalation_reason == ""


def test_pipeline_records_algorithm_substitution(monkeypatch, tmp_path):
    """Hermetic: engine escalation info must reach the FullAutoResult."""
    p = FullAutoPipeline()
    qm_result = types.SimpleNamespace(
        success=True, case_dir=str(tmp_path), algorithm="Tetrahedral",
        original_algorithm="CartesianHex", algorithm_substituted=True,
        escalation_reason="quality below threshold",
        cell_count=1000, quality_passed=True, warnings=[],
    )
    monkeypatch.setattr(
        p, "_step_import_heal",
        lambda geo: types.SimpleNamespace(
            success=True, meshes=[],
            geometry=types.SimpleNamespace(n_patches=1, bbox_max=1.0),
        ),
    )
    monkeypatch.setattr(p, "_step_mesh", lambda *a, **k: qm_result)
    monkeypatch.setattr(
        p, "_step_quality_fix",
        lambda cd: types.SimpleNamespace(passed=True, metrics=None),
    )
    monkeypatch.setattr(p, "_step_bc", lambda cd, auto_bc: [])
    monkeypatch.setattr(p, "_step_solver", lambda cd, tpl, bbox: [])
    monkeypatch.setattr(p, "_step_report", lambda cd, m, q: [])

    result = p.run(str(tmp_path / "model.step"))

    assert result.success
    assert result.algorithm_substituted
    assert result.original_algorithm == "CartesianHex"
    assert result.escalation_reason == "quality below threshold"
    assert result.algorithm_used == "Tetrahedral"


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
