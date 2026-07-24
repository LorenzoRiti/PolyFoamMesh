"""A long mesh run must be a decision, not a surprise.

cartesianMesh prints nothing for minutes at a time, so an accidentally fine
setting is indistinguishable from a hang. The benchmark's thin_gap case needed
3.3M cells and 90 s with no warning at all before this guard existed.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from cfmesh_autogui.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def guard(app, monkeypatch):
    """Bind the guard to a stub carrying only the thresholds it reads."""
    asked: list[str] = []

    def fake_question(_parent, _title, text, *a, **kw):
        asked.append(text)
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    stub = types.SimpleNamespace(
        LARGE_MESH_CELLS=MainWindow.LARGE_MESH_CELLS,
        LARGE_MESH_SECONDS=MainWindow.LARGE_MESH_SECONDS,
    )
    return stub, asked


def test_small_mesh_starts_without_asking(guard):
    stub, asked = guard
    assert MainWindow._confirm_large_mesh(stub, 50_000, 10.0, 0.1, 0.05) is True
    assert asked == []


def test_huge_cell_count_asks_first(guard):
    stub, asked = guard
    assert MainWindow._confirm_large_mesh(stub, 3_300_000, 90.0, 0.005, 0.0005) is False
    assert len(asked) == 1


def test_long_runtime_asks_even_when_cell_count_is_modest(guard):
    """Either threshold alone is enough — a slow run is just as surprising."""
    stub, asked = guard
    assert MainWindow._confirm_large_mesh(stub, 100_000, 600.0, 0.01, 0.001) is False
    assert len(asked) == 1


def test_prompt_names_the_knob_that_controls_the_cost(guard):
    """A warning the user can't act on is just noise: show the cell sizes."""
    stub, asked = guard
    MainWindow._confirm_large_mesh(stub, 5_000_000, 120.0, 0.005, 0.0005)
    text = asked[0]
    assert "5,000,000" in text
    assert "0.005" in text and "0.0005" in text
