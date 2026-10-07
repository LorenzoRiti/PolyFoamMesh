"""Regression: the initial post-meshing display must respect the
representation the UI actually shows.

show_mesh() schedules _delayed_display_mesh() via a timer.  That method
used to call self._display_mesh() UNCONDITIONALLY — the full internal
wireframe view — regardless of what the dropdown showed, so right after
meshing the dropdown read one thing and the mesh rendered another.

2026-10-06: the display combo was unified to ParaView-like
representations (Surface / Surface with Edges / Wireframe) applied to
whatever is shown (CAD or mesh), so this test now pins the NEW dispatch:
Wireframe -> _display_mesh, Surface -> _display_mesh_surface,
Surface with Edges -> _display_surface_edges, and the CAD path wins when
CAD is what is being shown.

Uses object.__new__ to build a bare ViewerWidget instance without running
__init__ (which needs a real Qt/VTK plotter) — only the attributes
_delayed_display_mesh actually touches are set.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from polyfoammesh.gui.viewer_widget import VIEW_MODES, ViewerWidget  # noqa: E402


def _bare_widget(
    current_index: int, enabled: bool = True, showing_cad: bool = False,
    cad_meshes: list | None = None,
) -> ViewerWidget:
    w = ViewerWidget.__new__(ViewerWidget)
    w._view_selector = MagicMock()
    w._view_selector.isEnabled.return_value = enabled
    w._view_selector.currentIndex.return_value = current_index
    w._showing_cad = showing_cad
    w._cad_meshes = cad_meshes if cad_meshes is not None else []
    w._display_mesh = MagicMock()
    w._display_surface_edges = MagicMock()
    w._display_mesh_surface = MagicMock()
    w._display_cad = MagicMock()
    return w


def test_surface_with_edges_selected_dispatches_to_surface_edges():
    w = _bare_widget(VIEW_MODES.index("Surface with Edges"))
    w._delayed_display_mesh()
    w._display_surface_edges.assert_called_once()
    w._display_mesh.assert_not_called()
    w._display_mesh_surface.assert_not_called()
    w._display_cad.assert_not_called()


def test_wireframe_selected_dispatches_to_display_mesh():
    w = _bare_widget(VIEW_MODES.index("Wireframe"))
    w._delayed_display_mesh()
    w._display_mesh.assert_called_once()
    w._display_surface_edges.assert_not_called()
    w._display_mesh_surface.assert_not_called()
    w._display_cad.assert_not_called()


def test_surface_selected_dispatches_to_mesh_surface():
    w = _bare_widget(VIEW_MODES.index("Surface"))
    w._delayed_display_mesh()
    w._display_mesh_surface.assert_called_once()
    w._display_mesh.assert_not_called()
    w._display_surface_edges.assert_not_called()
    w._display_cad.assert_not_called()


def test_cad_showing_wins_over_representation():
    w = _bare_widget(
        VIEW_MODES.index("Surface with Edges"), showing_cad=True,
        cad_meshes=[object()],
    )
    w._delayed_display_mesh()
    w._display_cad.assert_called_once()
    w._display_mesh.assert_not_called()
    w._display_surface_edges.assert_not_called()
    w._display_mesh_surface.assert_not_called()


def test_disabled_selector_is_a_no_op():
    w = _bare_widget(VIEW_MODES.index("Surface with Edges"), enabled=False)
    w._delayed_display_mesh()
    w._display_mesh.assert_not_called()
    w._display_surface_edges.assert_not_called()
    w._display_mesh_surface.assert_not_called()
    w._display_cad.assert_not_called()
