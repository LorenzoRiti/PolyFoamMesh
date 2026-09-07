"""Tests for the validation module."""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Direct import without triggering cadquery dependency
src = Path(__file__).resolve().parents[1] / "src" / "cfmesh_autogui" / "core" / "validation.py"
code = src.read_text(encoding="utf-8")

_mod = types.ModuleType("validation")
_mod.__file__ = str(src)
_mod.__package__ = "cfmesh_autogui.core"
sys.modules["validation"] = _mod

exec(compile(code, str(src), "exec"), _mod.__dict__)

validate_cell_size = _mod.validate_cell_size
validate_bl_params = _mod.validate_bl_params
validate_detail_level = _mod.validate_detail_level
validate_geometry_path = _mod.validate_geometry_path
validate_case_dir = _mod.validate_case_dir
sanitise_patch_name = _mod.sanitise_patch_name


def test_validate_cell_size_ok():
    r = validate_cell_size(max_cell=0.05, min_cell=0.01, bbox_dim=2.0)
    assert r.valid, f"Expected valid, got: {r.message}"


def test_validate_cell_size_non_positive():
    r = validate_cell_size(max_cell=-0.05, min_cell=0.01)
    assert not r.valid, "Expected invalid for negative max_cell"


def test_validate_cell_size_min_gt_max():
    r = validate_cell_size(max_cell=0.01, min_cell=0.05)
    assert not r.valid, "Expected invalid when min > max"


def test_validate_cell_size_exceeds_bbox():
    r = validate_cell_size(max_cell=10.0, min_cell=0.01, bbox_dim=2.0)
    assert not r.valid, "Expected invalid when max_cell > bbox"


def test_validate_cell_size_warns_coarse():
    r = validate_cell_size(max_cell=0.05, min_cell=0.01, bbox_dim=0.08)
    assert r.valid, "Should still be valid"
    assert len(r.warnings) > 0, "Expected warnings for coarse mesh"


def test_validate_bl_params_ok():
    r = validate_bl_params(n_layers=3, thickness_ratio=0.005, expansion_ratio=1.2)
    assert r.valid


def test_validate_bl_params_invalid_layers():
    r = validate_bl_params(n_layers=0, thickness_ratio=0.005, expansion_ratio=1.2)
    assert not r.valid


def test_validate_bl_params_invalid_expansion():
    r = validate_bl_params(n_layers=3, thickness_ratio=0.005, expansion_ratio=3.0)
    assert not r.valid


def test_validate_detail_level_ok():
    for d in ("coarse", "medium", "fine"):
        assert validate_detail_level(d).valid


def test_validate_detail_level_invalid():
    r = validate_detail_level("extreme")
    assert not r.valid


def test_validate_geometry_path_nonexistent():
    r = validate_geometry_path("Z:\\nonexistent\\file.step")
    assert not r.valid


def test_sanitise_patch_name():
    assert sanitise_patch_name("wall;01") == "wall_01"
    assert sanitise_patch_name("inlet") == "inlet"
    assert sanitise_patch_name("") == "unnamed"


def test_validate_case_dir_with_spaces():
    r = validate_case_dir("C:\\test\\my case")
    assert not r.valid, "Spaces should be rejected"


def test_validate_case_dir_with_special_chars():
    r = validate_case_dir("C:\\test\\case; rm -rf")
    assert not r.valid, "Shell chars should be rejected"


if __name__ == "__main__":
    test_validate_cell_size_ok()
    test_validate_cell_size_non_positive()
    test_validate_cell_size_min_gt_max()
    test_validate_cell_size_exceeds_bbox()
    test_validate_cell_size_warns_coarse()
    test_validate_bl_params_ok()
    test_validate_bl_params_invalid_layers()
    test_validate_bl_params_invalid_expansion()
    test_validate_detail_level_ok()
    test_validate_detail_level_invalid()
    test_validate_geometry_path_nonexistent()
    test_sanitise_patch_name()
    test_validate_case_dir_with_spaces()
    test_validate_case_dir_with_special_chars()
    print("ALL PASS")
