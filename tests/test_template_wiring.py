"""Case templates (Internal Flow, External Aero, CHT, ...) must actually reach
the params panel, scaled by the loaded geometry — not applied as a raw ratio
regardless of model size, and not through TemplateEngine.apply_template()'s own
meshDict write (which passes ratios straight through as absolute cell sizes and
predates the current boundary-layer key contract).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:  # cadquery/OCP native libs need their dir on PATH on Windows
    import OCP as _ocp

    _d = os.path.dirname(_ocp.__file__)
    if _d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app):
    from cfmesh_autogui.gui.params_panel import ParamsPanel

    return ParamsPanel()


def test_builtin_templates_are_listed(panel):
    names = [panel._template_combo.itemText(i) for i in range(panel._template_combo.count())]
    assert "Internal Flow" in names
    assert "External Aerodynamics" in names
    assert "Conjugate Heat Transfer" in names


def test_apply_template_sets_detail_and_boundary_layers(panel):
    idx = panel._template_combo.findText("Internal Flow")
    panel._template_combo.setCurrentIndex(idx)
    panel._on_apply_template()

    assert panel.get_detail_level() == "medium"
    assert panel._bl_checkbox.isChecked() is True
    assert panel._bl_n_layers.value() == 5


def test_cell_sizes_scale_with_the_loaded_geometrys_bounding_box(panel):
    """The regression this guards against: template ratios (e.g. 0.04) applied
    as absolute metres regardless of model size."""
    idx = panel._template_combo.findText("Internal Flow")
    panel._template_combo.setCurrentIndex(idx)

    panel._bbox_dim = 1.0
    panel._on_apply_template()
    small_max = panel._max_cell.value()

    panel._bbox_dim = 10.0
    panel._on_apply_template()
    large_max = panel._max_cell.value()

    assert large_max == pytest.approx(small_max * 10.0, rel=1e-6)


def test_different_templates_set_different_boundary_layer_counts(panel):
    panel._bbox_dim = 1.0
    panel._template_combo.setCurrentIndex(panel._template_combo.findText("Internal Flow"))
    panel._on_apply_template()
    internal_layers = panel._bl_n_layers.value()

    panel._template_combo.setCurrentIndex(
        panel._template_combo.findText("External Aerodynamics")
    )
    panel._on_apply_template()
    aero_layers = panel._bl_n_layers.value()

    assert aero_layers != internal_layers


def test_template_without_boundary_layers_disables_the_checkbox(panel):
    idx = panel._template_combo.findText("Multi-Phase (VOF)")
    panel._template_combo.setCurrentIndex(idx)
    panel._on_apply_template()
    assert panel._bl_checkbox.isChecked() is False
