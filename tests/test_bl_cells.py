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


# The viewer BL overlay was REMOVED (user request: the orange colouring
# and its scalar bar were unreadable - the layers are visible through the
# mesh edges, as in the previous release).  These tests pin that removal
# so the overlay cannot creep back in.
pytest.importorskip("PySide6")


def test_no_bl_overlay_left_on_a_real_widget(monkeypatch):
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
        assert not hasattr(w, "_bl_check")
        for name in (
            "_maybe_apply_bl_overlay", "_apply_bl_display",
            "_render_bl_grid", "_apply_bl_mask_to_grid", "_reset_bl_control",
        ):
            assert not hasattr(w, name), name
        assert not hasattr(w, "_bl_cells")
        assert not hasattr(w, "_bl_active")
    finally:
        w.deleteLater()
