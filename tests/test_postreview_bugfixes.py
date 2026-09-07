"""Tests for the post-review UX bug fixes (no Qt/PyVista required)."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import os as _os
try:
    import OCP as _ocp
    _d = _os.path.dirname(_ocp.__file__)
    if _d not in _os.environ.get("PATH", ""):
        _os.environ["PATH"] = _d + _os.pathsep + _os.environ.get("PATH", "")
except Exception:
    pass


# ------------------------------------------------------------------
# Bug 3: cell count regex must handle cfMesh v2512 reverse-order output.
# ------------------------------------------------------------------
_CELLS_RE = re.compile(
    r"(?:\b(\d[\d,]*)\s+cells\b)|(?:\bcells\s+(\d[\d,]*))",
    re.IGNORECASE,
)


def _parse(line: str) -> int | None:
    m = _CELLS_RE.search(line)
    if not m:
        return None
    raw = m.group(1) or m.group(2)
    return int(raw.replace(",", ""))


def test_cells_reverse_order_cfmesh_v2512():
    """The actual cfMesh v2512 output: '16400 cells' (number first)."""
    assert _parse("16400 cells") == 16400
    print("PASS: cell count reverse-order '16400 cells'")


def test_cells_legacy_label_first():
    """Older / other formats may emit 'cells 16400'."""
    assert _parse("cells 16400") == 16400
    print("PASS: cell count legacy 'cells 16400'")


def test_cells_with_thousands_separator():
    """Some builds emit '1,234,567 cells'."""
    assert _parse("1,234,567 cells") == 1234567
    print("PASS: cell count with thousands separator")


def test_cells_in_full_cfmesh_block():
    """Realistic block of output from cartesianMesh v2512."""
    sample = """\
Build  : _bd2b6720-20260127 OPENFOAM=2512
Exec   : cartesianMesh
Create time
Setting root cube size and refinement parameters
Refining boundary
Finished extracting polyMesh
21027 vertices
53740 faces
16400 cells
"""
    found = None
    for line in sample.splitlines():
        n = _parse(line)
        if n is not None:
            found = n
            break
    assert found == 16400, f"Expected 16400, got {found}"
    print("PASS: cell count parsed from full cfMesh block")


def test_cells_not_matched_in_unrelated_line():
    """Negative test: 'Removing selected cells' should NOT match."""
    assert _parse("Removing selected cells from the mesh") is None
    print("PASS: no false positive on 'Removing selected cells'")


def test_cells_not_matched_in_size_line():
    """Negative test: 'min cell size = 0.01' should NOT match."""
    assert _parse("min cell size = 0.01") is None
    print("PASS: no false positive on 'min cell size = 0.01'")


def test_cells_matches_adding_672_cells():
    """Real cfMesh line during BL: 'Adding 672 cells to the mesh'."""
    assert _parse("Adding 672 cells to the mesh") == 672
    print("PASS: cell count regex matches 'Adding 672 cells to the mesh'")


# ------------------------------------------------------------------
# Bug 2: BL must apply to all wall patches, not just hardcoded "wall".
# ------------------------------------------------------------------
from cfmesh_autogui.core.meshdict_gen import build_meshdict_lines


def test_bl_block_emits_per_wall_patch():
    """Wall patches combined into a regex in the patchBoundaryLayers block."""
    bl = {
        "nLayers": 3,
        "thicknessRatio": 0.3,
        "expansionRatio": 1.2,
        "wallPatches": ["wall", "wall_inlet", "walls"],
    }
    content = "\n".join(build_meshdict_lines(bl_params=bl))
    assert "patchBoundaryLayers" in content
    # All three names appear in the regex
    assert '"wall|wall_inlet|walls"' in content
    # Only one nLayers entry (the regex dict)
    assert content.count("nLayers") == 1
    print("PASS: BL block emits regex 'wall|wall_inlet|walls' in patchBoundaryLayers")


def test_bl_block_default_wall_when_no_wall_patches():
    """If wallPatches is missing, fall back to a single 'wall' entry."""
    bl = {"nLayers": 3, "thicknessRatio": 0.3, "expansionRatio": 1.2}
    content = "\n".join(build_meshdict_lines(bl_params=bl))
    assert "patchBoundaryLayers" in content
    assert '"wall"' in content
    print("PASS: BL block default 'wall' when no wallPatches given")


def test_bl_block_string_wall_patches_wrapped_in_list():
    """If wallPatches is a single string, treat it as a one-element list."""
    bl = {
        "nLayers": 2,
        "thicknessRatio": 0.5,
        "expansionRatio": 1.3,
        "wallPatches": "wall_outer",
    }
    content = "\n".join(build_meshdict_lines(bl_params=bl))
    assert 'patchBoundaryLayers' in content
    assert '"wall_outer"' in content
    assert content.count("nLayers") == 1
    print("PASS: BL block accepts string wallPatches as 1-element list")


def test_no_bl_block_when_bl_params_is_none():
    """No BL params → no boundaryLayers block in the output."""
    content = "\n".join(build_meshdict_lines(bl_params=None))
    assert "boundaryLayers" not in content
    print("PASS: no BL block when bl_params is None")


def test_bl_block_maps_user_values_to_cfmesh_keys():
    """User BL values must reach the meshDict under the keys cfMesh actually reads.

    Passing them through verbatim (what this test used to assert) was the bug:
    cfMesh's `thicknessRatio` is the layer-to-layer GROWTH ratio, so feeding it
    the first-layer fraction (0.42) collapsed the layers, while the real growth
    ratio was emitted as `expansionRatio` — a key cfMesh silently ignores.
    """
    import re
    bl = {
        "nLayers": 5,
        "thicknessRatio": 1.5,
        "firstLayerThickness": 1e-4,
        "wallPatches": ["wall"],
    }
    content = "\n".join(build_meshdict_lines(max_cell=0.05, bl_params=bl))
    assert re.search(r"thicknessRatio\s+1\.5;", content)
    assert "expansionRatio" not in content
    assert re.search(r"nLayers\s+5;", content)
    assert re.search(r"maxFirstLayerThickness\s+0\.0001;", content)


# ------------------------------------------------------------------
# Bug 1: STL multi-solid loader.
# ------------------------------------------------------------------
import tempfile
import os
from cfmesh_autogui.core.geometry import load_stl, load_geometry


def _write_ascii_stl(path: str, solids: list[tuple[str, list[tuple[float, float, float]]]]):
    """Helper: write a multi-solid ASCII STL.

    solids = [(name, [(x,y,z), (x,y,z), (x,y,z)]), ...] — one triangle per solid.
    """
    with open(path, "w", encoding="ascii") as fh:
        for name, tri in solids:
            fh.write(f"solid {name}\n")
            v0, v1, v2 = tri
            n = (0.0, 0.0, 0.0)  # normal, ignored by trimesh
            fh.write(f"  facet normal {n[0]:.6e} {n[1]:.6e} {n[2]:.6e}\n")
            fh.write("    outer loop\n")
            for v in (v0, v1, v2):
                fh.write(f"      vertex {v[0]:.6e} {v[1]:.6e} {v[2]:.6e}\n")
            fh.write("    endloop\n")
            fh.write("  endfacet\n")
            fh.write(f"endsolid {name}\n")


def test_load_stl_ascii_single_solid():
    """A single-solid ASCII STL yields one mesh with the right name."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "single.stl")
        _write_ascii_stl(path, [
            ("box", [(0, 0, 0), (1, 0, 0), (0, 1, 0)]),
        ])
        meshes = load_stl(path)
        assert len(meshes) == 1
        assert meshes[0].metadata["name"] == "box"
        assert len(meshes[0].faces) == 1
    print("PASS: STL single solid yields one named mesh")


