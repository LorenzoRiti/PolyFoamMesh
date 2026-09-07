"""Tests for snappyHexMesh integration."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("snappy_hex_mesh")
write_snappy_hex_mesh_dict = _mod.write_snappy_hex_mesh_dict
build_snappy_hex_mesh_dict_lines = _mod.build_snappy_hex_mesh_dict_lines
SnappyHexMeshRunner = _mod.SnappyHexMeshRunner
_cell_size_to_refinement_level = _mod._cell_size_to_refinement_level


def test_cell_size_to_refinement_level():
    assert _cell_size_to_refinement_level(0.01, 0.1) >= 3
    assert _cell_size_to_refinement_level(0.1, 0.1) == 1
    assert _cell_size_to_refinement_level(0.001, 0.1) >= 6


def test_build_dict_defaults():
    lines = build_snappy_hex_mesh_dict_lines()
    text = "\n".join(lines)
    assert "castellatedMesh true" in text
    assert "snap            true" in text
    assert "addLayers       false" in text
    assert "surface.stl" in text
    assert "maxNonOrtho 65" in text
    assert "snapControls" in text


def test_build_dict_with_layers():
    bl = {"nLayers": 5, "thicknessRatio": 1.2, "firstLayerThickness": 0.001}
    lines = build_snappy_hex_mesh_dict_lines(bl_params=bl)
    text = "\n".join(lines)
    assert "addLayers       true" in text
    assert "nSurfaceLayers 5" in text
    assert "expansionRatio" in text


def test_build_dict_refinement_surfaces():
    zones = [
        {"name": "inlet", "cell_size": 0.005},
        {"name": "outlet", "cell_size": 0.01},
    ]
    lines = build_snappy_hex_mesh_dict_lines(cell_zone_levels=zones)
    text = "\n".join(lines)
    assert "inlet" in text
    assert "outlet" in text
    assert "level (" in text


def test_write_snappy_hex_mesh_dict(tmp_path):
    case_dir = tmp_path / "test_case"
    case_dir.mkdir()
    (case_dir / "system").mkdir()

    result = write_snappy_hex_mesh_dict(case_dir)
    assert result.exists()
    text = result.read_text()
    assert "snappyHexMeshDict" in text
    assert "FoamFile" in text


def test_write_snappy_hex_mesh_dict_with_bl(tmp_path):
    case_dir = tmp_path / "test_case_bl"
    case_dir.mkdir()
    (case_dir / "system").mkdir()

    bl = {"nLayers": 3, "thicknessRatio": 1.3, "firstLayerThickness": 0.002}
    result = write_snappy_hex_mesh_dict(case_dir, bl_params=bl)
    text = result.read_text()
    assert "nSurfaceLayers 3" in text
    assert "addLayers       true" in text


def test_runner_init():
    """SnappyHexMeshRunner can be instantiated (WSL not required for init)."""
    class MockConfig:
        env_script = "/usr/lib/openfoam/openfoam2512/etc/bashrc"
        def wsl_linux_case_path(self, p): return str(p)
        def _build_wsl_cmd(self, c): return ["echo", c]
        def build_command(self, c): return ["echo", "cartesianMesh"]

    runner = SnappyHexMeshRunner(MockConfig())
    assert runner is not None


def test_runner_available_no_wsl():
    """Runner instantiation does not require WSL."""
    class MockConfig:
        env_script = "/usr/lib/openfoam/openfoam2512/etc/bashrc"
        def wsl_linux_case_path(self, p): return str(p)
        def _build_wsl_cmd(self, c): return ["echo", c]
        def build_command(self, c): return ["echo", "cartesianMesh"]

    runner = SnappyHexMeshRunner(MockConfig())
    assert runner.TIMEOUT_SNAPPY == 7200


if __name__ == "__main__":
    test_cell_size_to_refinement_level()
    test_build_dict_defaults()
    test_build_dict_with_layers()
    test_build_dict_refinement_surfaces()
    test_write_snappy_hex_mesh_dict(None)
    test_write_snappy_hex_mesh_dict_with_bl(None)
    test_runner_init()
    test_runner_available_no_wsl()
    print("ALL PASS")
