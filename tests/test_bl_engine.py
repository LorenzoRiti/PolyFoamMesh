"""Tests for the Boundary Layer Quality Engine."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("bl_engine")
BLParameters = _mod.BLParameters
BLCollisionReport = _mod.BLCollisionReport
BLQualityMetrics = _mod.BLQualityMetrics
BLEngine = _mod.BLEngine
FlowConditions = _mod.FlowConditions


def test_bl_parameters_defaults():
    p = BLParameters()
    assert p.first_layer_height == 0.0
    assert p.n_layers == 10
    assert p.growth_rate == 1.2


def test_bl_collision_report_defaults():
    r = BLCollisionReport()
    assert r.overlap_ratio == 0.0
    assert r.status == "ok"


def test_bl_quality_metrics_defaults():
    m = BLQualityMetrics()
    assert m.avg_skewness == 0.0
    assert m.max_skewness == 0.0
    assert m.passed()


def test_bl_quality_metrics_fails():
    m = BLQualityMetrics(max_skewness=8.0)
    assert not m.passed({"skewness_max": 4.0})


def test_flow_conditions_defaults():
    f = FlowConditions()
    assert f.reynolds_number == 1e6
    assert f.turbulence_model == "kOmegaSST"


def test_calculate_from_flow():
    bl = BLEngine()
    flow = FlowConditions(reynolds_number=1e7, reference_velocity=10.0, reference_length=1.0)
    params = bl.calculate_from_flow(flow)
    assert params.first_layer_height > 0, "First layer height should be positive"
    assert params.n_layers >= 1
    assert 1.0 < params.growth_rate <= 2.0
    assert params.target_yplus == 1.0
    assert params.estimated_yplus == 1.0


def test_calculate_from_flow_high_re():
    bl = BLEngine()
    flow = FlowConditions(reynolds_number=1e8, reference_velocity=50.0)
    params = bl.calculate_from_flow(flow)
    assert params.first_layer_height > 0
    assert params.total_thickness > params.first_layer_height


def test_calculate_from_flow_invalid_re():
    bl = BLEngine()
    flow = FlowConditions(reynolds_number=0)
    try:
        bl.calculate_from_flow(flow)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_detect_wall_patches():
    bl = BLEngine()
    names = ["inlet", "outlet", "wall", "wall_001", "symmetry"]
    walls = bl.detect_wall_patches(names)
    assert "wall" in walls
    assert "wall_001" in walls
    assert "inlet" not in walls


def test_detect_wall_patches_fallback():
    bl = BLEngine()
    names = ["inlet", "outlet", "symmetry"]
    walls = bl.detect_wall_patches(names)
    assert len(walls) == len(names), "Should fallback to all patches"


def test_detect_collisions_ok():
    bl = BLEngine()
    params = BLParameters(total_thickness=0.01)
    reports = bl.detect_collisions(params, cell_size=0.1, patch_names=["wall"])
    assert len(reports) == 1
    assert reports[0].status == "ok"


def test_detect_collisions_warning():
    bl = BLEngine()
    params = BLParameters(total_thickness=0.06)
    reports = bl.detect_collisions(params, cell_size=0.1, patch_names=["wall"])
    assert reports[0].status == "warning"


def test_detect_collisions_critical():
    bl = BLEngine()
    params = BLParameters(total_thickness=0.09)
    reports = bl.detect_collisions(params, cell_size=0.1, patch_names=["wall"])
    assert reports[0].status == "critical"


def test_suggest_remedy_ok():
    bl = BLEngine()
    r = BLCollisionReport(status="ok")
    assert "No action" in bl.suggest_remedy(r)


def test_suggest_remedy_critical():
    bl = BLEngine()
    r = BLCollisionReport(patch_name="wall", status="critical", overlap_ratio=0.9)
    assert "CRITICAL" in bl.suggest_remedy(r)


def test_yplus_from_height():
    yp = BLEngine.yplus_from_height(height=0.0001, u_ref=10.0, nu=1.5e-5, length=1.0)
    assert yp > 0, f"y+ should be positive, got {yp}"


def test_suggest_n_layers():
    n = BLEngine.suggest_n_layers(target_thickness=0.05, first_height=0.001)
    assert 1 <= n <= 50


if __name__ == "__main__":
    test_bl_parameters_defaults()
    test_bl_collision_report_defaults()
    test_bl_quality_metrics_defaults()
    test_bl_quality_metrics_fails()
    test_flow_conditions_defaults()
    test_calculate_from_flow()
    test_calculate_from_flow_high_re()
    test_calculate_from_flow_invalid_re()
    test_detect_wall_patches()
    test_detect_wall_patches_fallback()
    test_detect_collisions_ok()
    test_detect_collisions_warning()
    test_detect_collisions_critical()
    test_suggest_remedy_ok()
    test_suggest_remedy_critical()
    test_yplus_from_height()
    test_suggest_n_layers()
    print("ALL PASS")
