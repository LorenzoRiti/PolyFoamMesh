"""Regression: clicking empty 3D space clears the patch selection.

User report: a patch selected from the table stayed highlighted forever;
clicking the empty background did nothing, which made the selection
useless.  A left-click that hits no actor must clear both the highlight
and the table row.

Second report (2026-10-09): deselect STILL did not work, because
``pick_mouse_position()`` returns the picker's STALE actor on a miss
(classic VTK behaviour), so "nothing hit" was never observed.  The
handler now uses a FRESH vtkPropPicker, and clicking the highlighted
patch again toggles the selection off (``_on_mesh_picked``).
"""
from __future__ import annotations

import os
from typing import cast
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from polyfoammesh.gui.viewer_widget import ViewerWidget  # noqa: E402


class _FakePicker:
    actor = None

    def Pick(self, *_args):  # noqa: N802 - VTK API name
        return 1 if self.actor is not None else 0

    def GetActor(self):  # noqa: N802 - VTK API name
        return self.actor


def _bare(selected: str | None = "wall", actor=None) -> ViewerWidget:
    w = ViewerWidget.__new__(ViewerWidget)
    w._plotter = MagicMock()
    w._plotter.iren.get_event_position.return_value = (10, 10)
    w._plotter.renderer = object()
    w._selected_patch_name = selected
    w._patch_table = MagicMock()
    w._redisplay_current = MagicMock()
    return w


def _table(w: ViewerWidget) -> MagicMock:
    return cast(MagicMock, w._patch_table)


@pytest.fixture
def fake_picker(monkeypatch):
    import vtk

    monkeypatch.setattr(vtk, "vtkPropPicker", _FakePicker)
    yield _FakePicker


def test_click_on_empty_clears_selection(fake_picker):
    w = _bare()
    fake_picker.actor = None
    w._on_viewer_click()
    assert w._selected_patch_name is None
    _table(w).clearSelection.assert_called_once()
    _table(w).setCurrentItem.assert_called_once_with(None)
    w._redisplay_current.assert_called_once()


def test_click_on_an_actor_keeps_selection(fake_picker):
    w = _bare()
    fake_picker.actor = object()
    w._on_viewer_click()
    assert w._selected_patch_name == "wall"
    _table(w).clearSelection.assert_not_called()
    w._redisplay_current.assert_not_called()


def test_click_without_selection_is_a_noop(fake_picker):
    w = _bare(selected=None)
    fake_picker.actor = None
    w._on_viewer_click()
    _table(w).clearSelection.assert_not_called()


def test_picking_the_highlighted_patch_again_clears_it():
    """Toggle: ``_on_mesh_picked`` on the currently selected patch clears."""
    w = ViewerWidget.__new__(ViewerWidget)
    actor = MagicMock()
    actor.GetAddressAsString.return_value = "addr-wall"
    w._pvdata_to_name = {"addr-wall": "wall"}
    w._selected_patch_name = "wall"
    w._patch_table = MagicMock()
    w._redisplay_current = MagicMock()
    w._on_mesh_picked(actor)
    assert w._selected_patch_name is None
    w._redisplay_current.assert_called_once()


def test_picking_unknown_actor_is_a_noop():
    """A None actor (miss) or an unmapped actor must not touch the state."""
    w = ViewerWidget.__new__(ViewerWidget)
    w._pvdata_to_name = {}
    w._selected_patch_name = "wall"
    w._patch_table = MagicMock()
    w._redisplay_current = MagicMock()
    w._on_mesh_picked(None)
    actor = MagicMock()
    actor.GetAddressAsString.return_value = "addr-other"
    w._on_mesh_picked(actor)
    assert w._selected_patch_name == "wall"
    w._redisplay_current.assert_not_called()
