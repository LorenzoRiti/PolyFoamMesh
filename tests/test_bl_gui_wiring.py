"""The boundary-layer physics must actually reach the user and the meshDict.

`commercial/bl_engine.py` computed correct y+ based layers for a long time, but
nothing called it: the panel shipped hardcoded 3 layers and a 0.005 first-layer
fraction regardless of the flow. These tests pin the whole chain — flow inputs ->
BLEngine -> panel widgets -> bl_params -> meshDict keys.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:  # cadquery/OCP native libs need their dir on PATH on Windows
    import OCP as _ocp

    _d = os.path.dirname(_ocp.__file__)
    if _d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

import pytest

pytest.importorskip("PySide6")
cq = pytest.importorskip("cadquery")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cfmesh_autogui.core.geometry import (  # noqa: E402
    classify_faces,
    create_test_cylinder,
    tessellate_patches,
)
from cfmesh_autogui.core.meshdict_gen import build_meshdict_lines  # noqa: E402
from cfmesh_autogui.gui.params_panel import ParamsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app):
    p = ParamsPanel()
    p.set_suggest_meshes(
        tessellate_patches(classify_faces(create_test_cylinder(1.0, 2.0).val()))
    )
    return p


def test_wall_resolved_needs_more_layers_than_wall_functions(panel):
    panel._bl_wall_treatment.setCurrentIndex(0)  # wall functions, y+ ~30
    panel._on_bl_auto_compute()
    wall_funcs_layers = panel._bl_n_layers.value()

    panel._bl_wall_treatment.setCurrentIndex(1)  # wall-resolved, y+ ~1
    panel._on_bl_auto_compute()
    assert panel._bl_n_layers.value() > wall_funcs_layers


def test_faster_flow_gives_a_thinner_first_layer(panel):
    panel._bl_velocity.setValue(1.0)
    panel._on_bl_auto_compute()
    slow = panel._bl_thick.value()

    panel._bl_velocity.setValue(50.0)
    panel._on_bl_auto_compute()
    assert panel._bl_thick.value() < slow


def test_widget_ranges_do_not_truncate_the_physics(panel):
    """A wall-resolved case needs ~30 layers and a ~1e-4 fraction; the original
    1..10 / 0.001-minimum widget ranges silently clipped both."""
    assert panel._bl_n_layers.maximum() >= 30
    assert panel._bl_thick.minimum() <= 1e-4


def test_computed_values_reach_the_meshdict(panel):
    import re

    panel._bl_checkbox.setChecked(True)
    panel._bl_wall_treatment.setCurrentIndex(1)  # wall-resolved (y+ ~1)
    panel._on_bl_auto_compute()

    bl = panel.get_bl_params()
    assert bl["nLayers"] == panel._bl_n_layers.value()
    # thicknessRatio must be a real growth ratio (>1), not the first-layer
    # fraction — sending the fraction here is what collapsed the layers.
    assert bl["thicknessRatio"] > 1.0
    assert bl["firstLayerThickness"] > 0

    max_cell = 0.25
    content = "\n".join(
        build_meshdict_lines(max_cell=max_cell, bl_params={**bl, "wallPatches": ["wall"]})
    )
    assert re.search(rf"nLayers\s+{bl['nLayers']};", content)
    assert re.search(rf"thicknessRatio\s+{re.escape(str(bl['thicknessRatio']))};", content)
    # the y+-derived first layer must actually be written to the meshDict
    assert "maxFirstLayerThickness" in content


def test_reports_the_reynolds_number_to_the_user(panel):
    panel._on_bl_auto_compute()
    text = panel._bl_info.text()
    assert "Re=" in text and "y+" in text
