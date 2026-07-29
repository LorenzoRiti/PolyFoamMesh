"""Tests for the OpenMP acceleration engine."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("openmp_accel")
OpenMPAccel = _mod.OpenMPAccel
OMPConfig = _mod.OMPConfig
ThreadMode = _mod.ThreadMode


def test_omp_config_defaults():
    cfg = OMPConfig()
    assert cfg.n_threads == 1
    assert cfg.n_physical == 1
    assert cfg.n_logical == 1
    assert cfg.mode == ThreadMode.CONSERVATIVE
    assert cfg.bind_strategy == "spread"
    assert cfg.cell_estimate == 0


def test_omp_config_to_env():
    cfg = OMPConfig(n_threads=4, bind_strategy="spread")
    env = cfg.to_env()
    assert env["OMP_NUM_THREADS"] == "4"
    assert env["OMP_PROC_BIND"] == "spread"
    assert env["OMP_PLACES"] == "cores"


def test_omp_config_to_bash_export():
    cfg = OMPConfig(n_threads=4)
    export = cfg.to_bash_export()
    assert "export OMP_NUM_THREADS=4;" in export
    assert "export OMP_PROC_BIND=" in export
    assert "export OMP_PLACES=cores;" in export


def test_omp_config_speedup_estimate_serial():
    cfg = OMPConfig(n_threads=1)
    assert cfg.speedup_estimate() == 1.0


def test_omp_config_speedup_estimate_parallel():
    cfg = OMPConfig(n_threads=4)
    est = cfg.speedup_estimate()
    assert 1.0 < est < 4.0  # Amdahl's law: parallel frac < 1.0


def test_omp_config_summary():
    cfg = OMPConfig(n_threads=4, n_physical=4, n_logical=8)
    s = cfg.summary()
    assert "OpenMP:" in s
    assert "4 thread(s)" in s
    assert "4 physical" in s
    assert "8 logical" in s


def test_omp_accel_defaults():
    accel = OpenMPAccel()
    assert accel.n_physical >= 1
    assert accel.n_logical >= 1
    assert accel.n_physical <= accel.n_logical


def test_omp_accel_recommend_balanced():
    accel = OpenMPAccel()
    cfg = accel.recommend(cell_estimate=500_000, mode=ThreadMode.BALANCED)
    assert cfg.n_threads >= 1
    assert cfg.n_threads <= accel.n_logical
    assert cfg.mode == ThreadMode.BALANCED


def test_omp_accel_recommend_conservative():
    accel = OpenMPAccel()
    cfg = accel.recommend(cell_estimate=500_000, mode=ThreadMode.CONSERVATIVE)
    assert cfg.n_threads >= 1
    assert cfg.n_threads <= accel.n_physical
    assert cfg.mode == ThreadMode.CONSERVATIVE


def test_omp_accel_recommend_aggressive():
    accel = OpenMPAccel()
    cfg = accel.recommend(cell_estimate=500_000, mode=ThreadMode.AGGRESSIVE)
    assert cfg.n_threads >= 1
    assert cfg.n_threads <= accel.n_logical
    assert cfg.mode == ThreadMode.AGGRESSIVE


def test_omp_accel_recommend_custom():
    accel = OpenMPAccel()
    cfg = accel.recommend(mode=ThreadMode.CUSTOM, user_threads=2)
    assert cfg.n_threads == 2
    assert cfg.mode == ThreadMode.CUSTOM


def test_omp_accel_recommend_string_mode():
    accel = OpenMPAccel()
    cfg = accel.recommend(cell_estimate=500_000, mode="balanced")
    assert cfg.mode == ThreadMode.BALANCED


def test_omp_accel_small_mesh_serial():
    accel = OpenMPAccel()
    cfg = accel.recommend(cell_estimate=500, mode=ThreadMode.BALANCED)
    assert cfg.n_threads == 1  # < 10K cells = serial


def test_omp_accel_medium_mesh():
    accel = OpenMPAccel()
    cfg = accel.recommend(cell_estimate=30_000, mode=ThreadMode.BALANCED)
    assert cfg.n_threads >= 1


def test_omp_accel_with_mpi_ranks():
    accel = OpenMPAccel()
    cfg = accel.recommend(cell_estimate=1_000_000, mode=ThreadMode.BALANCED, mpi_ranks=4)
    assert cfg.n_threads >= 1
    total_threads = cfg.n_threads * 4
    assert total_threads <= accel.n_logical


def test_build_env_prefix():
    prefix = OpenMPAccel.build_env_prefix(cell_estimate=500_000)
    assert "export OMP_NUM_THREADS=" in prefix
    assert "export OMP_PROC_BIND=" in prefix


def test_default_mode_is_balanced():
    accel = OpenMPAccel()
    cfg = accel.recommend(cell_estimate=1_000_000)
    assert cfg.mode == ThreadMode.BALANCED


def test_bind_strategy_spread_for_mpi():
    cfg = OpenMPAccel._choose_bind_strategy(n_threads=4, mpi_ranks=4)
    assert cfg == "spread"


def test_bind_strategy_close_for_few_threads():
    cfg = OpenMPAccel._choose_bind_strategy(n_threads=2, mpi_ranks=0)
    assert cfg == "close"


def test_bind_strategy_spread_for_many_threads():
    cfg = OpenMPAccel._choose_bind_strategy(n_threads=8, mpi_ranks=0)
    assert cfg == "spread"


def test_thread_mode_values():
    assert ThreadMode.CONSERVATIVE.value == "conservative"
    assert ThreadMode.BALANCED.value == "balanced"
    assert ThreadMode.AGGRESSIVE.value == "aggressive"
    assert ThreadMode.CUSTOM.value == "custom"


def test_config_repr():
    cfg = OMPConfig(n_threads=8, n_physical=8, n_logical=16, mode=ThreadMode.AGGRESSIVE)
    s = repr(cfg)
    assert "n_threads=8" in s
    assert "mode=" in s


def test_physical_core_detection():
    accel = OpenMPAccel()
    assert isinstance(accel.n_physical, int)
    assert accel.n_physical >= 1


def test_logical_core_detection():
    accel = OpenMPAccel()
    assert isinstance(accel.n_logical, int)
    assert accel.n_logical >= 1


def test_scaling_by_problem_size_large():
    result = OpenMPAccel._scale_by_problem_size(8, 1_000_000)
    assert result == 8  # no scaling for large meshes


def test_scaling_by_problem_size_small():
    result = OpenMPAccel._scale_by_problem_size(8, 100)
    assert result == 1  # serial for tiny meshes


def test_recommend_with_mpi_conservative():
    accel = OpenMPAccel()
    cfg = accel.recommend(mode=ThreadMode.CONSERVATIVE, mpi_ranks=8)
    assert cfg.n_threads == 1  # conservative + MPI = 1 thread per rank


def test_custom_user_threads_clamped():
    accel = OpenMPAccel()
    cfg = accel.recommend(mode=ThreadMode.CUSTOM, user_threads=9999)
    assert cfg.n_threads <= accel.n_logical  # clamped to logical


def test_speedup_estimate_improves_with_more_threads():
    cfg2 = OMPConfig(n_threads=2)
    cfg4 = OMPConfig(n_threads=4)
    assert cfg4.speedup_estimate() > cfg2.speedup_estimate()


if __name__ == "__main__":
    test_omp_config_defaults()
    test_omp_config_to_env()
    test_omp_config_to_bash_export()
    test_omp_config_speedup_estimate_serial()
    test_omp_config_speedup_estimate_parallel()
    test_omp_config_summary()
    test_omp_accel_defaults()
    test_omp_accel_recommend_balanced()
    test_omp_accel_recommend_conservative()
    test_omp_accel_recommend_aggressive()
    test_omp_accel_recommend_custom()
    test_omp_accel_recommend_string_mode()
    test_omp_accel_small_mesh_serial()
    test_omp_accel_medium_mesh()
    test_omp_accel_with_mpi_ranks()
    test_build_env_prefix()
    test_default_mode_is_balanced()
    test_bind_strategy_spread_for_mpi()
    test_bind_strategy_close_for_few_threads()
    test_bind_strategy_spread_for_many_threads()
    test_thread_mode_values()
    test_config_repr()
    test_physical_core_detection()
    test_logical_core_detection()
    test_scaling_by_problem_size_large()
    test_scaling_by_problem_size_small()
    test_recommend_with_mpi_conservative()
    test_custom_user_threads_clamped()
    test_speedup_estimate_improves_with_more_threads()
    print("ALL PASS")
