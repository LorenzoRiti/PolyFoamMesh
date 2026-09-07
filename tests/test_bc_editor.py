"""Tests for the BC editor module."""
from __future__ import annotations
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("bc_editor")
BcPatchInfo = _mod.BcPatchInfo
BCEditor = _mod.BCEditor
BC_PRESETS = _mod.BC_PRESETS
PATCH_COLORS = _mod.PATCH_COLORS


def test_patch_colors_all_types():
    assert "inlet" in PATCH_COLORS
    assert "outlet" in PATCH_COLORS
    assert "wall" in PATCH_COLORS
    assert "symmetry" in PATCH_COLORS
    assert len(PATCH_COLORS) >= 5


def test_bc_presets_all_types():
    assert "inlet" in BC_PRESETS
    assert "outlet" in BC_PRESETS
    assert "wall" in BC_PRESETS
    assert "symmetry" in BC_PRESETS


def test_bc_preset_inlet_has_fields():
    preset = BC_PRESETS["inlet"]
    assert "U" in preset
    assert "p" in preset
    assert "k" in preset
    assert preset["U"]["type"] == "fixedValue"


def test_bc_preset_outlet():
    preset = BC_PRESETS["outlet"]
    assert preset["p"]["type"] == "fixedValue"
    assert preset["U"]["type"] == "zeroGradient"


def test_patch_info_defaults():
    p = BcPatchInfo()
    assert p.name == ""
    assert p.n_faces == 0
    assert p.bc_type == "patch"
    assert p.detected_by == ""


def test_patch_info_with_data():
    p = BcPatchInfo(name="inlet", n_faces=100, start_face=0, bc_type="inlet")
    assert p.name == "inlet"
    assert p.n_faces == 100
    assert p.bc_type == "inlet"


def test_editor_init():
    editor = BCEditor()
    assert editor is not None


def test_name_to_type_inlet():
    editor = BCEditor()
    assert editor._name_to_type("inlet") == "inlet"
    assert editor._name_to_type("velocityInlet") == "inlet"
    assert editor._name_to_type("inlet_02") == "inlet"


def test_name_to_type_outlet():
    editor = BCEditor()
    assert editor._name_to_type("outlet") == "outlet"
    assert editor._name_to_type("pressure_outlet") == "outlet"


def test_name_to_type_wall():
    editor = BCEditor()
    assert editor._name_to_type("wall") == "wall"
    assert editor._name_to_type("blade") == "wall"
    assert editor._name_to_type("body") == "wall"
    assert editor._name_to_type("wing") == "wall"


def test_name_to_type_symmetry():
    editor = BCEditor()
    assert editor._name_to_type("symmetry") == "symmetry"
    assert editor._name_to_type("cyclic") == "symmetry"


def test_name_to_type_unmatched_yields_no_signal():
    editor = BCEditor()
    # A name carrying no keyword must NOT be asserted to be a wall:
    # BCEditor._detect_type falls through to its geometric branch for these
    # (GMSH names every patch surface_N, and geometry is the only thing that
    # can tell an inlet from a wall there). "patch" means "name says nothing".
    assert editor._name_to_type("default") == "patch"
    assert editor._name_to_type("surface_0") == "patch"
    assert editor._name_to_type("random_name") == "patch"


def test_name_to_type_wall_keywords_still_detected():
    editor = BCEditor()
    # Positive wall signal must survive: these name a solid surface, so the
    # BC editor can type them without consulting geometry.
    for name in ("blade", "body", "wing", "housing", "casing"):
        assert editor._name_to_type(name) == "wall", name


def test_rename_patch():
    editor = BCEditor()
    p = BcPatchInfo(name="old_name", bc_type="patch")
    result = editor.rename_patch([p], "old_name", "inlet")
    assert result
    assert p.name == "inlet"
    assert p.bc_type == "inlet"


def test_rename_patch_not_found():
    editor = BCEditor()
    p = BcPatchInfo(name="existing")
    result = editor.rename_patch([p], "nonexistent", "new")
    assert not result


