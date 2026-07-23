from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QCheckBox, QSpinBox, QFileDialog, QMessageBox,
    QProgressBar, QListWidget,
)
from PySide6.QtCore import Qt, Slot, QThread
from PySide6.QtGui import QDragEnterEvent, QDropEvent

from cfmesh_autogui.gui.style import COLOR_ACCENT, COLOR_DANGER, COLOR_PASS, COLOR_TEXT_DIM

logger = logging.getLogger(__name__)


class DropZoneLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setText("Drag & Drop STEP/STL here\nor click Browse")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(120)
        self.setStyleSheet(
            f"border: 2px dashed {COLOR_TEXT_DIM}; border-radius: 8px; "
            "font-size: 14px; padding: 20px;"
        )

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".step", ".stp", ".stl")):
                self.parent().set_geometry_path(path)
                break


class Step1GeometryPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Geometry Selection")
        self.setSubTitle("Select a CAD geometry file to mesh.")

        self._geometry_path: str = ""

        layout = QVBoxLayout(self)

        self._drop_label = DropZoneLabel(self)
        layout.addWidget(self._drop_label)

        btn_layout = QHBoxLayout()
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._on_browse)
        btn_layout.addWidget(btn_browse)

        btn_test = QPushButton("Test Cylinder")
        btn_test.clicked.connect(self._on_test_cylinder)
        btn_layout.addWidget(btn_test)
        layout.addLayout(btn_layout)

        self._path_label = QLabel("")
        self._path_label.setWordWrap(True)
        layout.addWidget(self._path_label)

        self.registerField("geometry_path*", self._path_label, "text")

    def set_geometry_path(self, path: str):
        self._geometry_path = path
        self._path_label.setText(f"Selected: {path}")
        # QLabel has no textChanged signal, so registerField("geometry_path*", ...)
        # can't auto-detect this change — without emitting completeChanged
        # ourselves, the "Next" button never re-enables after picking a
        # geometry, permanently trapping the user on step 1.
        self.completeChanged.emit()

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Geometry",
            "",
            "Geometry (*.step *.stp *.stl);;All (*.*)",
        )
        if path:
            self.set_geometry_path(path)

    def _on_test_cylinder(self):
        from cfmesh_autogui.core.geometry import create_test_cylinder
        import cadquery as cq
        cyl = create_test_cylinder(1.0, 2.0)
        tmp = Path.home() / ".cfmesh_wizard_cylinder.step"
        cq.exporters.export(cyl, str(tmp), exportType="STEP")
        self.set_geometry_path(str(tmp))

    def get_geometry_path(self) -> str:
        return self._geometry_path


class Step2MeshSettingsPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Mesh Settings")
        self.setSubTitle("Configure meshing parameters.")

        layout = QVBoxLayout(self)

        detail_group = QGroupBox("Detail Level")
        detail_layout = QVBoxLayout(detail_group)
        self._detail_combo = QComboBox()
        self._detail_combo.addItems(["Coarse", "Medium", "Fine"])
        detail_layout.addWidget(QLabel("Level:"))
        detail_layout.addWidget(self._detail_combo)
        layout.addWidget(detail_group)

        cell_group = QGroupBox("Cell Sizes")
        cell_form = QFormLayout(cell_group)
        self._max_cell = QDoubleSpinBox()
        self._max_cell.setRange(0.001, 100.0)
        self._max_cell.setValue(0.05)
        self._max_cell.setDecimals(4)
        self._max_cell.setSuffix(" m")
        cell_form.addRow("Max Cell:", self._max_cell)

        self._min_cell = QDoubleSpinBox()
        self._min_cell.setRange(0.0001, 10.0)
        self._min_cell.setValue(0.01)
        self._min_cell.setDecimals(4)
        self._min_cell.setSuffix(" m")
        cell_form.addRow("Min Cell:", self._min_cell)
        layout.addWidget(cell_group)

        bl_group = QGroupBox("Boundary Layers")
        bl_layout = QVBoxLayout(bl_group)
        self._bl_check = QCheckBox("Enable Boundary Layers")
        bl_layout.addWidget(self._bl_check)

        bl_form = QFormLayout()
        self._bl_n = QSpinBox()
        self._bl_n.setRange(1, 10)
        self._bl_n.setValue(3)
        bl_form.addRow("nLayers:", self._bl_n)
        self._bl_thick = QDoubleSpinBox()
        self._bl_thick.setRange(0.001, 1.0)
        self._bl_thick.setValue(0.005)
        self._bl_thick.setDecimals(4)
        bl_form.addRow("Thickness:", self._bl_thick)
        self._bl_exp = QDoubleSpinBox()
        self._bl_exp.setRange(1.0, 2.0)
        self._bl_exp.setValue(1.2)
        self._bl_exp.setSingleStep(0.1)
        bl_form.addRow("Expansion:", self._bl_exp)
        bl_layout.addLayout(bl_form)
        layout.addWidget(bl_group)

        self.registerField("detail", self._detail_combo, "currentText")
        self.registerField("max_cell", self._max_cell, "value")
        self.registerField("min_cell", self._min_cell, "value")
        self.registerField("bl_enabled", self._bl_check, "checked")
        self.registerField("bl_n", self._bl_n, "value")
        self.registerField("bl_thick", self._bl_thick, "value")
        self.registerField("bl_exp", self._bl_exp, "value")


class Step3QualityPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Quality Check")
        self.setSubTitle("Preview quality criteria and run meshing.")

        layout = QVBoxLayout(self)

        criteria_group = QGroupBox("Quality Criteria")
        crit_layout = QVBoxLayout(criteria_group)
        self._criteria_list = QListWidget()
        self._criteria_list.addItems([
            "Max non-orthogonality < 65\u00b0 (warning at 65\u00b0, fail at 85\u00b0)",
            "Max skewness < 4 (warning at 4, fail at 10)",
            "Max aspect ratio < 1000 (warning at 1000, fail at 5000)",
            "Minimum volume > 0 (negative volumes = fail)",
        ])
        crit_layout.addWidget(self._criteria_list)
        layout.addWidget(criteria_group)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        layout.addStretch()

    def set_running(self, running: bool):
        self._progress.setVisible(running)
        self._progress.setRange(0, 0) if running else self._progress.setRange(0, 100)

    def set_status(self, text: str):
        self._status_label.setText(text)


class NewCaseWizard(QWizard):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Meshing Case Wizard")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setMinimumSize(600, 500)

        self._page1 = Step1GeometryPage(self)
        self._page2 = Step2MeshSettingsPage(self)
        self._page3 = Step3QualityPage(self)

        self.addPage(self._page1)
        self.addPage(self._page2)
        self.addPage(self._page3)

    def get_params(self) -> dict:
        return {
            "geometry_path": self._page1.get_geometry_path(),
            "detail": self.field("detail"),
            "max_cell": self.field("max_cell"),
            "min_cell": self.field("min_cell"),
            "bl_enabled": self.field("bl_enabled"),
            "bl_n": self.field("bl_n"),
            "bl_thick": self.field("bl_thick"),
            "bl_exp": self.field("bl_exp"),
        }
