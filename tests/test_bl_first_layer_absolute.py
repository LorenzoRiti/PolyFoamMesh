"""Regression: the physics-derived BL first layer is stored ABSOLUTE (m).

Real case (2026-10-07): "Compute layers from physics" produced a first
layer of 85.8 um for y+ 30, but the run used 6.1 um because the panel
stored it as a FRACTION of max cell and max cell changed (0.171 -> 0.0122)
between compute and run.  The simulated y+ came out ~0.09 instead of 30.
The absolute value is now authoritative; the spin box only shows the
fraction of the current max cell.

Also pins the flat-plate correlation itself against the textbook value.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math

import pytest

pytest.importorskip("PySide6")
import trimesh  # noqa: E402

from polyfoammesh.commercial.bl_engine import BLEngine, FlowConditions  # noqa: E402
from polyfoammesh.gui.params_panel import ParamsPanel  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_engine_flat_plate_textbook_value():
    """U=1 m/s, L=1 m, water: Re=1e6, Cf=0.027/Re^(1/7) -> h1 ~ 0.69 mm."""
    flow = FlowConditions.from_velocity(
        reference_velocity=1.0,
        reference_length=1.0,
        kinematic_viscosity=1e-6,
        turbulence_model="kEpsilon",
    )
    params = BLEngine().calculate_from_flow(flow)
    assert params.target_yplus == 30
    re = 1.0 * 1.0 / 1e-6
    cf = 0.027 / re ** (1.0 / 7.0)
    u_tau = 1.0 * math.sqrt(cf / 2.0)
    expected = 30.0 * 1e-6 / u_tau
    # The engine rounds the result to 6 significant digits.
    assert params.first_layer_height == pytest.approx(expected, rel=1e-4)
    assert params.first_layer_height == pytest.approx(6.93e-4, rel=0.05)


def _panel_with_geometry() -> ParamsPanel:
    panel = ParamsPanel()
    panel.set_suggest_meshes([trimesh.creation.box(extents=[1.0, 1.0, 1.0])])
    panel._bl_checkbox.setChecked(True)  # get_bl_params returns None otherwise
    return panel


def test_physics_first_layer_is_absolute_not_fraction():
    panel = _panel_with_geometry()
    panel._max_cell.setValue(0.171)
    panel._on_bl_auto_compute()
    first = panel.get_bl_params()["firstLayerThickness"]
    assert first > 0

    # Simulate a later detail-slider change of max cell: the absolute first
    # layer must NOT rescale (the historical bug did).
    panel._max_cell.setValue(0.0122)
    again = panel.get_bl_params()["firstLayerThickness"]
    assert again == pytest.approx(first, rel=1e-12)


def test_manual_fraction_edit_switches_to_manual_mode():
    panel = _panel_with_geometry()
    panel._max_cell.setValue(0.1)
    panel._on_bl_auto_compute()
    panel._bl_thick.setValue(0.001)  # manual edit
    assert panel._bl_first_layer_abs is None
    expected = 0.001 * panel._max_cell.value()
    assert panel.get_bl_params()["firstLayerThickness"] == pytest.approx(
        expected, rel=1e-12
    )


def test_velocity_change_recomputes_after_a_compute():
    panel = _panel_with_geometry()
    panel._max_cell.setValue(0.1)
    panel._bl_velocity.setValue(10.0)
    panel._on_bl_auto_compute()
    h_ten = panel.get_bl_params()["firstLayerThickness"]
    panel._bl_velocity.setValue(1.0)  # auto-recompute
    h_one = panel.get_bl_params()["firstLayerThickness"]
    assert h_one > h_ten  # lower velocity -> lower u_tau -> thicker first layer
    # h1 ~ 1/(U * sqrt(Cf)) with Cf ~ Re^(-1/7) ~ U^(-1/7): the ratio is
    # 10^(13/14), not exactly 10.
    assert h_one / h_ten == pytest.approx(10.0 ** (13.0 / 14.0), rel=0.02)


def test_engine_pipe_correlation_matches_standard_calculators():
    """Internal flows: Blasius (Re<1e5) / 1/5-power pipe law above.

    Hand-computed reference values - the same correlations the standard
    online y+ calculators use for pipe flows.
    """
    engine = BLEngine()

    # Re = U*D/nu = 1*0.01/1e-6 = 1e4 -> Blasius Cf = 0.079/Re^0.25 = 0.0079
    flow = FlowConditions.from_velocity(
        reference_velocity=1.0, reference_length=0.01,
        kinematic_viscosity=1e-6, turbulence_model="kEpsilon",
    )
    p = engine.calculate_from_flow(flow, flow_type="pipe")
    u_tau = 1.0 * math.sqrt(0.0079 / 2.0)
    assert p.first_layer_height == pytest.approx(30.0 * 1e-6 / u_tau, rel=1e-4)

    # Re = 1e5 -> Cf = 0.046/Re^0.2 = 0.0046
    flow2 = FlowConditions.from_velocity(
        reference_velocity=1.0, reference_length=0.1,
        kinematic_viscosity=1e-6, turbulence_model="kEpsilon",
    )
    p2 = engine.calculate_from_flow(flow2, flow_type="pipe")
    u_tau2 = 1.0 * math.sqrt(0.0046 / 2.0)
    assert p2.first_layer_height == pytest.approx(30.0 * 1e-6 / u_tau2, rel=1e-4)


def test_flow_type_inference_pipe_vs_flat_plate():
    """Internal flows (inlet+outlet patches) use the inlet equivalent
    diameter and the pipe correlation; external ones the bbox."""
    panel = ParamsPanel()
    inlet = trimesh.creation.box(extents=[0.1, 0.1, 0.01])
    inlet.metadata["name"] = "inlet"
    outlet = trimesh.creation.box(extents=[0.1, 0.1, 0.01])
    outlet.metadata["name"] = "outlet"
    wall = trimesh.creation.box(extents=[0.2, 0.2, 0.01])
    wall.metadata["name"] = "wall"

    panel.set_suggest_meshes([inlet, outlet, wall])
    length, flow_type = panel._bl_flow_length_and_type()
    assert flow_type == "pipe"
    assert length == pytest.approx(
        math.sqrt(4.0 * float(inlet.area) / math.pi), rel=1e-6
    )

    panel.set_suggest_meshes([wall])
    _length2, flow_type2 = panel._bl_flow_length_and_type()
    assert flow_type2 == "flat_plate"
