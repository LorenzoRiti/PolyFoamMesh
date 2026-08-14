from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import trimesh  # ✅ F-017
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QButtonGroup,
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

from cfmesh_autogui.gui.design_tokens import (
    NEUTRAL_100,
    NEUTRAL_200,
    NEUTRAL_300,
    NEUTRAL_800,
    ORANGE_500,
    ORANGE_600,
    RIBBON_TEXT,
)
from cfmesh_autogui.gui.style import (
    COLOR_DANGER,
    COLOR_TEXT_DIM,
    FS_METRIC,
    metric_label,
)

logger = logging.getLogger(__name__)

# Mesher button toggle styles — shared between _add_mesher_btn and _on_mesher_button_clicked
_MESHER_BTN_OFF = (
    f"QPushButton {{"
    f"  border:1px solid {NEUTRAL_200}; border-radius:4px; padding:8px 6px;"
    f"  background:{NEUTRAL_100}; color:{NEUTRAL_800}; font-weight:600; text-align:left;"
    f"}}"
    f"QPushButton:hover {{ background:{NEUTRAL_200}; border-color:{NEUTRAL_300}; }}"
)
_MESHER_BTN_ON = (
    f"QPushButton {{"
    f"  border:2px solid {ORANGE_500}; border-radius:4px; padding:8px 6px;"
    f"  background:{ORANGE_500}; color:{RIBBON_TEXT}; font-weight:700; text-align:left;"
    f"}}"
    f"QPushButton:hover {{ background:{ORANGE_600}; }}"
)


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
    add_box_requested = Signal()
    refinements_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._case_dir = ""
        self._meshing_state = False
        self._suggest_meshes: list[trimesh.Trimesh] = []  # ✅ F-017
        self._feature_min_cell_cache: dict[str, float] = {}
        self._patch_sizes_cache: dict[str, dict] = {}
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

        # Master control (Lorenzo, live testing feedback: too many peer
        # controls with no hierarchy — Max/Min Cell Size, Detail, Adaptive
        # all fighting for attention). One slider drives sizing for the
        # geometry actually loaded (Adaptive sizing, on by default — see
        # _adaptive_sizing_check below); Max/Min Cell Size move to a manual
        # override under the Advanced tab, off by default. This directly
        # closes off the class of bug behind today's cylinder timeout too:
        # that happened because the OLD peer-level Max/Min fields carried
        # fixed defaults (5cm/1cm) that don't scale to whatever geometry is
        # loaded — a single geometry-aware slider can't drift out of scale
        # the same way.
        # Mesh Fineness is the SINGLE sizing control: a cell-count scale
        # from ~10K to ~20M cells (log). It governs everything — the GMSH
        # max_cells_target, and the derived max/min cell sizes used by
        # cfMesh. Manual numeric override no longer exists as the primary
        # input (see _slider-controlled sizing under the Advanced tab).
        detail_group = QGroupBox("Mesh Fineness")
        detail_layout = QVBoxLayout(detail_group)
        detail_caption = QLabel(
            "Dimensioni della mesh finale (⚠ celle totali indicativo):"
        )
        detail_caption.setWordWrap(True)
        detail_caption.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        detail_layout.addWidget(detail_caption)
        self._detail_slider = QSlider(Qt.Horizontal)
        self._detail_slider.setRange(0, 20)
        self._detail_slider.setValue(10)
        self._detail_slider.setTickPosition(QSlider.TicksBelow)
        self._detail_slider.setTickInterval(2)
        self._detail_slider.setToolTip(
            "Controlla la dimensione della mesh finale da ~10K a ~20M celle.\n"
            "Questo unico controllo guida cell size, target celle e qualità."
        )
        self._detail_label = QLabel("")
        self._detail_label.setAlignment(Qt.AlignCenter)
        self._detail_label.setStyleSheet(f"font-weight: bold; color: {ORANGE_500}; font-size: 14px;")
        self._detail_slider.valueChanged.connect(self._on_detail_changed)
        detail_layout.addWidget(self._detail_slider)
        detail_layout.addWidget(self._detail_label)

        # Real, visible toggle (previously hidden and unwired — the code
        # always ran the adaptive path regardless of this checkbox's state,
        # so there was no way to actually turn it off; see
        # _on_adaptive_sizing_toggled and MainWindow._start_gmsh_volume_worker
        # for the wiring that now makes this genuinely switch behaviour).
        # ON (default, recommended): mesh sized finer near small curves,
        # narrow gaps, and curved surfaces (a bounded curvature-adaptive
        # field, capped by the Mesh Fineness slider — never uses GMSH's own
        # unbounded curvature engine, which is what used to refine tiny,
        # CFD-irrelevant fillets everywhere; see gmsh_wrapper._configure_
        # adaptive_sizing). OFF: literal, uniform cell size from the slider
        # (predictable, no local refinement) — the Advanced tab's Cell
        # Sizes become editable in this mode.
        self._adaptive_sizing_check = QCheckBox(
            "Rifinitura automatica (curvatura, spigoli, strettoie) — consigliato"
        )
        self._adaptive_sizing_check.setChecked(True)
        self._adaptive_sizing_check.setToolTip(
            "ON: infittisce automaticamente vicino a curve piccole, "
            "strettoie e superfici curve, entro i limiti dello slider Mesh "
            "Fineness.\nOFF: dimensione di cella uniforme e letterale "
            "(nessuna rifinitura locale) — 'Cell Sizes' nella scheda "
            "Advanced diventa modificabile."
        )
        self._adaptive_sizing_check.toggled.connect(self._on_adaptive_sizing_toggled)
        detail_layout.addWidget(self._adaptive_sizing_check)
        mesh_layout.addWidget(detail_group)

        self._bbox_dim = 1.0
        self._bbox_extents = (1.0, 1.0, 1.0)
        self._geometry_volume = 0.0
        self._geometry_estimate: tuple[int, int, int] | None = None

        # Max/Min Cell Size, the cell estimate label, and Auto-Suggest all
        # moved to the Advanced tab (manual override section) — see the
        # Mesh Fineness slider above for why. Constructed further down,
        # inside adv_layout, so the widgets still exist under the same
        # self._max_cell / self._min_cell / self._btn_suggest names
        # everything else in this file already refers to.

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
        # Three explicit mesher choices instead of a single dropdown. Each is
        # a checkable toggle in an exclusive group, and get_mesher_type()
        # returns the SAME pipeline keys as before ("cfmesh", "gmsh_direct_poly",
        # "gmsh_direct"), so the meshing engines are untouched.
        self._mesher_buttons = QButtonGroup(self)
        self._mesher_buttons.setExclusive(True)
        self._mesher_keys: list[tuple[QPushButton, str]] = []

        def _add_mesher_btn(text: str, key: str, tip: str, checked: bool = False) -> QPushButton:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setStyleSheet(_MESHER_BTN_ON if checked else _MESHER_BTN_OFF)
            mesher_layout.addWidget(btn)
            self._mesher_buttons.addButton(btn)
            self._mesher_keys.append((btn, key))
            # Capture key and btn by default-arg so each closure keeps its own value.
            btn.clicked.connect(
                lambda _checked, b=btn: self._on_mesher_button_clicked(b)
            )
            if checked:
                btn.setChecked(True)
            return btn

        _add_mesher_btn(
            "Cartesian cfMesh  (richiede WSL2)",
            "cfmesh",
            "cartesianMesh via WSL2/OpenFOAM — hex-dominant, la qualit"
            "\u00e0 pi\u00f9 alta. RICHIEDE WSL2/OpenFOAM installato.\n"
            "Opzionale: abilita 'Convert to polyhedral mesh' (Advanced) per\n"
            "convertire l'hex-dominant in poliedrico (polyDualMesh).\n"
            "Il dual di una mesh cartesiana resta molto regolare/a griglia\n"
            "(celle di bordo ~99% quadrilateri).",
            checked=True,
        )
        _add_mesher_btn(
            "Polymesh  (poly, no-WSL)",
            "gmsh_direct_poly",
            "Mesh poliedrica del nostro mesher Polymesh: barycentric-dual\n"
            "rebuild — 100% di copertura poliedrica, conteggio celle ~5.5x\n"
            "inferiore (stile STAR-CCM+). Ideale per CFD. Nessun WSL\n"
            "richiesto. Funziona meglio con input STEP/CAD reale.",
            checked=False,
        )
        _add_mesher_btn(
            "FEM Tetra  (GMSH, no-WSL)",
            "gmsh_direct",
            "Mesh tetraedrica GMSH pura, nessun WSL richiesto.\n"
            "Per solver FEM nativi tet. Nessuna conversione poliedrica.",
        )
        mesh_layout.addWidget(mesher_group)
        self._bl_group = bl_group
        self._mesher_group = mesher_group

        self._refine_group = QGroupBox("Local Refinement")
        refine_layout = QVBoxLayout(self._refine_group)
        # Superseded by "Rifinitura automatica" (Mesh tab, next to the Mesh
        # Fineness slider): that toggle now drives a single, vectorized,
        # tested passage-width sizing field wired directly into GMSH's own
        # sizing callback (see gmsh_wrapper._sample_passage_thickness_field)
        # — the SAME "detect narrow passages, refine there" job this
        # checkbox did, via core/throat_detector.py's own SEPARATE,
        # unbatched (one Python-level ray-cast per sample point, not the
        # 512-per-call batched approach the other two thickness samplers in
        # this codebase already used) reimplementation. Two controls both
        # claiming to do automatic narrow-passage refinement, with no way
        # for a user to tell which one actually ran, was the confusion
        # reported directly ("ci sono 2 tasti... ne voglio uno che
        # funziona"). Kept as a hidden, off-by-default attribute so
        # get_auto_refine_enabled() and the (untouched) throat_detector
        # code path keep working if re-enabled — not deleted outright,
        # since throat_detector's explicit refinement ZONES (shown as
        # spheres in the viewer) are a different, still possibly useful
        # capability from a silent sizing field; it just should not be a
        # second, competing "automatic" switch next to the real one.
        self._auto_refine_check = QCheckBox("Auto-refine narrow sections")
        self._auto_refine_check.setChecked(False)
        self._auto_refine_check.setToolTip(
            "Superseded by 'Rifinitura automatica' nella tab Mesh (piu' "
            "veloce, stesso obiettivo)."
        )
        self._auto_refine_check.setVisible(False)
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
        self._add_box_btn = QPushButton("▣ Box 3D")
        self._add_box_btn.setMaximumWidth(90)
        self._add_box_btn.setToolTip(
            "Add a 3D refinement box edited in the viewer with the box gizmo: "
            "drag the handles on its faces to size it. You only choose the "
            "level (1 = base, 2 = half cells, ...)."
        )
        self._add_box_btn.clicked.connect(self.add_box_requested.emit)
        refine_btn_row.addWidget(self._add_box_btn)
        refine_btn_row.addWidget(self._add_refine_btn)
        refine_btn_row.addWidget(self._remove_refine_btn)
        refine_btn_row.addStretch()
        refine_layout.addLayout(refine_btn_row)

        # Per-selected-zone refinement LEVEL (1..6). Applies to both spheres
        # and boxes; for boxes it is THE user choice (size comes from it).
        level_row = QHBoxLayout()
        level_row.addWidget(QLabel("Livello:"))
        self._refine_level_spin = QSpinBox()
        self._refine_level_spin.setRange(1, 6)
        self._refine_level_spin.setValue(2)
        self._refine_level_spin.setToolTip(
            "Livello di raffinamento della zona selezionata:\n"
            "  1 = dimensione base (nessun raffinamento extra)\n"
            "  2 = metà celle\n"
            "  3 = un quarto delle celle\n"
            "  ... ogni livello dimezza la dimensione precedente"
        )
        self._refine_level_spin.valueChanged.connect(self._on_refine_level_changed)
        self._refine_level_spin.setEnabled(False)
        level_row.addWidget(self._refine_level_spin)
        self._refine_level_hint = QLabel(
            "1=base 2=metà 3=¼ 4=⅛ …"
        )
        self._refine_level_hint.setStyleSheet("color: gray; font-size: 10px;")
        level_row.addWidget(self._refine_level_hint)
        level_row.addStretch()
        refine_layout.addLayout(level_row)

        self._refine_list.itemSelectionChanged.connect(
            self._on_refine_selection_changed
        )
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
        # Sync the "Convert to polyhedral mesh" checkbox to the mesher
        # button selected at startup (default cfMesh -> checkbox visible).
        self._on_mesher_changed(self.get_mesher_type())

        # Cell sizes are derived from the Mesh Fineness slider (single
        # control). They are shown here read-only so the user always sees
        # what sizes the slider set — never edited by hand.
        mesh_group = QGroupBox("Cell Sizes (derivati dallo slider)")
        mesh_form = QFormLayout(mesh_group)
        self._max_cell = QDoubleSpinBox()
        self._max_cell.setRange(1e-10, 100.0)
        self._max_cell.setValue(0.05)
        self._max_cell.setSingleStep(0.01)
        self._max_cell.setDecimals(10)
        self._max_cell.setSuffix(" m")
        self._max_cell.setReadOnly(True)
        self._max_cell.setToolTip("Derivato automaticamente dallo slider Mesh Fineness.")
        mesh_form.addRow("Max Cell Size:", self._max_cell)

        self._min_cell = QDoubleSpinBox()
        self._min_cell.setRange(1e-10, 10.0)
        self._min_cell.setValue(0.01)
        self._min_cell.setSingleStep(0.001)
        self._min_cell.setDecimals(10)
        self._min_cell.setSuffix(" m")
        self._min_cell.setReadOnly(True)
        self._min_cell.setToolTip("Derivato automaticamente dallo slider Mesh Fineness.")
        mesh_form.addRow("Min Cell Size:", self._min_cell)

        self._cell_est_label = QLabel("Cells: --")
        self._cell_est_label.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        mesh_form.addRow(self._cell_est_label)

        self._prev_cell_params["max_cell"] = self._max_cell.value()
        self._prev_cell_params["min_cell"] = self._min_cell.value()
        self._max_cell.valueChanged.connect(self._on_max_cell_changed)
        self._min_cell.valueChanged.connect(self._on_min_cell_changed)
        adv_layout.addWidget(mesh_group)

        # Auto-Suggest predates the Mesh Fineness slider and is superseded
        # by it; kept as a hidden attribute so any remaining reference
        # keeps working. The Adaptive toggle itself now lives on the Mesh
        # tab, next to the slider it governs (see mesh_layout above) — it
        # is a real, wired control, not superseded by anything.
        btn_suggest = QPushButton("Auto-Suggest Cell Sizes")
        btn_suggest.clicked.connect(self._on_suggest_sizes)
        self._btn_suggest = btn_suggest
        btn_suggest.setVisible(False)
        adv_layout.addWidget(btn_suggest)

        max_cells_row = QHBoxLayout()
        max_cells_row.addWidget(QLabel("Max cells target:"))
        self._max_cells_target = QSpinBox()
        self._max_cells_target.setRange(0, 50_000_000)
        self._max_cells_target.setSingleStep(100_000)
        # 0 = Auto: the Mesh Fineness slider's detail level already picks a
        # sensible default cell budget. A nonzero target here is only meant
        # for a deliberate "I want MORE resolution than this detail level's
        # own ceiling" override — confirmed live that leaving a large fixed
        # value here by default fights the slider (pulled a 3m part's bulk
        # mesh far finer than "medium" should give, and stalled).
        self._max_cells_target.setValue(0)
        self._max_cells_target.setSpecialValueText("Auto (hardware-based)")
        self._max_cells_target.setToolTip(
            "Superseded by the Mesh Fineness slider (hidden)."
        )
        max_cells_row.addWidget(self._max_cells_target)
        adv_layout.addLayout(max_cells_row)
        # Whole "Max cells target" row is superseded by the slider.
        max_cells_target_label = max_cells_row.itemAt(0).widget()
        if max_cells_target_label is not None:
            max_cells_target_label.setVisible(False)
        self._max_cells_target.setVisible(False)
        # Initialize the Mesh Fineness label + derived cell sizes from the
        # slider's default value (now that _max_cell/_min_cell exist).
        self._on_detail_changed(self._detail_slider.value())
        # Initialize Cell Sizes read-only/editable state from the Adaptive
        # toggle's default (now that _max_cell/_min_cell exist too).
        self._on_adaptive_sizing_toggled(self._adaptive_sizing_check.isChecked())

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

        # Advanced mode ON by default: the mesher choice buttons, boundary
        # layers and the Advanced tab are all visible from the start. Hiding
        # them behind "simple mode" confused users (e.g. the 3 mesher buttons
        # were invisible), so the simple mode is disabled by default.
        self._expert_mode = True
        self.set_expert_mode(True)

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

    def _on_adaptive_sizing_toggled(self, checked: bool):
        """Adaptive ON: Max/Min Cell Size are read-only DISPLAY of what the
        Mesh Fineness slider derived (they're not what actually drives
        sizing in this mode — the adaptive fields are, see
        gmsh_wrapper._configure_adaptive_sizing). Adaptive OFF: they become
        the real, editable input — literal uniform sizing, no curvature-
        driven local refinement, so what's typed here is exactly what gets
        meshed."""
        self._max_cell.setReadOnly(checked)
        self._min_cell.setReadOnly(checked)
        self._max_cell.setToolTip(
            "Derivato automaticamente dallo slider Mesh Fineness."
            if checked else
            "Dimensione di cella massima (modalità manuale — rifinitura "
            "automatica disattivata)."
        )
        self._min_cell.setToolTip(
            "Derivato automaticamente dallo slider Mesh Fineness."
            if checked else
            "Dimensione di cella minima (modalità manuale — rifinitura "
            "automatica disattivata)."
        )

    # Kinematic viscosity at 20 °C, m²/s.
    _FLUID_NU = {"Air (20°C)": 1.5e-5, "Water (20°C)": 1.0e-6}

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
        extent = max(dx, dy, dz)
        # (0, 0, 0) is the reset/no-geometry state, not a micron-sized part.
        self._bbox_dim = extent if extent > 0 else 1.0
        self._bbox_extents = (
            (dx, dy, dz) if extent > 0 else (1.0, 1.0, 1.0)
        )
        if hasattr(self, "_detail_slider"):
            self._on_detail_changed(self._detail_slider.value())

    def set_suggest_meshes(self, meshes: list) -> None:
        self._suggest_meshes = list(meshes) if meshes else []
        self._geometry_volume = self._safe_geometry_volume()
        # Invalidate the feature-aware minCellSize / per-patch-size caches
        # (see _derive_cell_sizes_from_target / _geometry_cell_estimate)
        # -- new geometry, old ray-cast results no longer apply.
        self._feature_min_cell_cache = {}
        self._patch_sizes_cache = {}
        if hasattr(self, "_detail_slider"):
            self._on_detail_changed(self._detail_slider.value())

    def set_cell_estimate(self, text: str):
        self._cell_est_label.setText(text)

    def set_geometry_cell_estimate(self, low: int, nominal: int, high: int):
        """Set the pre-mesh estimate computed by the full pipeline.

        The main window knows patch-specific refinement sizes, so its
        estimate is more representative than the panel-only volume fallback.
        Keep it as the source for the slider label too.
        """
        self._geometry_estimate = (int(low), int(nominal), int(high))
        if hasattr(self, "_detail_slider"):
            self._on_detail_changed(self._detail_slider.value())

    def set_real_cell_count(self, count: int):
        self._geometry_estimate = (int(count), int(count), int(count))
        self._cell_est_label.setText(f"Cells: {count:,}")
        if hasattr(self, "_detail_slider"):
            self._on_detail_changed(self._detail_slider.value())

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
            # Repair a stale/clamped pair after a geometry change or an old
            # saved state that used the previous 1e-4 spinbox floor.
            self._derive_cell_sizes_from_target(self.get_target_cells())
            max_val = self._max_cell.value()
            min_val = self._min_cell.value()
        if max_val <= min_val:
            QMessageBox.warning(
                self, "Invalid Mesh Parameters",
                f"Max Cell Size ({max_val:.6g} m) must be strictly greater than "
                f"Min Cell Size ({min_val:.6g} m).\n\n"
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
            # poly path (barycentric dual): checked -> BL on every boundary
            # patch; unchecked (default) -> wall-typed / wall-named patches
            # only (G1 fix, FASE 1)
            "applyToAll": self._bl_apply_all.isChecked(),
        }

    def get_bl_apply_all(self) -> bool:
        return self._bl_apply_all.isChecked()

    def get_poly_conversion(self) -> bool:
        return self._poly_check.isChecked()

    # Cell-count scale for the Mesh Fineness slider (log: 10K .. 20M cells).
    _CELL_TARGET_MIN = 10_000
    _CELL_TARGET_MAX = 20_000_000
    _CELL_TARGET_BUCKETS = 20

    _DETAIL_LABELS = ["Molto Grossolana", "Grossolana", "Media", "Fine", "Molto Fine"]
    _DETAIL_MAP = {0: "very_coarse", 1: "coarse", 2: "medium", 3: "fine", 4: "very_fine"}

    def _slider_to_cells(self, value: int) -> int:
        """Map slider position 0..20 to a target cell count (log 10K..20M)."""
        t = max(0, min(self._CELL_TARGET_BUCKETS, int(value))) / self._CELL_TARGET_BUCKETS
        import math
        cells = self._CELL_TARGET_MIN * (self._CELL_TARGET_MAX / self._CELL_TARGET_MIN) ** t
        return int(round(cells))

    def _cells_label(self, cells: int) -> str:
        if cells >= 1_000_000:
            return f"{cells / 1e6:.1f}M"
        if cells >= 1_000:
            return f"{round(cells / 1e3)}K"
        return str(cells)

    def _on_detail_changed(self, value: int):
        """Slider now drives the target cell count AND the derived cell sizes."""
        cells = self._slider_to_cells(value)
        detail = self.get_detail_level()
        self._derive_cell_sizes_from_target(cells)
        estimate = self._geometry_estimate or self._geometry_cell_estimate()
        name = self._DETAIL_LABELS[self._DETAIL_INDEX[detail]]
        if estimate is None:
            self._detail_label.setText(
                f"{name} · cap ~{self._cells_label(cells)} celle"
            )
            if hasattr(self, "_cell_est_label"):
                self._cell_est_label.setText(
                    "Stima geometrica: carica una geometria valida"
                )
        else:
            lo, nominal, hi = estimate
            self._detail_label.setText(
                f"{name} · cap ~{self._cells_label(cells)} · "
                f"stima ~{self._cells_label(nominal)} celle"
            )
            if hasattr(self, "_cell_est_label"):
                self._cell_est_label.setText(
                    f"Stima geometrica: ~{nominal:,} celle "
                    f"(range {lo:,}-{hi:,})"
                )

    _DETAIL_INDEX = {v: k for k, v in _DETAIL_MAP.items()}

    def _derive_cell_sizes_from_target(self, cells: int) -> None:
        """Set _max_cell/_min_cell from the target cell count + known bbox.

        cell size ~ (bbox_volume / cells)^(1/3), max a bit above, min a
        bit below. bbox defaults to 1m when geometry hasn't been loaded.

        minCellSize is then tightened (never loosened) toward the
        geometry's own local passage/feature thickness when a geometry is
        loaded (``suggest_cell_sizes`` — ray-cast based, see
        ``core.geometry.sample_thickness_field``), instead of staying a
        fixed 0.5x multiple of the uniform bulk size. A fixed multiple
        never adapts to how narrow an actual throat or fin is: GMSH's own
        mesher recomputes a rich local sizing FIELD internally regardless
        of what min/max bounds this slider sends it, so a generic minCell
        barely matters there, but cfMesh's cartesianMesh has exactly ONE
        knob for local refinement (minCellSize — it only refines cells
        already larger than the estimated feature size), so if that knob
        never reflects the true local thickness, cartesianMesh has
        nothing meaningful to refine toward and the slider only ever
        changes overall coarseness uniformly, never local detail.
        """
        if not hasattr(self, "_max_cell") or not hasattr(self, "_min_cell"):
            return
        bbox = getattr(self, "_bbox_dim", 1.0) or 1.0
        cell_size = (bbox ** 3 / max(cells, 1)) ** (1.0 / 3.0)
        floor = 1e-10
        s_max = max(cell_size * 1.6, floor * 2.0)
        # Keep a strict gap after QDoubleSpinBox precision/clamping.
        s_min = max(min(cell_size * 0.5, s_max * 0.45), floor)

        meshes = getattr(self, "_suggest_meshes", None)
        if meshes:
            # Cached per detail level: the fine-grained cell-count slider
            # fires this on every drag tick (0..20 positions), but only 5
            # distinct detail buckets ever feed suggest_cell_sizes below —
            # re-running its ray-casting on every tick would make dragging
            # the slider visibly laggy for no benefit (the feature-derived
            # minimum doesn't change between ticks in the same bucket).
            detail = self.get_detail_level()
            cache = getattr(self, "_feature_min_cell_cache", None)
            if cache is None:
                cache = self._feature_min_cell_cache = {}
            if detail in cache:
                feature_min = cache[detail]
            else:
                feature_min = 0.0
                try:
                    from cfmesh_autogui.core.geometry import suggest_cell_sizes
                    _, feature_min = suggest_cell_sizes(
                        meshes, detail=detail, bbox_max_dim=bbox,
                    )
                except Exception:
                    logger.debug(
                        "Feature-aware minCellSize skipped (slider update)",
                        exc_info=True,
                    )
                cache[detail] = feature_min
            if feature_min > floor:
                s_min = min(s_min, feature_min)

        if s_min >= s_max:
            s_min = s_max * 0.4
        self._max_cell.blockSignals(True)
        self._max_cell.setValue(s_max)
        self._max_cell.blockSignals(False)
        self._min_cell.blockSignals(True)
        self._min_cell.setValue(s_min)
        self._min_cell.blockSignals(False)

    def _safe_geometry_volume(self) -> float:
        """Return a trustworthy solid volume, or zero when unavailable."""
        if not getattr(self, "_suggest_meshes", None):
            return 0.0
        try:
            from cfmesh_autogui.core.geometry import compute_volume

            volume = float(compute_volume(self._suggest_meshes))
        except Exception as exc:
            logger.debug("Geometry volume estimate skipped: %s", exc)
            return 0.0
        dx, dy, dz = self._bbox_extents
        bbox_volume = max(dx * dy * dz, 0.0)
        # A summed patch volume outside the bbox volume is a sign that the
        # tessellation is open/duplicated; do not present that as precision.
        if volume <= 0.0 or bbox_volume <= 0.0 or volume > bbox_volume * 1.05:
            return 0.0
        return volume

    def _geometry_cell_estimate(self) -> tuple[int, int, int] | None:
        """Estimate cells from actual solid volume and derived cell sizes.

        Uses the geometry-aware two-zone model (near-wall shell + bulk
        core, ``estimate_cell_count_geometric``) instead of dividing the
        WHOLE volume by one average cell size — the blind average is
        exactly what the codebase's own history flags as producing >10x
        estimate errors (a small, finely-sized patch occupies a tiny
        fraction of the VOLUME but a huge fraction of the CELLS). This is
        the same model already used right before the real meshing run
        starts (main_window.py's pre-flight estimate) — the live slider
        label was the one place still using the older, blind formula.

        An earlier attempt at this wiring found and had to revert a real
        bug in estimate_cell_count_geometric first (a patch coarser than
        the core cell could make its "shell volume" clamp to the ENTIRE
        domain, collapsing the estimate to the 100-cell floor) — now
        fixed at the source (patches not finer than core_cell are
        skipped, matching what compute_patch_cell_sizes's own docstring
        already claimed but never enforced), so this wiring is safe.
        """
        volume = float(getattr(self, "_geometry_volume", 0.0))
        meshes = getattr(self, "_suggest_meshes", None)
        if volume <= 0.0 or not hasattr(self, "_max_cell") or not meshes:
            return None
        try:
            from cfmesh_autogui.core.geometry import (
                compute_patch_cell_sizes, estimate_cell_count_geometric,
            )

            detail = self.get_detail_level()
            cache = getattr(self, "_patch_sizes_cache", None)
            if cache is None:
                cache = self._patch_sizes_cache = {}
            if detail not in cache:
                patch_sizes, _bc_size, _bc_thick = compute_patch_cell_sizes(
                    meshes, detail=detail,
                )
                cache[detail] = patch_sizes
            return estimate_cell_count_geometric(
                meshes, volume, self._max_cell.value(), cache[detail],
            )
        except Exception as exc:
            logger.debug("Geometry cell estimate skipped: %s", exc)
            return None

    def get_detail_level(self) -> str:
        """5-level detail derived from the 0..20 cell-count slider."""
        value = self._detail_slider.value() if hasattr(self, "_detail_slider") else 10
        bucket = int(round(value / 5.0))
        bucket = max(0, min(4, bucket))
        return self._DETAIL_MAP.get(bucket, "medium")

    def get_target_cells(self) -> int:
        """Target cell count driven by the Mesh Fineness slider."""
        if not hasattr(self, "_detail_slider"):
            return 500_000
        return self._slider_to_cells(self._detail_slider.value())

    def set_current_tab(self, index: int) -> None:
        if hasattr(self, "_tabs") and 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)

    def get_mesher_type(self) -> str:
        for btn, key in getattr(self, "_mesher_keys", []):
            if btn.isChecked():
                return key
        return "cfmesh"

    def get_adaptive_sizing_enabled(self) -> bool:
        return getattr(self, "_adaptive_sizing_check", None) is not None and self._adaptive_sizing_check.isChecked()

    def get_max_cells_target(self) -> int:
        """Target cell count (polymesh) — driven by the Mesh Fineness slider."""
        return self.get_target_cells()

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

    # --- 3D refinement boxes -------------------------------------------------
    def add_refinement_box(self, box: dict) -> None:
        """Add a box entry to the manual-refinement list."""
        from cfmesh_autogui.core.refinement_boxes import label_for
        box = dict(box)
        box.setdefault("type", "box")
        item = QListWidgetItem(label_for(box))
        item.setData(Qt.UserRole, box)
        item.setToolTip(
            f"Level {int(box.get('level', 2))} -> cell size "
            f"{box.get('cell_size', 0):.5g} m. Trascina le frecce nel viewer "
            "per dimensionarlo."
        )
        self._refine_list.addItem(item)
        self._refresh_refine_placeholder()
        self._refine_list.setCurrentItem(item)
        self._on_refine_selection_changed()

    def set_refinement_boxes(self, boxes: list) -> None:
        """Replace the stored box entries after a viewer drag (same order)."""
        from cfmesh_autogui.core.refinement_boxes import label_for
        box_items = [i for i in range(self._refine_list.count())
                     if isinstance(self._refine_list.item(i).data(Qt.UserRole), dict)
                     and self._refine_list.item(i).data(Qt.UserRole).get("type") == "box"]
        if len(box_items) < len(boxes):
            return
        for idx, box in zip(box_items, boxes):
            item = self._refine_list.item(idx)
            item.setData(Qt.UserRole, dict(box))
            item.setText(label_for(box))
            item.setToolTip(
                f"Level {int(box.get('level', 2))} -> cell size "
                f"{box.get('cell_size', 0):.5g} m"
            )
        # keep the level spin in sync if the selected item was edited
        self._on_refine_selection_changed()

    def _on_refine_selection_changed(self) -> None:
        item = self._refine_list.currentItem()
        if item is None:
            self._refine_level_spin.setEnabled(False)
            return
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict):
            self._refine_level_spin.setEnabled(False)
            return
        lvl = int(data.get("level", 2))
        self._refine_level_spin.blockSignals(True)
        self._refine_level_spin.setValue(lvl)
        self._refine_level_spin.blockSignals(False)
        self._refine_level_spin.setEnabled(True)

    def _on_refine_level_changed(self, level: int) -> None:
        item = self._refine_list.currentItem()
        if item is None:
            return
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict):
            return
        base = float(data.get("base_size") or 0.01)
        from cfmesh_autogui.core.refinement_boxes import (
            apply_level, label_for, box_cell_size,
        )
        data["level"] = int(level)
        data["cell_size"] = box_cell_size(int(level), base)
        item.setData(Qt.UserRole, data)
        item.setText(label_for(data))
        item.setToolTip(
            f"Level {int(level)} -> cell size {data['cell_size']:.5g} m"
        )
        # Notify the caller (main window) so the viewer label can refresh.
        self.refinements_changed.emit(self.get_manual_refinements())

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
        for btn, _key in getattr(self, "_mesher_keys", []):
            btn.setEnabled(enabled)
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

    def _on_mesher_button_clicked(self, clicked_btn: QPushButton) -> None:
        """Refresh visual state of all mesher buttons and dispatch the change."""
        for b, _k in self._mesher_keys:
            b.setStyleSheet(_MESHER_BTN_ON if b is clicked_btn else _MESHER_BTN_OFF)
        self._on_mesher_changed(self.get_mesher_type())

    def _on_mesher_changed(self, key: str) -> None:
        # "Polymesh" (gmsh_direct_poly) forces the poly conversion ON and
        # hides the checkbox (nothing to forget); "FEM Tetra" (gmsh_direct)
        # forces it OFF; "Cartesian cfMesh" keeps the manual checkbox —
        # poly there is optional either way (its hex-dual is grid-like/
        # regular regardless). This mirrors the previous dropdown behaviour
        # exactly, keyed by the pipeline values get_mesher_type() returns.
        if key == "gmsh_direct_poly":
            self._poly_check.setVisible(False)
            self._poly_check.setChecked(True)
        elif key == "gmsh_direct":
            self._poly_check.setVisible(False)
            self._poly_check.setChecked(False)
        else:  # cfmesh
            self._poly_check.setVisible(True)

    def save_params(self, s):
        s.setValue("params/detail_slider", self._detail_slider.value())
        s.setValue("params/bl_checked", self._bl_checkbox.isChecked())
        s.setValue("params/unit", self._unit_selector.currentText())
        s.setValue("ui/expert_mode", self._expert_mode)

    def restore_params(self, s):
        slider = s.value("params/detail_slider", None)
        if slider is not None:
            self._detail_slider.setValue(int(slider))
            self._on_detail_changed(self._detail_slider.value())
        bl = s.value("params/bl_checked", None, type=bool)
        if bl is not None:
            self._bl_checkbox.setChecked(bool(bl))
        unit = s.value("params/unit", None)
        if unit is not None:
            self._unit_selector.setCurrentText(unit)
        expert = s.value("ui/expert_mode", None, type=bool)
        if expert is not None:
            self.set_expert_mode(bool(expert))
