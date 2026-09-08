"""Tests for the Mosaic / Poly-Hedral Connectivity module."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module, space_free_tmp_root

_mod = load_commercial_module("mosaic")
MosaicParams = _mod.MosaicParams
MosaicResult = _mod.MosaicResult
MosaicEngine = _mod.MosaicEngine
MAX_VOLUME_RATIO = _mod.MAX_VOLUME_RATIO


def test_mosaic_params_defaults():
    p = MosaicParams()
    assert p.n_smoothing_iterations == 3
    assert p.volume_ratio_target == 8.0
    assert p.preserve_original
    assert p.poly_dual_aggregation


def test_mosaic_params_custom():
    p = MosaicParams(n_smoothing_iterations=5, volume_ratio_target=6.0)
    assert p.n_smoothing_iterations == 5
    assert p.volume_ratio_target == 6.0


def test_mosaic_result_defaults():
    r = MosaicResult()
    assert not r.success
    assert r.hex_cells == 0
    assert r.poly_cells == 0
    assert r.cell_reduction_pct == 0.0
    assert r.volume_ratio == 0.0
    assert r.errors == []


def test_mosaic_result_with_data():
    r = MosaicResult(success=True, hex_cells=500000, poly_cells=120000)
    assert r.success
    assert r.hex_cells == 500000
    assert r.poly_cells == 120000


def test_max_volume_ratio():
    assert MAX_VOLUME_RATIO == 10.0


def test_engine_init():
    me = MosaicEngine()
    assert me._of_config is not None
    assert isinstance(me._params, MosaicParams)


def test_engine_set_params():
    me = MosaicEngine()
    p = MosaicParams(n_smoothing_iterations=6)
    me.set_params(p)
    assert me._params.n_smoothing_iterations == 6


def test_engine_set_case_dir_nonexistent():
    me = MosaicEngine()
    try:
        me.set_case_dir("Z:\\nonexistent")
        assert False, "Should have raised"
    except (ValueError, FileNotFoundError):
        pass


def _safe_case_dir() -> Path:
    """Create a case dir at a path without spaces (portable tmp root)."""
    d = space_free_tmp_root() / "tmp_mosaic_test"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_engine_set_case_dir_valid():
    me = MosaicEngine()
    d = _safe_case_dir()
    me.set_case_dir(d)
    assert me._case_dir == d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_engine_run_no_case():
    me = MosaicEngine()
    result = me.run()
    assert not result.success
    assert len(result.errors) > 0


def test_volume_ratio_acceptable():
    me = MosaicEngine()
    me._result.volume_ratio = 5.0
    assert me.volume_ratio_acceptable()


def test_volume_ratio_not_acceptable():
    me = MosaicEngine()
    me._result.volume_ratio = 15.0
    assert not me.volume_ratio_acceptable()


def test_estimate_volume_ratio_no_case():
    me = MosaicEngine()
    ratio = me._estimate_volume_ratio()
    assert ratio == 0.0


def test_mosaic_result_cell_reduction():
    r = MosaicResult(hex_cells=500000, poly_cells=250000)
    reduction = round((1 - r.poly_cells / r.hex_cells) * 100, 1)
    assert reduction == 50.0


if __name__ == "__main__":
    test_mosaic_params_defaults()
    test_mosaic_params_custom()
    test_mosaic_result_defaults()
    test_mosaic_result_with_data()
    test_max_volume_ratio()
    test_engine_init()
    test_engine_set_params()
    test_engine_set_case_dir_nonexistent()
    test_engine_set_case_dir_valid()
    test_engine_run_no_case()
    test_volume_ratio_acceptable()
    test_volume_ratio_not_acceptable()
    test_estimate_volume_ratio_no_case()
    test_mosaic_result_cell_reduction()
    print("ALL PASS")
