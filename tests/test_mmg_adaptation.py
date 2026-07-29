"""Tests for MMG adaptation module."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("mmg_adaptation")
MmgAdaptationRunner = _mod.MmgAdaptationRunner


def test_runner_init():
    """MmgAdaptationRunner can be instantiated without WSL."""
    class MockConfig:
        env_script = "/usr/lib/openfoam/openfoam2512/etc/bashrc"
        def wsl_linux_case_path(self, p): return str(p)
        def _build_wsl_cmd(self, c): return ["echo", c]
        def build_check_mesh_cmd(self, c): return ["echo", "checkMesh"]

    runner = MmgAdaptationRunner(MockConfig())
    assert runner is not None
    assert runner.TIMEOUT_ADAPT == 600


def test_is_available_no_wsl():
    """is_available returns False when WSL not accessible."""
    class MockConfig:
        env_script = "/usr/lib/openfoam/openfoam2512/etc/bashrc"
        def _build_wsl_cmd(self, c): return ["cmd_that_does_not_exist"]

    runner = MmgAdaptationRunner(MockConfig())
    assert not runner.is_available()


def test_install_cmd():
    class MockConfig:
        pass
    runner = MmgAdaptationRunner(MockConfig())
    cmd = runner.install_cmd()
    assert "apt-get" in cmd
    assert "mmg" in cmd


def test_mmg_size_params():
    class MockConfig:
        pass
    runner = MmgAdaptationRunner(MockConfig())

    hmin, hmax = runner._mmg_size_params("very_fine")
    assert hmin < hmax
    assert hmin < 0.001

    hmin, hmax = runner._mmg_size_params("very_coarse")
    assert hmin < hmax
    assert hmin > 0.005


def test_mmg_hausdorff():
    class MockConfig:
        pass
    runner = MmgAdaptationRunner(MockConfig())

    d = runner._mmg_hausdorff("fine")
    assert d > 0


def test_run_available_no_wsl():
    """Run returns error when MMG not available."""
    class MockConfig:
        env_script = "/usr/lib/openfoam/openfoam2512/etc/bashrc"
        def _build_wsl_cmd(self, c): return ["cmd_not_found"]
        def build_check_mesh_cmd(self, c): return ["echo", "checkMesh"]
        def wsl_linux_case_path(self, p): return str(p)

    runner = MmgAdaptationRunner(MockConfig())
    result = runner.run(Path("/tmp/nonexistent"))
    assert not result["success"]
    assert result.get("errors")


if __name__ == "__main__":
    test_runner_init()
    test_is_available_no_wsl()
    test_install_cmd()
    test_mmg_size_params()
    test_mmg_hausdorff()
    test_run_available_no_wsl()
    print("ALL PASS")
