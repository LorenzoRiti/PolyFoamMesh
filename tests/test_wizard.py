from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PySide6.QtWidgets import QApplication, QWizard

from cfmesh_autogui.gui.new_case_wizard import (
    NewCaseWizard, Step1GeometryPage, Step2MeshSettingsPage,
    Step3QualityPage, DropZoneLabel,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestDropZoneLabel:
    def test_init(self, qapp):
        label = DropZoneLabel()
        assert label.acceptDrops() is True
        assert "Drag & Drop" in label.text()


class TestStep1GeometryPage:
    def test_init(self, qapp):
        page = Step1GeometryPage()
        assert page.title() == "Geometry Selection"
        assert page.get_geometry_path() == ""


class TestStep2MeshSettingsPage:
    def test_init(self, qapp):
        page = Step2MeshSettingsPage()
        assert page.title() == "Mesh Settings"

    def test_change_detail(self, qapp):
        page = Step2MeshSettingsPage()
        page._detail_combo.setCurrentText("Fine")
        assert page._detail_combo.currentText() == "Fine"

    def test_enable_bl(self, qapp):
        page = Step2MeshSettingsPage()
        page._bl_check.setChecked(True)
        assert page._bl_check.isChecked() is True
        assert page._bl_n.value() == 3


class TestStep3QualityPage:
    def test_init(self, qapp):
        page = Step3QualityPage()
        assert page.title() == "Quality Check"

    def test_set_running(self, qapp):
        page = Step3QualityPage()
        assert page._progress.isVisible() is False

    def test_set_status(self, qapp):
        page = Step3QualityPage()
        page.set_status("Running...")
        assert page._status_label.text() == "Running..."

    def test_criteria_list(self, qapp):
        page = Step3QualityPage()
        assert page._criteria_list.count() == 4


class TestNewCaseWizard:
    def test_init(self, qapp):
        wizard = NewCaseWizard()
        assert wizard.windowTitle() == "New Meshing Case Wizard"
        ids = list(wizard.pageIds())
        assert len(ids) == 3

    def test_get_params_defaults(self, qapp):
        wizard = NewCaseWizard()
        params = wizard.get_params()
        assert "detail" in params
        assert "max_cell" in params
        assert "min_cell" in params
        assert "bl_enabled" in params
        assert abs(params["max_cell"] - 0.05) < 1e-9

    def test_next_buttons_exist(self, qapp):
        wizard = NewCaseWizard()
        assert wizard.button(QWizard.WizardButton.NextButton) is not None
        assert wizard.button(QWizard.WizardButton.BackButton) is not None
        assert wizard.button(QWizard.WizardButton.CancelButton) is not None

    def test_wizard_style(self, qapp):
        wizard = NewCaseWizard()
        assert wizard.wizardStyle() == QWizard.WizardStyle.ModernStyle
