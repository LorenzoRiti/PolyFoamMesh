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
    assert MeshingAlgorithm.SNAPPY_HEX_MESH.value == "SnappyHexMesh"
    assert MeshingAlgorithm.MMG_ADAPTATION.value == "MmgAdaptation"
    assert len(MeshingAlgorithm) == 9


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


# ---------------------------------------------------------------------------
# Adaptive escalation tests
# ---------------------------------------------------------------------------

def test_adaptive_escalation_enabled_by_default():
    p = MeshEngineParams()
    assert p.adaptive_escalation


def test_adaptive_escalation_params():
    p = MeshEngineParams(adaptive_escalation=True, max_escalation_steps=5)
    assert p.adaptive_escalation
    assert p.max_escalation_steps == 5


def test_adaptive_escalation_disable():
    p = MeshEngineParams(adaptive_escalation=False)
    assert not p.adaptive_escalation


def test_quality_acceptable_all_good():
    me = MeshEngine()
    quality = {
        "neg_cells": 0,
        "max_skewness": 0.5,
        "max_non_orthogonality": 50.0,
        "max_aspect_ratio": 500.0,
    }
    assert me._quality_acceptable(quality)


def test_quality_acceptable_bad_skewness():
    me = MeshEngine()
    quality = {
        "neg_cells": 0,
        "max_skewness": 0.95,
        "max_non_orthogonality": 50.0,
        "max_aspect_ratio": 500.0,
    }
    assert not me._quality_acceptable(quality)


def test_quality_acceptable_bad_non_ortho():
    me = MeshEngine()
    quality = {
        "neg_cells": 0,
        "max_skewness": 0.5,
        "max_non_orthogonality": 75.0,
        "max_aspect_ratio": 500.0,
    }
    assert not me._quality_acceptable(quality)


def test_quality_acceptable_neg_cells():
    me = MeshEngine()
    quality = {
        "neg_cells": 5,
        "max_skewness": 0.5,
        "max_non_orthogonality": 50.0,
        "max_aspect_ratio": 500.0,
    }
    assert not me._quality_acceptable(quality)


def test_quality_acceptable_bad_aspect():
    me = MeshEngine()
    quality = {
        "neg_cells": 0,
        "max_skewness": 0.5,
        "max_non_orthogonality": 50.0,
        "max_aspect_ratio": 1500.0,
    }
    assert not me._quality_acceptable(quality)


def test_next_escalation_from_hex():
    me = MeshEngine()
    algo, reason = me._next_escalation(MeshingAlgorithm.CARTESIAN_HEX)
    assert algo == MeshingAlgorithm.HEX_CORE_POLY  # next in ladder
    assert reason


def test_next_escalation_from_tetrahedral():
    me = MeshEngine()
    algo, _ = me._next_escalation(MeshingAlgorithm.TETRAHEDRAL)
    assert algo in (
        MeshingAlgorithm.POLY_AGGREGATED,
        MeshingAlgorithm.SNAPPY_HEX_MESH,
    )  # next robust


def test_algorithm_robustness_map():
    assert MeshingAlgorithm.CARTESIAN_HEX in _mod.ALGORITHM_ROBUSTNESS
    assert MeshingAlgorithm.SNAPPY_HEX_MESH in _mod.ALGORITHM_ROBUSTNESS
    assert MeshingAlgorithm.MMG_ADAPTATION in _mod.ALGORITHM_ROBUSTNESS
    assert (_mod.ALGORITHM_ROBUSTNESS[MeshingAlgorithm.SNAPPY_HEX_MESH]
            > _mod.ALGORITHM_ROBUSTNESS[MeshingAlgorithm.TETRAHEDRAL])


def test_escalation_ladder():
    """Verify that the escalation ladder entries all map to valid algorithms."""
    for algo, _ in _mod.ESCALATION_LADDER:
        assert algo in MeshingAlgorithm


def test_adaptive_thresholds_defined():
    """Verify all required thresholds are defined."""
    for key in ("non_ortho_max", "skewness_max", "aspect_ratio_max"):
        assert key in _mod.ADAPTIVE_THRESHOLDS
        assert _mod.ADAPTIVE_THRESHOLDS[key] > 0


def test_keep_hex_decision_is_visible():
    """Fase 3 P3.1: a polyDualMesh failure after cartesianMesh must record a
    visible keep-hex decision (result.warnings + event), never a silent keep."""
    engine = MeshEngine()
    result = MeshEngineResult(algorithm="Polyhedral", case_dir="/tmp/case")
    engine._keep_hex_decision(
        result, MeshingAlgorithm.POLYHEDRAL,
        RuntimeError("polyDualMesh failed (exit 1)"),
        reason="polyDualMesh failed after cartesianMesh — keeping the hex mesh",
    )
    assert result.warnings, "keep-hex decision must land in result.warnings"
    assert "keep" in result.warnings[0].lower()
    assert "Polyhedral" in result.warnings[0]


