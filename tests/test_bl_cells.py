"""Boundary-layer cell classification + "Show BL" viewer smoke tests.

Builds tiny synthetic OpenFOAM polyMesh cases (no real mesher) whose
topology is exact, so the prism detection and the flood fill can be pinned
against a known answer.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import numpy as np
import pytest

if TYPE_CHECKING:
    from polyfoammesh.gui.viewer_widget import ViewerWidget

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from polyfoammesh.core.bl_cells import classify_bl_cells  # noqa: E402
from polyfoammesh.core.foam_mesh_io import write_polymesh  # noqa: E402


def _pentagon(radius: float, z: float, cx: float = 0.0, cy: float = 0.0):
    return [
        (
            cx + radius * math.cos(math.radians(90 + 72 * k)),
            cy + radius * math.sin(math.radians(90 + 72 * k)),
            z,
        )
        for k in range(5)
    ]


def _write_case(case_dir: Path, points, faces, owner, neighbour, patches) -> None:
    poly = case_dir / "constant" / "polyMesh"
    poly.mkdir(parents=True, exist_ok=True)
    write_polymesh(
        poly,
        np.asarray(points, dtype=np.float64),
        faces,
        np.asarray(owner, dtype=np.int32),
        np.asarray(neighbour, dtype=np.int32),
        patches,
    )


def _stacked_pentagon_prisms_and_hexes(case_dir: Path) -> None:
    """2 stacked pentagonal prisms sharing a face + 2 disjoint hexes.

    Cell 0/1 are the stacked prisms (wall-adjacent seed = cell 0; flood must
    reach cell 1). Cells 2/3 are hexahedra (all-quad faces, so NOT prism-like)
    sharing one internal face — they must stay unlabelled.
    """
    points = (
        _pentagon(1.0, 0.0)
        + _pentagon(1.0, 1.0)
        + _pentagon(1.0, 2.0)
        + [
            (10, 0, 0), (11, 0, 0), (11, 1, 0), (10, 1, 0),
            (10, 0, 1), (11, 0, 1), (11, 1, 1), (10, 1, 1),
            (12, 0, 0), (12, 1, 0), (12, 0, 1), (12, 1, 1),
        ]
    )
    faces = [
        # internal faces first
        [5, 6, 7, 8, 9],                 # F0 prism0 top == prism1 base
        [16, 17, 21, 20],                # F1 hex2/hex3 shared quad
        # wall patch: the lower prism's base pentagon
        [0, 1, 2, 3, 4],                 # F2
        # "outlet" patch: every other boundary face
        [0, 1, 6, 5], [1, 2, 7, 6], [2, 3, 8, 7], [3, 4, 9, 8],
        [4, 0, 5, 9],                    # F3..F7 lower prism sides
        [10, 11, 12, 13, 14],            # F8 upper prism top
        [5, 6, 11, 10], [6, 7, 12, 11], [7, 8, 13, 12], [8, 9, 14, 13],
        [9, 5, 10, 14],                  # F9..F13 upper prism sides
        [15, 16, 17, 18], [19, 20, 21, 22], [15, 16, 20, 19],
        [17, 18, 22, 21], [18, 15, 19, 22],   # F14..F18 hex2
        [16, 23, 24, 17], [20, 25, 26, 21], [16, 23, 25, 20],
        [17, 24, 26, 21], [23, 24, 26, 25],   # F19..F23 hex3
    ]
    owner = [0, 2, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3]
    neighbour = [1, 3]
    patches = [
        {"name": "wall", "nFaces": 1, "startFace": 2, "type": "wall"},
        {"name": "outlet", "nFaces": 21, "startFace": 3, "type": "patch"},
    ]
    _write_case(case_dir, points, faces, owner, neighbour, patches)


def _wedge_and_hex(case_dir: Path) -> None:
    """One triangular prism (m=3 wedge) + one disjoint hexahedron."""
    points = [
        (0, 0, 0), (1, 0, 0), (0, 1, 0),          # wedge base triangle
        (0, 0, 1), (1, 0, 1), (0, 1, 1),          # wedge top triangle
        (10, 0, 0), (11, 0, 0), (11, 1, 0), (10, 1, 0),
        (10, 0, 1), (11, 0, 1), (11, 1, 1), (10, 1, 1),
    ]
    faces = [
        [0, 1, 2],                        # F0 wall: wedge base triangle
        [3, 4, 5],                        # F1 top
        [0, 1, 4, 3], [1, 2, 5, 4], [2, 0, 3, 5],   # F2..F4 sides
        [6, 7, 8, 9], [10, 11, 12, 13], [6, 7, 11, 10],
        [7, 8, 12, 11], [8, 9, 13, 12], [9, 6, 10, 13],   # F5..F10 hex
    ]
    owner = [0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1]
    neighbour: list[int] = []
    patches = [
        {"name": "wall", "nFaces": 1, "startFace": 0, "type": "wall"},
        {"name": "outlet", "nFaces": 10, "startFace": 1, "type": "patch"},
    ]
    _write_case(case_dir, points, faces, owner, neighbour, patches)


# ---------------------------------------------------------------------------
# classify_bl_cells
# ---------------------------------------------------------------------------

def test_classify_labels_only_the_prism_stack(tmp_path):
    _stacked_pentagon_prisms_and_hexes(tmp_path)
    mask, count = classify_bl_cells(tmp_path)

    assert count == 2
    assert mask.dtype == bool
    assert list(mask) == [True, True, False, False]


def test_classify_labels_a_triangular_wedge(tmp_path):
    _wedge_and_hex(tmp_path)
    mask, count = classify_bl_cells(tmp_path)

    assert count == 1
    assert list(mask) == [True, False]


def test_classify_missing_case_never_raises(tmp_path):
    mask, count = classify_bl_cells(tmp_path / "does_not_exist")
    assert count == 0
    assert mask.size == 0


def test_wall_role_is_not_excluded_by_name_inference(tmp_path):
    """A patch literally named "wall" must be treated as a wall by
    infer_patch_roles even though every patch has a semantic name here."""
    from polyfoammesh.core.boundary_reader import parse_boundary
    from polyfoammesh.core.case_setup import infer_patch_roles

    _stacked_pentagon_prisms_and_hexes(tmp_path)
    boundary = tmp_path / "constant" / "polyMesh" / "boundary"
    roles = infer_patch_roles(tmp_path, parse_boundary(boundary))
    assert roles["wall"] == "wall"


# ---------------------------------------------------------------------------
# Viewer "Show BL" smoke (offscreen, no real plotter)
# ---------------------------------------------------------------------------

pytest.importorskip("PySide6")


def _bare_with_mask_attrs() -> "ViewerWidget":
    from polyfoammesh.gui.viewer_widget import ViewerWidget

    w = ViewerWidget.__new__(ViewerWidget)
    w._plotter = None
    w._showing_cad = True
    w._mesh_case_dir = ""
    w._bl_cells = None
    w._bl_grid = None
    w._bl_active = False
    w._bl_check = MagicMock()
    return w


def test_bl_toggle_off_and_on_without_plotter_does_not_raise():
    w = _bare_with_mask_attrs()
    # OFF branch hits _redisplay_current, which no-ops with no plotter.
    w._on_bl_toggled(False)
    # ON branch with no case dir logs and unchecks — must not raise.
    w._on_bl_toggled(True)
    w._bl_check.setChecked.assert_called()


def test_toggle_exists_on_a_real_widget(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from polyfoammesh.gui.viewer_widget import ViewerWidget

    # The widget schedules _init_vtk via a 0 ms timer; pytest-qt pumps the
    # event loop, which would try to build a real QtInteractor (no OpenGL
    # offscreen). Stub it so only the toolbar construction is exercised.
    monkeypatch.setattr(ViewerWidget, "_init_vtk", lambda self: None)

    w = ViewerWidget()
    try:
        assert hasattr(w, "_bl_check")
        assert w._bl_check.text() == "Show BL"
        assert w._bl_check.isVisible() is False  # CAD/no mesh -> hidden
    finally:
        w.deleteLater()


def test_apply_mask_to_grid_adds_integer_array():
    pv = pytest.importorskip("pyvista")
    from polyfoammesh.gui.viewer_widget import ViewerWidget

    pts = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [2, 0, 0], [2, 1, 0]],
        dtype=float,
    )
    grid = pv.PolyData(pts, np.array([3, 0, 1, 2, 3, 3, 4, 5]))
    assert grid.n_cells == 2

    ok = ViewerWidget._apply_bl_mask_to_grid(grid, np.array([True, False]))
    assert ok is True
    assert "bl_cells" in grid.cell_data
    assert list(grid.cell_data["bl_cells"]) == [1, 0]

    # A mask whose length does not match the grid is refused, not applied.
    assert ViewerWidget._apply_bl_mask_to_grid(grid, np.array([True])) is False


def test_apply_bl_display_paints_the_grid_and_sets_active():
    pv = pytest.importorskip("pyvista")
    from polyfoammesh.gui.viewer_widget import ViewerWidget

    pts = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [2, 0, 0], [2, 1, 0]],
        dtype=float,
    )
    grid = pv.PolyData(pts, np.array([3, 0, 1, 2, 3, 3, 4, 5]))

    w = _bare_with_mask_attrs()
    w._bl_cells = np.array([True, False])
    w._bl_grid = grid
    w._plotter = MagicMock()
    w._bg_color = (1.0, 1.0, 1.0)
    w._text_color = "black"
    w._section_enabled = False
    w._view_selector = MagicMock()
    w._view_selector.currentIndex.return_value = 1  # Surface with Edges

    w._apply_bl_display()

    assert w._bl_active is True
    assert "bl_cells" in grid.cell_data
    w._plotter.add_mesh.assert_called_once()
    w._plotter.render.assert_called_once()