def test_set_type():
    editor = BCEditor()
    p = BcPatchInfo(name="test", bc_type="patch")
    editor.set_type(p, "wall")
    assert p.bc_type == "wall"
    assert p.detected_by == "user"


def test_set_type_invalid():
    editor = BCEditor()
    p = BcPatchInfo()
    try:
        editor.set_type(p, "invalid_type")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_read_boundary_nonexistent():
    editor = BCEditor()
    try:
        editor.read_boundary("Z:\\nonexistent")
        assert False, "Should have raised"
    except (FileNotFoundError, ValueError):
        pass


def test_auto_detect_by_name():
    editor = BCEditor()
    p = BcPatchInfo(name="inlet", centroid=(0, 0.5, 0.5), normal=(1, 0, 0))
    result = editor._detect_type(p, bbox=(2, 1, 1))
    assert result == "inlet", f"Expected inlet, got {result}"


def test_detect_unknown_becomes_patch():
    editor = BCEditor()
    # No keyword in the name, and no geometry to fall back on (bbox and
    # centroid are both zero): the honest answer is the generic OpenFOAM
    # "patch", not a wall.
    p = BcPatchInfo(name="random_name")
    result = editor._detect_type(p, bbox=(0, 0, 0))
    assert result == "patch"


def test_export_fields_creates_files():
    tmp = Path(tempfile.mkdtemp(dir=os.environ.get("TEMP", "/tmp")))
    editor = BCEditor()
    patches = [
        BcPatchInfo(name="inlet", n_faces=50, start_face=0, bc_type="inlet"),
        BcPatchInfo(name="outlet", n_faces=50, start_face=50, bc_type="outlet"),
        BcPatchInfo(name="wall", n_faces=200, start_face=100, bc_type="wall"),
    ]
    files = editor.export_fields(tmp, patches)
    assert len(files) >= 2
    assert (tmp / "0/U").exists()
    assert (tmp / "0/p").exists()
    content = (tmp / "0/U").read_text()
    assert "inlet" in content
    import shutil; shutil.rmtree(tmp, ignore_errors=True)


def test_export_boundary_file():
    tmp = Path(tempfile.mkdtemp(dir=os.environ.get("TEMP", "/tmp")))
    editor = BCEditor()
    patches = [BcPatchInfo(name="wall", n_faces=100, start_face=0, bc_type="wall")]
    path = editor.export_boundary_file(tmp, patches)
    content = Path(path).read_text()
    assert "wall" in content
    assert "nFaces 100" in content
    import shutil; shutil.rmtree(tmp, ignore_errors=True)


def test_colour_for_patch():
    p = BcPatchInfo(bc_type="inlet")
    colour = BCEditor.colour_for_patch(p)
    assert colour == PATCH_COLORS["inlet"]


def test_colour_for_patch_default():
    p = BcPatchInfo(bc_type="unknown")
    colour = BCEditor.colour_for_patch(p)
    assert colour == PATCH_COLORS["default"]


if __name__ == "__main__":
    import shutil
    test_patch_colors_all_types()
    test_bc_presets_all_types()
    test_bc_preset_inlet_has_fields()
    test_bc_preset_outlet()
    test_patch_info_defaults()
    test_patch_info_with_data()
    test_editor_init()
    test_name_to_type_inlet()
    test_name_to_type_outlet()
    test_name_to_type_wall()
    test_name_to_type_symmetry()
    test_name_to_type_unmatched_yields_no_signal()
    test_name_to_type_wall_keywords_still_detected()
    test_rename_patch()
    test_rename_patch_not_found()
    test_set_type()
    test_set_type_invalid()
    test_read_boundary_nonexistent()
    test_auto_detect_by_name()
    test_detect_unknown_becomes_patch()
    test_export_fields_creates_files()
    test_export_boundary_file()
    test_colour_for_patch()
    test_colour_for_patch_default()
    tmp = Path(os.environ.get("TEMP", "/tmp"))
    for d in tmp.glob("tmp*"):
        if d.is_dir(): shutil.rmtree(d, ignore_errors=True)
    print("ALL PASS")
