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
            "Use 'Compute layers from physics (y+)' to derive it."
        )
        bl_form.addRow("First Layer (× cell):", self._bl_thick)
        self._bl_exp = QDoubleSpinBox()
        self._bl_exp.setRange(1.0, 2.0)
        self._bl_exp.setValue(1.2)
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

        # 2026-10-06: ONE robust-BL flag bundles the two measured fixes
        # (benchmark combo: poly 9/10 vs snappy 6/10). The edge filter drops
        # sliver-EDGE base faces (0.9 x median) so the per-vertex inversion
        # clamp stops capping every stack at the smallest sliver edge, and
        # the budget termination reduces the layer count at FULL size instead
        # of squeezing pancake stacks. Opt-in, unchecked by default: on
        # geometries with many sliver faces ~20% of wall AREA loses its
        # prisms, so it stays a user choice, never a default. The concave
        # closure checkbox above is a DIFFERENT mechanism (per-vertex
        # exclusions) and stays separate on purpose.
        self._bl_robust = QCheckBox("Robust boundary layer (recommended, experimental)")
        self._bl_robust.setToolTip(
            "Enables the two boundary-layer fixes measured on the benchmark "
            "(poly 9/10 vs snappy 6/10):\n"
            "• sliver-face filter: excludes wall faces with a tiny edge "
            "(< 0.9 x median) from extrusion, which otherwise cap the height "
            "of the WHOLE stack (0.5*min_edge clamp) and produce squashed, "
            "non-orthogonal prisms up to 146°;\n"
            "• budget termination: where space is insufficient, reduces the "
            "NUMBER of layers at full size instead of compressing every layer "
            "into a squashed stack.\n"
            "Measured on a real checkMesh: cube_duct 80.6→58.9 (PASS), "
            "venturi 146→74, concave 105→66.8 (PASS). Cost: on geometries "
            "with many sliver faces ~20% of the wall AREA is left without "
            "prisms."
        )
        bl_form.addRow(self._bl_robust)

        # Physical inputs: the layer parameters above are derived from these
        # rather than guessed, which is how commercial meshers do it.
        self._bl_velocity = QDoubleSpinBox()
        self._bl_velocity.setRange(0.001, 1000.0)
        self._bl_velocity.setValue(10.0)
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

        # BL <-> mesh sizing consistency hint (upgrade A). Compares the
        # requested BL stack with the mesh's estimated wall cell size and warns
        # when the stack exceeds the ~0.34 * wall-cell inversion budget (beyond
        # that the first layer gets compressed). Live: refreshes on any BL
        # parameter or cell-size change.
        self._bl_stack_hint = QLabel("")
        self._bl_stack_hint.setWordWrap(True)
        self._bl_stack_hint.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        bl_form.addRow(self._bl_stack_hint)

        self._bl_form_widget = QWidget()
        self._bl_form_widget.setLayout(bl_form)
        self._bl_form_widget.setVisible(False)
        bl_layout.addWidget(self._bl_form_widget)
        # Live BL consistency hint: any input that changes the stack (h1, ratio,
        # nLayers) or the wall-cell estimate (derived Min Cell Size, surface
        # resolution) refreshes it.
        self._bl_n_layers.valueChanged.connect(self._refresh_bl_hint)
        self._bl_thick.valueChanged.connect(self._refresh_bl_hint)
        self._bl_exp.valueChanged.connect(self._refresh_bl_hint)
        self._bl_checkbox.toggled.connect(self._refresh_bl_hint)
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

        # Platform-conditional WSL/OpenFOAM wording (see helper): on
        # Windows OpenFOAM runs in WSL2; on Linux it is native and
        # resolved via PATH. Windows text kept byte-identical.
        _w = _platform_mesher_wording()

        _add_mesher_btn(
            f"Cartesian cfMesh  ({_w['of_req']})",
            "cfmesh",
            f"cartesianMesh via {_w['of_via']} — hex-dominant, the highest "
            f"quality. REQUIRES {_w['of_via']} installed.\n"
            "Optional: enable 'Convert to polyhedral mesh' (Advanced) to\n"
            "convert the hex-dominant mesh to polyhedral (polyDualMesh).\n"
            "The dual of a cartesian mesh stays very regular/grid-like\n"
            "(boundary cells ~99% quadrilaterals).",
        )
        # Polymesh is the DEFAULT path (user request: the poly route is the
        # product's core, cfMesh is the optional alternative).
        _add_mesher_btn(
            f"Polymesh  (poly, {_w['no_of_label']})",
            "gmsh_direct_poly",
            "Polyhedral mesh from our Polymesh mesher: barycentric-dual\n"
            "rebuild — 100% polyhedral coverage, ~5.5x lower cell count\n"
            f"(STAR-CCM+ style). Ideal for CFD. {_w['no_of_sentence']}\n"
            "Works best with real STEP/CAD input.",
            checked=True,
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
        # Sync the "Convert to polyhedral mesh" checkbox to the mesher
        # button selected at startup (default cfMesh -> checkbox visible).
        self._on_mesher_changed(self.get_mesher_type())

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
            and not getattr(self, "_bl_user_manual", False)
            and self._suggest_meshes
        ):
            self._on_bl_auto_compute()

    def _estimated_wall_cell_size(self) -> float:
        """Best available proxy for the automatic mesh's wall cell size.

        The derived Min Cell Size is the finest size the current Mesh Fineness
        / geometry estimate will place (curvature/small-feature floor), which
        is exactly what the boundary-layer stack has to fit inside.
        """
        try:
            return max(float(self._min_cell.value()), 1e-12)
        except (AttributeError, ValueError):
            return 1e-12

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
        self._refresh_bl_hint()

    def _on_bl_physics_inputs_changed(self, *_args) -> None:
        """Recompute silently when the physics inputs change after a compute."""
        if getattr(self, "_bl_first_layer_abs", None) is None:
            return
        if not self._suggest_meshes:
            return
        self._on_bl_auto_compute()

    def _refresh_bl_hint(self) -> None:
        if not hasattr(self, "_bl_stack_hint"):
            return
        try:
            from polyfoammesh.core.gmsh_wrapper import compute_bl_stack
        except Exception:
            return
        h1 = self._bl_first_layer()
        stack = compute_bl_stack(h1, self._bl_exp.value(), self._bl_n_layers.value())
        y = self._estimated_wall_cell_size()
        # Inversion budget: the stack may take at most ~0.34 of the local wall
        # cell edge before the first layer is squeezed.
        max_stack = 0.34 * y
        stack_mm, y_mm, budget_mm = stack * 1000.0, y * 1000.0, max_stack * 1000.0
        if not self._bl_checkbox.isChecked():
            self._bl_stack_hint.setText(
                f"BL stack {stack_mm:.4g} mm; estimated mesh wall cells "
                f"~{y_mm:.4g} mm (enable BL to check fit)."
            )
        elif stack <= max_stack:
            self._bl_stack_hint.setText(
                f"BL stack {stack_mm:.4g} mm; mesh wall cells ~{y_mm:.4g} mm "
                f"-> fits (inversion budget ~{budget_mm:.4g} mm)."
            )
        else:
            compressed = max_stack / stack * 100.0
            self._bl_stack_hint.setText(
                f"BL stack {stack_mm:.4g} mm; mesh wall cells ~{y_mm:.4g} mm "
                f"-> the engine compresses the stack to ~{compressed:.0f}% "
                f"(inversion budget ~{budget_mm:.4g} mm)."
            )

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
            params = BLEngine().calculate_from_flow(flow, flow_type=flow_type)
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
        if not self._bl_checkbox.isChecked():
            return None
        # Contract: thicknessRatio = growth ratio, firstLayerThickness = metres.
        # The "First Layer" field is a FRACTION of the max cell size, so convert
        # it to an absolute thickness here — sending the fraction as
        # thicknessRatio (the old behaviour) made cfMesh collapse the layers.
        return {
            "nLayers": self._bl_n_layers.value(),
            "thicknessRatio": self._bl_exp.value(),
            "firstLayerThickness": self._bl_first_layer(),
            # poly path (barycentric dual): checked -> BL on every boundary
            # patch; unchecked (default) -> wall-typed / wall-named patches
            # only (G1 fix, FASE 1)
            "applyToAll": self._bl_apply_all.isChecked(),
            # concave closure is now automatic (worker fallback chain), so
            # no 'concaveClosure' key is emitted any more.
            # 2026-10-06 BL quality fixes (poly path, opt-in, default off):
            # ONE flag bundles the sliver-edge base-face filter (0.9) and the
            # budget termination; 0.0/False reproduce the historical path.
            "minBaseEdgeFraction": (
                0.9 if self._bl_robust.isChecked() else 0.0
            ),
            "terminateByBudget": self._bl_robust.isChecked(),
        }

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

    # Cell-count scale for the Mesh Fineness slider (log: 10K .. 20M cells).
    _CELL_TARGET_MIN = 10_000
    _CELL_TARGET_MAX = 20_000_000
    _CELL_TARGET_BUCKETS = 20

    _DETAIL_LABELS = ["Very Coarse", "Coarse", "Medium", "Fine", "Very Fine"]
    _DETAIL_MAP = {0: "very_coarse", 1: "coarse", 2: "medium", 3: "fine", 4: "very_fine"}

    def _slider_to_cells(self, value: int) -> int:
        """Map slider position 0..20 to a target cell count (log 10K..20M)."""
        t = max(0, min(self._CELL_TARGET_BUCKETS, int(value))) / self._CELL_TARGET_BUCKETS
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
        if "Conservative" in text:
            return ("conservative", None)
        if "Aggressive" in text:
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
        # surface_resolution / reuse_cached_mesh are AUTOMATIC now (derived
        # from the slider / always on): nothing to persist.
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
