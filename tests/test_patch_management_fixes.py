"""Patch-management fixes (user report 2026-11-17).

Three real defects reported on the patch panel:

1. A rename typed with a space (or any ``;{}"/`` char) was silently
   REVERTED instead of applied — "non le cambia veramente". The name
   must be SANITIZED (invalid chars -> ``_``) and committed.
2. Renaming froze the GUI: ``_rename_cad_patch`` called
   ``set_suggest_meshes`` which recomputes the per-patch sizes
   synchronously (numpy ray-casts) on the GUI thread. A rename only
   touches mesh metadata, so it must only DROP the caches.
3. ESC must clear the patch selection (the empty-click deselect works
   only on background pixels; a keyboard escape is the natural exit).

Plus the requested Role column: the patch panel must let the user
declare what each surface IS (auto / inlet / outlet / wall), and the
override must steer the BL wall selection and the case-setup BC roles.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QApplication,
    QTableWidget,
)

from polyfoammesh.gui.viewer_widget import (  # noqa: E402
    ViewerWidget,
    sanitize_patch_name,
)


class _NameItem:
    """Minimal QTableWidgetItem stand-in for a Name cell."""

    def __init__(self, row: int, text: str):
        self._row = row
        self._text = text

    def column(self) -> int:
        return 0

    def row(self) -> int:
        return self._row

    def text(self) -> str:
        return self._text

    def setText(self, value: str) -> None:  # noqa: N802 - Qt API name
        self._text = value


def _bare_viewer(names: list[str]) -> ViewerWidget:
    w = ViewerWidget.__new__(ViewerWidget)
    w._patch_rename_guard = False
    w._patch_names = list(names)
    w._showing_cad = True
    w._patch_table = MagicMock()
    w.patch_renamed = MagicMock()  # shadows the Signal for emit capture
    w.mesh_patch_renamed = MagicMock()
    w.patch_role_changed = MagicMock()
    w._redisplay_current = MagicMock()
    w._patch_role_overrides = {}
    return w


# ---------------------------------------------------------------------------
# 1. sanitize_patch_name — pure function
# ---------------------------------------------------------------------------
def test_sanitize_replaces_invalid_chars_with_underscores():
    assert sanitize_patch_name("my patch") == "my_patch"
    assert sanitize_patch_name("in;let") == "in_let"
    assert sanitize_patch_name('a{b}"c') == "a_b_c"
    assert sanitize_patch_name("plain") == "plain"


def test_sanitize_drops_leading_trailing_underscores():
    assert sanitize_patch_name("  inlet ") == "inlet"
    assert sanitize_patch_name("/outlet/") == "outlet"


def test_sanitize_of_only_invalid_chars_is_empty():
    assert sanitize_patch_name(" / ; ") == ""
    assert sanitize_patch_name("   ") == ""


# ---------------------------------------------------------------------------
# 1. table rename commits the sanitized name instead of reverting
# ---------------------------------------------------------------------------
def test_rename_with_space_is_committed_sanitized():
    w = _bare_viewer(["ASCII"])
    item = _NameItem(0, "my patch")
    w._patch_table.item = MagicMock(return_value=item)
    w._on_patch_item_changed(item)
    assert w._patch_names == ["my_patch"]
    assert item.text() == "my_patch"
    w.patch_renamed.emit.assert_called_once_with("ASCII", "my_patch")


def test_rename_collision_gets_a_numbered_suffix():
    w = _bare_viewer(["ASCII", "inlet"])
    item = _NameItem(0, "inlet")
    w._patch_table.item = MagicMock(return_value=item)
    w._on_patch_item_changed(item)
    assert w._patch_names == ["inlet_2", "inlet"]
    w.patch_renamed.emit.assert_called_once_with("ASCII", "inlet_2")


def test_rename_of_only_invalid_chars_still_reverts():
    w = _bare_viewer(["ASCII"])
    item = _NameItem(0, " / ; ")
    w._patch_table.item = MagicMock(return_value=item)
    w._on_patch_item_changed(item)
    assert w._patch_names == ["ASCII"]
    assert item.text() == "ASCII"
    w.patch_renamed.emit.assert_not_called()


def test_rename_on_generated_mesh_emits_mesh_signal():
    w = _bare_viewer(["surface_1"])
    w._showing_cad = False
    item = _NameItem(0, "wall x")
    w._patch_table.item = MagicMock(return_value=item)
    w._on_patch_item_changed(item)
    w.mesh_patch_renamed.emit.assert_called_once_with("surface_1", "wall_x")


def test_rename_follows_the_role_override_key():
    w = _bare_viewer(["ASCII"])
    w._patch_role_overrides = {"ASCII": "inlet"}
    item = _NameItem(0, "inlet2")
    w._patch_table.item = MagicMock(return_value=item)
    w._on_patch_item_changed(item)
    assert w._patch_role_overrides == {"inlet2": "inlet"}


# ---------------------------------------------------------------------------
# 2. rename must not recompute the sizing (GUI freeze)
# ---------------------------------------------------------------------------
def test_rename_cad_patch_skips_suggest_meshes_and_drops_caches():
    from polyfoammesh.gui.main_window import MainWindow

    mw = MainWindow.__new__(MainWindow)
    mesh = SimpleNamespace(metadata={"name": "ASCII"})
    mw._meshes = [mesh]
    mw._params = MagicMock()
    mw._viewer = MagicMock()
    mw._log = MagicMock()

    mw._rename_cad_patch("ASCII", "inlet2")

    assert mesh.metadata["name"] == "inlet2"
    mw._params.set_suggest_meshes.assert_not_called()
    mw._params.invalidate_mesh_caches.assert_called_once_with()
    mw._viewer.show_cad.assert_called_once_with(mw._meshes)
    mw._viewer.highlight_patch.assert_called_once_with("inlet2")


def test_params_invalidate_mesh_caches_drops_caches_without_recompute(monkeypatch):
    pytest.importorskip("PySide6")
    QApplication.instance() or QApplication([])
    from polyfoammesh.gui.params_panel import ParamsPanel

    panel = ParamsPanel()
    panel._feature_min_cell_cache = {"a": 1}
    panel._patch_sizes_cache = {"b": 2}

    def _boom(*_a, **_k):
        raise AssertionError("_on_detail_changed must not run on cache drop")

    monkeypatch.setattr(ParamsPanel, "_on_detail_changed", _boom)
    panel.invalidate_mesh_caches()
    assert panel._feature_min_cell_cache == {}
    assert panel._patch_sizes_cache == {}


# ---------------------------------------------------------------------------
# 2b. 3D-click rename sanitizes too (no more "Invalid Name" dead end)
# ---------------------------------------------------------------------------
def test_face_picked_rename_sanitizes_input(monkeypatch):
    from polyfoammesh.gui import main_window as mw_mod
    from polyfoammesh.gui.main_window import MainWindow

    mw = MainWindow.__new__(MainWindow)
    mw._viewer = MagicMock()
    mw._viewer.is_showing_cad.return_value = True
    mw._viewer.unique_patch_name = lambda base: base
    mw._log = MagicMock()
    monkeypatch.setattr(
        mw_mod.QInputDialog, "getText",
        lambda *a, **k: ("my patch", True),
    )
    seen: dict = {}
    mw._rename_cad_patch = lambda old, new: seen.update(old=old, new=new)

    mw._on_face_picked("ASCII")

    assert seen == {"old": "ASCII", "new": "my_patch"}


def test_face_picked_rename_of_only_invalid_chars_warns_and_skips(monkeypatch):
    from polyfoammesh.gui import main_window as mw_mod
    from polyfoammesh.gui.main_window import MainWindow

    mw = MainWindow.__new__(MainWindow)
    mw._viewer = MagicMock()
    mw._viewer.is_showing_cad.return_value = True
    mw._viewer.unique_patch_name = lambda base: base
    mw._log = MagicMock()
    monkeypatch.setattr(
        mw_mod.QInputDialog, "getText",
        lambda *a, **k: (" / ; ", True),
    )
    warned: list = []
    monkeypatch.setattr(
        mw_mod.QMessageBox, "warning", lambda *a, **k: warned.append(a)
    )
    seen: dict = {}
    mw._rename_cad_patch = lambda old, new: seen.update(old=old, new=new)

    mw._on_face_picked("ASCII")

    assert seen == {} and warned


# ---------------------------------------------------------------------------
# 3. ESC clears the patch selection
# ---------------------------------------------------------------------------
def _esc_viewer(selected: str | None = "wall") -> ViewerWidget:
    w = ViewerWidget.__new__(ViewerWidget)
    w._selected_patch_name = selected
    w._patch_table = MagicMock()
    w._patch_table.state.return_value = 0  # not editing
    w._redisplay_current = MagicMock()
    return w


def _esc_event() -> QKeyEvent:
    return QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)


def test_esc_clears_the_patch_selection():
    QApplication.instance() or QApplication([])
    w = _esc_viewer()
    assert w.eventFilter(w, _esc_event()) is True
    assert w._selected_patch_name is None
    w._redisplay_current.assert_called_once()


def test_esc_while_the_table_is_editing_is_left_to_the_editor():
    QApplication.instance() or QApplication([])
    w = _esc_viewer()
    # The REAL flag value: State comparisons must use the enum's .value
    # (PySide6 raises TypeError on flags & member — measured).
    w._patch_table.state.return_value = QAbstractItemView.EditingState.value
    assert w.eventFilter(w, _esc_event()) is False
    assert w._selected_patch_name == "wall"


def test_esc_without_a_selection_is_not_consumed():
    QApplication.instance() or QApplication([])
    w = _esc_viewer(selected=None)
    assert w.eventFilter(w, _esc_event()) is False
    w._redisplay_current.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Role column: user-declared surface role
# ---------------------------------------------------------------------------
def _panel_viewer() -> ViewerWidget:
    QApplication.instance() or QApplication([])
    w = ViewerWidget.__new__(ViewerWidget)
    w._patch_rename_guard = False
    w._patch_names = []
    w._patch_table = QTableWidget(0, 3)
    w.patch_renamed = MagicMock()
    w.mesh_patch_renamed = MagicMock()
    w.patch_role_changed = MagicMock()
    w._patch_role_overrides = {}
    w._patch_panel = MagicMock()
    return w


def test_populate_builds_role_combos_from_the_inferred_type():
    w = _panel_viewer()
    w._populate_patch_panel([
        ("surface_1", 12, "other"),
        ("myWall", 8, "wall"),
        ("inlet", 4, "inlet"),
    ])
    combos = [w._patch_table.cellWidget(r, 2) for r in range(3)]
    assert [c.currentText() for c in combos] == ["auto", "wall", "inlet"]


def test_real_panel_has_role_header_and_esc_filter(monkeypatch):
    """The production panel (real _build_patch_panel) wires the Role header,
    the override store and the ESC filter."""
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(ViewerWidget, "_init_vtk", lambda self: None)
    w = ViewerWidget()
    try:
        assert w._patch_table.horizontalHeaderItem(2).text() == "Role"
        assert w._patch_role_overrides == {}
        # no selection yet: ESC is not consumed
        assert w.eventFilter(w, _esc_event()) is False
        # a selection is cleared by ESC on the real panel
        w._selected_patch_name = "wall"
        assert w.eventFilter(w, _esc_event()) is True
        assert w._selected_patch_name is None
    finally:
        w.deleteLater()


def test_role_combo_change_stores_and_emits_the_override():
    w = _panel_viewer()
    w._populate_patch_panel([("surface_1", 12, "other")])
    combo = w._patch_table.cellWidget(0, 2)

    combo.setCurrentText("inlet")
    assert w._patch_role_overrides == {"surface_1": "inlet"}
    w.patch_role_changed.emit.assert_called_with("surface_1", "inlet")

    combo.setCurrentText("auto")
    assert w._patch_role_overrides == {}
    w.patch_role_changed.emit.assert_called_with("surface_1", "")


def test_patch_role_override_accessor():
    w = _panel_viewer()
    w._patch_role_overrides = {"surface_1": "outlet"}
    assert w.patch_role_override("surface_1") == "outlet"
    assert w.patch_role_override("missing") is None


def test_case_patch_rename_remaps_the_role_override():
    w = _bare_viewer(["surface_1"])
    w._mesh_patches = {"surface_1": object()}
    w._patch_roles = {"surface_1": "wall"}
    w._patch_role_overrides = {"surface_1": "inlet"}
    w.apply_case_patch_rename("surface_1", "wall_flat")
    assert w._patch_role_overrides == {"wall_flat": "inlet"}


# ---------------------------------------------------------------------------
# 4b. the override steers the BL wall selection and case-setup roles
# ---------------------------------------------------------------------------
def _mw_with_roles(overrides: dict[str, str]):
    from polyfoammesh.gui.main_window import MainWindow

    mw = MainWindow.__new__(MainWindow)
    mw._params = SimpleNamespace(patch_role_overrides=dict(overrides))
    return mw


def test_user_role_override_beats_the_name_classifier():
    mw = _mw_with_roles({"surface_1": "inlet"})
    assert mw._user_is_wall_patch("surface_1") is False

    mw = _mw_with_roles({"surface_9": "wall"})
    assert mw._user_is_wall_patch("surface_9") is True

    mw = _mw_with_roles({})
    assert mw._user_is_wall_patch("inlet") is False
    assert mw._user_is_wall_patch("wall") is True


def test_case_setup_kwargs_carries_explicit_role_overrides():
    mw = _mw_with_roles({"surface_1": "inlet", "gone": ""})
    assert mw._case_setup_kwargs() == {"patch_roles": {"surface_1": "inlet"}}

    mw = _mw_with_roles({})
    assert mw._case_setup_kwargs() == {}
