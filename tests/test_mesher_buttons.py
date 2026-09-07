"""The 3-button mesher selector must return exactly the pipeline keys the
meshing engines understand, and drive the poly-conversion checkbox the way
the old dropdown did (CFD Poly forces poly ON + hides the checkbox, FEM
Tetra forces it OFF, Cartesian cfMesh keeps it visible/optional)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cfmesh_autogui.gui.params_panel import ParamsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app):
    return ParamsPanel()


def _buttons(panel) -> dict:
    return {k: b for b, k in panel._mesher_keys}


def test_default_mesher_is_cfmesh(panel):
    assert panel.get_mesher_type() == "cfmesh"
    assert set(_buttons(panel)) == {"cfmesh", "gmsh_direct_poly", "gmsh_direct"}
    # cfMesh keeps the optional "Convert to polyhedral mesh" checkbox visible
    assert not panel._poly_check.isHidden()


def test_cfd_poly_forces_conversion_on_and_hides_checkbox(panel):
    _buttons(panel)["gmsh_direct_poly"].click()
    assert panel.get_mesher_type() == "gmsh_direct_poly"
    assert panel._poly_check.isChecked()
    assert panel._poly_check.isHidden()


def test_fem_tetra_forces_conversion_off(panel):
    _buttons(panel)["gmsh_direct"].click()
    assert panel.get_mesher_type() == "gmsh_direct"
    assert not panel._poly_check.isChecked()
    assert panel._poly_check.isHidden()


def test_switching_back_to_cfmesh_restores_checkbox(panel):
    buttons = _buttons(panel)
    buttons["gmsh_direct"].click()
    buttons["cfmesh"].click()
    assert panel.get_mesher_type() == "cfmesh"
    assert not panel._poly_check.isHidden()


def test_set_all_enabled_toggles_mesher_buttons(panel):
    buttons = _buttons(panel)
    panel.set_all_enabled(False)
    assert all(not b.isEnabled() for b in buttons.values())
    panel.set_all_enabled(True)
    assert all(b.isEnabled() for b in buttons.values())
