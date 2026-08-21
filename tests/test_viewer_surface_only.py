"""Regression: "Surface + Edges" must show only the mesh's outer boundary.

Before this fix, both "Volume Mesh" and "Surface + Edges" rendered the
SAME full internal.vtu (every 3D cell in the whole volume) as a full
wireframe — so a poly mesh with any real cell count looked like solid
static from outside: every internal cell edge, from every cell, was
superimposed in screen space. That visual noise has nothing to do with
whether the mesh is actually well-graded; it happens even on a perfectly
graded mesh, because you're looking straight through the whole volume.
"""
from __future__ import annotations

import numpy as np
import pytest

pv = pytest.importorskip("pyvista")
vtk_common = pytest.importorskip("vtkmodules.vtkCommonDataModel")

from cfmesh_autogui.gui.viewer_widget import ViewerWidget  # noqa: E402


def _two_adjacent_hex_cells() -> "pv.UnstructuredGrid":
    """Two unit-cube hexahedra sharing one face: a small, fully analytic
    fixture (no bundled example data) with a known-exact boundary face
    count (12 total faces - 2 shared = 10) — a stable regression baseline
    that doesn't depend on pyvista's example dataset staying unchanged."""
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],  # cube 1
        [2, 0, 0], [2, 1, 0], [2, 0, 1], [2, 1, 1],  # cube 2's extra points
    ], dtype=float)
    cell1 = [8, 0, 1, 2, 3, 4, 5, 6, 7]
    cell2 = [8, 1, 8, 9, 2, 5, 10, 11, 6]
    cells = np.array(cell1 + cell2)
    cell_types = np.array([vtk_common.VTK_HEXAHEDRON, vtk_common.VTK_HEXAHEDRON])
    return pv.UnstructuredGrid(cells, cell_types, pts)


def test_surface_edges_mode_extracts_only_the_boundary():
    grid = _two_adjacent_hex_cells()
    assert grid.n_cells == 2  # 2 volumetric cells, 12 faces if unshared

    out = ViewerWidget._prepare_internal_vtu_grid(grid, "surface_edges")

    assert isinstance(out, pv.PolyData)
    # exactly the 10 EXTERNAL faces — the 2 internal (shared) faces between
    # the two hexahedra must be excluded, not rendered through the surface
    assert out.n_cells == 10


def test_surface_edges_mode_preserves_true_polygon_shapes():
    """The whole point of wireframing instead of surface-filling: a poly
    cell's n-gon face must stay an n-gon, not get triangulated — otherwise
    a 100% polyhedral mesh visually looks like a tet mesh."""
    grid = _two_adjacent_hex_cells()
    out = ViewerWidget._prepare_internal_vtu_grid(grid, "surface_edges")
    face_sizes = {len(cell.point_ids) for cell in out.cell}
    assert face_sizes == {4}  # every boundary face is a quad, none triangulated


def test_volume_mesh_mode_is_left_untouched():
    """"Volume Mesh" exists specifically to inspect internal structure —
    it must NOT be surface-extracted."""
    grid = _two_adjacent_hex_cells()
    out = ViewerWidget._prepare_internal_vtu_grid(grid, "internal")
    assert out is grid
    assert out.n_cells == grid.n_cells

def test_find_internal_vtu_checks_constant_dir():
    """foamToVTK -constant writes under constant/<name>/; the viewer must
    find internal.vtu there (T3.3), not only under <case>/<name>/."""
    import tempfile
    from pathlib import Path
    from cfmesh_autogui.gui.viewer_widget import _find_internal_vtu
    with tempfile.TemporaryDirectory() as td:
        case = Path(td)
        # Only the constant/ variant exists (the -constant output location).
        target = case / "constant" / "VTK_view" / "0_0" / "internal.vtu"
        target.parent.mkdir(parents=True)
        target.write_text("dummy", encoding="utf-8")
        found = _find_internal_vtu(case)
        assert found and found[0] == target
