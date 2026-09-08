"""Tests for the quick mesh module."""
from __future__ import annotations
import sys
import types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("quick_mesh")
QuickMeshResult = _mod.QuickMeshResult
QuickMesh = _mod.QuickMesh


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


# ---------------------------------------------------------------------------
# Algorithm substitution transparency
# ---------------------------------------------------------------------------
def test_quick_mesh_result_substitution_fields():
    r = QuickMeshResult()
    assert not r.algorithm_substituted
    assert r.original_algorithm == ""
    assert r.escalation_reason == ""


def test_run_records_algorithm_substitution(monkeypatch, tmp_path):
    """Hermetic: engine escalation info must reach the QuickMeshResult."""
    import polyfoammesh.commercial.mesh_engine as _me_mod
    import polyfoammesh.core.openfoam_runner as _ofr

    qm = QuickMesh()
    qm._of_config = types.SimpleNamespace(validate=lambda: True)

    fake_engine = types.SimpleNamespace(
        auto_select=lambda **kw: _me_mod.MeshingAlgorithm.CARTESIAN_HEX,
        configure=lambda params: None,
        run=lambda *a, **k: _me_mod.MeshEngineResult(
            success=True, algorithm="Tetrahedral",
            original_algorithm="CartesianHex",
            escalation_reason="quality below threshold",
            cell_count=1000, quality_passed=True, max_skewness=0.5,
        ),
    )
    monkeypatch.setattr(_me_mod, "MeshEngine", lambda *a, **k: fake_engine)

    monkeypatch.setattr(
        _mod, "validate_geometry_path",
        lambda p: types.SimpleNamespace(valid=True, message=""),
    )
    monkeypatch.setattr(
        qm, "_import_geometry",
        lambda p, ext: [types.SimpleNamespace(
            is_watertight=True, metadata={"name": "wall"})],
    )
    monkeypatch.setattr(_mod, "_lazy_geom", lambda: types.SimpleNamespace(
        compute_bbox_dim=lambda m: 1.0,
        suggest_cell_sizes=lambda m, detail: (0.1, 0.05),
    ))
    monkeypatch.setattr(qm, "_auto_bl_params", lambda m, b, w: None)
    monkeypatch.setattr(_mod, "_lazy_stl", lambda: types.SimpleNamespace(
        export_surface_file=lambda m, cd: None))
    monkeypatch.setattr(_mod, "_lazy_md", lambda: types.SimpleNamespace(
        write_meshdict=lambda *a, **k: None))
    monkeypatch.setattr(_mod, "_write_ctrl_dict", lambda cd: None)
    monkeypatch.setattr(_ofr, "generate_fms", lambda cd, angle=60.0: False)

    result = qm.run(str(tmp_path / "model.stl"), output_dir=str(tmp_path / "case"))

    assert result.success
    assert result.algorithm_substituted
    assert result.original_algorithm == "CartesianHex"
    assert result.escalation_reason == "quality below threshold"
    assert result.algorithm == "Tetrahedral"


if __name__ == "__main__":
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
