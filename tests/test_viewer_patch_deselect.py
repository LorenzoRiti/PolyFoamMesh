"""Regression: clicking empty 3D space clears the patch selection.

User report: a patch selected from the table stayed highlighted forever;
clicking the empty background did nothing, which made the selection
useless.  A left-click that hits no actor must clear both the highlight
and the table row.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from polyfoammesh.gui.viewer_widget import ViewerWidget  # noqa: E402


def _bare(selected="wall", pick=(None, None)) -> ViewerWidget:
    w = ViewerWidget.__new__(ViewerWidget)
    w._plotter = MagicMock()
    w._plotter.pick_mouse_position.return_value = pick
    w._selected_patch_name = selected
    w._patch_table = MagicMock()
    w._redisplay_current = MagicMock()
    return w


def test_click_on_empty_clears_selection():
    w = _bare()
    w._on_viewer_click()
    assert w._selected_patch_name is None
    w._patch_table.clearSelection.assert_called_once()
    w._patch_table.setCurrentItem.assert_called_once_with(None)
    w._redisplay_current.assert_called_once()


def test_click_on_an_actor_keeps_selection():
    w = _bare(pick=(object(), (0.0, 0.0, 0.0)))
    w._on_viewer_click()
    assert w._selected_patch_name == "wall"
    w._patch_table.clearSelection.assert_not_called()
    w._redisplay_current.assert_not_called()


def test_click_without_selection_is_a_noop():
    w = _bare(selected=None)
    w._on_viewer_click()
    w._plotter.pick_mouse_position.assert_not_called()
    w._patch_table.clearSelection.assert_not_called()
