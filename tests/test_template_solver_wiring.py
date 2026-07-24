"""A case template's turbulence model must reach setup_case(); its solver must
NOT, unless case_setup.py actually knows how to write dictionaries for it.

case_setup.py's fvSchemes/fvSolution are a fixed steady-RAS SIMPLE
configuration. Silently swapping in a template's `application` (e.g.
interFoam, chtMultiRegionFoam) without matching scheme/solution dictionaries
would produce an internally inconsistent case — controlDict claims one solver,
fvSolution is tuned for another — which is worse than staying on the known-
good default and telling the user. Templates use only the two_ fields via
metadata; the actual call happens through MainWindow._case_setup_kwargs, tested
here directly (not via a full MainWindow, which isn't pytest-safe — see the
note in the guided-workflow / template-wiring test files re: VTK/pyvistaqt).
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

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app):
    from cfmesh_autogui.gui.params_panel import ParamsPanel

    return ParamsPanel()


class _FakeLog:
    def __init__(self):
        self.lines: list[str] = []

    def append_log(self, s: str) -> None:
        self.lines.append(s)


def _kwargs_for(panel):
    """Call the real MainWindow._case_setup_kwargs unbound, against a stub
    carrying only what it reads (params + a log + the solver allow-list)."""
    from cfmesh_autogui.gui.main_window import MainWindow

    stub = types.SimpleNamespace(
        _params=panel,
        _log=_FakeLog(),
        _CASE_SETUP_SUPPORTED_SOLVERS=MainWindow._CASE_SETUP_SUPPORTED_SOLVERS,
    )
    kwargs = MainWindow._case_setup_kwargs(stub)
    return kwargs, stub._log.lines


def test_no_template_applied_means_no_override(panel):
    kwargs, logs = _kwargs_for(panel)
    assert kwargs == {}
    assert logs == []


def test_supported_solver_is_carried_through(panel):
    idx = panel._template_combo.findText("Internal Flow")  # simpleFoam / kOmegaSST
    panel._template_combo.setCurrentIndex(idx)
    panel._on_apply_template()

    kwargs, logs = _kwargs_for(panel)
    assert kwargs == {"turbulence_model": "kOmegaSST", "application": "simpleFoam"}
    assert logs == []


def test_unsupported_solver_only_applies_turbulence_and_warns(panel):
    idx = panel._template_combo.findText("Heat Transfer (Solid)")  # laplacianFoam / laminar
    panel._template_combo.setCurrentIndex(idx)
    panel._on_apply_template()

    kwargs, logs = _kwargs_for(panel)
    assert kwargs == {"turbulence_model": "laminar"}
    assert "application" not in kwargs
    assert any("laplacianFoam" in line for line in logs)
