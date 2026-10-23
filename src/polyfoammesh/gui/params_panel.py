from __future__ import annotations

import logging
import math
import os
import re
import sys

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

from polyfoammesh.core.bl_config import BLConfig, BLConfigError
from polyfoammesh.core.bl_presets import BLPresetStore
from polyfoammesh.core.mesh_config import MeshConfig, target_cells_for_slider
from polyfoammesh.core.mesher_registry import (
    DEFAULT_MESHER_KEY,
    ENGINE_KEYS,
    MODE_BY_KEY,
    normalize_mesher_key,
)
from polyfoammesh.core.mesh_presets import (
    MeshPresetStore,
    list_mesh_presets,
    resolve_mesh_preset,
)
from polyfoammesh.gui.design_tokens import (
    NEUTRAL_100,
    NEUTRAL_200,
    NEUTRAL_300,
    NEUTRAL_800,
    ORANGE_500,
    ORANGE_600,
    RIBBON_TEXT,
)
from polyfoammesh.gui.style import (
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


def _platform_mesher_wording() -> dict[str, str]:
    """Platform-conditional WSL/OpenFOAM user wording.

    On Windows OpenFOAM runs in WSL2; on Linux it is native and resolved
    via PATH. The Windows strings are kept byte-identical to the
    historical WSL wording (the full fast suite asserts on them).
    """
    on_win = sys.platform == "win32"
    return {
        "of_req": "requires WSL2" if on_win else "requires OpenFOAM on PATH",
        "of_via": "WSL2/OpenFOAM" if on_win else "OpenFOAM",
        "no_of_label": "no-WSL" if on_win else "no OpenFOAM",
        "no_of_sentence": "No WSL required." if on_win else "No OpenFOAM required.",
        "no_of_sentence_l": "no WSL required." if on_win else "no OpenFOAM required.",
        "mem_ctx": (
            "inside WSL2 (which caps its own memory well below the host's)"
            if on_win else "on this machine"
        ),
    }


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
    scale_changed = Signal()
    param_changed = Signal(str, object)
    suggestion_completed = Signal(str)
    pick_refinement_requested = Signal()
    add_box_requested = Signal()
    refinements_changed = Signal(object)
    #: Index of the selected manual refinement BOX (0-based, boxes only), or
    #: -1 when the selection is a sphere/nothing. Lets the main window
    #: highlight the matching box in the 3D viewer.
    refinement_selection_changed = Signal(int)

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
        self._scale_apply = QPushButton("Apply Scale")
        self._scale_apply.setToolTip(
            "Apply the scale factor to the loaded geometry (and to every "
            "subsequent mesh: the meshers receive the scaled geometry, so "
            "the cell sizes stay in metres)."
        )
        self._scale_apply.clicked.connect(self.scale_changed.emit)
        form_geom.addRow(self._scale_apply)
        cad_layout.addLayout(form_geom)

        geom_layout.addWidget(cad_group)

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
            "Final mesh size (⚠ total cell count is approximate):"
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
            "Controls the final mesh size from ~10K to ~20M cells.\n"
            "This single control drives cell size, cell target and quality."
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
            "Automatic refinement (curvature, edges, narrow gaps) — recommended"
        )
        self._adaptive_sizing_check.setChecked(True)
        self._adaptive_sizing_check.setToolTip(
            "ON: automatically refines near small curves, narrow gaps and "
            "curved surfaces, within the Mesh Fineness slider limits.\n"
            "OFF: uniform, literal cell size (no local refinement) — "
            "'Cell Sizes' on the Advanced tab becomes editable."
        )
        self._adaptive_sizing_check.toggled.connect(self._on_adaptive_sizing_toggled)
        detail_layout.addWidget(self._adaptive_sizing_check)

        # Surface resolution and mesh-cache reuse are AUTOMATIC now (user
        # request: no knobs for things the app should just do): the mesher
        # always uses the historical "Medium" curvature resolution and
        # always reuses a cached tet mesh when the sizing inputs are
        # unchanged. The engine parameters still exist
        # (gmsh_wrapper surface_resolution / mesh_cache), the UI does not
        # expose them. See get_surface_resolution / get_reuse_cached_mesh.
        mesh_layout.addWidget(detail_group)
        self._build_mesh_preset_group(mesh_layout)

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
        self._bl_n_layers.setValue(BLConfig().n_layers)
        bl_form.addRow("nLayers:", self._bl_n_layers)
        self._bl_thick = QDoubleSpinBox()
        # First layer as a fraction of max cell size. y+~1 gives fractions of
        # order 1e-4, far below the old 0.001 floor.
        self._bl_thick.setRange(1e-6, 1.0)
        # Decimals BEFORE the value: with the old order Qt rounded 0.005 to
        # 0.01 and that is the default users actually got. W7 preserves the
        # measured effective default (0.01) from the single BLConfig source.
        self._bl_thick.setDecimals(6)
        self._bl_thick.setValue(BLConfig().first_layer_fraction)
        self._bl_thick.setToolTip(
            "First-layer thickness as a fraction of the max cell size. "
            "Use 'Compute layers from physics (y+)' to derive it."
        )
        bl_form.addRow("First Layer (× cell):", self._bl_thick)
        self._bl_exp = QDoubleSpinBox()
        self._bl_exp.setRange(1.0, 2.0)
        self._bl_exp.setValue(BLConfig().growth_rate)
        self._bl_exp.setSingleStep(0.1)
        bl_form.addRow("Expansion Ratio:", self._bl_exp)
        self._bl_apply_all = QCheckBox("Apply BL to all patches")
        bl_form.addRow(self._bl_apply_all)

        # 2026-10-06: the concave closure (per-vertex local termination,
        # H4/decoupled_vertex) is no longer a checkbox — the worker now
        # retries it AUTOMATICALLY when the standard robust path fails
        # (standard -> concave closure -> halved first layer), so users get
        # it exactly when it helps (groove1/slot1-style concave junctions)
        # without degrading the cases where the standard path already wins.

        # 2026-10-06: the robust-BL bundle (sliver-edge filter 0.9 + budget
        # termination, the benchmark combo: poly 9/10 vs snappy 6/10) used to
        # be a checkbox. It is now ALWAYS ON whenever the BL is enabled - it
        # measured better everywhere and a checkbox the user must know to
        # tick is exactly the kind of knob this app avoids (user request:
        # "basta spuntare enable boundary layers e lui si inserisce"). The
        # concave closure is a DIFFERENT mechanism (per-vertex exclusions)
        # and stays automatic in the worker fallback chain.

        # Physical inputs: the layer parameters above are derived from these
        # rather than guessed, which is how commercial meshers do it.
        self._bl_velocity = QDoubleSpinBox()
        self._bl_velocity.setRange(0.001, 1000.0)
        self._bl_velocity.setValue(BLConfig().velocity)
        self._bl_velocity.setDecimals(3)
        self._bl_velocity.setSuffix(" m/s")
        self._bl_velocity.setToolTip("Characteristic flow velocity (free-stream or mean).")
        bl_form.addRow("Velocity:", self._bl_velocity)

        self._bl_fluid = QComboBox()
        self._bl_fluid.addItems(["Air (20°C)", "Water (20°C)"])
        self._bl_fluid.setToolTip("Determines the kinematic viscosity used for the Reynolds number.")
        bl_form.addRow("Fluid:", self._bl_fluid)

        self._bl_wall_treatment = QComboBox()
        self._bl_wall_treatment.addItems([
            "Wall functions (y+ ≈ 30)",
            "Wall-resolved (y+ ≈ 1)",
        ])
        self._bl_wall_treatment.setToolTip(
            "Wall functions: fewer cells, suited to k-epsilon.\n"
            "Wall-resolved: very thin first layer, required by k-omega SST and LES."
        )
        bl_form.addRow("Wall treatment:", self._bl_wall_treatment)

        self._btn_bl_auto = QPushButton("Compute layers from physics (y+)")
        self._btn_bl_auto.setToolTip(
            "Derives nLayers, first-layer thickness and growth ratio from "
            "Reynolds and the y+ target, instead of guessing them."
        )
        self._btn_bl_auto.clicked.connect(self._on_bl_auto_compute)
        bl_form.addRow(self._btn_bl_auto)

        # The physics-derived first layer is stored in METRES, not only as a
        # fraction of max cell: max cell changes with the detail slider and
        # the adaptive sizing, and a stored fraction silently rescaled the
        # first layer by the same factor (measured on a real case: 85.8 um
        # -> 6.1 um, y+ target 30 -> ~0.2 achieved).
        self._bl_first_layer_abs: float | None = None
        self._bl_thick_updating = False
        # True once the user edits the BL values by hand: suppresses the
        # automatic y+ recompute so manual values are never overwritten.
        self._bl_user_manual = False
        self._bl_thick.valueChanged.connect(self._on_bl_thick_edited)
        # Physics inputs: recompute silently after a compute so the derived
        # parameters never go stale (velocity 10 -> 1 m/s used to keep the
        # old first layer).
        self._bl_velocity.valueChanged.connect(self._on_bl_physics_inputs_changed)
        self._bl_fluid.currentIndexChanged.connect(self._on_bl_physics_inputs_changed)
        self._bl_wall_treatment.currentIndexChanged.connect(
            self._on_bl_physics_inputs_changed
        )

        self._bl_info = QLabel("")
        self._bl_info.setWordWrap(True)
        self._bl_info.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        bl_form.addRow(self._bl_info)

        # BL stack readout (facts only, user decision 2026-10-17). This used
        # to compare the requested stack against an ESTIMATED wall-cell size
        # and predict an "engine compresses the stack to ~X%" number. The
        # estimate (a Min Cell Size proxy) and the engine's own per-vertex
        # clamp are different quantities and were measured to disagree (hint
        # said ~1.55 mm wall cells / ~19% compression; the mesh came out with
        # ~0.1 mm first layers on ~75% of the wall faces). The user's rule:
        # if we cannot make an accurate estimate, don't show one — state the
        # requested stack here, and report what was ACTUALLY built (coverage,
        # real first-layer thickness) in the log after the build.
        self._bl_stack_hint = QLabel("")
        self._bl_stack_hint.setWordWrap(True)
        self._bl_stack_hint.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        bl_form.addRow(self._bl_stack_hint)

        self._bl_form_widget = QWidget()
        self._bl_form_widget.setLayout(bl_form)
        self._bl_form_widget.setVisible(False)
        bl_layout.addWidget(self._bl_form_widget)
        # Live BL stack readout: any input that changes the stack (h1, ratio,
        # nLayers) refreshes it.
        self._bl_n_layers.valueChanged.connect(self._refresh_bl_hint)
        self._bl_thick.valueChanged.connect(self._refresh_bl_hint)
        self._bl_exp.valueChanged.connect(self._refresh_bl_hint)
        self._bl_checkbox.toggled.connect(self._refresh_bl_hint)
        # W7: one provenance record for every BL field.  It is updated by the
        # dedicated _wire_bl_provenance() pass once the advanced controls
        # exist; defaults come from BLConfig (single source).
        self._bl_applying = False
        self._bl_provenance = dict(BLConfig().provenance)
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

        def _add_mesher_btn(
            text: str, key: str, tip: str, checked: bool = False,
            visible: bool = True,
        ) -> QPushButton:
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
            # W13: the historical engine buttons stay as lossless, tested
            # controls but are not separate CTAs; the visible Polymesh CTA
            # and the Advanced Engine selector replace them.
            btn.setVisible(visible)
            return btn

        # Platform-conditional WSL/OpenFOAM wording (see helper): on
        # Windows OpenFOAM runs in WSL2; on Linux it is native and
        # resolved via PATH. Windows text kept byte-identical.
        _w = _platform_mesher_wording()

        # Polymesh is the DEFAULT path and comes FIRST (user request: the
        # poly route is the product's core, cfMesh is the alternative).
        _add_mesher_btn(
            "Polymesh",
            "gmsh_direct_poly",
            "One CTA for the polyhedral family. The engine (legacy GMSH\n"
            "tet→poly, fTetWild, Auto, BL on tets) is chosen in Advanced\n"
            "→ Polymesh engine; cfMesh/FEM tetra remain legacy engines\n"
            "there for compatibility, not separate CTAs.",
            checked=True,
        )
        _add_mesher_btn(
            "Polymesh experimental (fTetWild)",
            "ftetwild_poly",
            "Legacy hidden control kept lossless. Reachable from\n"
            "Advanced → Polymesh engine → fTetWild.",
            visible=False,
        )
        _add_mesher_btn(
            "Auto (GMSH surface + fTetWild volume)",
            "auto_tet",
            "Legacy hidden control kept lossless. Reachable from\n"
            "Advanced → Polymesh engine → Auto.",
            visible=False,
        )
        _add_mesher_btn(
            f"Cartesian cfMesh ({_w['of_req']})",
            "cfmesh",
            f"Legacy hidden control kept lossless. Reachable from\n"
            f"Advanced → Polymesh engine → Cartesian cfMesh. Requires {_w['of_via']}.",
            visible=False,
        )
        # "FEM Tetra (GMSH)" was removed (user request): a pure tet mesh for
        # FEM solvers was a niche that duplicated the GMSH stage; the
        # internal TETRAHEDRAL algorithm remains as the auto fallback for
        # complex geometry, it just has no user-facing button any more.
        mesh_layout.addWidget(mesher_group)
        self._bl_group = bl_group
        self._mesher_group = mesher_group

        self._refine_group = QGroupBox("Local Refinement")
        refine_layout = QVBoxLayout(self._refine_group)
        # There used to be a second "Auto-refine narrow sections" checkbox
        # here, hidden and off-by-default, kept around "in case it was
        # needed again" instead of being removed when "Rifinitura
        # automatica" (Mesh tab) took over the same job. It could never
        # actually be reached by a user (setVisible(False),
        # setChecked(False)) and get_auto_refine_enabled() therefore always
        # returned False — dead code masquerading as a fallback. Removed
        # outright (2026-08-14) rather than left dark: throat_detector.py's
        # own zone-detection is now wired directly off "Rifinitura
        # automatica" + the cfMesh mesher choice in main_window.py, which
        # is the actual single control this comment always said it should
        # be.
        self._refine_list_label = QLabel("Refinement zones (sphere / box) — select one to edit its level:")
        refine_layout.addWidget(self._refine_list_label)
        self._refine_placeholder = QLabel(
            "No refinement zones yet.\n"
            "Click + to add a sphere (pick a point in the 3D view or type "
            "coordinates), or ▣ Box 3D to add a box you size in the viewer."
        )
        self._refine_placeholder.setWordWrap(True)
        self._refine_placeholder.setStyleSheet("color: gray; font-style: italic; padding: 4px;")
        refine_layout.addWidget(self._refine_placeholder)
        self._refine_list = QListWidget()
        self._refine_list.setMaximumHeight(90)
        self._refine_list.setVisible(False)
        refine_layout.addWidget(self._refine_list)
        refine_btn_row = QHBoxLayout()
        self._add_refine_btn = QPushButton("+")
        self._add_refine_btn.setMaximumWidth(32)
        self._add_refine_btn.setToolTip(
            "Add a sphere refinement zone: pick a point in the 3D view "
            "or type its coordinates."
        )
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

        # Per-selected-zone refinement LEVEL (1..6). It is THE user choice for
        # box zones (their cell size is derived from it); sphere zones carry
        # an explicit cell size instead, so the spin is disabled for them.
        level_row = QHBoxLayout()
        level_row.addWidget(QLabel("Level:"))
        self._refine_level_spin = QSpinBox()
        self._refine_level_spin.setRange(1, 6)
        self._refine_level_spin.setValue(2)
        self._refine_level_spin.setToolTip(
            "Refinement level of the selected box zone:\n"
            "  1 = base size (no extra refinement)\n"
            "  2 = half cells\n"
            "  3 = quarter cells\n"
            "  ... each level halves the previous size"
        )
        self._refine_level_spin.valueChanged.connect(self._on_refine_level_changed)
        self._refine_level_spin.setEnabled(False)
        level_row.addWidget(self._refine_level_spin)
        self._refine_level_hint = QLabel("1=base 2=half 3=¼ 4=⅛ …")
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
            "Experimental — these features are still being optimized and may "
            "not work with all geometries or configurations."
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

        # W13: one mesher CTA, all historical engines preserved.  This
        # selector is inside Advanced, not a second row of CTA buttons.
        engine_group = QGroupBox("Polymesh engine (Advanced)")
        engine_form = QFormLayout(engine_group)
        self._engine_combo = QComboBox()
        for _key in ENGINE_KEYS:
            _mode = MODE_BY_KEY[_key]
            self._engine_combo.addItem(_mode.label, _key)
            self._engine_combo.setItemData(
                self._engine_combo.count() - 1,
                f"{_mode.description}\n"
                f"legacy key: {_mode.key}",
                Qt.ToolTipRole,
            )
        _default_idx = self._engine_combo.findData(DEFAULT_MESHER_KEY)
        if _default_idx >= 0:
            self._engine_combo.setCurrentIndex(_default_idx)
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        engine_form.addRow("Engine:", self._engine_combo)
        _engine_note = QLabel(
            "One CTA, every historical path. Auto is opt-in until its "
            "corpus A/B is green; the measured legacy GMSH→dual path is "
            "the default. cfMesh and FEM tetra remain here for compatibility."
        )
        _engine_note.setWordWrap(True)
        _engine_note.setStyleSheet(f"color:{COLOR_TEXT_DIM};")
        engine_form.addRow(_engine_note)
        adv_layout.addWidget(engine_group)

        # Sync the visible CTA and the poly-conversion checkbox to the
        # engine selected at startup.
        self._on_engine_changed()

        # Cell sizes are derived from the Mesh Fineness slider (single
        # control). They are shown here read-only so the user always sees
        # what sizes the slider set — never edited by hand.
        mesh_group = QGroupBox("Cell Sizes (derived from the slider)")
        mesh_form = QFormLayout(mesh_group)
        self._max_cell = QDoubleSpinBox()
        self._max_cell.setRange(1e-10, 100.0)
        self._max_cell.setValue(0.05)
        self._max_cell.setSingleStep(0.01)
        self._max_cell.setDecimals(10)
        self._max_cell.setSuffix(" m")
        self._max_cell.setReadOnly(True)
        self._max_cell.setToolTip("Derived automatically from the Mesh Fineness slider.")
        mesh_form.addRow("Max Cell Size:", self._max_cell)

        self._min_cell = QDoubleSpinBox()
        self._min_cell.setRange(1e-10, 10.0)
        self._min_cell.setValue(0.01)
        self._min_cell.setSingleStep(0.001)
        self._min_cell.setDecimals(10)
        self._min_cell.setSuffix(" m")
        self._min_cell.setReadOnly(True)
        self._min_cell.setToolTip("Derived automatically from the Mesh Fineness slider.")
        mesh_form.addRow("Min Cell Size:", self._min_cell)

        self._cell_est_label = QLabel("Cells: --")
        self._cell_est_label.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        mesh_form.addRow(self._cell_est_label)

        self._prev_cell_params["max_cell"] = self._max_cell.value()
        self._prev_cell_params["min_cell"] = self._min_cell.value()
        self._max_cell.valueChanged.connect(self._on_max_cell_changed)
        self._min_cell.valueChanged.connect(self._on_min_cell_changed)
        # The derived Min Cell Size is the wall-cell estimate used by the BL
        # hint, so keep it in sync when the slider/geometry changes it.
        self._min_cell.valueChanged.connect(self._refresh_bl_hint)
        adv_layout.addWidget(mesh_group)

        # W7: the complete poly-path BL controls (the engine already
        # supported them; before this they were hardcoded in get_bl_params).
        self._build_bl_advanced_group(adv_layout)

        # fTetWild surface fidelity (experimental mesher, issue #12): the
        # wrapper's knobs exposed so the user can trade fidelity for speed.
        ftet_group = QGroupBox("fTetWild surface fidelity (experimental)")
        ftet_form = QFormLayout(ftet_group)
        self._ftet_eps_frac = QDoubleSpinBox()
        self._ftet_eps_frac.setRange(1.0, 50.0)
        self._ftet_eps_frac.setValue(15.0)
        self._ftet_eps_frac.setDecimals(1)
        self._ftet_eps_frac.setSingleStep(1.0)
        self._ftet_eps_frac.setSuffix(" % of cell")
        self._ftet_eps_frac.setToolTip(
            "Dynamic surface envelope: maximum distance between the fTetWild "
            "output surface and the input surface, as a PERCENTAGE of the "
            "local target cell edge. It scales automatically with the part "
            "and the resolution slider (15% -> 1.2 mm on an 8 mm cell, "
            "0.15 mm on a 1 mm cell), so fidelity is proportional everywhere "
            "instead of being a fixed millimetre value. Lower = more "
            "faithful and slower; below ~1% the tetrahedralization stalls."
        )
        ftet_form.addRow("Surface tolerance:", self._ftet_eps_frac)
        self._ftet_adaptive = QCheckBox(
            "Adaptive refinement (curvature / thin passages)"
        )
        self._ftet_adaptive.setChecked(True)
        self._ftet_adaptive.setToolTip(
            "Image of cfMesh's surfaceThicknessRefinement + "
            "surfaceCurvatureRefinement: builds a local sizing field from "
            "the measured passage thickness and the surface curvature, so "
            "thin passages and tightly curved regions get finer cells while "
            "the bulk stays at the target edge. Costs a few seconds of "
            "sampling before the tetrahedralization; shows its measured "
            "target-edge range in the log."
        )
        ftet_form.addRow(self._ftet_adaptive)
        self._ftet_simplify = QCheckBox(
            "Simplify surface (slower, less faithful)"
        )
        self._ftet_simplify.setChecked(False)
        self._ftet_simplify.setToolTip(
            "ON: fTetWild runs its internal edge-collapsing simplification - "
            "the measured expensive phase (660 s on the reference part) and a "
            "deviation source. OFF: skip it - faster and more faithful."
        )
        ftet_form.addRow(self._ftet_simplify)
        adv_layout.addWidget(ftet_group)

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
            "Balanced (physical, recommended)",
            "Conservative (physical/2)",
            "Aggressive (logical)",
            "Custom...",
        ])
        self._openmp_combo.setToolTip(
            "Controls how many OpenMP threads cfMesh uses for parallel computation.\n\n"
            "• Balanced: uses all physical cores (recommended)\n"
            "• Conservative: half the physical cores (less memory, slower)\n"
            "• Aggressive: all logical cores, including hyperthreads\n"
            "• Custom: manually set the number of threads\n\n"
            "Each thread consumes memory, so more aggressive modes can cause "
            "OOM on large meshes."
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
        self._openmp_spin.setToolTip("Number of OpenMP threads to use (Custom mode).")
        openmp_row.addWidget(self._openmp_spin)
        openmp_group_layout.addLayout(openmp_row)
        adv_layout.addWidget(openmp_group)

        parallel_group = QGroupBox("Parallel Meshing (MPI)")
        parallel_group_layout = QVBoxLayout(parallel_group)

        parallel_row = QHBoxLayout()
        self._parallel_check = QCheckBox("Parallel meshing (multi-core)")
        # Windows: WSL2 caps its own memory below the host's. Linux: the
        # machine's own RAM is the constraint. Windows text byte-identical.
        self._parallel_check.setToolTip(
            "Runs cartesianMesh across multiple CPU cores via MPI "
            "(cartesianMesh -parallel) and reconstructs the result. "
            "Worth it above a few hundred thousand cells; the "
            "decompose/reconstruct overhead can outweigh the benefit on "
            "small meshes.\n\n"
            "Each core loads a full copy of the geometry, so memory use "
            f"scales with the core count — {_w['mem_ctx']} too many cores on a large "
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
        # count, and the available-memory cap (WSL2's on Windows) is the
        # usual real constraint.
        self._parallel_cores.setValue(max(2, min(4, cpu_n // 2)))
        self._parallel_cores.setSuffix(" cores")
        self._parallel_cores.setEnabled(False)
        parallel_row.addWidget(self._parallel_cores)
        parallel_group_layout.addLayout(parallel_row)
        adv_layout.addWidget(parallel_group)

        adv_layout.addStretch()
        self._adv_tab = adv_tab
        self._tabs.addTab(adv_tab, "Advanced")

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

        # Cancel (moved here from the ribbon, user request): shown by the
        # main window only while a meshing run is active, directly under
        # "Reset All" where the user is looking during a run.
        btn_cancel_layout = QHBoxLayout()
        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setMinimumHeight(40)
        self._btn_cancel.setToolTip("Stop the meshing run in progress.")
        self._btn_cancel.setStyleSheet(
            f"QPushButton {{ background:{COLOR_DANGER}; color:white; "
            f"border:1px solid {COLOR_DANGER}; border-radius:4px; "
            "font-weight:700; }}"
        )
        self._btn_cancel.setVisible(False)
        self._btn_cancel.clicked.connect(self.cancel_meshing.emit)
        btn_cancel_layout.addWidget(self._btn_cancel)
        outer.addLayout(btn_cancel_layout)

        # Advanced mode ON by default: the mesher choice buttons, boundary
        # layers and the Advanced tab are all visible from the start. Hiding
        # them behind "simple mode" confused users (e.g. the 3 mesher buttons
        # were invisible), so the simple mode is disabled by default.
        self._expert_mode = True
        self.set_expert_mode(True)
        # Establish the BL consistency hint from the initial widget state.
        self._refresh_bl_hint()

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
        # AUTOMATIC y+ calculation: enabling the boundary layer derives the
        # layers from the flow conditions on the spot - no button press.  A
        # user who edited the values by hand keeps them (_bl_user_manual).
        if (
            checked
            and not getattr(self, "_bl_applying", False)
            and not getattr(self, "_bl_user_manual", False)
            and self._suggest_meshes
        ):
            self._on_bl_auto_compute()

    def _bl_first_layer(self) -> float:
        """First layer height in metres.

        The physics-computed ABSOLUTE value when available, else the manual
        fraction x max cell.  See the note in __init__ for why the absolute
        value is authoritative (fraction x moving max_cell silently rescaled
        the layer on real runs).
        """
        abs_h1 = getattr(self, "_bl_first_layer_abs", None)
        if abs_h1:
            return float(abs_h1)
        return self._bl_thick.value() * self._max_cell.value()

    def _on_bl_thick_edited(self, _value: float) -> None:
        """A manual edit of the fraction switches back to manual mode."""
        if getattr(self, "_bl_thick_updating", False):
            return
        self._bl_first_layer_abs = None
        self._bl_user_manual = True
        # The absolute value is gone: its provenance falls back to default.
        self._bl_provenance["first_height"] = "default"
        self._refresh_bl_hint()

    def _on_bl_physics_inputs_changed(self, *_args) -> None:
        """Recompute silently when the physics inputs change after a compute."""
        if getattr(self, "_bl_applying", False):
            return
        if getattr(self, "_bl_first_layer_abs", None) is None:
            return
        if not self._suggest_meshes:
            return
        self._on_bl_auto_compute()

    def _refresh_bl_hint(self) -> None:
        """Show the REQUESTED BL stack — facts only.

        No wall-cell estimate, no compression prediction (user decision
        2026-10-17: an inaccurate estimate is worse than none). What was
        actually built (coverage, effective first-layer thickness) is
        reported in the log after the build — see
        openfoam_runner.boundary_layer_report.
        """
        if not hasattr(self, "_bl_stack_hint"):
            return
        try:
            from polyfoammesh.core.gmsh_wrapper import compute_bl_stack
        except Exception:
            return
        h1 = self._bl_first_layer()
        stack = compute_bl_stack(h1, self._bl_exp.value(), self._bl_n_layers.value())
        stack_mm, h1_mm = stack * 1000.0, h1 * 1000.0
        n_layers = int(self._bl_n_layers.value())
        if not self._bl_checkbox.isChecked():
            self._bl_stack_hint.setText(
                f"BL off. Requested stack: {stack_mm:.4g} mm "
                f"({n_layers} layers, h1 {h1_mm:.4g} mm)."
            )
        else:
            self._bl_stack_hint.setText(
                f"Requested stack: {stack_mm:.4g} mm "
                f"({n_layers} layers, h1 {h1_mm:.4g} mm). Real coverage and "
                "the effective first-layer thickness are reported in the "
                "log after the build."
            )

    # ------------------------------------------------------------------
    # W7: advanced BL controls, one validated config, presets
    # ------------------------------------------------------------------
    def _build_bl_advanced_group(self, parent_layout: QVBoxLayout) -> None:
        """The complete poly-path BL controls (engine-supported knobs)."""
        group = QGroupBox("Boundary Layer — advanced (poly path)")
        form = QFormLayout(group)

        self._bl_min_base_edge = QDoubleSpinBox()
        self._bl_min_base_edge.setRange(0.0, 2.0)
        self._bl_min_base_edge.setDecimals(2)
        self._bl_min_base_edge.setSingleStep(0.05)
        self._bl_min_base_edge.setValue(BLConfig().min_base_edge_fraction)
        self._bl_min_base_edge.setToolTip(
            "Sliver base-face filter, as a fraction of the median boundary "
            "edge (0 = off). The historical robust value is 0.9: faces with "
            "a tiny edge are still extruded but with a short stack, so the "
            "wall is not left bare. Raising it filters more aggressively; "
            "lowering it keeps more full stacks on faceted duals."
        )
        form.addRow("Min base-edge fraction:", self._bl_min_base_edge)

        self._bl_terminate_budget = QCheckBox(
            "Terminate the stack by local budget (recommended)"
        )
        self._bl_terminate_budget.setChecked(BLConfig().terminate_by_budget)
        self._bl_terminate_budget.setToolTip(
            "ON: at a narrow wall vertex the engine keeps the full-height "
            "layers that fit instead of compressing the whole stack into "
            "pancakes. This is the measured robust default. OFF restores the "
            "historical uniform compression."
        )
        form.addRow(self._bl_terminate_budget)

        self._bl_edge_cap_pct = QDoubleSpinBox()
        self._bl_edge_cap_pct.setRange(0.0, 100.0)
        self._bl_edge_cap_pct.setDecimals(1)
        self._bl_edge_cap_pct.setSingleStep(5.0)
        self._bl_edge_cap_pct.setSuffix(" %")
        self._bl_edge_cap_pct.setValue(BLConfig().edge_cap_percentile)
        self._bl_edge_cap_pct.setToolTip(
            "Inversion-clamp cap: 0 = the historical smallest incident wall "
            "edge (a single sliver can collapse the local stack). Above 0 "
            "uses this percentile of all incident wall-edge lengths, so one "
            "bad edge no longer dictates the thickness. Measured to have a "
            "failure mode on some duals (venturi): leave 0 unless you are "
            "deliberately testing it."
        )
        form.addRow("Edge-cap percentile:", self._bl_edge_cap_pct)

        self._bl_local_term = QComboBox()
        self._bl_local_term.addItem("Off (standard path)", "off")
        self._bl_local_term.addItem(
            "Local termination (decoupled)", "decoupled"
        )
        self._bl_local_term.addItem(
            "Local termination (per-vertex, concave closure)",
            "decoupled_vertex",
        )
        self._bl_local_term.setToolTip(
            "Off: the standard stack. 'decoupled': retry a failed stack at "
            "smaller height scales without carrying exclusions. 'Per-vertex': "
            "exclude defective wall VERTICES iteratively; this is the path "
            "that closes the layer on concave junctions (valve/groove/slot). "
            "The worker still retries it automatically when the standard "
            "path fails; selecting it here makes that choice explicit."
        )
        form.addRow("Local termination:", self._bl_local_term)

        self._bl_height_retry = QCheckBox(
            "Retry a shorter local height before excluding a vertex"
        )
        self._bl_height_retry.setChecked(False)
        self._bl_height_retry.setEnabled(False)
        self._bl_height_retry.setToolTip(
            "Only available with per-vertex local termination. Before "
            "excluding a defective wall vertex, try one shorter stack at "
            "that vertex; if it validates it is kept, so the wall keeps its "
            "layer. Costs one extra rebuild per exclusion round."
        )
        form.addRow(self._bl_height_retry)

        self._bl_local_rounds = QSpinBox()
        self._bl_local_rounds.setRange(1, 10)
        self._bl_local_rounds.setValue(BLConfig().local_max_rounds)
        self._bl_local_rounds.setToolTip(
            "Maximum exclusion rounds per height scale when local "
            "termination is active. More rounds can converge on coarsely "
            "faceted duals, at the cost of extra builds."
        )
        form.addRow("Local max rounds:", self._bl_local_rounds)

        preset_row = QHBoxLayout()
        self._bl_preset_combo = QComboBox()
        self._bl_preset_combo.setMinimumWidth(180)
        self._bl_preset_combo.setToolTip(
            "Named BL presets. Selecting one applies the full snapshot "
            "(layers, first height, growth, advanced settings)."
        )
        self._bl_save_preset_btn = QPushButton("Save preset…")
        self._bl_save_preset_btn.setToolTip(
            "Save the current BL configuration under a name. Presets are "
            "stored with a schema version and restored on the next start."
        )
        self._bl_delete_preset_btn = QPushButton("Delete")
        self._bl_delete_preset_btn.setToolTip("Delete the selected BL preset.")
        preset_row.addWidget(self._bl_preset_combo, 1)
        preset_row.addWidget(self._bl_save_preset_btn)
        preset_row.addWidget(self._bl_delete_preset_btn)
        form.addRow("Preset:", preset_row)

        self._bl_preset_status = QLabel("")
        self._bl_preset_status.setWordWrap(True)
        self._bl_preset_status.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        form.addRow(self._bl_preset_status)

        parent_layout.addWidget(group)
        self._preset_store = BLPresetStore()

        self._bl_save_preset_btn.clicked.connect(self._on_save_bl_preset_clicked)
        self._bl_delete_preset_btn.clicked.connect(
            self._on_delete_bl_preset_clicked
        )
        self._bl_preset_combo.currentIndexChanged.connect(
            self._on_bl_preset_selected
        )
        self._bl_local_term.currentIndexChanged.connect(
            self._on_bl_local_term_changed
        )
        self._bl_height_retry.toggled.connect(self._on_bl_height_retry_toggled)

        # Establish the initial enabled/disabled state WITHOUT marking the
        # default as user provenance.
        self._bl_applying = True
        try:
            self._on_bl_local_term_changed()
        finally:
            self._bl_applying = False
        self._wire_bl_provenance()
        self._refresh_bl_presets()

    # ------------------------------------------------------------------
    # W10: one MeshConfig + Fast/Balanced/Fine presets
    # ------------------------------------------------------------------
    def _build_mesh_preset_group(self, parent_layout: QVBoxLayout) -> None:
        group = QGroupBox("Mesh preset")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self._mesh_preset_combo = QComboBox()
        self._mesh_preset_combo.setMinimumWidth(170)
        self._mesh_preset_combo.setToolTip(
            "Fast/Balanced/Fine are measured profiles (see the status line "
            "for their evidence). User presets are full MeshConfig snapshots."
        )
        self._mesh_save_preset_btn = QPushButton("Save mesh preset…")
        self._mesh_delete_preset_btn = QPushButton("Delete")
        row.addWidget(self._mesh_preset_combo, 1)
        row.addWidget(self._mesh_save_preset_btn)
        row.addWidget(self._mesh_delete_preset_btn)
        layout.addLayout(row)
        self._mesh_preset_status = QLabel("")
        self._mesh_preset_status.setWordWrap(True)
        self._mesh_preset_status.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        layout.addWidget(self._mesh_preset_status)

        parent_layout.addWidget(group)
        self._mesh_preset_store = MeshPresetStore()
        self._mesh_preset_combo.currentIndexChanged.connect(
            self._on_mesh_preset_selected
        )
        self._mesh_save_preset_btn.clicked.connect(
            self._on_save_mesh_preset_clicked
        )
        self._mesh_delete_preset_btn.clicked.connect(
            self._on_delete_mesh_preset_clicked
        )
        self._refresh_mesh_presets()

    def _refresh_mesh_presets(self) -> None:
        if not hasattr(self, "_mesh_preset_combo"):
            return
        combo = self._mesh_preset_combo
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("(custom)", "")
            for preset in list_mesh_presets(self._mesh_preset_store):
                label = preset.name
                if preset.origin == "user":
                    label += " (user)"
                if preset.experimental:
                    label += " (experimental)"
                combo.addItem(label, preset.name)
            last = self._mesh_preset_store.last_used
            idx = combo.findData(last) if last else 0
            combo.setCurrentIndex(max(idx, 0))
        finally:
            combo.blockSignals(False)
        if self._mesh_preset_store.errors:
            self._mesh_preset_status.setText(
                "Mesh presets: " + " | ".join(self._mesh_preset_store.errors)
            )

    def get_mesh_config(self) -> MeshConfig:
        cfg = MeshConfig(
            detail_slider=int(self._detail_slider.value())
            if hasattr(self, "_detail_slider") else 10,
            mesher=self.get_mesher_type(),
            adaptive_sizing=bool(self._adaptive_sizing_check.isChecked())
            if hasattr(self, "_adaptive_sizing_check") else True,
            ftet_eps_frac=(
                float(self._ftet_eps_frac.value()) / 100.0
                if hasattr(self, "_ftet_eps_frac") else 0.15
            ),
            ftet_adaptive=bool(self._ftet_adaptive.isChecked())
            if hasattr(self, "_ftet_adaptive") else True,
            ftet_simplify=bool(self._ftet_simplify.isChecked())
            if hasattr(self, "_ftet_simplify") else False,
            bl=self.get_bl_config(),
        )
        fixed, warnings = cfg.normalized()
        for warning in warnings:
            logger.warning("Mesh config: %s", warning)
            self.suggestion_completed.emit(f"[preset] config clamp: {warning}")
        return fixed

    def set_mesh_config(self, config: MeshConfig, source: str | None = None) -> None:
        """Apply a full mesh config (project restore or preset)."""
        cfg = config
        cfg.raise_if_invalid()
        self._detail_slider.setValue(int(cfg.detail_slider))
        for btn, key in getattr(self, "_mesher_keys", []):
            if key == cfg.mesher:
                btn.setChecked(True)
                self._on_mesher_button_clicked(btn)
                break
        if hasattr(self, "_engine_combo"):
            idx = self._engine_combo.findData(cfg.mesher)
            if idx >= 0 and self._engine_combo.currentIndex() != idx:
                self._engine_combo.blockSignals(True)
                self._engine_combo.setCurrentIndex(idx)
                self._engine_combo.blockSignals(False)
                self._on_engine_changed()
        if hasattr(self, "_adaptive_sizing_check"):
            self._adaptive_sizing_check.setChecked(bool(cfg.adaptive_sizing))
        if hasattr(self, "_ftet_eps_frac"):
            self._ftet_eps_frac.setValue(float(cfg.ftet_eps_frac) * 100.0)
        if hasattr(self, "_ftet_adaptive"):
            self._ftet_adaptive.setChecked(bool(cfg.ftet_adaptive))
        if hasattr(self, "_ftet_simplify"):
            self._ftet_simplify.setChecked(bool(cfg.ftet_simplify))
        # BLConfig provenance only accepts preset|user|default|clamped; the
        # mesh preset name/origin is carried by the mesh log, not this field.
        self.set_bl_config(cfg.bl, source="preset" if source else None)
        if source:
            self.suggestion_completed.emit(
                f"[preset] applied {source}: {cfg.to_engine_summary()}"
            )

    def apply_mesh_preset(self, name: str) -> bool:
        preset = resolve_mesh_preset(self._mesh_preset_store, name)
        if preset is None:
            self._mesh_preset_status.setText(
                f"Mesh preset '{name}' was not found; current values kept."
            )
            return False
        try:
            self.set_mesh_config(preset.config, source=f"preset:{name}")
        except Exception as exc:  # noqa: BLE001 - a bad preset must not crash
            self._mesh_preset_status.setText(
                f"Mesh preset '{name}' is invalid and was not applied: {exc}"
            )
            return False
        idx = self._mesh_preset_combo.findData(name)
        self._mesh_preset_combo.blockSignals(True)
        try:
            self._mesh_preset_combo.setCurrentIndex(max(idx, 0))
        finally:
            self._mesh_preset_combo.blockSignals(False)
        if preset.origin == "user":
            try:
                self._mesh_preset_store.set_last_used(name)
            except Exception:  # noqa: BLE001
                logger.debug("Could not persist last mesh preset", exc_info=True)
        evidence = preset.evidence or "no measurement recorded"
        self._mesh_preset_status.setText(
            f"{name} ({preset.origin}"
            + (", experimental" if preset.experimental else "")
            + f"): {evidence}"
        )
        self.suggestion_completed.emit(
            f"[preset] mesh preset '{name}' ({preset.origin}) applied; "
            f"evidence: {evidence}"
        )
        return True

    def save_mesh_preset(self, name: str) -> bool:
        clean = str(name).strip()
        if not clean:
            self._mesh_preset_status.setText("Preset name must not be empty.")
            return False
        try:
            self._mesh_preset_store.save(clean, self.get_mesh_config())
        except Exception as exc:  # noqa: BLE001 - reported, not fatal
            self._mesh_preset_status.setText(f"Cannot save mesh preset: {exc}")
            return False
        self._refresh_mesh_presets()
        idx = self._mesh_preset_combo.findData(clean)
        self._mesh_preset_combo.blockSignals(True)
        try:
            self._mesh_preset_combo.setCurrentIndex(max(idx, 0))
        finally:
            self._mesh_preset_combo.blockSignals(False)
        self._mesh_preset_status.setText(f"Mesh preset '{clean}' saved.")
        return True

    def delete_mesh_preset(self, name: str) -> bool:
        removed = self._mesh_preset_store.delete(name)
        self._refresh_mesh_presets()
        self._mesh_preset_status.setText(
            f"Mesh preset '{name}' deleted." if removed
            else f"Mesh preset '{name}' was not found."
        )
        return removed

    def _on_save_mesh_preset_clicked(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Save mesh preset", "Preset name:",
            text=self._mesh_preset_combo.currentData() or "",
        )
        if ok:
            self.save_mesh_preset(str(name))

    def _on_delete_mesh_preset_clicked(self) -> None:
        name = self._mesh_preset_combo.currentData()
        if name:
            self.delete_mesh_preset(str(name))

    def _on_mesh_preset_selected(self, _index: int) -> None:
        name = self._mesh_preset_combo.currentData()
        if name:
            self.apply_mesh_preset(str(name))

    def _wire_bl_provenance(self) -> None:
        """Attribute a widget edit to 'user' (preset/default stay intact)."""
        pairs = (
            (self._bl_checkbox.toggled, "enabled"),
            (self._bl_n_layers.valueChanged, "n_layers"),
            (self._bl_thick.valueChanged, "first_layer_fraction"),
            (self._bl_exp.valueChanged, "growth_rate"),
            (self._bl_apply_all.toggled, "apply_to_all"),
            (self._bl_velocity.valueChanged, "velocity"),
            (self._bl_fluid.currentIndexChanged, "fluid"),
            (self._bl_wall_treatment.currentIndexChanged, "wall_treatment"),
            (self._bl_min_base_edge.valueChanged, "min_base_edge_fraction"),
            (self._bl_terminate_budget.toggled, "terminate_by_budget"),
            (self._bl_edge_cap_pct.valueChanged, "edge_cap_percentile"),
            (self._bl_local_rounds.valueChanged, "local_max_rounds"),
        )
        for signal, field_name in pairs:
            signal.connect(
                lambda *_args, f=field_name: self._mark_bl_user(f)
            )

    def _mark_bl_user(self, field_name: str) -> None:
        if getattr(self, "_bl_applying", False):
            return
        self._bl_provenance[field_name] = "user"

    def _on_bl_local_term_changed(self, *_args) -> None:
        mode = self._bl_local_term.currentData()
        vertex = mode == "decoupled_vertex"
        self._bl_height_retry.setEnabled(vertex)
        if not vertex and self._bl_height_retry.isChecked():
            self._bl_height_retry.blockSignals(True)
            try:
                self._bl_height_retry.setChecked(False)
            finally:
                self._bl_height_retry.blockSignals(False)
        self._mark_bl_user("local_termination")

    def _on_bl_height_retry_toggled(self, checked: bool) -> None:
        if checked and self._bl_local_term.currentData() != "decoupled_vertex":
            self._bl_height_retry.blockSignals(True)
            try:
                self._bl_height_retry.setChecked(False)
            finally:
                self._bl_height_retry.blockSignals(False)
            self._bl_preset_status.setText(
                "Local height retry requires per-vertex local termination; "
                "it was left off."
            )
            return
        self._mark_bl_user("local_height_retry")

    def get_bl_config(self) -> BLConfig:
        """The one validated BL config the GUI hands to the worker."""
        cfg = BLConfig(
            enabled=self._bl_checkbox.isChecked(),
            n_layers=int(self._bl_n_layers.value()),
            first_height=float(self._bl_first_layer_abs or 0.0),
            first_layer_fraction=float(self._bl_thick.value()),
            growth_rate=float(self._bl_exp.value()),
            apply_to_all=self._bl_apply_all.isChecked(),
            min_base_edge_fraction=float(self._bl_min_base_edge.value()),
            terminate_by_budget=self._bl_terminate_budget.isChecked(),
            edge_cap_percentile=float(self._bl_edge_cap_pct.value()),
            local_termination=str(self._bl_local_term.currentData() or "off"),
            local_height_retry=self._bl_height_retry.isChecked(),
            local_max_rounds=int(self._bl_local_rounds.value()),
            velocity=float(self._bl_velocity.value()),
            fluid=(
                "water" if "Water" in self._bl_fluid.currentText() else "air"
            ),
            wall_treatment=(
                "wall_resolved"
                if "resolved" in self._bl_wall_treatment.currentText().lower()
                else "wall_functions"
            ),
            provenance=dict(self._bl_provenance),
        )
        fixed, warnings = cfg.normalized()
        for warning in warnings:
            logger.warning("BL config: %s", warning)
            self.suggestion_completed.emit(f"[bl] config clamp: {warning}")
        return fixed

    def set_bl_config(self, config, source: str | None = None) -> None:
        """Apply a config to the widgets (preset load / project restore).

        *source* attributes every field (``"preset"`` for a preset); when
        None the provenance carried by *config* is kept.
        """
        if isinstance(config, dict):
            cfg = BLConfig.from_mapping(
                config, max_cell=max(self._max_cell.value(), 1e-12),
            )
        else:
            cfg = config
        if source is not None:
            cfg = cfg.with_provenance(source)
        cfg.raise_if_invalid()

        self._bl_applying = True
        try:
            self._bl_checkbox.blockSignals(True)
            try:
                self._bl_checkbox.setChecked(bool(cfg.enabled))
            finally:
                self._bl_checkbox.blockSignals(False)
            self._bl_n_layers.setValue(int(cfg.n_layers))
            self._bl_exp.setValue(float(cfg.growth_rate))
            self._bl_apply_all.setChecked(bool(cfg.apply_to_all))
            self._bl_min_base_edge.setValue(float(cfg.min_base_edge_fraction))
            self._bl_terminate_budget.setChecked(bool(cfg.terminate_by_budget))
            self._bl_edge_cap_pct.setValue(float(cfg.edge_cap_percentile))
            idx = self._bl_local_term.findData(cfg.local_termination)
            self._bl_local_term.setCurrentIndex(idx if idx >= 0 else 0)
            self._bl_height_retry.setEnabled(
                cfg.local_termination == "decoupled_vertex"
            )
            self._bl_height_retry.setChecked(bool(cfg.local_height_retry))
            self._bl_local_rounds.setValue(int(cfg.local_max_rounds))
            self._bl_velocity.setValue(float(cfg.velocity))
            self._bl_fluid.setCurrentIndex(
                1 if cfg.fluid == "water" else 0
            )
            self._bl_wall_treatment.setCurrentIndex(
                1 if cfg.wall_treatment == "wall_resolved" else 0
            )

            if cfg.first_height_mode == "absolute":
                self._bl_first_layer_abs = float(cfg.first_height)
                fraction = cfg.first_height / max(self._max_cell.value(), 1e-12)
                self._bl_thick_updating = True
                try:
                    self._bl_thick.setValue(
                        min(
                            max(fraction, self._bl_thick.minimum()),
                            self._bl_thick.maximum(),
                        )
                    )
                finally:
                    self._bl_thick_updating = False
                self._bl_user_manual = True
            else:
                self._bl_first_layer_abs = None
                self._bl_thick_updating = True
                try:
                    self._bl_thick.setValue(float(cfg.first_layer_fraction))
                finally:
                    self._bl_thick_updating = False
                self._bl_user_manual = False
            self._bl_provenance = dict(cfg.provenance)
        finally:
            self._bl_applying = False
        self._refresh_bl_hint()

    # ------------------------------------------------------------------
    # BL presets
    # ------------------------------------------------------------------
    def _refresh_bl_presets(self) -> None:
        if not hasattr(self, "_bl_preset_combo"):
            return
        self._bl_preset_combo.blockSignals(True)
        try:
            self._bl_preset_combo.clear()
            self._bl_preset_combo.addItem("(no preset)", "")
            for name in self._preset_store.names():
                self._bl_preset_combo.addItem(name, name)
            last = self._preset_store.last_used
            idx = self._bl_preset_combo.findData(last) if last else 0
            self._bl_preset_combo.setCurrentIndex(max(idx, 0))
        finally:
            self._bl_preset_combo.blockSignals(False)
        if self._preset_store.errors:
            self._bl_preset_status.setText(
                "BL presets: " + " | ".join(self._preset_store.errors)
            )

    def _on_bl_preset_selected(self, _index: int) -> None:
        name = self._bl_preset_combo.currentData()
        if not name:
            return
        if not self.apply_bl_preset(str(name)):
            self._bl_preset_combo.blockSignals(True)
            try:
                last = self._preset_store.last_used
                idx = self._bl_preset_combo.findData(last) if last else 0
                self._bl_preset_combo.setCurrentIndex(max(idx, 0))
            finally:
                self._bl_preset_combo.blockSignals(False)

    def apply_bl_preset(self, name: str) -> bool:
        """Apply a named preset; report clamps without a modal dialog."""
        cfg = self._preset_store.get(name)
        if cfg is None:
            self._bl_preset_status.setText(
                f"Preset '{name}' was not found; the current values were kept."
            )
            return False
        warnings = [
            warning for warning in self._preset_store.warnings
            if warning.startswith(f"preset {name!r}:")
        ]
        try:
            self.set_bl_config(cfg, source="preset")
        except BLConfigError as exc:
            self._bl_preset_status.setText(
                f"Preset '{name}' is invalid and was not applied: {exc}"
            )
            return False
        idx = self._bl_preset_combo.findData(name)
        self._bl_preset_combo.blockSignals(True)
        try:
            self._bl_preset_combo.setCurrentIndex(max(idx, 0))
        finally:
            self._bl_preset_combo.blockSignals(False)
        try:
            self._preset_store.set_last_used(name)
        except (BLConfigError, ValueError) as exc:
            logger.debug("Could not persist last BL preset: %s", exc)
        if warnings:
            self._bl_preset_status.setText(
                f"Preset '{name}' loaded with clamping: "
                + " | ".join(warnings)
            )
        else:
            self._bl_preset_status.setText(f"Preset '{name}' loaded.")
        return True

    def save_bl_preset(self, name: str, config=None) -> bool:
        """Save *config* (default: the current widgets) as a named preset."""
        clean = str(name).strip()
        if not clean:
            self._bl_preset_status.setText("Preset name must not be empty.")
            return False
        try:
            self._preset_store.save(
                clean, config if config is not None else self.get_bl_config(),
            )
        except (BLConfigError, ValueError) as exc:
            self._bl_preset_status.setText(f"Cannot save preset: {exc}")
            return False
        self._refresh_bl_presets()
        idx = self._bl_preset_combo.findData(clean)
        self._bl_preset_combo.blockSignals(True)
        try:
            self._bl_preset_combo.setCurrentIndex(max(idx, 0))
        finally:
            self._bl_preset_combo.blockSignals(False)
        self._bl_preset_status.setText(f"Preset '{clean}' saved.")
        return True

    def delete_bl_preset(self, name: str) -> bool:
        removed = self._preset_store.delete(name)
        self._refresh_bl_presets()
        self._bl_preset_status.setText(
            f"Preset '{name}' deleted." if removed
            else f"Preset '{name}' was not found."
        )
        return removed

    def _on_save_bl_preset_clicked(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Save BL preset", "Preset name:",
            text=self._bl_preset_combo.currentData() or "",
        )
        if ok:
            self.save_bl_preset(str(name))

    def _on_delete_bl_preset_clicked(self) -> None:
        name = self._bl_preset_combo.currentData()
        if name:
            self.delete_bl_preset(str(name))

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
            "Derived automatically from the Mesh Fineness slider."
            if checked else
            "Maximum cell size (manual mode — automatic refinement "
            "disabled)."
        )
        self._min_cell.setToolTip(
            "Derived automatically from the Mesh Fineness slider."
            if checked else
            "Minimum cell size (manual mode — automatic refinement "
            "disabled)."
        )

    # Kinematic viscosity at 20 °C, m²/s.
    _FLUID_NU = {"Air (20°C)": 1.5e-5, "Water (20°C)": 1.0e-6}

    def _bl_flow_length_and_type(self) -> tuple[float, str]:
        """(characteristic length, flow type) for the y+ calculation.

        AUTOMATIC, from the same patch classifier the rest of the app uses:

        - the CAD has both an inlet and an outlet patch -> INTERNAL flow:
          length = the inlet's equivalent diameter sqrt(4A/pi), which is the
          hydraulic length the pipe friction correlations need;
        - otherwise -> EXTERNAL flow: length = the bounding-box dimension,
          used with the flat-plate correlation.

        This is what makes a tube/nozzle case land close to the solver's
        real y+ instead of the flat-plate estimate over the whole bbox.
        """
        try:
            from polyfoammesh.core.patch_roles import match_role

            inlet_area = 0.0
            has_outlet = False
            for m in self._suggest_meshes or []:
                role = match_role(str(m.metadata.get("name", ""))) or ""
                if role == "inlet":
                    inlet_area += float(m.area)
                elif role == "outlet":
                    has_outlet = True
            if inlet_area > 0.0 and has_outlet:
                diameter = (4.0 * inlet_area / math.pi) ** 0.5
                if diameter > 0.0:
                    return diameter, "pipe"
        except Exception:  # noqa: BLE001 - fall back to the flat plate
            logger.debug("flow-type inference failed", exc_info=True)
        from polyfoammesh.core.geometry import compute_bbox_dim

        return compute_bbox_dim(self._suggest_meshes), "flat_plate"

    def _on_bl_auto_compute(self):
        """Derive boundary-layer parameters from flow physics.

        AUTOMATIC and flow-type aware: internal flows (inlet + outlet
        patches present) use the pipe friction correlation with the inlet's
        equivalent diameter; external flows use the flat-plate correlation
        with the bounding-box length.  Same basis as the standard online y+
        calculators, so the numbers match theirs for the same inputs.
        """
        if not self._suggest_meshes:
            QMessageBox.information(
                self, "No Geometry",
                "Load a geometry first: the characteristic length is required.",
            )
            return

        from polyfoammesh.commercial.bl_engine import BLEngine, FlowConditions

        length, flow_type = self._bl_flow_length_and_type()
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
            # The growth widget drives the physics compute (user request
            # 2026-10-18): a higher growth reaches the same total thickness
            # with FEWER, thicker layers - "comfortable" stacks that fit
            # the dual's boundary cells better and validate where a 1.2
            # stack of the same total was compressed into pancakes.  The
            # engine solves the layer COUNT from this rate.
            params = BLEngine().calculate_from_flow(
                flow, flow_type=flow_type,
                growth_rate=float(self._bl_exp.value()),
            )
        except Exception as e:
            logger.exception("Boundary-layer physics calculation failed")
            QMessageBox.warning(self, "Calculation failed", str(e))
            return

        # meshDict takes the first layer as a fraction of the max cell size.
        max_cell = max(self._max_cell.value(), 1e-9)
        fraction = params.first_layer_height / max_cell
        clamped = min(max(fraction, self._bl_thick.minimum()), self._bl_thick.maximum())

        self._bl_n_layers.setValue(
            min(max(params.n_layers, self._bl_n_layers.minimum()),
                self._bl_n_layers.maximum())
        )
        # The physics result is stored as an ABSOLUTE first layer (metres):
        # the spin box shows the fraction of the CURRENT max cell for
        # reference, but the run uses the absolute value, so a later change
        # of max cell (detail slider / adaptive sizing) cannot rescale it.
        self._bl_first_layer_abs = params.first_layer_height
        self._bl_user_manual = False
        self._bl_thick_updating = True
        try:
            self._bl_thick.setValue(clamped)
        finally:
            self._bl_thick_updating = False
        self._bl_exp.setValue(
            min(max(params.growth_rate, self._bl_exp.minimum()), self._bl_exp.maximum())
        )
        # Physics-derived values are user-driven, not defaults/presets.
        for field_name in (
            "n_layers", "first_height", "first_layer_fraction", "growth_rate",
        ):
            self._bl_provenance[field_name] = "user"

        note = ""
        if params.n_layers > self._bl_n_layers.maximum():
            note = (
                f"  ⚠ Physics requires {params.n_layers} layers, "
                f"limited to {self._bl_n_layers.maximum()}."
            )
        if abs(clamped - fraction) > 1e-12:
            note += "  ⚠ First layer clamped to the field range."

        kind = "pipe/Blasius" if flow_type == "pipe" else "flat plate"
        msg = (
            f"Re={flow.reynolds_number:.3g} ({kind}, L={length:.3g} m) · "
            f"y+ target={params.target_yplus} · "
            f"first layer={params.first_layer_height:.3g} m (absolute) · "
            f"{params.n_layers} layers · growth {params.growth_rate:.2f} · "
            f"total thickness={params.total_thickness:.4g} m{note}"
        )
        self._bl_info.setText(msg)
        self.suggestion_completed.emit(f"[bl] {msg}")

    # Fluid density (kg/m^3) for turning wallShearStress into u_tau.
    _FLUID_RHO = {"Air (20°C)": 1.2, "Water (20°C)": 998.0}

    def _bl_target_yplus(self) -> float:
        """Target y+ encoded in the wall-treatment combo ("y+ ≈ 30" / "≈ 1")."""
        text = self._bl_wall_treatment.currentText()
        m = re.search(r"y\+\s*[≈~=]?\s*(\d+(?:\.\d+)?)", text)
        if m:
            return float(m.group(1))
        return 1.0 if "resolved" in text.lower() else 30.0

    def _apply_bl_calibration(self, proposed_h1: float, result=None) -> None:
        """Make the proposed first layer the authoritative ABSOLUTE value.

        Mirrors the physics path: the spin box is only a display of the
        fraction of the CURRENT max cell, while the run uses the absolute
        metres, so a later max-cell change cannot rescale it.
        """
        if not proposed_h1 or proposed_h1 <= 0:
            return
        max_cell = max(self._max_cell.value(), 1e-12)
        fraction = min(
            max(proposed_h1 / max_cell, self._bl_thick.minimum()),
            self._bl_thick.maximum(),
        )
        self._bl_first_layer_abs = float(proposed_h1)
        self._bl_thick_updating = True
        try:
            self._bl_thick.setValue(fraction)
        finally:
            self._bl_thick_updating = False
        if result is not None and getattr(result, "ok", False):
            self.suggestion_completed.emit(
                f"[bl] calibration: mean y+ {result.mean_yplus:.3g} -> "
                f"first layer {proposed_h1:.4g} m"
            )
        self._refresh_bl_hint()

    def auto_analyze(self) -> None:
        """Log the geometry measurements the sizers work from (2026-10-19:
        'we don't have the possibility to understand the measurements').

        Read-only: never changes the cell sizes.  Emits a compact report -
        bbox / cross-section, thickness percentiles, curvature-limited cell,
        suggested max/min cells and the expected cell count for the current
        Detail level - so the user can SEE why a mesh is fine or coarse
        instead of guessing.  Sampling is the same validated ray-cast used
        by the sizer (a few seconds); failures only log a warning.
        """
        if not self._suggest_meshes:
            return
        detail = self.get_detail_level()
        try:
            from polyfoammesh.core.geometry import (
                analyze_local_thickness,
                compute_curvature_sizing,
                compute_bbox_full,
                suggest_cell_sizes,
            )
            dx, dy, dz = compute_bbox_full(self._suggest_meshes)
            analysis = analyze_local_thickness(self._suggest_meshes)
            s_max, s_min = suggest_cell_sizes(
                self._suggest_meshes, detail=detail,
            )
            bbox_max = max(dx, dy, dz)
            curv = compute_curvature_sizing(
                self._suggest_meshes, bbox_max=bbox_max,
            )
            cross = sorted([dx, dy, dz])[1]
            vol = float(sum(float(m.volume) for m in self._suggest_meshes))
            est_cells = int(vol / max(s_max, 1e-12) ** 3) if vol > 0 else 0
            lines = [
                f"[suggest] domain {dx:.4g} x {dy:.4g} x {dz:.4g} m | "
                f"cross-section {cross:.4g} m"
            ]
            if analysis.get("n_samples", 0) > 0:
                lines.append(
                    f"[suggest] wall thickness p10 {analysis['p10'] * 1e3:.4g} mm, "
                    f"p50 {analysis['p50'] * 1e3:.4g} mm "
                    f"({analysis['n_samples']} ray samples)"
                )
            else:
                lines.append("[suggest] thickness sampling failed - bbox fallback")
            if curv is not None:
                lines.append(
                    f"[suggest] curvature-limited cell {curv * 1e3:.4g} mm "
                    "(8 cells per curvature radius)"
                )
            lines.append(
                f"[suggest] detail={detail}: suggested max cell "
                f"{s_max * 1e3:.4g} mm, min cell {s_min * 1e3:.4g} mm, "
                f"bulk estimate ~{est_cells:,} cells"
            )
            for ln in lines:
                self.suggestion_completed.emit(ln)
        except Exception:
            logger.exception("auto geometry analysis failed")
            self.suggestion_completed.emit(
                "[suggest] geometry analysis unavailable for this model"
            )

    def _on_suggest_sizes(self):
        if not self._suggest_meshes:
            QMessageBox.information(self, "No Geometry", "Load a CAD geometry first.")
            return
        detail = self.get_detail_level()
        from polyfoammesh.core.geometry import (
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
        self._refresh_bl_hint()

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
        self._refresh_bl_hint()

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
        """Backward-compatible dict, derived from the ONE BLConfig.

        ``meshDict`` and the autopoly bridge still read these legacy keys;
        the translation now lives in ``BLConfig.legacy_meshdict_dict`` so
        this panel cannot disagree with the poly worker.
        """
        cfg = self.get_bl_config()
        if not cfg.enabled:
            return None
        max_cell = max(float(self._max_cell.value()), 1e-12)
        return cfg.legacy_meshdict_dict(max_cell)

    def get_bl_apply_all(self) -> bool:
        return self._bl_apply_all.isChecked()

    #: Curvature resolution derived from the Detail slider (AUTOMATIC): the
    #: single user control drives both the cell budget and how tightly the
    #: mesh follows curvature - no second knob.
    _DETAIL_SURFACE_RESOLUTION = {
        "very_coarse": "Coarse",
        "coarse": "Coarse",
        "medium": "Medium",
        "fine": "Fine",
        "very_fine": "Very fine",
    }

    def get_surface_resolution(self) -> str:
        """Curvature-following preset derived from the Detail slider.

        The slider already tells the app how fine the user wants the mesh;
        the curvature resolution follows it automatically (very coarse /
        coarse -> Coarse, medium -> Medium, fine / very fine -> Fine / Very
        fine).  Callers pass it to gmsh_wrapper, which scales the curvature
        angular error accordingly.
        """
        try:
            key = str(self.get_detail_level()).lower().replace(" ", "_")
        except Exception:
            key = "medium"
        return self._DETAIL_SURFACE_RESOLUTION.get(key, "Medium")

    def get_reuse_cached_mesh(self) -> bool:
        """Mesh-cache reuse is AUTOMATIC (no user knob): a cached tet mesh
        is reused whenever the sizing inputs are unchanged."""
        return True

    def get_bl_wall_size_floor(self) -> float:
        """Wall cell-size floor (metres) coupling the BL stack to the mesh.

        ``stack / 5`` capped at 10% of the geometry extent, or 0.0 (off) when
        BL is disabled. Returns 0.0 on any failure so meshing never blocks on
        this hint machinery.
        """
        if not self._bl_checkbox.isChecked():
            return 0.0
        try:
            from polyfoammesh.core.gmsh_wrapper import (
                compute_bl_wall_size_floor,
            )
            h1 = self._bl_thick.value() * self._max_cell.value()
            return compute_bl_wall_size_floor(
                h1, self._bl_exp.value(), self._bl_n_layers.value(),
                getattr(self, "_bbox_dim", 0.0),
            )
        except Exception:
            logger.debug("BL wall-size floor unavailable", exc_info=True)
            return 0.0

    def get_poly_conversion(self) -> bool:
        return self._poly_check.isChecked()

    _DETAIL_LABELS = ["Very Coarse", "Coarse", "Medium", "Fine", "Very Fine"]
    _DETAIL_MAP = {0: "very_coarse", 1: "coarse", 2: "medium", 3: "fine", 4: "very_fine"}

    def _slider_to_cells(self, value: int) -> int:
        """Delegate to the one mapping in ``core.mesh_config``."""
        return target_cells_for_slider(value)

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
                f"{name} · cap ~{self._cells_label(cells)} cells"
            )
            if hasattr(self, "_cell_est_label"):
                self._cell_est_label.setText(
                    "Geometric estimate: load a valid geometry"
                )
        else:
            lo, nominal, hi = estimate
            self._detail_label.setText(
                f"{name} · cap ~{self._cells_label(cells)} · "
                f"estimate ~{self._cells_label(nominal)} cells"
            )
            if hasattr(self, "_cell_est_label"):
                self._cell_est_label.setText(
                    f"Geometric estimate: ~{nominal:,} cells "
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
        extents = getattr(self, "_bbox_extents", None)
        if extents and all(v > 0 for v in extents):
            volume = extents[0] * extents[1] * extents[2]
        else:
            volume = bbox ** 3
        cell_size = (volume / max(cells, 1)) ** (1.0 / 3.0)
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
                    from polyfoammesh.core.geometry import suggest_cell_sizes
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
            from polyfoammesh.core.geometry import compute_volume

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
            from polyfoammesh.core.geometry import (
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

    def get_ftetwild_eps_frac(self) -> float:
        """Dynamic surface envelope as a fraction of the cell edge."""
        return (
            float(self._ftet_eps_frac.value()) / 100.0
            if hasattr(self, "_ftet_eps_frac") else 0.0
        )

    def get_ftetwild_adaptive(self) -> bool:
        """Adaptive local sizing (thickness + curvature) for fTetWild."""
        return (
            bool(self._ftet_adaptive.isChecked())
            if hasattr(self, "_ftet_adaptive") else True
        )

    def get_ftetwild_simplify(self) -> bool:
        """fTetWild internal simplification (True = default behaviour)."""
        return (
            bool(self._ftet_simplify.isChecked())
            if hasattr(self, "_ftet_simplify") else True
        )

    def get_target_cells(self) -> int:
        """Target cell count driven by the Mesh Fineness slider."""
        if not hasattr(self, "_detail_slider"):
            return 500_000
        return self._slider_to_cells(self._detail_slider.value())

    def set_current_tab(self, index: int) -> None:
        if hasattr(self, "_tabs") and 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)

    def get_mesher_type(self) -> str:
        # W13: the Advanced Engine selector is authoritative.  The hidden
        # historical buttons remain for compatibility/tests, but cannot
        # silently diverge from the visible choice.
        if hasattr(self, "_engine_combo"):
            return normalize_mesher_key(
                self._engine_combo.currentData(), DEFAULT_MESHER_KEY,
            )
        for btn, key in getattr(self, "_mesher_keys", []):
            if btn.isChecked():
                return key
        return DEFAULT_MESHER_KEY

    def get_adaptive_sizing_enabled(self) -> bool:
        return getattr(self, "_adaptive_sizing_check", None) is not None and self._adaptive_sizing_check.isChecked()

    def get_max_cells_target(self) -> int:
        """Target cell count (polymesh) — driven by the Mesh Fineness slider."""
        return self.get_target_cells()

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
        item = QListWidgetItem("")
        item.setData(Qt.UserRole, entry)
        self._refine_list.addItem(item)
        self._refine_list.setCurrentItem(item)
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
        item = QListWidgetItem("")
        item.setData(Qt.UserRole, entry)
        self._refine_list.addItem(item)
        self._refine_list.setCurrentItem(item)
        self._refresh_refine_placeholder()

    def _on_remove_refinement(self):
        row = self._refine_list.currentRow()
        if row >= 0:
            self._refine_list.takeItem(row)
            self._refresh_refine_placeholder()

    def _refresh_refine_placeholder(self):
        self._refresh_zone_labels()
        has_items = self._refine_list.count() > 0
        self._refine_placeholder.setVisible(not has_items)
        self._refine_list.setVisible(has_items)

    @staticmethod
    def _zone_is_box(data: dict) -> bool:
        """A zone is a box either by explicit type or by carrying extents."""
        return data.get("type") == "box" or "xmin" in data

    @staticmethod
    def _fmt_zone_size(metres: float) -> str:
        """Human-readable size for zone labels (mm, the usual zone scale)."""
        try:
            value = float(metres)
        except (TypeError, ValueError):
            return "?"
        return f"{value * 1000.0:.4g} mm"

    def _refresh_zone_labels(self) -> None:
        """Rewrite each row as a readable, numbered label.

        Only the display text/tooltip changes: the dict stored under
        ``Qt.UserRole`` (the payload returned by ``get_manual_refinements``
        and emitted via ``refinements_changed``) is left untouched.
        """
        box_idx = sphere_idx = 0
        for i in range(self._refine_list.count()):
            item = self._refine_list.item(i)
            data = item.data(Qt.UserRole)
            if not isinstance(data, dict):
                continue
            if self._zone_is_box(data):
                box_idx += 1
                level = int(data.get("level", 2))
                size = data.get("cell_size", 0.0)
                item.setText(
                    f"Box {box_idx} · level {level} · {self._fmt_zone_size(size)}"
                )
                item.setToolTip(
                    f"Box zone {box_idx} — level {level} → cell size "
                    f"{self._fmt_zone_size(size)}.\n"
                    "Drag the arrows on the box in the 3D view to resize it."
                )
            elif "centre" in data:
                sphere_idx += 1
                radius = data.get("radius", 0.0)
                size = data.get("cell_size", 0.0)
                item.setText(
                    f"Sphere {sphere_idx} · radius "
                    f"{self._fmt_zone_size(radius)} · size "
                    f"{self._fmt_zone_size(size)}"
                )
                item.setToolTip(
                    f"Sphere zone {sphere_idx} — radius "
                    f"{self._fmt_zone_size(radius)}, target cell size "
                    f"{self._fmt_zone_size(size)}."
                )

    # --- 3D refinement boxes -------------------------------------------------
    def add_refinement_box(self, box: dict) -> None:
        """Add a box entry to the manual-refinement list."""
        box = dict(box)
        box.setdefault("type", "box")
        item = QListWidgetItem("")
        item.setData(Qt.UserRole, box)
        self._refine_list.addItem(item)
        self._refresh_refine_placeholder()
        self._refine_list.setCurrentItem(item)
        self._on_refine_selection_changed()

    def set_refinement_boxes(self, boxes: list) -> None:
        """Replace the stored box entries after a viewer drag (same order)."""
        box_items = [i for i in range(self._refine_list.count())
                     if isinstance(self._refine_list.item(i).data(Qt.UserRole), dict)
                     and self._refine_list.item(i).data(Qt.UserRole).get("type") == "box"]
        if len(box_items) < len(boxes):
            return
        for idx, box in zip(box_items, boxes):
            item = self._refine_list.item(idx)
            item.setData(Qt.UserRole, dict(box))
        self._refresh_zone_labels()
        # keep the level spin in sync if the selected item was edited
        self._on_refine_selection_changed()

    def selected_refinement_box_index(self) -> int:
        """Index (among boxes) of the currently selected row, or -1."""
        return self._refinement_box_index_of_row(self._refine_list.currentRow())

    def _refinement_box_index_of_row(self, row: int) -> int:
        """0-based index among BOX entries, or -1 when the row is not a box."""
        if row < 0:
            return -1
        item = self._refine_list.item(row)
        data = item.data(Qt.UserRole) if item is not None else None
        if not isinstance(data, dict) or not self._zone_is_box(data):
            return -1
        index = 0
        for i in range(row):
            d = self._refine_list.item(i).data(Qt.UserRole)
            if isinstance(d, dict) and self._zone_is_box(d):
                index += 1
        return index

    def _on_refine_selection_changed(self) -> None:
        item = self._refine_list.currentItem()
        data = item.data(Qt.UserRole) if item is not None else None
        # Tell the viewer which box to highlight (parameterised index among
        # boxes, so sphere rows correctly clear the highlight).
        self.refinement_selection_changed.emit(
            self._refinement_box_index_of_row(self._refine_list.currentRow())
        )
        if not isinstance(data, dict):
            self._refine_level_spin.setEnabled(False)
            self._refine_level_hint.setText("Select a zone to edit its level")
            return
        if self._zone_is_box(data):
            self._refine_level_spin.blockSignals(True)
            self._refine_level_spin.setValue(int(data.get("level", 2)))
            self._refine_level_spin.blockSignals(False)
            self._refine_level_spin.setEnabled(True)
            self._refine_level_hint.setText("1=base 2=half 3=¼ 4=⅛ …")
        else:
            # Sphere zones carry an explicit cell size, not a level.
            self._refine_level_spin.setEnabled(False)
            self._refine_level_hint.setText("Sphere zones use their own cell size")

    def _on_refine_level_changed(self, level: int) -> None:
        item = self._refine_list.currentItem()
        if item is None:
            return
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict) or not self._zone_is_box(data):
            return
        base = float(data.get("base_size") or 0.01)
        from polyfoammesh.core.refinement_boxes import box_cell_size
        data["level"] = int(level)
        data["cell_size"] = box_cell_size(int(level), base)
        item.setData(Qt.UserRole, data)
        self._refresh_zone_labels()
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
        from polyfoammesh.core.geometry import unit_to_scale
        return unit_to_scale(self._unit_selector.currentText())

    def get_patch_names(self) -> list[str]:
        """Names of the currently loaded geometry patches.

        The viewer's patch table is the single editable patch list; this
        panel no longer owns a patch widget (it was redundant). Names are
        derived from the meshes handed to :meth:`set_suggest_meshes`, so
        callers that only need a count (e.g. enabling "Generate Mesh")
        keep working unchanged.
        """
        return [
            m.metadata.get("name", f"patch_{i}")
            for i, m in enumerate(getattr(self, "_suggest_meshes", []) or [])
        ]

    def set_case_dir(self, path: str):
        self._case_dir = path
        self._cad_label.setText(f"Case: {path}")

    def set_meshing_enabled(self, enabled: bool):
        self._btn_run.setEnabled(enabled)

    def cancel_button(self) -> QPushButton:
        """The Cancel control, embedded by the main window below Reset All."""
        return self._btn_cancel

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
        if hasattr(self, "_engine_combo"):
            self._engine_combo.setEnabled(enabled)
        self._parallel_check.setEnabled(enabled)
        self._parallel_cores.setEnabled(enabled and self._parallel_check.isChecked())

    def set_meshing_state(self, running: bool):
        if running:
            # The run button keeps its identity: the dedicated Cancel button
            # below "Reset All" is the single stop control now.  Relabelling
            # this one "Cancel" too (legacy from when no dedicated button
            # existed) showed TWO red Cancel buttons during a run.
            self._btn_run.setText("Generate Mesh")
            self._btn_run.setStyleSheet("")
            self._btn_run.setEnabled(False)
            self._btn_reset.setEnabled(False)
            self._meshing_state = True
        else:
            self._btn_run.setText("Generate Mesh")
            self._btn_run.setStyleSheet("")
            self._btn_run.setEnabled(True)
            self._btn_reset.setEnabled(True)
            self._meshing_state = False

    def trigger_meshing(self):
        self._on_run()

    def _on_openmp_mode_changed(self, text: str) -> None:
        is_custom = "Custom" in text
        self._openmp_spin.setEnabled(is_custom)

    def get_openmp_params(self) -> tuple[str, int | None]:
        text = self._openmp_combo.currentText()
        if "Conservative" in text:
            return ("conservative", None)
        if "Aggressive" in text:
            return ("aggressive", None)
        if "Custom" in text:
            return ("custom", self._openmp_spin.value())
        return ("balanced", None)

    def _on_mesher_button_clicked(self, clicked_btn: QPushButton) -> None:
        """Refresh visual state and keep the Advanced selector in sync."""
        clicked_key = next(
            (k for b, k in self._mesher_keys if b is clicked_btn),
            None,
        )
        # Both the visible CTA and the hidden historical buttons map to
        # their key; every old path stays invocable.  The Advanced selector
        # is the normal way to choose a non-default engine.
        if clicked_key and hasattr(self, "_engine_combo"):
            idx = self._engine_combo.findData(clicked_key)
            if idx >= 0:
                self._engine_combo.blockSignals(True)
                self._engine_combo.setCurrentIndex(idx)
                self._engine_combo.blockSignals(False)
        for b, _k in self._mesher_keys:
            b.setStyleSheet(_MESHER_BTN_ON if b is clicked_btn else _MESHER_BTN_OFF)
        self._on_mesher_changed(self.get_mesher_type())

    def _on_engine_changed(self, *_args) -> None:
        """Drive the hidden legacy buttons and the shared UI rules."""
        if not hasattr(self, "_engine_combo"):
            return
        key = normalize_mesher_key(
            self._engine_combo.currentData(), DEFAULT_MESHER_KEY,
        )
        matched = False
        for btn, btn_key in self._mesher_keys:
            selected = btn_key == key
            btn.setChecked(selected)
            btn.setStyleSheet(
                _MESHER_BTN_ON if selected else _MESHER_BTN_OFF
            )
            matched = matched or selected
        if not matched and getattr(self, "_mesher_keys", None):
            # Keys with no legacy button (bl_on_tets) still belong to the
            # Polymesh CTA; keep that single visible control checked.
            self._mesher_keys[0][0].setChecked(True)
        self._on_mesher_changed(key)

    def _on_mesher_changed(self, key: str) -> None:
        # "Polymesh" (gmsh_direct_poly) forces the poly conversion ON and
        # hides the checkbox (nothing to forget); "FEM Tetra" (gmsh_direct)
        # forces it OFF; "Cartesian cfMesh" shows the manual checkbox,
        # UNCHECKED by default (user request: with cfMesh the conversion
        # must not be ticked out of the box - the hex-dominant mesh is the
        # point there; the user enables the poly dual explicitly if wanted).
        # The experimental fTetWild Polymesh behaves like Polymesh: its
        # whole point is feeding the poly dual from a different tet mesher.
        if key in ("gmsh_direct_poly", "ftetwild_poly", "auto_tet"):
            self._poly_check.setVisible(False)
            self._poly_check.setChecked(True)
        elif key in ("gmsh_direct", "bl_on_tets"):
            # BL on tets deliberately keeps the tet core; it is not a
            # polyDual conversion and must not trigger one downstream.
            self._poly_check.setVisible(False)
            self._poly_check.setChecked(False)
        else:  # cfmesh
            self._poly_check.setVisible(True)
            self._poly_check.setChecked(False)

    def save_params(self, s):
        # W10: one versioned MeshConfig for the whole mesh/project state.
        # The old split keys (detail_slider/bl_checked/bl_config) are no
        # longer written; MeshConfig.from_settings still reads them once.
        s.setValue("params/mesh_config", self.get_mesh_config().to_json())
        s.setValue("params/unit", self._unit_selector.currentText())
        # surface_resolution / reuse_cached_mesh are AUTOMATIC now (derived
        # from the slider / always on): nothing to persist.
        s.setValue("ui/expert_mode", self._expert_mode)

    def restore_params(self, s):
        unit = s.value("params/unit", None)
        if unit is not None:
            self._unit_selector.setCurrentText(unit)
        expert = s.value("ui/expert_mode", None, type=bool)
        if expert is not None:
            self.set_expert_mode(bool(expert))

        # W10: new project key first; the adapter migrates the old
        # detail_slider/bl_config keys if the project predates W10.  When
        # there is no project state at all, keep the W7 behaviour: restore
        # the last-used BL preset (and, if present, the last mesh preset).
        try:
            if MeshConfig.has_settings(s):
                cfg = MeshConfig.from_settings(s)
                self.set_mesh_config(cfg, source=None)
                self._mesh_preset_combo.blockSignals(True)
                try:
                    self._mesh_preset_combo.setCurrentIndex(0)
                finally:
                    self._mesh_preset_combo.blockSignals(False)
            else:
                last_bl = self._preset_store.last_used
                if last_bl:
                    self.apply_bl_preset(last_bl)
                last_mesh = self._mesh_preset_store.last_used
                if last_mesh:
                    self.apply_mesh_preset(last_mesh)
        except Exception as exc:  # noqa: BLE001 - never block startup
            logger.warning("Could not restore mesh config: %s", exc)
            self._mesh_preset_status.setText(
                f"Saved mesh config could not be restored: {exc}"
            )
