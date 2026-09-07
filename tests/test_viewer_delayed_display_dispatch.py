"""Regression: the initial post-meshing display must respect the view
mode the UI actually shows.

show_mesh() sets the view-mode dropdown to "Surface + Edges" and then
schedules _delayed_display_mesh() via a timer. _delayed_display_mesh()
used to call self._display_mesh() UNCONDITIONALLY — the "Volume Mesh"
full internal-wireframe view — regardless of what the dropdown showed.
So right after meshing, the dropdown read "Surface + Edges" but the mesh
actually rendered was always the full internal wireframe (every internal
cell edge of the whole volume visible) — the "i tet si vedono ancora"
symptom survived even after fixing _display_surface_edges()'s own
rendering, because that method was never being called on this path.

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


def _bare_widget(current_index: int, enabled: bool = True) -> ViewerWidget:
    w = ViewerWidget.__new__(ViewerWidget)
    w._view_selector = MagicMock()
    w._view_selector.isEnabled.return_value = enabled
    w._view_selector.currentIndex.return_value = current_index
    w._display_mesh = MagicMock()
    w._display_surface_edges = MagicMock()
    w._display_cad = MagicMock()
    return w


def test_surface_edges_selected_dispatches_to_surface_edges():
    w = _bare_widget(VIEW_MODES.index("Surface + Edges"))
    w._delayed_display_mesh()
    w._display_surface_edges.assert_called_once()
    w._display_mesh.assert_not_called()
    w._display_cad.assert_not_called()


def test_volume_mesh_selected_dispatches_to_display_mesh():
    w = _bare_widget(VIEW_MODES.index("Volume Mesh"))
    w._delayed_display_mesh()
    w._display_mesh.assert_called_once()
    w._display_surface_edges.assert_not_called()


def test_cad_surfaces_selected_dispatches_to_display_cad():
    w = _bare_widget(VIEW_MODES.index("CAD Surfaces"))
    w._delayed_display_mesh()
    w._display_cad.assert_called_once()
    w._display_mesh.assert_not_called()
    w._display_surface_edges.assert_not_called()


def test_disabled_selector_is_a_no_op():
    w = _bare_widget(VIEW_MODES.index("Surface + Edges"), enabled=False)
    w._delayed_display_mesh()
    w._display_mesh.assert_not_called()
    w._display_surface_edges.assert_not_called()
    w._display_cad.assert_not_called()
