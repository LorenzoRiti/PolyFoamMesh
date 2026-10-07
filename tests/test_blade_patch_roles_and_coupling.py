"""Tests for the blade-mandate fixes:
- BL wall-size floor constant derived from the 0.34 inversion budget (step 3)
- wall-only surfaces for the A floor (step 1d)
- generated-mesh patch rename written to constant/polyMesh/boundary (step 1c)
- inferred patch role shown in the viewer patch table (step 1b)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from polyfoammesh.core import gmsh_wrapper as gw
from polyfoammesh.core.boundary_reader import (
    parse_boundary,
    rename_boundary_patch,
)


# ---------------------------------------------------------------------------
# Step 3: floor constant / inversion budget
# ---------------------------------------------------------------------------
def test_floor_cells_across_derives_from_inversion_budget():
    """floor = stack / 3, because the 0.34 inversion budget says the stack may
    span at most ~1/0.34 ~= 3 wall cells. The old value of 5 under-corrected
    the floor by ~1.7x (0.34*floor was only 0.068*stack)."""
    assert gw._BL_CELLS_ACROSS_STACK == round(1.0 / gw._BL_INVERSION_BUDGET)
    assert gw._BL_CELLS_ACROSS_STACK == 3
    # The two constants are consistent: cells_across * budget ~= 1.
    assert abs(gw._BL_CELLS_ACROSS_STACK * gw._BL_INVERSION_BUDGET - 1.0) < 0.05


def test_bl_wall_size_floor_is_stack_over_three_and_extent_capped():
    h1, r, n = 3.981e-4, 1.2, 16
    stack = gw.compute_bl_stack(h1, r, n)
    assert gw.compute_bl_wall_size_floor(h1, r, n, 100.0) == pytest.approx(
        stack / 3.0
    )
    # capped at 10% of the extent (binding on a small domain)
    floor_small = gw.compute_bl_wall_size_floor(h1, r, n, 0.01)
    assert floor_small == pytest.approx(0.001)
    # not binding when stack/3 is already below the cap
    assert gw.compute_bl_wall_size_floor(h1, r, n, 0.5) == pytest.approx(stack / 3.0)
    # disabled for a degenerate stack
    assert gw.compute_bl_wall_size_floor(0.0, r, n, 100.0) == 0.0
    assert gw.compute_bl_wall_size_floor(h1, r, 0, 100.0) == 0.0


# ---------------------------------------------------------------------------
# Step 1d: wall-only surfaces
# ---------------------------------------------------------------------------
def test_infer_wall_surface_tags_excludes_flow_axis_extremes():
    gmsh = pytest.importorskip("gmsh")
    gmsh.initialize()
    try:
        gmsh.model.add("box")
        gmsh.model.occ.addBox(0, 0, 0, 2.0, 0.2, 0.2)
        gmsh.model.occ.synchronize()
        all_tags = [t for _d, t in gmsh.model.getEntities(2)]
        walls, every = gw._infer_wall_surface_tags(gmsh)
        assert every == all_tags
        # The two x-extreme faces are inferred inlet/outlet and excluded.
        assert len(walls) == len(all_tags) - 2
        assert len(set(walls)) == len(walls)
    finally:
        gmsh.clear()


def test_sample_wall_points_restricts_to_given_tags():
    gmsh = pytest.importorskip("gmsh")
    gmsh.initialize()
    try:
        gmsh.model.add("box")
        gmsh.model.occ.addBox(0, 0, 0, 2.0, 0.2, 0.2)
        gmsh.model.occ.synchronize()
        walls, _every = gw._infer_wall_surface_tags(gmsh)
        allp = gw._sample_wall_points(gmsh, per_entity=16)
        wallp = gw._sample_wall_points(gmsh, per_entity=16, tags=walls)
        assert 0 < len(wallp) < len(allp)
    finally:
        gmsh.clear()


# ---------------------------------------------------------------------------
# Step 1c: generated-mesh patch rename -> boundary file
# ---------------------------------------------------------------------------
_BOUNDARY = """FoamFile
{
    version     2.0;
    format      ascii;
    class       polyBoundaryMesh;
    object      boundary;
}
2
(
    surface_1
    {
        type            patch;
        physicalType    patch;
        nFaces          10;
        startFace       0;
    }
    surface_2
    {
        type            patch;
        physicalType    patch;
        nFaces          5;
        startFace       10;
    }
)
"""


def _make_case(tmp_path: Path) -> Path:
    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "boundary").write_text(_BOUNDARY, encoding="latin-1")
    return tmp_path


def test_rename_boundary_patch_writes_and_backs_up(tmp_path):
    case = _make_case(tmp_path)
    rename_boundary_patch(case, "surface_1", "inlet")
    text = (case / "constant" / "polyMesh" / "boundary").read_text("latin-1")
    assert "    inlet\n" in text
    assert "    surface_1\n" not in text
    # surface_2 untouched, patch data preserved
    names = {p.name for p in parse_boundary(case / "constant" / "polyMesh" / "boundary")}
    assert names == {"inlet", "surface_2"}
    # backup holds the ORIGINAL name
    bak = case / "constant" / "polyMesh" / "boundary.bak"
    assert bak.exists()
    assert "    surface_1\n" in bak.read_text("latin-1")
    # a second rename must not overwrite the original backup
    rename_boundary_patch(case, "surface_2", "outlet")
    assert "    surface_1\n" in bak.read_text("latin-1")


def test_rename_boundary_patch_rejects_bad_names(tmp_path):
    case = _make_case(tmp_path)
    with pytest.raises(ValueError):
        rename_boundary_patch(case, "surface_1", "surface_2")  # duplicate
    with pytest.raises(ValueError):
        rename_boundary_patch(case, "surface_1", "bad name")   # whitespace
    with pytest.raises(ValueError):
        rename_boundary_patch(case, "surface_1", "bad;name")   # structural
    with pytest.raises(ValueError):
        rename_boundary_patch(case, "nope", "ok")
    with pytest.raises(ValueError):
        rename_boundary_patch(case, "surface_1", "   ")


def test_rename_boundary_patch_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        rename_boundary_patch(tmp_path, "a", "b")


# ---------------------------------------------------------------------------
# Step 2: boundary-manifold validation (checkMesh "multiply connected")
# ---------------------------------------------------------------------------
def test_boundary_manifold_defects_detects_shared_edge():
    from polyfoammesh.core.bl_poly import _boundary_manifold_defects

    # closed tetrahedron boundary: every edge shared by exactly 2 faces
    tetra = [[0, 1, 2], [0, 3, 1], [0, 2, 3], [1, 3, 2]]
    assert _boundary_manifold_defects(tetra, 0) == 0
    # duplicating a face makes 3 of its edges incidence 3 (non-manifold)
    assert _boundary_manifold_defects(tetra + [[0, 1, 2]], 0) == 3
    # internal faces (before n_int) are ignored
    assert _boundary_manifold_defects([[9, 9, 9]] + tetra, 1) == 0

def test_display_patch_type_prefers_inferred_role():
    pytest.importorskip("pyvista")
    from polyfoammesh.gui.viewer_widget import ViewerWidget

    # self is unused by the helper, so call it unbound (no QApplication needed).
    roles = {"surface_1": "inlet", "surface_2": "wall", "surface_3": "outlet"}
    types = {"surface_1": "patch", "surface_2": "patch", "surface_3": "patch"}
    assert ViewerWidget._display_patch_type(None, "surface_1", types, roles) == "inlet"
    assert ViewerWidget._display_patch_type(None, "surface_2", types, roles) == "wall"
    assert ViewerWidget._display_patch_type(None, "surface_3", types, roles) == "outlet"
    # no role -> fall back to the name/type classifier (raw surface_N -> other)
    assert ViewerWidget._display_patch_type(None, "surface_9", types, {}) == "other"
    # a named wall still classifies as wall via detect_patch_type
    assert ViewerWidget._display_patch_type(None, "myWall", {"myWall": "wall"}, {}) == "wall"
