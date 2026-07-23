"""Tests for the parallel meshing engine."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("parallel_mesh")
DecomposeParams = _mod.DecomposeParams
ParallelMeshResult = _mod.ParallelMeshResult
ParallelMeshEngine = _mod.ParallelMeshEngine
DECOMP_METHODS = _mod.DECOMP_METHODS


def test_decompose_params_defaults():
    p = DecomposeParams()
    assert p.method == "scotch"
    assert p.n_cores == 4
    assert p.overlap == 0


def test_decompose_params_custom():
    p = DecomposeParams(method="metis", n_cores=8, overlap=2)
    assert p.method == "metis"
    assert p.n_cores == 8
    assert p.overlap == 2


def test_parallel_mesh_result_defaults():
    r = ParallelMeshResult()
    assert not r.success
    assert r.n_cores == 0
    assert r.cell_count == 0
    assert r.cell_count_per_rank == []


def test_parallel_mesh_result_with_data():
    r = ParallelMeshResult(success=True, n_cores=4, cell_count=500000)
    assert r.success
    assert r.n_cores == 4
    assert r.cell_count == 500000


def test_engine_init():
    pe = ParallelMeshEngine()
    assert pe._max_cell == 0.05
    assert pe._min_cell == 0.01
    assert pe.MIN_CORES == 2
    assert pe.MAX_CORES == 128


def test_engine_set_cell_sizes_valid():
    pe = ParallelMeshEngine()
    pe.set_cell_sizes(0.05, 0.01)
    assert pe._max_cell == 0.05


def test_engine_set_cell_sizes_invalid():
    pe = ParallelMeshEngine()
    try:
        pe.set_cell_sizes(-0.05, 0.01)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_engine_setup_case_invalid():
    pe = ParallelMeshEngine()
    try:
        pe.setup_case("Z:\\nonexistent\\case", n_cores=4)
        assert False, "Should have raised error"
    except (FileNotFoundError, ValueError):
        pass


def _tmp_case_dir() -> Path:
    """Create a temp case dir in a path without spaces."""
    safe_dir = Path("C:/") / "tmp_parallel_test"
    safe_dir.mkdir(parents=True, exist_ok=True)
    return safe_dir


def test_engine_setup_case_invalid_cores():
    pe = ParallelMeshEngine()
    d = _tmp_case_dir()
    try:
        pe.setup_case(d, n_cores=1)
        assert False, "Should have raised ValueError for < 2 cores"
    except ValueError:
        pass
    try:
        pe.setup_case(d, n_cores=200)
        assert False, "Should have raised ValueError for > 128 cores"
    except ValueError:
        pass
    _clean_tmp(d)


def test_engine_setup_case_invalid_method():
    pe = ParallelMeshEngine()
    d = _tmp_case_dir()
    try:
        pe.setup_case(d, n_cores=4, method="unknown")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    _clean_tmp(d)


def test_engine_setup_case_valid():
    pe = ParallelMeshEngine()
    d = _tmp_case_dir()
    (d / "system").mkdir(exist_ok=True)
    (d / "constant").mkdir(exist_ok=True)
    pe.setup_case(d, n_cores=4, method="scotch")
    assert pe._params.n_cores == 4
    assert pe._params.method == "scotch"
    _clean_tmp(d)


def _clean_tmp(d: Path) -> None:
    import shutil
    try:
        shutil.rmtree(d)
    except Exception:
        pass


def test_decomp_methods_available():
    assert "scotch" in DECOMP_METHODS
    assert "metis" in DECOMP_METHODS
    assert len(DECOMP_METHODS) >= 4


def test_estimate_speedup():
    est = ParallelMeshEngine.estimate_speedup(n_cores=8)
    assert est["speedup"] > 1.0
    assert 0 < est["efficiency"] <= 1.0
    assert est["serial_fraction"] == 0.05


def test_estimate_speedup_many_cores():
    est = ParallelMeshEngine.estimate_speedup(n_cores=128)
    assert est["speedup"] > 1.0
    # With 5% serial fraction, max speedup ~20x
    assert est["speedup"] < 30


def test_estimate_speedup_single_core():
    est = ParallelMeshEngine.estimate_speedup(n_cores=1)
    assert round(est["speedup"]) == 1.0


def test_decompose_params_string_rep():
    p = DecomposeParams(method="hierarchical", n_cores=16)
    assert "hierarchical" in str(p)
    assert "16" in str(p)


def test_engine_run_no_case():
    pe = ParallelMeshEngine()
    result = pe.run()
    assert not result.success
    assert len(result.errors) > 0


if __name__ == "__main__":
    test_decompose_params_defaults()
    test_decompose_params_custom()
    test_parallel_mesh_result_defaults()
    test_parallel_mesh_result_with_data()
    test_engine_init()
    test_engine_set_cell_sizes_valid()
    test_engine_set_cell_sizes_invalid()
    test_engine_setup_case_invalid()
    test_engine_setup_case_invalid_cores()
    test_engine_setup_case_invalid_method()
    test_engine_setup_case_valid()
    test_decomp_methods_available()
    test_estimate_speedup()
    test_estimate_speedup_many_cores()
    test_estimate_speedup_single_core()
    test_decompose_params_string_rep()
    test_engine_run_no_case()
    print("ALL PASS")
