"""The mesher selector must return exactly the pipeline keys the meshing
engines understand, and drive the poly-conversion checkbox the way the old
dropdown did (Polymesh forces poly ON + hides the checkbox, Cartesian
cfMesh keeps it visible/optional).

2026-10-08: Polymesh is the DEFAULT path (the product's core) and the
"FEM Tetra (GMSH)" button was removed - a pure tet mesh for FEM solvers
was a niche duplicating the GMSH stage.  The internal TETRAHEDRAL
algorithm remains as the auto fallback for complex geometry."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from polyfoammesh.gui.params_panel import ParamsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app):
    return ParamsPanel()


def _buttons(panel) -> dict:
    return {k: b for b, k in panel._mesher_keys}


def test_default_mesher_is_polymesh_and_fem_is_gone(panel):
    # Polymesh is the default (user request); cfMesh is the alternative.
    assert panel.get_mesher_type() == "gmsh_direct_poly"
    assert set(_buttons(panel)) == {"cfmesh", "gmsh_direct_poly"}
    assert "gmsh_direct" not in _buttons(panel)


def test_cfd_poly_forces_conversion_on_and_hides_checkbox(panel):
    _buttons(panel)["gmsh_direct_poly"].click()
    assert panel.get_mesher_type() == "gmsh_direct_poly"
    assert panel._poly_check.isChecked()
    assert panel._poly_check.isHidden()


def test_switching_back_to_cfmesh_restores_checkbox(panel):
    buttons = _buttons(panel)
    buttons["cfmesh"].click()
    assert panel.get_mesher_type() == "cfmesh"
    # cfMesh keeps the optional "Convert to polyhedral mesh" checkbox visible
    assert not panel._poly_check.isHidden()
    buttons["gmsh_direct_poly"].click()
    assert panel.get_mesher_type() == "gmsh_direct_poly"
    assert panel._poly_check.isHidden()


def test_set_all_enabled_toggles_mesher_buttons(panel):
    buttons = _buttons(panel)
    panel.set_all_enabled(False)
    assert all(not b.isEnabled() for b in buttons.values())
    panel.set_all_enabled(True)
    assert all(b.isEnabled() for b in buttons.values())
