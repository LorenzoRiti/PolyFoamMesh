from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import trimesh  # ✅ F-017
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cfmesh_autogui.gui.design_tokens import ORANGE_500
from cfmesh_autogui.gui.style import (
    COLOR_DANGER,
    COLOR_TEXT_DIM,
    FS_METRIC,
    metric_label,
)

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
    pick_refinement_requested = Signal()

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

        # Flat field styling inside sections: remove individual dark boxes
        # per field — only the QGroupBox sections keep their background.
        self.setStyleSheet("""
            QGroupBox QLabel,
            QGroupBox QSpinBox,
            QGroupBox QDoubleSpinBox,
            QGroupBox QComboBox,
            QGroupBox QCheckBox,
            QGroupBox QListWidget {
                background: transparent;
                border: none;
                color: palette(text);
            }
            QGroupBox QLabel[role="status-pass"],
            QGroupBox QLabel[role="status-warn"],
            QGroupBox QLabel[role="status-fail"],
            QGroupBox QLabel[role="status-neutral"] {
                background: transparent;
                border: none;
            }
            QGroupBox QPushButton {
                background: palette(button);
                border: 1px solid palette(mid);
                border-radius: 4px;
                padding: 4px 12px;
            }
            QGroupBox QPushButton:disabled {
                color: palette(dark);
                background: palette(window);
                border-color: palette(midlight);
            }
        """)

        self._tabs = QTabWidget()

        # Wrap in a scroll area so shrinking the side panel (dragging the
        # main window's horizontal splitter) makes the panel scrollable
        # instead of squeezing/clipping every group box and control onto
        # top of each other with no way to reach the ones pushed off-screen.
        scroll = QScrollArea()
        scroll.setWidget(self._tabs)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        geom_tab = QWidget()
        geom_layout = QVBoxLayout(geom_tab)

        # CAD Geometry first: loading geometry is the first thing you do
        # on this (first) tab, before picking a case template that only
        # makes sense once there's something to apply it to.
        cad_group = QGroupBox("CAD Geometry")
        cad_layout = QVBoxLayout(cad_group)
        self._cad_label = QLabel("No CAD loaded")
        self._cad_label.setWordWrap(True)
        cad_layout.addWidget(self._cad_label)

        btn_load = QPushButton("Load Geometry...")
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
        self._domain_label.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        cad_layout.addWidget(self._domain_label)

        form_geom = QFormLayout()
        self._scale_factor = QDoubleSpinBox()
        self._scale_factor.setRange(0.001, 1000.0)
        self._scale_factor.setValue(1.0)
        self._scale_factor.setDecimals(6)
        form_geom.addRow("Scale Factor:", self._scale_factor)
        cad_layout.addLayout(form_geom)

        geom_layout.addWidget(cad_group)

        template_group = QGroupBox("Case Template")
        template_layout = QVBoxLayout(template_group)
        # Distinct from the Mesh tab's self._template_combo below (a separate
        # TemplateEngine-backed picker) — same "Case Template" group box
        # label, different widget/purpose, previously both assigned to the
        # same attribute name (the second silently shadowed this one).
        self._geometry_template_combo = QComboBox()
        self._geometry_template_combo.addItems(["None (manual)", "Internal Flow", "External Aero", "CHT"])
        self._geometry_template_combo.currentTextChanged.connect(self._on_template_selected)
        template_layout.addWidget(self._geometry_template_combo)
        template_layout.addWidget(QLabel("Preset: geometry + mesh defaults per case type"))
        geom_layout.addWidget(template_group)

        patch_group = QGroupBox("Patches")
        patch_layout = QVBoxLayout(patch_group)
        self._patch_list = QListWidget()
        self._patch_list.setMinimumHeight(40)
        self._patch_list.setMaximumHeight(120)
        patch_layout.addWidget(self._patch_list)
        self._patch_placeholder = QLabel("Nessuna patch — carica una geometria")
        self._patch_placeholder.setAlignment(Qt.AlignCenter)
        self._patch_placeholder.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        self._patch_placeholder.setVisible(True)
        patch_layout.addWidget(self._patch_placeholder)
        self._patch_list.setVisible(False)
        geom_layout.addWidget(patch_group)

        geom_layout.addStretch()
        self._tabs.addTab(geom_tab, "Geometry")

        mesh_tab = QWidget()
        mesh_layout = QVBoxLayout(mesh_tab)

        # Case templates: pick a starting point (Internal Flow, External Aero,
        # CHT, ...) instead of guessing detail level / BL / cell-size ratios
        # from scratch. Only reads template metadata (name/description/detail/
        # BL/cell-size RATIOS) — deliberately does NOT call TemplateEngine's own
        # case-writing path, which passes those ratios straight through as
        # absolute cell sizes and predates the current BL contract; scaling by
        # the loaded geometry and going through the normal meshing pipeline
        # here keeps every fix already made (BL keys, patch typing, ...) intact.
        template_group = QGroupBox("Case Template")
        template_layout = QHBoxLayout(template_group)
        self._template_combo = QComboBox()
        self._template_combo.setToolTip(
            "Preimposta livello di dettaglio, strati limite e rapporto "
            "dimensione celle per un tipo di caso comune."
        )
        try:
            from cfmesh_autogui.commercial.template_engine import TemplateEngine
            self._templates = TemplateEngine().list_templates()
        except Exception:
            self._templates = []
        for t in self._templates:
            self._template_combo.addItem(t.metadata.name)
        template_layout.addWidget(self._template_combo)
        btn_apply_template = QPushButton("Apply")
        btn_apply_template.clicked.connect(self._on_apply_template)
        template_layout.addWidget(btn_apply_template)
        mesh_layout.addWidget(template_group)
        self._bbox_dim = 1.0
        self._applied_template_solver: str | None = None
        self._applied_template_turbulence: str | None = None

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
        # Wall-resolved meshes (y+~1) legitimately need 30+ layers; the old
        # 1..10 cap silently truncated whatever the physics asked for.
        self._bl_n_layers.setRange(1, 40)
        self._bl_n_layers.setValue(3)
        bl_form.addRow("nLayers:", self._bl_n_layers)
        self._bl_thick = QDoubleSpinBox()
        # First layer as a fraction of max cell size. y+~1 gives fractions of
        # order 1e-4, far below the old 0.001 floor.
        self._bl_thick.setRange(1e-6, 1.0)
        self._bl_thick.setValue(0.005)
        self._bl_thick.setDecimals(6)
        self._bl_thick.setToolTip(
            "First-layer thickness as a fraction of the max cell size. "
            "Use 'Calcola strati dalla fisica (y+)' to derive it."
        )
        bl_form.addRow("First Layer (× cell):", self._bl_thick)
        self._bl_exp = QDoubleSpinBox()
        self._bl_exp.setRange(1.0, 2.0)
        self._bl_exp.setValue(1.2)
        self._bl_exp.setSingleStep(0.1)
        bl_form.addRow("Expansion Ratio:", self._bl_exp)
        self._bl_apply_all = QCheckBox("Apply BL to all patches")
        bl_form.addRow(self._bl_apply_all)

        # Physical inputs: the layer parameters above are derived from these
        # rather than guessed, which is how commercial meshers do it.
        self._bl_velocity = QDoubleSpinBox()
        self._bl_velocity.setRange(0.001, 1000.0)
        self._bl_velocity.setValue(10.0)
        self._bl_velocity.setDecimals(3)
        self._bl_velocity.setSuffix(" m/s")
        self._bl_velocity.setToolTip("Velocità caratteristica del flusso (free-stream o media).")
        bl_form.addRow("Velocity:", self._bl_velocity)

        self._bl_fluid = QComboBox()
        self._bl_fluid.addItems(["Air (20°C)", "Water (20°C)"])
        self._bl_fluid.setToolTip("Determina la viscosità cinematica usata per il numero di Reynolds.")
        bl_form.addRow("Fluid:", self._bl_fluid)

        self._bl_wall_treatment = QComboBox()
        self._bl_wall_treatment.addItems([
            "Wall functions (y+ ≈ 30)",
            "Wall-resolved (y+ ≈ 1)",
        ])
        self._bl_wall_treatment.setToolTip(
            "Wall functions: meno celle, adatto a k-epsilon.\n"
            "Wall-resolved: primo strato molto sottile, richiesto da k-omega SST e LES."
        )
        bl_form.addRow("Wall treatment:", self._bl_wall_treatment)

        self._btn_bl_auto = QPushButton("Calcola strati dalla fisica (y+)")
        self._btn_bl_auto.setToolTip(
            "Deriva nLayers, spessore del primo strato e rapporto di crescita da "
            "Reynolds e dal target y+, invece di indovinarli."
        )
        self._btn_bl_auto.clicked.connect(self._on_bl_auto_compute)
        bl_form.addRow(self._btn_bl_auto)

        self._bl_info = QLabel("")
        self._bl_info.setWordWrap(True)
        self._bl_info.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        bl_form.addRow(self._bl_info)

        self._bl_form_widget = QWidget()
        self._bl_form_widget.setLayout(bl_form)
        self._bl_form_widget.setVisible(False)
        bl_layout.addWidget(self._bl_form_widget)
        mesh_layout.addWidget(bl_group)

        mesher_group = QGroupBox("Mesher")
        mesher_layout = QVBoxLayout(mesher_group)
        self._mesher_combo = QComboBox()
        self._mesher_combo.addItems([
            "Automatic (recommended)",
            "cfMesh (hexa-dominant + WSL2)",
            "Tetrahedral (FEM) — GMSH direct",
            "Polyhedral (CFD) — GMSH + poly",
            "autopoly (sperimentale, no-WSL)",
        ])
        self._mesher_combo.setToolTip(
            "Automatic sceglie il miglior mesher disponibile e genera una\n"
            "mesh POLIEDRICA vera:\n"
            "  • cfMesh (WSL2) + conversione polyDualMesh — verificato pulito\n"
            "    con checkMesh anche su geometrie curve complesse\n"
            "  • autopoly (nativo, sperimentale) — solo se WSL2 non disponibile\n"
            "  • GMSH direct — tetraedrico, fallback universale\n\n"
            "cfMesh: forza cartesianMesh via WSL2/OpenFOAM, poi converte in\n"
            "poliedrico (spunta 'Convert to polyhedral mesh' in Advanced).\n"
            "Il dual di una mesh cartesiana resta molto regolare/a griglia\n"
            "(celle di bordo ~99% quadrilateri).\n\n"
            "Tetrahedral (FEM) / Polyhedral (CFD): mesh tetraedrica non strutturata\n"
            "(via GMSH, funziona meglio con input STEP/CAD reale) + stessa\n"
            "conversione polyDualMesh — dualizzare una mesh NON strutturata\n"
            "produce poliedri irregolari veri, stile Voronoi/STAR-CCM+\n"
            "(verificato: checkMesh pulito, celle da 7 a 23 facce). Da STL\n"
            "grezzo (senza topologia CAD) la generazione può ancora fallire\n"
            "su superfici curve lunghe e sottili — preferire STEP quando\n"
            "possibile.\n\n"
            "autopoly: motore CVT Python nativo, no WSL richiesto — ma su\n"
            "superfici molto curve produce ancora bordi non perfettamente\n"
            "conformi (verificato con checkMesh). Usare solo se WSL2/OpenFOAM\n"
            "non è disponibile."
        )
        self._mesher_combo.setCurrentText("Automatic (recommended)")
        mesher_layout.addWidget(self._mesher_combo)
        self._mesher_combo.currentTextChanged.connect(self._on_mesher_changed)
        mesh_layout.addWidget(mesher_group)
        self._bl_group = bl_group
        self._mesher_group = mesher_group

        self._refine_group = QGroupBox("Local Refinement")
        refine_layout = QVBoxLayout(self._refine_group)
        self._auto_refine_check = QCheckBox("Auto-refine narrow sections")
        self._auto_refine_check.setChecked(True)
        self._auto_refine_check.setToolTip(
            "Automatically detect throats/constrictions in duct-like geometries "
            "and create local refinement zones with finer cells."
        )
        refine_layout.addWidget(self._auto_refine_check)
        self._refine_list_label = QLabel("Manual refinement zones (box/sphere):")
        refine_layout.addWidget(self._refine_list_label)
        self._refine_placeholder = QLabel("Nessun raffinamento — aggiungi usando il pulsante +")
        self._refine_placeholder.setStyleSheet("color: gray; font-style: italic; padding: 4px;")
        refine_layout.addWidget(self._refine_placeholder)
        self._refine_list = QListWidget()
        self._refine_list.setMaximumHeight(80)
        self._refine_list.setVisible(False)
        refine_layout.addWidget(self._refine_list)
        refine_btn_row = QHBoxLayout()
        self._add_refine_btn = QPushButton("+")
        self._add_refine_btn.setMaximumWidth(32)
        self._add_refine_btn.setToolTip("Add a manual refinement zone (opens dialog)")
        self._add_refine_btn.clicked.connect(self._on_add_refinement)
        self._remove_refine_btn = QPushButton("−")
        self._remove_refine_btn.setMaximumWidth(32)
        self._remove_refine_btn.setToolTip("Remove selected refinement zone")
        self._remove_refine_btn.clicked.connect(self._on_remove_refinement)
        refine_btn_row.addWidget(self._add_refine_btn)
        refine_btn_row.addWidget(self._remove_refine_btn)
        refine_btn_row.addStretch()
        refine_layout.addLayout(refine_btn_row)
        mesh_layout.addWidget(self._refine_group)

        mesh_layout.addStretch()
        self._tabs.addTab(mesh_tab, "Mesh")

        adv_tab = QWidget()
        adv_layout = QVBoxLayout(adv_tab)

        exp_label = QLabel(
            "Experimental — queste funzioni sono ancora in fase di "
            "ottimizzazione e potrebbero non funzionare con tutte le "
            "geometrie o configurazioni."
        )
        exp_label.setWordWrap(True)
        exp_label.setStyleSheet(
            f"color: {ORANGE_500}; font-weight: bold; padding: 4px;"
        )
        adv_layout.addWidget(exp_label)
        adv_layout.addSpacing(8)

        self._poly_check = QCheckBox("Convert to polyhedral mesh")
        self._poly_check.setToolTip(
            "Post-process: converts the hex-dominant mesh into an arbitrary\n"
            "polyhedral mesh using cfMesh's polyDualMesh utility.\n\n"
            "Effects:\n"
            "  • Cell count INCREASES 15-30% (dual mesh operation)\n"
            "  • Non-orthogonality improves (smoother cells)\n"
            "  • Boundary layers preserved as prism cells\n"
            "  • Mesh becomes more suitable for some solvers\n\n"
            "Not the same as STAR-CCM+ polyhedral meshing\n"
            "(which reduces cell count vs tetrahedral).\n"
            "Use only when a polyhedral topology is required."
        )
        adv_layout.addWidget(self._poly_check)

        # Guides for the GMSH adaptive/automatic refinement (Tetrahedral
        # FEM / Polyhedral CFD mesher only): without these the "Max/Min
        # Cell Size" fields above — always populated by Auto-Suggest —
        # silently forced the old uniform sizing every time, so the
        # adaptive algorithm never actually ran in practice. Two knobs:
        # a switch back to manual uniform sizing, and a direct cap on
        # how large the mesh is allowed to grow (the "8M tet cells"
        # concern — the auto hardware budget only guides how fine small
        # features get resolved, it doesn't hard-cap the total count).
        self._adaptive_sizing_check = QCheckBox("Adaptive automatic sizing (GMSH mesher, recommended)")
        self._adaptive_sizing_check.setChecked(True)
        self._adaptive_sizing_check.setToolTip(
            "Refines only near small features/curved surfaces, coarse\n"
            "elsewhere, sized from the geometry itself and the available\n"
            "hardware — instead of one uniform size everywhere (the\n"
            "Max/Min Cell Size fields above, still used when this is off,\n"
            "or as a starting point Auto-Suggest fills in).\n"
            "Only applies to the Tetrahedral (FEM) / Polyhedral (CFD) mesher."
        )
        adv_layout.addWidget(self._adaptive_sizing_check)

        max_cells_row = QHBoxLayout()
        max_cells_row.addWidget(QLabel("Max cells target:"))
        self._max_cells_target = QSpinBox()
        self._max_cells_target.setRange(0, 50_000_000)
        self._max_cells_target.setSingleStep(100_000)
        self._max_cells_target.setValue(0)
        self._max_cells_target.setSpecialValueText("Auto (hardware-based)")
        self._max_cells_target.setToolTip(
            "0 = decide automatically from free RAM/CPU cores.\n"
            "Set a value to cap it directly instead — e.g. if an 8M-cell\n"
            "tetrahedral mesh feels like more than you want to wait on."
        )
        max_cells_row.addWidget(self._max_cells_target)
        adv_layout.addLayout(max_cells_row)

        openmp_group = QGroupBox("OpenMP Acceleration")
        openmp_group_layout = QVBoxLayout(openmp_group)
        openmp_row = QHBoxLayout()
        self._openmp_combo = QComboBox()
        self._openmp_combo.addItems([
            "Bilanciato (fisici, consigliato)",
            "Conservativo (fisici/2)",
            "Aggressivo (logici)",
            "Custom...",
        ])
        self._openmp_combo.setToolTip(
            "Controlla quante thread OpenMP usa cfMesh per il calcolo parallelo.\n\n"
            "• Bilanciato: usa tutti i core fisici (consigliato)\n"
            "• Conservativo: metà dei core fisici (meno memoria, più lento)\n"
            "• Aggressivo: tutti i core logici, inclusi hyperthread\n"
            "• Custom: imposta manualmente il numero di thread\n\n"
            "Ogni thread consuma memoria, quindi modi più aggressivi "
            "possono causare OOM su mesh grandi."
        )
        self._openmp_combo.currentTextChanged.connect(self._on_openmp_mode_changed)
        openmp_row.addWidget(QLabel("Thread mode:"))
        openmp_row.addWidget(self._openmp_combo)
        self._openmp_spin = QSpinBox()
        cpu_n = os.cpu_count() or 4
        self._openmp_spin.setRange(1, max(1, cpu_n))
        self._openmp_spin.setValue(max(1, cpu_n // 2))
        self._openmp_spin.setSuffix(" threads")
        self._openmp_spin.setEnabled(False)
        self._openmp_spin.setToolTip("Numero di thread OpenMP da utilizzare (modalità Custom).")
        openmp_row.addWidget(self._openmp_spin)
        openmp_group_layout.addLayout(openmp_row)
        adv_layout.addWidget(openmp_group)

        parallel_group = QGroupBox("Parallel Meshing (MPI)")
        parallel_group_layout = QVBoxLayout(parallel_group)

        parallel_row = QHBoxLayout()
        self._parallel_check = QCheckBox("Parallel meshing (multi-core)")
        self._parallel_check.setToolTip(
            "Runs cartesianMesh across multiple CPU cores via MPI "
            "(cartesianMesh -parallel) and reconstructs the result. "
            "Worth it above a few hundred thousand cells; the "
            "decompose/reconstruct overhead can outweigh the benefit on "
            "small meshes.\n\n"
            "Each core loads a full copy of the geometry, so memory use "
            "scales with the core count — inside WSL2 (which caps its own "
            "memory well below the host's) too many cores on a large "
            "geometry can exhaust that memory and hang rather than speed "
            "things up. Start low and increase only if it works reliably."
        )
        self._parallel_check.toggled.connect(
            lambda on: self._parallel_cores.setEnabled(on)
        )
        parallel_row.addWidget(self._parallel_check)
        self._parallel_cores = QSpinBox()
        cpu_n = os.cpu_count() or 4
        self._parallel_cores.setRange(2, max(2, cpu_n))
        # Half the cores, not "all but one": each rank redundantly loads
        # the full geometry, so memory (not just CPU) scales with core
        # count, and WSL2's own memory cap is the usual real constraint.
        self._parallel_cores.setValue(max(2, min(4, cpu_n // 2)))
        self._parallel_cores.setSuffix(" cores")
        self._parallel_cores.setEnabled(False)
        parallel_row.addWidget(self._parallel_cores)
        parallel_group_layout.addLayout(parallel_row)
        adv_layout.addWidget(parallel_group)

        adv_layout.addStretch()
        self._adv_tab = adv_tab
        self._tabs.addTab(adv_tab, "Advanced")

        qual_tab = QWidget()
        qual_layout = QVBoxLayout(qual_tab)
        self._quality_label = QLabel("No quality data yet. Run a mesh first.")
        self._quality_label.setWordWrap(True)
        qual_layout.addWidget(self._quality_label)
        qual_layout.addStretch()
        self._tabs.addTab(qual_tab, "Quality")

        # "Quick Mesh" (auto-suggest sizes + run, for a first pass / less
        # experienced users) lives only in the ribbon now — it used to be
        # duplicated here as a second button doing the exact same thing,
        # which read as two competing "run" actions with no clear
        # distinction. This panel keeps just "Generate Mesh": the
        # deliberate, tuned-settings action for when you've set your own
        # cell sizes / boundary layers / mesher choice on this tab.
        btn_layout = QHBoxLayout()
        self._btn_run = QPushButton("Generate Mesh")
        self._btn_run.setMinimumHeight(40)
        self._btn_run.setEnabled(False)
        self._btn_run.setToolTip("Start meshing with the settings on this tab. Shortcut: Ctrl+R.")
        self._btn_run.clicked.connect(self._on_run)
        btn_layout.addWidget(self._btn_run)

        outer.addLayout(btn_layout)

        btn_reset_layout = QHBoxLayout()
        self._btn_reset = QPushButton("Reset All")
        self._btn_reset.clicked.connect(self.reset_all.emit)
        btn_reset_layout.addWidget(self._btn_reset)
        outer.addLayout(btn_reset_layout)

        # Default to simple mode: hide the knobs a first-time user doesn't
        # need yet (boundary layers, mesher choice, parallel/polyhedral).
        # Cell sizes + detail level + Generate Mesh are enough to get a
        # working mesh; set_expert_mode(True) reveals the rest for users
        # who want to tune them.
        self._expert_mode = False
        self.set_expert_mode(False)

    def is_expert_mode(self) -> bool:
        return self._expert_mode

    def set_expert_mode(self, enabled: bool) -> None:
        self._expert_mode = enabled
        self._bl_group.setVisible(enabled)
        self._mesher_group.setVisible(enabled)
        idx = self._tabs.indexOf(self._adv_tab)
        if idx >= 0:
            self._tabs.setTabVisible(idx, enabled)

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

    def _on_bl_toggled(self, checked: bool):
        self._bl_form_widget.setVisible(checked)

    # Kinematic viscosity at 20 °C, m²/s.
    _FLUID_NU = {"Air (20°C)": 1.5e-5, "Water (20°C)": 1.0e-6}

    def _on_apply_template(self):
        """Apply a case-template PRESET (detail, BL, cell-size ratio).

        Deliberately only reads TemplatePreset metadata and never calls
        TemplateEngine.apply_template() itself — that method writes meshDict
        directly from the template's *ratios* as if they were absolute cell
        sizes (wrong for any geometry but the one it was tuned on) and predates
        the current boundary-layer key contract. Scaling the ratios by the
        loaded geometry's bounding box here, and setting the same widgets the
        user would set by hand, means the template goes through the normal
        (already-correct) meshing pipeline.
        """
        idx = self._template_combo.currentIndex()
        if idx < 0 or idx >= len(self._templates):
            return
        t = self._templates[idx]

        detail_to_slider = {v: k for k, v in self._DETAIL_MAP.items()}
        self._detail_slider.setValue(detail_to_slider.get(t.detail, 2))

        self._bl_checkbox.setChecked(bool(t.bl_enabled))
        if t.bl_enabled:
            self._bl_n_layers.setValue(
                min(max(t.bl_n_layers, self._bl_n_layers.minimum()),
                    self._bl_n_layers.maximum())
            )

        max_cell = t.max_cell_ratio * self._bbox_dim
        min_cell = t.min_cell_ratio * self._bbox_dim
        self._max_cell.setValue(max_cell)
        self._min_cell.setValue(min_cell)

        # Remembered so main_window can carry the solver/turbulence choice
        # into setup_case() once meshing finishes.
        self._applied_template_solver = t.metadata.solver
        self._applied_template_turbulence = t.metadata.turbulence

        note = "" if self._bbox_dim != 1.0 else " (load a geometry for a real cell size)"
        self.suggestion_completed.emit(
            f"[template] {t.metadata.name}: {t.metadata.description} — "
            f"detail={t.detail} BL={'on' if t.bl_enabled else 'off'} "
            f"(n={t.bl_n_layers}) max={max_cell:.4g}m min={min_cell:.4g}m{note}"
        )

    def get_template_solver_turbulence(self) -> tuple[str, str] | None:
        """(solver, turbulence_model) from the last applied template, or None."""
        solver = getattr(self, "_applied_template_solver", None)
        turb = getattr(self, "_applied_template_turbulence", None)
        if solver is None or turb is None:
            return None
        return solver, turb

    def _on_bl_auto_compute(self):
        """Derive boundary-layer parameters from flow physics.

        Replaces hand-guessed layer counts with the standard flat-plate
        correlation (Cf -> u_tau -> y1 from the y+ target, layer count from the
        99% BL thickness) — the same calculation commercial meshers expose as a
        "y+ calculator".
        """
        if not self._suggest_meshes:
            QMessageBox.information(
                self, "No Geometry",
                "Carica prima una geometria: serve la lunghezza caratteristica.",
            )
            return

        from cfmesh_autogui.commercial.bl_engine import BLEngine, FlowConditions
        from cfmesh_autogui.core.geometry import compute_bbox_dim

        length = compute_bbox_dim(self._suggest_meshes)
        velocity = self._bl_velocity.value()
        nu = self._FLUID_NU.get(self._bl_fluid.currentText(), 1.5e-5)
        resolved = "resolved" in self._bl_wall_treatment.currentText().lower()
        model = "kOmegaSST" if resolved else "kEpsilon"

        try:
            flow = FlowConditions.from_velocity(
                reference_velocity=velocity,
                reference_length=length,
                kinematic_viscosity=nu,
                turbulence_model=model,
            )
            params = BLEngine().calculate_from_flow(flow)
        except Exception as e:
            logger.exception("Boundary-layer physics calculation failed")
            QMessageBox.warning(self, "Calcolo non riuscito", str(e))
            return

        # meshDict takes the first layer as a fraction of the max cell size.
        max_cell = max(self._max_cell.value(), 1e-9)
        fraction = params.first_layer_height / max_cell
        clamped = min(max(fraction, self._bl_thick.minimum()), self._bl_thick.maximum())

        self._bl_n_layers.setValue(
            min(max(params.n_layers, self._bl_n_layers.minimum()),
                self._bl_n_layers.maximum())
        )
        self._bl_thick.setValue(clamped)
        self._bl_exp.setValue(
            min(max(params.growth_rate, self._bl_exp.minimum()), self._bl_exp.maximum())
        )

        note = ""
        if params.n_layers > self._bl_n_layers.maximum():
            note = (
                f"  ⚠ La fisica richiede {params.n_layers} strati, "
                f"limitati a {self._bl_n_layers.maximum()}."
            )
        if abs(clamped - fraction) > 1e-12:
            note += "  ⚠ Primo strato limitato dal range del campo."

        msg = (
            f"Re={flow.reynolds_number:.3g} · y+ target={params.target_yplus} · "
            f"primo strato={params.first_layer_height:.3g} m · "
            f"{params.n_layers} strati · crescita {params.growth_rate:.2f} · "
            f"spessore totale={params.total_thickness:.4g} m (L={length:.3g} m){note}"
        )
        self._bl_info.setText(msg)
        self.suggestion_completed.emit(f"[bl] {msg}")

    def _on_suggest_sizes(self):
        if not self._suggest_meshes:
            QMessageBox.information(self, "No Geometry", "Load a CAD geometry first.")
            return
        detail = self.get_detail_level()
        from cfmesh_autogui.core.geometry import (
            analyze_local_thickness,
            suggest_cell_sizes,
        )
        try:
            analysis = analyze_local_thickness(self._suggest_meshes)
            s_max, s_min = suggest_cell_sizes(self._suggest_meshes, detail=detail)
        except Exception as e:
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
        self._bbox_dim = max(dx, dy, dz, 1e-6)

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
        has_patches = len(patch_names) > 0
        if has_patches:
            for name in patch_names:
                self._patch_list.addItem(name)
            self._patch_list.setVisible(True)
            self._patch_placeholder.setVisible(False)
        else:
            self._patch_list.setVisible(False)
            self._patch_placeholder.setVisible(True)
        self._btn_run.setEnabled(has_patches)

    def get_mesh_params(self) -> dict:
        return {
            "max_cell_size": self._max_cell.value(),
            "min_cell_size": self._min_cell.value(),
        }

    def set_bl_enabled(self, enabled: bool) -> None:
        self._bl_checkbox.setChecked(enabled)

    def get_bl_enabled(self) -> bool:
        return self._bl_checkbox.isChecked()

    def get_bl_params(self) -> dict | None:
        if not self._bl_checkbox.isChecked():
            return None
        # Contract: thicknessRatio = growth ratio, firstLayerThickness = metres.
        # The "First Layer" field is a FRACTION of the max cell size, so convert
        # it to an absolute thickness here — sending the fraction as
        # thicknessRatio (the old behaviour) made cfMesh collapse the layers.
        return {
            "nLayers": self._bl_n_layers.value(),
            "thicknessRatio": self._bl_exp.value(),
            "firstLayerThickness": self._bl_thick.value() * self._max_cell.value(),
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
        if "Automatic" in text:
            return "auto"
        if "autopoly" in text:
            return "autopoly"
        if "Tetrahedral" in text or "Polyhedral" in text:
            return "gmsh_direct"
        return "cfmesh"

    def get_adaptive_sizing_enabled(self) -> bool:
        return getattr(self, "_adaptive_sizing_check", None) is not None and self._adaptive_sizing_check.isChecked()

    def get_max_cells_target(self) -> int:
        return getattr(self, "_max_cells_target", None) and self._max_cells_target.value() or 0

    def get_auto_refine_enabled(self) -> bool:
        return getattr(self, "_auto_refine_check", None) is not None and self._auto_refine_check.isChecked()

    def get_manual_refinements(self) -> list[dict]:
        """Return list of {centre, radius, cell_size} from manual zone entries."""
        refs: list[dict] = []
        for i in range(self._refine_list.count()):
            item = self._refine_list.item(i)
            data = item.data(Qt.UserRole)
            if isinstance(data, dict):
                refs.append(data)
        return refs

    def _on_add_refinement(self):
        """Add a manual refinement zone — pick from 3D or enter coordinates."""
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Refinement Zone",
            "How do you want to define the refinement zone?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Cancel:
            return
        if reply == QMessageBox.Yes:
            # Pick from 3D viewer
            self.pick_refinement_requested.emit()
            return
        # Manual coordinate entry
        cx, ok = QInputDialog.getDouble(self, "Refinement Zone Centre X",
                                         "X coordinate (m):", 0.0, -1000, 1000, 4)
        if not ok:
            return
        cy, ok = QInputDialog.getDouble(self, "Refinement Zone Centre Y",
                                         "Y coordinate (m):", 0.0, -1000, 1000, 4)
        if not ok:
            return
        cz, ok = QInputDialog.getDouble(self, "Refinement Zone Centre Z",
                                         "Z coordinate (m):", 0.0, -1000, 1000, 4)
        if not ok:
            return
        radius, ok = QInputDialog.getDouble(self, "Refinement Zone Radius",
                                             "Radius (half-diagonal, m):", 0.1, 0.001, 100, 4)
        if not ok:
            return
        cell_size, ok = QInputDialog.getDouble(self, "Refinement Cell Size",
                                                "Target cell size inside zone (m):",
                                                0.01, 0.0001, 10, 5)
        if not ok:
            return
        entry = {"centre": (cx, cy, cz), "radius": radius, "cell_size": cell_size}
        label = f"Box ({cx:.3f},{cy:.3f},{cz:.3f}) r={radius:.3f} cs={cell_size:.5f}"
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, entry)
        self._refine_list.addItem(item)
        self._refresh_refine_placeholder()

    def add_refinement_from_pick(self, cx: float, cy: float, cz: float):
        """Add a refinement zone from a 3D pick — prompts for radius and cell size."""
        radius, ok = QInputDialog.getDouble(self, "Refinement Zone Radius",
                                             "Radius (m):", 0.1, 0.001, 100, 4)
        if not ok:
            return
        cell_size, ok = QInputDialog.getDouble(self, "Refinement Cell Size",
                                                "Target cell size inside zone (m):",
                                                0.01, 0.0001, 10, 5)
        if not ok:
            return
        entry = {"centre": (cx, cy, cz), "radius": radius, "cell_size": cell_size}
        label = f"Pick ({cx:.3f},{cy:.3f},{cz:.3f}) r={radius:.3f} cs={cell_size:.5f}"
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, entry)
        self._refine_list.addItem(item)
        self._refresh_refine_placeholder()

    def _on_remove_refinement(self):
        row = self._refine_list.currentRow()
        if row >= 0:
            self._refine_list.takeItem(row)
            self._refresh_refine_placeholder()

    def _refresh_refine_placeholder(self):
        has_items = self._refine_list.count() > 0
        self._refine_placeholder.setVisible(not has_items)
        self._refine_list.setVisible(has_items)

    def get_parallel_params(self) -> tuple[bool, int]:
        """(enabled, n_cores) for MPI-parallel cartesianMesh."""
        return self._parallel_check.isChecked(), self._parallel_cores.value()

    def get_max_cell(self) -> float:
        return self._max_cell.value()

    def get_min_cell(self) -> float:
        return self._min_cell.value()

    def get_scale_factor(self) -> float:
        from cfmesh_autogui.core.geometry import unit_to_scale
        return unit_to_scale(self._unit_selector.currentText())

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
        self._max_cell.setEnabled(enabled)
        self._min_cell.setEnabled(enabled)
        self._bl_checkbox.setEnabled(enabled)
        self._unit_selector.setEnabled(enabled)
        self._mesher_combo.setEnabled(enabled)
        self._parallel_check.setEnabled(enabled)
        self._parallel_cores.setEnabled(enabled and self._parallel_check.isChecked())

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

    def _on_openmp_mode_changed(self, text: str) -> None:
        is_custom = "Custom" in text
        self._openmp_spin.setEnabled(is_custom)

    def get_openmp_params(self) -> tuple[str, int | None]:
        text = self._openmp_combo.currentText()
        if "Conservativo" in text:
            return ("conservative", None)
        if "Aggressivo" in text:
            return ("aggressive", None)
        if "Custom" in text:
            return ("custom", self._openmp_spin.value())
        return ("balanced", None)

    def _on_mesher_changed(self, text: str) -> None:
        # The GMSH-direct path used to be one entry with a separate
        # "Convert to polyhedral mesh" checkbox — easy to forget to tick
        # (reported live: mesh finished as tet-only, no indication poly
        # was ever an option). Split into two explicit choices instead,
        # matching how FEM vs. CFD actually differ in practice (FEM
        # solvers are tet-native; CFD benefits from polyhedral cells —
        # fewer cells, less numerical diffusion): "Tetrahedral (FEM)"
        # forces poly off, "Polyhedral (CFD)" forces it on. Neither
        # exposes the checkbox anymore, so there's nothing to forget.
        # cfMesh keeps the manual checkbox — poly there is optional
        # either way (its hex-dual is grid-like/regular regardless).
        if "Polyhedral" in text:
            self._poly_check.setVisible(False)
            self._poly_check.setChecked(True)
        elif "Tetrahedral" in text:
            self._poly_check.setVisible(False)
            self._poly_check.setChecked(False)
        else:
            show_poly = "cfMesh" in text
            self._poly_check.setVisible(show_poly)
            if not show_poly:
                self._poly_check.setChecked(False)

    def save_params(self, s):
        s.setValue("params/max_cell", self._max_cell.value())
        s.setValue("params/min_cell", self._min_cell.value())
        s.setValue("params/bl_checked", self._bl_checkbox.isChecked())
        s.setValue("params/unit", self._unit_selector.currentText())
        s.setValue("ui/expert_mode", self._expert_mode)

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
        expert = s.value("ui/expert_mode", None, type=bool)
        if expert is not None:
            self.set_expert_mode(bool(expert))

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