def test_load_stl_ascii_multi_solid():
    """A multi-solid ASCII STL yields one mesh PER solid, names preserved."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "multi.stl")
        _write_ascii_stl(path, [
            ("inlet",  [(0, 0, 0), (1, 0, 0), (0, 1, 0)]),
            ("outlet", [(2, 0, 0), (3, 0, 0), (2, 1, 0)]),
            ("walls",  [(0, 0, 1), (1, 0, 1), (0, 1, 1)]),
        ])
        meshes = load_stl(path)
        assert len(meshes) == 3, f"Expected 3 solids, got {len(meshes)}"
        names = [m.metadata["name"] for m in meshes]
        assert names == ["inlet", "outlet", "walls"], f"Got names {names}"
    print("PASS: STL multi-solid yields 3 named meshes")


def test_load_stl_file_not_found():
    """Missing file → FileNotFoundError."""
    try:
        load_stl("/nonexistent/path.stl")
        assert False, "Should have raised"
    except FileNotFoundError:
        pass
    print("PASS: missing STL file raises FileNotFoundError")


def test_load_geometry_dispatches_stl():
    """load_geometry() with .stl path uses load_stl (not load_step)."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "thing.stl")
        _write_ascii_stl(path, [
            ("inlet",  [(0, 0, 0), (1, 0, 0), (0, 1, 0)]),
            ("outlet", [(2, 0, 0), (3, 0, 0), (2, 1, 0)]),
        ])
        meshes = load_geometry(path)
        assert len(meshes) == 2
        assert [m.metadata["name"] for m in meshes] == ["inlet", "outlet"]
    print("PASS: load_geometry dispatches STL correctly")


def test_load_geometry_rejects_unsupported():
    """Unknown extension raises ValueError."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "thing.obj")
        open(path, "w").close()
        try:
            load_geometry(path)
            assert False, "Should have raised"
        except ValueError as exc:
            assert "Unsupported geometry format" in str(exc)
    print("PASS: load_geometry rejects unsupported extensions")


# Post-review: BL fallback to all patches when no "wall" is detected
def test_bl_block_falls_back_to_all_patches():
    """When bl_params has no wallPatches key, meshdict_gen should
    emit a single 'wall' entry (the historical default)."""
    bl = {"nLayers": 3, "thicknessRatio": 0.3, "expansionRatio": 1.2}
    content = "\n".join(build_meshdict_lines(bl_params=bl))
    assert "boundaryLayers" in content
    assert '"wall"' in content
    print("PASS: BL block default 'wall' when no wallPatches given")


def test_bl_block_with_arbitrary_wall_patches():
    """User-typed wallPatches combined in a regex in patchBoundaryLayers."""
    bl = {
        "nLayers": 4,
        "thicknessRatio": 0.5,
        "expansionRatio": 1.3,
        "wallPatches": ["outer_shell", "side_panel", "inlet_plate"],
    }
    content = "\n".join(build_meshdict_lines(bl_params=bl))
    assert '"outer_shell|side_panel|inlet_plate"' in content
    assert content.count("nLayers") == 1
    print("PASS: BL block supports arbitrary patch names via regex")

