from __future__ import annotations

import json
import logging
from pathlib import Path

import trimesh  # ✅ F-017

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QDoubleSpinBox, QSpinBox,
    QPushButton, QListWidget, QLabel, QGroupBox, QMessageBox, QCheckBox,
    QComboBox, QScrollArea, QTabWidget, QHBoxLayout, QSlider,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QUndoCommand, QUndoStack

from cfmesh_autogui.gui.style import (
    COLOR_ACCENT, COLOR_DANGER, COLOR_TEXT_DIM, FS_METRIC, metric_label,
)
from cfmesh_autogui.gui.design_tokens import ORANGE_500

logger = logging.getLogger(__name__)


class ParamChangeCommand(QUndoCommand):
    def __init__(self, key: str, old_val: float, new_val: float, panel):
        super().__init__(f"Change {key}")
        self._key = key
        self._old_val = old_val
        self._new_val = new_val
        self._panel = panel

    def undo(self):
        self._panel._max_cell.blockSignals(True)
        self._panel._min_cell.blockSignals(True)
        if self._key == "max_cell_size":
            self._panel._max_cell.setValue(self._old_val)
        elif self._key == "min_cell_size":
            self._panel._min_cell.setValue(self._old_val)
        self._panel._max_cell.blockSignals(False)
        self._panel._min_cell.blockSignals(False)
        self._panel._emit_param_changed()

    def redo(self):
        self._panel._max_cell.blockSignals(True)
        self._panel._min_cell.blockSignals(True)
        if self._key == "max_cell_size":
            self._panel._max_cell.setValue(self._new_val)
        elif self._key == "min_cell_size":
            self._panel._min_cell.setValue(self._new_val)
        self._panel._max_cell.blockSignals(False)
        self._panel._min_cell.blockSignals(False)
        self._panel._emit_param_changed()


