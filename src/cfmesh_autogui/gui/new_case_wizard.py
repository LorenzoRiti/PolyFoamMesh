from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QCheckBox, QSpinBox, QFileDialog, QMessageBox,
    QProgressBar, QListWidget, QTextEdit, QApplication,
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
            if path.lower().endswith((".step", ".stp", ".stl", ".iges", ".igs", ".brep")):
                self.parent().set_geometry_path(path)
                break


class Step1GeometryPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Geometry Selection")
        self.setSubTitle("Select a CAD geometry file. Features will be auto-detected.")

        self._geometry_path: str = ""
        self._pipeline_result = None

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

        btn_analyze = QPushButton("Analyze Geometry")
        btn_analyze.clicked.connect(self._on_analyze)
        btn_analyze.setStyleSheet(f"background:{COLOR_ACCENT};color:white;font-weight:bold;")
        btn_layout.addWidget(btn_analyze)
        layout.addLayout(btn_layout)

        self._path_label = QLabel("")
        self._path_label.setWordWrap(True)
        layout.addWidget(self._path_label)

        self._feature_info = QTextEdit()
        self._feature_info.setReadOnly(True)
        self._feature_info.setMaximumHeight(180)
        self._feature_info.setPlaceholderText("Feature detection results appear here...")
        layout.addWidget(self._feature_info)

        self.registerField("geometry_path*", self._path_label, "text")

    def set_geometry_path(self, path: str):
        self._geometry_path = path
        self._path_label.setText(f"Selected: {path}")
        self.completeChanged.emit()

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Geometry",
            "",
            "Geometry (*.step *.stp *.stl *.iges *.igs *.brep);;All (*.*)",
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

    def _on_analyze(self):
        if not self._geometry_path:
            QMessageBox.warning(self, "No Geometry", "Select a geometry file first.")
            return
        from cfmesh_autogui.commercial.geometry_pipeline import GeometryPipeline
        from cfmesh_autogui.gui.task_runner import FunctionWorker, TaskManager
        self._feature_info.setText("Analyzing geometry...")

        def work(worker):
            worker.report_progress("Analyzing geometry...", 20.0)
            gp = GeometryPipeline()
            return gp.run(
                self._geometry_path, heal=True, extract_features=True,
                classify_patches=True,
            )

        def on_done(_name, result):
            self._pipeline_result = result
            self._feature_info.setText("Analysis complete")
            if result.success:
                g = result.geometry
                h = result.healing
                f = result.features
                lines = [
                    f"Format: {g.format.upper()} | Unit: {g.detected_unit}",
                    f"Patches: {g.n_patches} | Watertight: {g.watertight}",
                    f"Bounding box: {g.bbox[0]:.3f} x {g.bbox[1]:.3f} x {g.bbox[2]:.3f} m",
                    f"Volume: {g.volume:.6f} m³" if g.volume else "",
                    "",
                    "--- Healing ---",
                    f"Holes filled: {h.holes_filled}" if h else "N/A",
                    f"Gaps stitched: {h.gaps_stitched}" if h else "N/A",
                    f"Slivers removed: {h.slivers_removed}" if h else "N/A",
                    "",
                    "--- Features ---",
                    f"Sharp edges (>30°): {f.n_sharp_edges}" if f else "N/A",
                    f"Min curvature radius: {f.min_curvature_radius:.6f} m" if f and f.min_curvature_radius else "N/A",
                    f"Gap regions: {f.n_gap_regions}" if f else "N/A",
                    "",
                    "--- Patch Classification ---",
                ]
                if f and f.patch_classification:
                    for name, ptype in f.patch_classification.items():
                        lines.append(f"  {name}: {ptype}")
                elif result.meshes:
                    for m in result.meshes:
                        lines.append(f"  {m.metadata.get('name', '?')}")
                self._feature_info.setText("\n".join(line for line in lines if line))
            else:
                self._feature_info.setText(
                    "Analysis failed:\n" + "\n".join(result.errors)
                )

        def on_failed(_name, msg):
            self._feature_info.setText(f"Analysis failed:\n{msg}")

        tasks = getattr(self, "_wizard_tasks", None)
        if tasks is None:
            tasks = TaskManager(self)
            self._wizard_tasks = tasks
        tasks.submit(
            "geometry_analyze", FunctionWorker(work),
            on_finished=on_done, on_failed=on_failed,
            heartbeat_timeout_s=600.0,
        )

    def get_geometry_path(self) -> str:
        return self._geometry_path

    def get_pipeline_result(self):
        return self._pipeline_result


