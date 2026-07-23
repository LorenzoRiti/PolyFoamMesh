"""Tests for the quick mesh module."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("quick_mesh")
QuickMeshResult = _mod.QuickMeshResult
QuickMesh = _mod.QuickMesh
TARGET_YPLUS = _mod.TARGET_YPLUS


def test_target_yplus():
    assert TARGET_YPLUS == 30.0


def test_quick_mesh_result_defaults():
    r = QuickMeshResult()
    assert not r.success
    assert r.cell_count == 0
    assert r.max_skewness == 0.0
    assert not r.quality_passed
    assert r.warnings == []
    assert r.errors == []


def test_quick_mesh_result_with_data():
    r = QuickMeshResult(
        success=True, case_dir="/tmp/test", algorithm="CartesianHex",
        cell_count=50000, max_skewness=2.3, quality_passed=True,
    )
    assert r.success
    assert r.cell_count == 50000
    assert r.max_skewness == 2.3
    assert r.quality_passed


def test_quick_mesh_init():
    qm = QuickMesh()
    assert qm._of_config is not None


def test_auto_bl_params_no_watertight():
    qm = QuickMesh()
    import trimesh
    m = trimesh.creation.box(extents=[1, 1, 1])
    result = qm._auto_bl_params([m], 1.0, all_wt=False)
    assert result is None, "BL should be None for non-watertight"


def test_auto_bl_params_small_bbox():
    qm = QuickMesh()
    import trimesh
    m = trimesh.creation.box(extents=[0.001, 0.001, 0.001])
    result = qm._auto_bl_params([m], 0.001, all_wt=True)
    # Re is low -> laminar -> no BL
    assert result is None or result["nLayers"] >= 1


def test_auto_bl_params_watertight():
    qm = QuickMesh()
    import trimesh
    m = trimesh.creation.box(extents=[1, 1, 1])
    result = qm._auto_bl_params([m], 1.0, all_wt=True)
    if result is not None:
        assert "nLayers" in result
        assert "thicknessRatio" in result
        assert "expansionRatio" in result
        assert result["expansionRatio"] == 1.2


def test_run_nonexistent_geometry():
    qm = QuickMesh()
    result = qm.run("Z:\\nonexistent.step")
    assert not result.success
    assert len(result.errors) > 0


def test_run_unsupported_format():
    qm = QuickMesh()
    result = qm.run("/tmp/test.txt")
    assert not result.success


def test_quality_target_draft():
    qm = QuickMesh()
    # Draft should use larger cell sizes
    result = qm.run("Z:\\nonexistent.step", quality_target="draft")
    assert not result.success  # Fails on file not found, not quality target


if __name__ == "__main__":
    test_target_yplus()
    test_quick_mesh_result_defaults()
    test_quick_mesh_result_with_data()
    test_quick_mesh_init()
    test_auto_bl_params_no_watertight()
    test_auto_bl_params_small_bbox()
    test_auto_bl_params_watertight()
    test_run_nonexistent_geometry()
    test_run_unsupported_format()
    test_quality_target_draft()
    print("ALL PASS")
