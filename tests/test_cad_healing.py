"""CAD healing (commercial/cad_healer.py) must run BEFORE the watertight
check, not just at STL export time.

Degenerate-face removal / hole-filling already ran automatically before every
STL export, but only there, only at DEBUG-level logging the user never sees,
and only well after the watertight check and cell-size sizing had already
looked at the RAW mesh. A CAD file with one small (trivially fillable) hole
would fail the watertight check with a scary "not watertight" warning that
healing would have fixed anyway — a false alarm, exactly the kind of
"impazzimento" this app is meant to prevent.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
trimesh = pytest.importorskip("trimesh")
np = pytest.importorskip("numpy")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _FakeLog:
    def __init__(self):
        self.lines: list[str] = []

    def append_log(self, s: str) -> None:
        self.lines.append(s)


def _fake_window():
    return types.SimpleNamespace(_log=_FakeLog(), _refresh_workflow=lambda **k: None)


def _box_with_one_hole():
    box = trimesh.creation.box(extents=(1, 1, 1))
    box.metadata["name"] = "wall"
    keep = np.ones(len(box.faces), dtype=bool)
    keep[3] = False  # drop one interior face -> one small isolated hole
    box.update_faces(keep)
    return box


def test_a_trivially_fillable_hole_fails_watertight_before_healing():
    """Establishes the baseline the fix corrects."""
    box = _box_with_one_hole()
    assert not box.is_watertight


def test_heal_geometry_fixes_the_hole_and_logs_it(app):
    from cfmesh_autogui.gui.main_window import MainWindow

    box = _box_with_one_hole()
    win = _fake_window()

    MainWindow._heal_geometry(win, [box])

    assert box.is_watertight
    assert any("filled" in line and "hole" in line for line in win._log.lines)


def test_watertight_check_reports_ok_after_healing_runs_first(app):
    """The actual regression: without healing-before-checking, this mesh would
    warn "not watertight" even though the defect is trivially repairable."""
    from cfmesh_autogui.gui.main_window import MainWindow
    from cfmesh_autogui.gui.log_tags import Tag

    box = _box_with_one_hole()
    win = _fake_window()

    MainWindow._heal_geometry(win, [box])
    MainWindow._check_watertight(win, [box])

    assert any(Tag.WARN in line for line in win._log.lines) is False
    assert any("OK" in line for line in win._log.lines)


def test_healing_is_a_no_op_on_already_clean_geometry(app):
    """A mesh with no defects should heal without spurious log noise."""
    from cfmesh_autogui.gui.main_window import MainWindow

    box = trimesh.creation.box(extents=(1, 1, 1))
    box.metadata["name"] = "wall"
    box.merge_vertices()
    box.fix_normals()
    win = _fake_window()

    MainWindow._heal_geometry(win, [box])

    assert box.is_watertight
