"""Tests for the multi-algorithm mesh engine."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("mesh_engine")
MeshingAlgorithm = _mod.MeshingAlgorithm
ALGORITHM_INFO = _mod.ALGORITHM_INFO
MeshEngineParams = _mod.MeshEngineParams
MeshEngineResult = _mod.MeshEngineResult
MeshEngine = _mod.MeshEngine


def test_meshing_algorithm_enum():
    assert MeshingAlgorithm.CARTESIAN_HEX.value == "CartesianHex"
    assert MeshingAlgorithm.POLYHEDRAL.value == "Polyhedral"
    assert MeshingAlgorithm.TETRAHEDRAL.value == "Tetrahedral"
    assert len(MeshingAlgorithm) == 5


def test_algorithm_info_all_present():
    for algo in MeshingAlgorithm:
        assert algo in ALGORITHM_INFO
        assert "label" in ALGORITHM_INFO[algo]
        assert "requires_wsl" in ALGORITHM_INFO[algo]


def test_algorithm_info_hex():
    info = ALGORITHM_INFO[MeshingAlgorithm.CARTESIAN_HEX]
    assert "cartesian" in info["label"].lower()
    assert info["requires_wsl"]
    assert info["quality_rank"] == 1


def test_algorithm_info_tetrahedral():
    info = ALGORITHM_INFO[MeshingAlgorithm.TETRAHEDRAL]
    assert not info["requires_wsl"]


def test_mesh_engine_params_defaults():
    p = MeshEngineParams()
    assert p.algorithm == MeshingAlgorithm.CARTESIAN_HEX
    assert p.detail_level == "medium"
    assert p.max_cell == 0.05
    assert p.bl_enabled


def test_mesh_engine_params_custom():
    p = MeshEngineParams(
        algorithm=MeshingAlgorithm.TETRAHEDRAL,
        detail_level="fine",
        max_cell=0.02,
        bl_enabled=False,
    )
    assert p.algorithm == MeshingAlgorithm.TETRAHEDRAL
    assert p.detail_level == "fine"
    assert p.max_cell == 0.02
    assert not p.bl_enabled


def test_cell_size_multiplier():
    p = MeshEngineParams()
    assert p.cell_size_multiplier == 1.0
    p.detail_level = "very_fine"
    assert p.cell_size_multiplier == 0.3
    p.detail_level = "very_coarse"
    assert p.cell_size_multiplier == 3.0


def test_detail_label():
    p = MeshEngineParams()
    assert p.detail_label == "Media"
    p.detail_level = "very_fine"
    assert p.detail_label == "Molto Fine"
    p.detail_level = "coarse"
    assert p.detail_label == "Grossolana"


def test_mesh_engine_result_defaults():
    r = MeshEngineResult()
    assert not r.success
    assert r.cell_count == 0
    assert r.warnings == []
    assert r.errors == []


def test_mesh_engine_init():
    me = MeshEngine()
    assert me._params.algorithm == MeshingAlgorithm.CARTESIAN_HEX
    assert me._of_config is not None


def test_mesh_engine_configure():
    me = MeshEngine()
    p = MeshEngineParams(algorithm=MeshingAlgorithm.POLYHEDRAL, detail_level="fine")
    me.configure(p)
    assert me._params.algorithm == MeshingAlgorithm.POLYHEDRAL
    assert me._params.detail_level == "fine"


def test_auto_select_no_wsl():
    me = MeshEngine()
    algo = me.auto_select(has_wsl=False)
    assert algo == MeshingAlgorithm.TETRAHEDRAL


def test_auto_select_with_wsl():
    me = MeshEngine()
    algo = me.auto_select(watertight=True, has_wsl=True)
    assert algo in (MeshingAlgorithm.CARTESIAN_HEX, MeshingAlgorithm.HEX_CORE_POLY)


def test_auto_select_not_watertight():
    me = MeshEngine()
    algo = me.auto_select(watertight=False, has_wsl=True)
    assert algo == MeshingAlgorithm.TETRAHEDRAL


def test_auto_select_cht_solver():
    me = MeshEngine()
    algo = me.auto_select(solver="chtMultiRegionFoam", has_wsl=True)
    assert algo == MeshingAlgorithm.CARTESIAN_HEX


def test_auto_select_many_patches():
    me = MeshEngine()
    algo = me.auto_select(patch_count=100, watertight=True, has_wsl=True)
    assert algo == MeshingAlgorithm.TETRAHEDRAL


def test_run_no_case():
    me = MeshEngine()
    result = me.run("Z:\\nonexistent")
    assert not result.success
    assert len(result.errors) > 0


def test_params_cell_size_validation():
    p = MeshEngineParams(max_cell=0.05, min_cell=0.01)
    assert p.max_cell == 0.05
    assert p.min_cell == 0.01
    assert p.min_cell < p.max_cell


if __name__ == "__main__":
    test_meshing_algorithm_enum()
    test_algorithm_info_all_present()
    test_algorithm_info_hex()
    test_algorithm_info_tetrahedral()
    test_mesh_engine_params_defaults()
    test_mesh_engine_params_custom()
    test_cell_size_multiplier()
    test_detail_label()
    test_mesh_engine_result_defaults()
    test_mesh_engine_init()
    test_mesh_engine_configure()
    test_auto_select_no_wsl()
    test_auto_select_with_wsl()
    test_auto_select_not_watertight()
    test_auto_select_cht_solver()
    test_auto_select_many_patches()
    test_run_no_case()
    test_params_cell_size_validation()
    print("ALL PASS")
