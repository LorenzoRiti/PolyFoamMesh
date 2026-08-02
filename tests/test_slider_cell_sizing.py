"""Regression tests for the single cell-count slider sizing path."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cfmesh_autogui.core.geometry import validate_cell_sizes  # noqa: E402
from cfmesh_autogui.gui.params_panel import ParamsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app):
    return ParamsPanel()


def test_very_fine_small_geometry_keeps_strict_max_min_order(panel):
    """The old 1e-4 spinbox floor made both values equal on 2mm parts."""
    panel.set_bbox(0.002, 0.002, 0.002)
    panel._detail_slider.setValue(20)
    assert panel.get_target_cells() == 20_000_000
    assert 0 < panel.get_min_cell() < panel.get_max_cell()
    assert panel._validate_params()


def test_bbox_change_recomputes_sizes_from_same_slider(panel):
    panel._detail_slider.setValue(10)
    panel.set_bbox(0.002, 0.002, 0.002)
    small = (panel.get_max_cell(), panel.get_min_cell())
    panel.set_bbox(2.0, 2.0, 2.0)
    large = (panel.get_max_cell(), panel.get_min_cell())
    assert large[0] > small[0]
    assert large[1] > small[1]
    assert panel._validate_params()


def test_slider_target_is_monotonic(panel):
    targets = []
    for value in range(21):
        panel._detail_slider.setValue(value)
        targets.append(panel.get_target_cells())
    assert targets == sorted(targets)
    assert targets[0] == 10_000
    assert targets[-1] == 20_000_000


def test_core_validation_preserves_sub_0_1mm_sizes():
    safe_max, safe_min, _warnings = validate_cell_sizes(
        0.0000117889, 0.000003684, 0.002,
    )
    assert safe_max > safe_min > 0
    assert safe_min / safe_max < 0.5