class ParamsPanel(QWidget):
    mesh_params_changed = Signal(dict)
    run_meshing = Signal()
    reset_all = Signal()
    load_step_requested = Signal()
    cancel_meshing = Signal()
    unit_changed = Signal(str)
    param_changed = Signal(str, object)
    suggestion_completed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._case_dir = ""
        self._meshing_state = False
        self._suggest_meshes: list[trimesh.Trimesh] = []  # ✅ F-017
        self._undo_stack: QUndoStack | None = None
        self._prev_cell_params = {"max_cell": 0.05, "min_cell": 0.01}
        self._setup_ui()

    def set_undo_stack(self, stack: QUndoStack):
        self._undo_stack = stack

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        outer.addWidget(self._tabs)

        geom_tab = QWidget()
        geom_layout = QVBoxLayout(geom_tab)

        template_group = QGroupBox("Case Template")
        template_layout = QVBoxLayout(template_group)
        self._template_combo = QComboBox()
        self._template_combo.addItems(["None (manual)", "Internal Flow", "External Aero", "CHT"])
        self._template_combo.currentTextChanged.connect(self._on_template_selected)
        template_layout.addWidget(self._template_combo)
        template_layout.addWidget(QLabel("Preset: geometry + mesh defaults per case type"))
        geom_layout.addWidget(template_group)

        cad_group = QGroupBox("CAD Geometry")
        cad_layout = QVBoxLayout(cad_group)
        self._cad_label = QLabel("No CAD loaded")
        self._cad_label.setWordWrap(True)
        cad_layout.addWidget(self._cad_label)

        btn_load = QPushButton("Load STEP...")
        btn_load.setToolTip("Load a STEP/STP CAD or STL mesh file. Shortcut: Ctrl+O")
        btn_load.clicked.connect(self.load_step_requested.emit)
        self._btn_load = btn_load
        cad_layout.addWidget(btn_load)

        unit_form = QFormLayout()
        self._unit_selector = QComboBox()
        self._unit_selector.addItems(["m", "mm", "cm", "inch", "ft"])
        self._unit_selector.setCurrentText("m")
        self._unit_selector.currentTextChanged.connect(self.unit_changed.emit)
        unit_form.addRow("CAD Unit:", self._unit_selector)
        cad_layout.addLayout(unit_form)

        self._domain_label = QLabel("Domain: --")
        self._domain_label.setStyleSheet(f"color: {COLOR_ACCENT}; font-weight: bold;")
        cad_layout.addWidget(self._domain_label)

        form_geom = QFormLayout()
        self._scale_factor = QDoubleSpinBox()
        self._scale_factor.setRange(0.001, 1000.0)
        self._scale_factor.setValue(1.0)
        self._scale_factor.setDecimals(6)
        form_geom.addRow("Scale Factor:", self._scale_factor)
        cad_layout.addLayout(form_geom)

        geom_layout.addWidget(cad_group)

        patch_group = QGroupBox("Patches")
        patch_layout = QVBoxLayout(patch_group)
        self._patch_list = QListWidget()
        self._patch_list.setMaximumHeight(120)
        patch_layout.addWidget(self._patch_list)
        self._patch_hint = QLabel(
            "Tip: enable 'Select Patch' in the viewer, then click a face to rename."
        )
        self._patch_hint.setWordWrap(True)
        self._patch_hint.setStyleSheet(metric_label(COLOR_TEXT_DIM, 10))
        patch_layout.addWidget(self._patch_hint)
        geom_layout.addWidget(patch_group)

        geom_layout.addStretch()
        self._tabs.addTab(geom_tab, "Geometry")

        mesh_tab = QWidget()
        mesh_layout = QVBoxLayout(mesh_tab)

        mesh_group = QGroupBox("Cell Sizes")
        mesh_form = QFormLayout(mesh_group)
        self._max_cell = QDoubleSpinBox()
        self._max_cell.setRange(0.001, 100.0)
        self._max_cell.setValue(0.05)
        self._max_cell.setSingleStep(0.01)
        self._max_cell.setDecimals(4)
        self._max_cell.setSuffix(" m")
        mesh_form.addRow("Max Cell Size:", self._max_cell)

        self._min_cell = QDoubleSpinBox()
        self._min_cell.setRange(0.0001, 10.0)
        self._min_cell.setValue(0.01)
        self._min_cell.setSingleStep(0.001)
        self._min_cell.setDecimals(4)
        self._min_cell.setSuffix(" m")
        mesh_form.addRow("Min Cell Size:", self._min_cell)

        self._cell_est_label = QLabel("Cells: --")
        self._cell_est_label.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        mesh_form.addRow(self._cell_est_label)

        self._prev_cell_params["max_cell"] = self._max_cell.value()
        self._prev_cell_params["min_cell"] = self._min_cell.value()
        self._max_cell.valueChanged.connect(self._on_max_cell_changed)
        self._min_cell.valueChanged.connect(self._on_min_cell_changed)
        mesh_layout.addWidget(mesh_group)

        detail_group = QGroupBox("Detail Level")
        detail_layout = QVBoxLayout(detail_group)
        self._detail_slider = QSlider(Qt.Horizontal)
        self._detail_slider.setRange(0, 4)
        self._detail_slider.setValue(2)
        self._detail_slider.setTickPosition(QSlider.TicksBelow)
        self._detail_slider.setTickInterval(1)
        self._detail_slider.setToolTip("Controlla la finezza della mesh: da Molto Grossolana (celle grandi, mesh veloce) a Molto Fine (celle piccole, mesh accurata)")
        self._detail_label = QLabel("Media")
        self._detail_label.setAlignment(Qt.AlignCenter)
        self._detail_label.setStyleSheet(f"font-weight: bold; color: {ORANGE_500};")
        self._detail_slider.valueChanged.connect(self._on_detail_changed)
        detail_layout.addWidget(self._detail_slider)
        detail_layout.addWidget(self._detail_label)
        mesh_layout.addWidget(detail_group)

        btn_suggest = QPushButton("Auto-Suggest Cell Sizes")
        btn_suggest.clicked.connect(self._on_suggest_sizes)
        self._btn_suggest = btn_suggest
        mesh_layout.addWidget(btn_suggest)

        bl_group = QGroupBox("Boundary Layers")
        bl_layout = QVBoxLayout(bl_group)
        self._bl_checkbox = QCheckBox("Enable Boundary Layers")
        self._bl_checkbox.toggled.connect(self._on_bl_toggled)
        bl_layout.addWidget(self._bl_checkbox)

        bl_form = QFormLayout()
        self._bl_n_layers = QSpinBox()
        self._bl_n_layers.setRange(1, 10)
        self._bl_n_layers.setValue(3)
        bl_form.addRow("nLayers:", self._bl_n_layers)
        self._bl_thick = QDoubleSpinBox()
        self._bl_thick.setRange(0.001, 1.0)
        self._bl_thick.setValue(0.005)
        self._bl_thick.setDecimals(4)
        bl_form.addRow("Thickness Ratio:", self._bl_thick)
        self._bl_exp = QDoubleSpinBox()
        self._bl_exp.setRange(1.0, 2.0)
        self._bl_exp.setValue(1.2)
        self._bl_exp.setSingleStep(0.1)
        bl_form.addRow("Expansion Ratio:", self._bl_exp)
        self._bl_apply_all = QCheckBox("Apply BL to all patches")
        bl_form.addRow(self._bl_apply_all)
        self._bl_form_widget = QWidget()
        self._bl_form_widget.setLayout(bl_form)
        self._bl_form_widget.setVisible(False)
        bl_layout.addWidget(self._bl_form_widget)
        mesh_layout.addWidget(bl_group)

        mesher_group = QGroupBox("Mesher")
        mesher_layout = QVBoxLayout(mesher_group)
        self._mesher_combo = QComboBox()
        self._mesher_combo.addItems([
            "cfMesh (hexa-dominant + WSL2)",
            "GMSH hybrid (surface + cfMesh volume)",
            "GMSH direct (tetra + prism, no WSL)",
        ])
        self._mesher_combo.setCurrentText("cfMesh (hexa-dominant + WSL2)")
        mesher_layout.addWidget(self._mesher_combo)
        self._mesher_combo.currentTextChanged.connect(self._on_mesher_changed)
        mesh_layout.addWidget(mesher_group)

        mesh_layout.addStretch()
        self._tabs.addTab(mesh_tab, "Mesh")

        adv_tab = QWidget()
        adv_layout = QVBoxLayout(adv_tab)

        self._poly_check = QCheckBox("Convert to polyhedral mesh")
        adv_layout.addWidget(self._poly_check)

        self._parallel_check = QCheckBox("Parallel meshing (future)")
        self._parallel_check.setEnabled(False)
        adv_layout.addWidget(self._parallel_check)

        adv_layout.addStretch()
        self._tabs.addTab(adv_tab, "Advanced")

        qual_tab = QWidget()
        qual_layout = QVBoxLayout(qual_tab)
        self._quality_label = QLabel("No quality data yet. Run a mesh first.")
        self._quality_label.setWordWrap(True)
        qual_layout.addWidget(self._quality_label)
        qual_layout.addStretch()
        self._tabs.addTab(qual_tab, "Quality")

        btn_layout = QHBoxLayout()
        self._btn_quick = QPushButton("Quick Mesh")
        self._btn_quick.setToolTip("Auto-suggest params and run. Shortcut: Ctrl+M")
        self._btn_quick.clicked.connect(self._on_quick_mesh)
        btn_layout.addWidget(self._btn_quick)

        self._btn_run = QPushButton("Generate Mesh")
        self._btn_run.setMinimumHeight(40)
        self._btn_run.setEnabled(False)
        self._btn_run.setToolTip("Start meshing. Shortcut: Ctrl+R.")
        self._btn_run.clicked.connect(self._on_run)
        btn_layout.addWidget(self._btn_run)

        outer.addLayout(btn_layout)

        btn_reset_layout = QHBoxLayout()
        self._btn_reset = QPushButton("Reset All")
        self._btn_reset.clicked.connect(self.reset_all.emit)
        btn_reset_layout.addWidget(self._btn_reset)
        outer.addLayout(btn_reset_layout)

    def _on_max_cell_changed(self, v: float):
        old = self._prev_cell_params["max_cell"]
        self._prev_cell_params["max_cell"] = v
        if self._undo_stack and abs(v - old) > 1e-9:
            self._undo_stack.push(ParamChangeCommand("max_cell_size", old, v, self))
        self._emit_param_changed()

    def _on_min_cell_changed(self, v: float):
        old = self._prev_cell_params["min_cell"]
        self._prev_cell_params["min_cell"] = v
        if self._undo_stack and abs(v - old) > 1e-9:
            self._undo_stack.push(ParamChangeCommand("min_cell_size", old, v, self))
        self._emit_param_changed()

    def _emit_param_changed(self):
        self.param_changed.emit("cell_sizes", {
            "max_cell": self._max_cell.value(),
            "min_cell": self._min_cell.value(),
        })

    def _on_quick_mesh(self):
        from PySide6.QtCore import QMetaObject, Q_ARG, Qt
        parent = self.parent()
        while parent:
            if hasattr(parent, "_on_quick_mesh"):
                parent._on_quick_mesh()
                break
            parent = parent.parent()

    def _on_bl_toggled(self, checked: bool):
        self._bl_form_widget.setVisible(checked)

    def _on_suggest_sizes(self):
        if not self._suggest_meshes:
            QMessageBox.information(self, "No Geometry", "Load a CAD geometry first.")
            return
        detail = self.get_detail_level()
        from cfmesh_autogui.core.geometry import suggest_cell_sizes, analyze_local_thickness
        try:
            analysis = analyze_local_thickness(self._suggest_meshes)
            s_max, s_min = suggest_cell_sizes(self._suggest_meshes, detail=detail)
        except Exception as e:
            # A silent failure here (uncaught exception in a Qt slot) looks
            # exactly like the button doing nothing — surface it instead.
            logger.exception("Auto-suggest cell sizes failed")
            QMessageBox.warning(self, "Auto-Suggest Failed", str(e))
            return

        # "Auto-Suggest" is the confirmation — a modal "apply these values?"
        # dialog on top of it is redundant friction, and if missed/dismissed
        # (e.g. opens behind the main window) it looks exactly like the
        # button silently did nothing. Apply directly; the log line below
        # already tells the user what changed and why.
        self._max_cell.setValue(s_max)
        self._min_cell.setValue(s_min)
        logger.info(
            "Auto-suggest cell sizes: max=%.4f, min=%.4f (detail=%s, n=%d)",
            s_max, s_min, detail, analysis.get("n_samples", 0),
        )
        if analysis.get("n_samples", 0) > 0:
            self.suggestion_completed.emit(
                f"[suggest] detail={detail}  thinnest feature (p10)={analysis['p10']:.4f} m  "
                f"-> max={s_max:.4f}  min={s_min:.4f} m"
            )
        else:
            self.suggestion_completed.emit(
                f"[suggest] detail={detail}  (thickness sampling failed, used bbox fallback)  "
                f"-> max={s_max:.4f}  min={s_min:.4f} m"
            )

    def set_bbox(self, dx: float, dy: float, dz: float):
        self._domain_label.setText(f"Domain: {dx:.3f} \u00d7 {dy:.3f} \u00d7 {dz:.3f} m")

    def set_suggest_meshes(self, meshes: list) -> None:
        self._suggest_meshes = list(meshes) if meshes else []

    def set_cell_estimate(self, text: str):
        self._cell_est_label.setText(text)

    def set_real_cell_count(self, count: int):
        self._cell_est_label.setText(f"Cells: {count:,}")

    def _on_run(self):
        if self._meshing_state:
            self.cancel_meshing.emit()
            return
        if not self._validate_params():
            return
        self.run_meshing.emit()

    def _validate_params(self) -> bool:
        max_val = self._max_cell.value()
        min_val = self._min_cell.value()
        if max_val <= min_val:
            QMessageBox.warning(
                self, "Invalid Mesh Parameters",
                f"Max Cell Size ({max_val:.4f} m) must be strictly greater than "
                f"Min Cell Size ({min_val:.4f} m).\n\n"
                "Please adjust the values before generating the mesh.",
            )
            return False
        return True

    def set_patches(self, patch_names: list[str]):
        self._patch_list.clear()
        for name in patch_names:
            self._patch_list.addItem(name)
        self._btn_run.setEnabled(len(patch_names) > 0)

    def get_mesh_params(self) -> dict:
        return {
            "max_cell_size": self._max_cell.value(),
            "min_cell_size": self._min_cell.value(),
        }

    def get_bl_params(self) -> dict | None:
        if not self._bl_checkbox.isChecked():
            return None
        return {
            "nLayers": self._bl_n_layers.value(),
            "thicknessRatio": self._bl_thick.value(),
            "expansionRatio": self._bl_exp.value(),
        }

    def get_bl_apply_all(self) -> bool:
        return self._bl_apply_all.isChecked()

    def get_poly_conversion(self) -> bool:
        return self._poly_check.isChecked()

    _DETAIL_LABELS = ["Molto Grossolana", "Grossolana", "Media", "Fine", "Molto Fine"]
    _DETAIL_MAP = {0: "very_coarse", 1: "coarse", 2: "medium", 3: "fine", 4: "very_fine"}

    def _on_detail_changed(self, value: int):
        self._detail_label.setText(self._DETAIL_LABELS[value])

    def get_detail_level(self) -> str:
        return self._DETAIL_MAP.get(self._detail_slider.value(), "medium")

    def set_current_tab(self, index: int) -> None:
        if hasattr(self, "_tabs") and 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)

    def get_mesher_type(self) -> str:
        text = self._mesher_combo.currentText()
        if "hybrid" in text:
            return "gmsh_hybrid"
        if "direct" in text:
            return "gmsh_direct"
        return "cfmesh"

    def set_poly_enabled(self, enabled: bool) -> None:
        self._poly_check.setEnabled(enabled)

    def get_max_cell(self) -> float:
        return self._max_cell.value()

    def get_min_cell(self) -> float:
        return self._min_cell.value()

    def get_scale_factor(self) -> float:
        from cfmesh_autogui.core.geometry import unit_to_scale
        return unit_to_scale(self._unit_selector.currentText())

    def get_n_cores(self) -> int:
        return 1

    def get_patch_names(self) -> list[str]:
        return [
            self._patch_list.item(i).text()
            for i in range(self._patch_list.count())
        ]

    def set_case_dir(self, path: str):
        self._case_dir = path
        self._cad_label.setText(f"Case: {path}")

    def set_meshing_enabled(self, enabled: bool):
        self._btn_run.setEnabled(enabled)

    def set_all_enabled(self, enabled: bool):
        self._btn_run.setEnabled(enabled)
        self._btn_reset.setEnabled(enabled)
        self._btn_load.setEnabled(enabled)
        self._btn_suggest.setEnabled(enabled)
        self._btn_quick.setEnabled(enabled)
        self._max_cell.setEnabled(enabled)
        self._min_cell.setEnabled(enabled)
        self._bl_checkbox.setEnabled(enabled)
        self._unit_selector.setEnabled(enabled)
        self._mesher_combo.setEnabled(enabled)

    def set_meshing_state(self, running: bool):
        if running:
            self._btn_run.setText("Cancel")
            self._btn_run.setStyleSheet(f"background-color:{COLOR_DANGER};color:white")
            self._btn_run.setEnabled(True)
            self._btn_reset.setEnabled(False)
            self._meshing_state = True
        else:
            self._btn_run.setText("Generate Mesh")
            self._btn_run.setStyleSheet("")
            self._btn_reset.setEnabled(True)
            self._meshing_state = False

    def trigger_meshing(self):
        self._on_run()

    def _on_mesher_changed(self, text: str) -> None:
        is_gmsh_direct = "direct" in text
        self._poly_check.setVisible(not is_gmsh_direct)

    def save_params(self, s):
        s.setValue("params/max_cell", self._max_cell.value())
        s.setValue("params/min_cell", self._min_cell.value())
        s.setValue("params/bl_checked", self._bl_checkbox.isChecked())
        s.setValue("params/unit", self._unit_selector.currentText())

    def restore_params(self, s):
        max_cell = s.value("params/max_cell", None)
        if max_cell is not None:
            self._max_cell.setValue(float(max_cell))
        min_cell = s.value("params/min_cell", None)
        if min_cell is not None:
            self._min_cell.setValue(float(min_cell))
        bl = s.value("params/bl_checked", None, type=bool)
        if bl is not None:
            self._bl_checkbox.setChecked(bool(bl))
        unit = s.value("params/unit", None)
        if unit is not None:
            self._unit_selector.setCurrentText(unit)

    def _on_template_selected(self, text: str):
        if text == "None (manual)":
            return
        template_map = {
            "Internal Flow": "internal_flow.json",
            "External Aero": "external_aero.json",
            "CHT": "cht.json",
        }
        fname = template_map.get(text)
        if not fname:
            return
        tmpl_path = Path(__file__).resolve().parent.parent.parent.parent / "templates" / fname
        if not tmpl_path.exists():
            logger.warning("Template not found: %s", tmpl_path)
            QMessageBox.warning(self, "Template Not Found", f"Template file not found:\n{tmpl_path}")  # ✅ F-018
            return
        try:
            with open(tmpl_path) as f:
                tmpl = json.load(f)
            defaults = tmpl.get("defaults", {})
            detail_map = {"very_coarse": 0, "coarse": 1, "medium": 2, "fine": 3, "very_fine": 4}
            detail = defaults.get("detail", "medium")
            self._detail_slider.setValue(detail_map.get(detail, 2))
            max_r = defaults.get("max_cell_ratio", 0.05)
            min_r = defaults.get("min_cell_ratio", 0.005)
            self._max_cell.setValue(max_r)
            self._min_cell.setValue(min_r)
            bl = defaults.get("boundary_layers", {})
            if bl:
                self._bl_checkbox.setChecked(True)
                self._bl_n_layers.setValue(bl.get("nLayers", 3))
                self._bl_thick.setValue(bl.get("thicknessRatio", 0.005))
                self._bl_exp.setValue(bl.get("expansionRatio", 1.2))
            logger.info("Template loaded: %s", text)
        except Exception as e:
            logger.warning("Failed to load template %s: %s", fname, e)