class Step2MeshSettingsPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Mesh Settings")
        self.setSubTitle("Configure meshing parameters.")

        layout = QVBoxLayout(self)

        self._quick_mesh_btn = QPushButton("Quick Mesh (Auto)")
        self._quick_mesh_btn.setCheckable(True)
        self._quick_mesh_btn.setChecked(True)
        self._quick_mesh_btn.setStyleSheet(
            f"background:{COLOR_ACCENT};color:white;font-weight:bold;padding:8px;"
        )
        self._quick_mesh_btn.toggled.connect(self._on_quick_mesh_toggled)
        layout.addWidget(self._quick_mesh_btn)

        self._quick_mesh_label = QLabel(
            "Auto-select algorithm, cell sizes, and boundary layers based on geometry."
        )
        self._quick_mesh_label.setStyleSheet(f"color:{COLOR_TEXT_DIM};")
        layout.addWidget(self._quick_mesh_label)

        detail_group = QGroupBox("Detail Level (Quick Mesh)")
        detail_layout = QVBoxLayout(detail_group)
        self._detail_combo = QComboBox()
        self._detail_combo.addItems(["Coarse", "Medium", "Fine", "Very Fine"])
        detail_layout.addWidget(QLabel("Level:"))
        detail_layout.addWidget(self._detail_combo)
        layout.addWidget(detail_group)

        self._advanced_group = QGroupBox("Advanced Settings (Manual)")
        self._advanced_group.setEnabled(False)
        adv_layout = QVBoxLayout(self._advanced_group)

        cell_form = QFormLayout()
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

        self._algorithm_combo = QComboBox()
        self._algorithm_combo.addItems(
            ["CartesianHex", "Tetrahedral", "Polyhedral", "HexCorePoly",
             "NativePoly"],
        )
        cell_form.addRow("Algorithm:", self._algorithm_combo)
        adv_layout.addLayout(cell_form)

        bl_group = QGroupBox("Boundary Layers")
        bl_layout = QVBoxLayout(bl_group)
        self._bl_check = QCheckBox("Enable Boundary Layers")
        self._bl_check.setChecked(True)
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
        adv_layout.addWidget(bl_group)

        layout.addWidget(self._advanced_group)

        self.registerField("detail", self._detail_combo, "currentText")
        self.registerField("max_cell", self._max_cell, "value")
        self.registerField("min_cell", self._min_cell, "value")
        self.registerField("algorithm", self._algorithm_combo, "currentText")
        self.registerField("bl_enabled", self._bl_check, "checked")
        self.registerField("bl_n", self._bl_n, "value")
        self.registerField("bl_thick", self._bl_thick, "value")
        self.registerField("bl_exp", self._bl_exp, "value")

    def _on_quick_mesh_toggled(self, checked: bool):
        self._advanced_group.setEnabled(not checked)
        if checked:
            self._quick_mesh_btn.setText("Quick Mesh (Auto)")
            self._quick_mesh_label.setText(
                "Auto-select algorithm, cell sizes, and boundary layers based on geometry."
            )
        else:
            self._quick_mesh_btn.setText("Manual Setup")
            self._quick_mesh_label.setText("Configure meshing parameters manually.")

    def is_quick_mesh(self) -> bool:
        return self._quick_mesh_btn.isChecked()


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
        self.setMinimumSize(680, 560)

        self._page1 = Step1GeometryPage(self)
        self._page2 = Step2MeshSettingsPage(self)
        self._page3 = Step3QualityPage(self)

        self.addPage(self._page1)
        self.addPage(self._page2)
        self.addPage(self._page3)

    def get_params(self) -> dict:
        pipe_result = self._page1.get_pipeline_result()
        p = {
            "geometry_path": self._page1.get_geometry_path(),
            "use_quick_mesh": self._page2.is_quick_mesh(),
            "detail": self.field("detail"),
            "max_cell": self.field("max_cell"),
            "min_cell": self.field("min_cell"),
            "algorithm": self.field("algorithm"),
            "bl_enabled": self.field("bl_enabled"),
            "bl_n": self.field("bl_n"),
            "bl_thick": self.field("bl_thick"),
            "bl_exp": self.field("bl_exp"),
        }
        if pipe_result:
            p["pipeline_result"] = pipe_result
        return p