# ---------------------------------------------------------------------------
# SnappyHexMesh algorithm info
# ---------------------------------------------------------------------------
def test_algorithm_info_snappy():
    assert MeshingAlgorithm.SNAPPY_HEX_MESH in ALGORITHM_INFO
    info = ALGORITHM_INFO[MeshingAlgorithm.SNAPPY_HEX_MESH]
    assert "SnappyHexMesh" in info["label"]
    assert info["requires_wsl"]


def test_algorithm_info_mmg():
    assert MeshingAlgorithm.MMG_ADAPTATION in ALGORITHM_INFO
    info = ALGORITHM_INFO[MeshingAlgorithm.MMG_ADAPTATION]
    assert "MMG" in info["label"]
    assert info["requires_wsl"]


# ---------------------------------------------------------------------------
# MeshEngineResult extended fields
# ---------------------------------------------------------------------------
def test_mesh_engine_result_escalation_fields():
    r = MeshEngineResult()
    assert r.escalation_reason == ""
    assert r.escalation_steps == 0
    assert r.metrics_before is None
    assert r.metrics_after is None
    assert r.original_algorithm == ""


def test_mesh_engine_result_non_ortho_field():
    r = MeshEngineResult(max_non_orthogonality=65.0)
    assert r.max_non_orthogonality == 65.0
    r.max_non_orthogonality = 70.0
    assert r.max_non_orthogonality == 70.0


def test_mesh_engine_result_neg_cells():
    r = MeshEngineResult(neg_cells=3)
    assert r.neg_cells == 3


# ---------------------------------------------------------------------------
# Verification module test
# ---------------------------------------------------------------------------
def test_verification_metrics_score():
    from _test_helpers import load_commercial_module
    vmod = load_commercial_module("verification")
    m = vmod.AlgorithmMetrics(
        algorithm="CartesianHex",
        cell_count=10000,
        max_skewness=0.8,
        max_non_ortho=60.0,
        max_aspect_ratio=500.0,
        passed=True,
    )
    score = m.quality_score()
    assert 0 < score < 2.0


def test_verification_metrics_score_with_neg_cells():
    from _test_helpers import load_commercial_module
    vmod = load_commercial_module("verification")
    m = vmod.AlgorithmMetrics(
        algorithm="Tetrahedral",
        cell_count=5000,
        max_skewness=0.5,
        max_non_ortho=30.0,
        max_aspect_ratio=200.0,
        neg_cells=5,
        passed=False,
    )
    score = m.quality_score()
    # Neg cells weight = 0.2 * 5 = 1.0, so score >= 1.0
    assert score >= 1.0


if __name__ == "__main__":
    test_meshing_algorithm_enum()
    test_algorithm_info_all_present()
    test_algorithm_info_hex()
    test_algorithm_info_tetrahedral()
    test_algorithm_info_snappy()
    test_algorithm_info_mmg()
    test_mesh_engine_params_defaults()
    test_mesh_engine_params_custom()
    test_cell_size_multiplier()
    test_detail_label()
    test_mesh_engine_result_defaults()
    test_mesh_engine_result_escalation_fields()
    test_mesh_engine_result_non_ortho_field()
    test_mesh_engine_result_neg_cells()
    test_mesh_engine_init()
    test_mesh_engine_configure()
    test_auto_select_no_wsl()
    test_auto_select_with_wsl()
    test_auto_select_not_watertight()
    test_auto_select_cht_solver()
    test_auto_select_many_patches()
    test_run_no_case()
    test_params_cell_size_validation()
    test_adaptive_escalation_enabled_by_default()
    test_adaptive_escalation_params()
    test_adaptive_escalation_disable()
    test_quality_acceptable_all_good()
    test_quality_acceptable_bad_skewness()
    test_quality_acceptable_bad_non_ortho()
    test_quality_acceptable_neg_cells()
    test_quality_acceptable_bad_aspect()
    test_next_escalation_from_hex()
    test_next_escalation_from_tetrahedral()
    test_algorithm_robustness_map()
    test_escalation_ladder()
    test_adaptive_thresholds_defined()
    test_keep_hex_decision_is_visible()
    test_verification_metrics_score()
    test_verification_metrics_score_with_neg_cells()
    print("ALL PASS")
