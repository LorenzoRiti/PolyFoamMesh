from __future__ import annotations

import logging
import contextlib
from datetime import datetime
from pathlib import Path

import os
import sys
import cadquery as cq
import trimesh

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QFileDialog, QMessageBox, QInputDialog, QLabel, QProgressBar,
    QApplication, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem,
    QDockWidget, QFrame, QSizePolicy, QDialog,
)
from PySide6.QtCore import Qt, Slot, QThread, QSize, QMetaObject, Q_ARG
from PySide6.QtGui import (
    QShortcut, QGuiApplication, QKeySequence, QUndoStack, QIcon, QPainter,
    QPixmap, QColor, QFont,
)

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.geometry import (
    load_step, load_geometry, classify_faces, tessellate_patches, create_test_cylinder,
    compute_bbox_dim, compute_bbox_full, validate_cell_sizes,
    compute_volume, estimate_cell_count, suggest_cell_sizes,
    compute_patch_cell_sizes,
    scale_meshes, unit_to_scale,
)
from cfmesh_autogui.core.gmsh_wrapper import (
    compute_sizing as gmsh_compute_sizing,
    generate_surface_stl as gmsh_generate_surface_stl,
    generate_volume_mesh as gmsh_generate_volume_mesh,
    gmsh_shutdown,
    from_step as gmsh_read_step,
)
from cfmesh_autogui.core.mesh_converter import msh_to_of_polymesh
from cfmesh_autogui.core.stl_writer import export_surface_file
from cfmesh_autogui.core.meshdict_gen import write_meshdict
from cfmesh_autogui.core.openfoam_runner import (
    RetryRunner, CheckMeshWorker, PolyDualWorker, QualityFixWorker,
    ParallelMeshWorker, DecomposeParWorker, WslCheckWorker,
    analyze_error, ErrorType,
)
from cfmesh_autogui.core.feature_detector import FeatureDetectWorker
from cfmesh_autogui.core.boundary_reader import parse_boundary
from cfmesh_autogui.core.case_setup import setup_case
from cfmesh_autogui.core.workflow import MeshingWorkflow, Step, Status
from cfmesh_autogui.gui.quality_panel import QualityPanel
from cfmesh_autogui.gui.viewer_widget import ViewerWidget
from cfmesh_autogui.gui.params_panel import ParamsPanel
from cfmesh_autogui.gui.log_panel import LogPanel
from cfmesh_autogui.gui.style import COLOR_TEXT_DISABLED, COLOR_DANGER, COLOR_PASS
from cfmesh_autogui.gui.constants import MAX_STEP_FILE_BYTES, MAX_RECENT_STEP_FILES
from cfmesh_autogui.gui.log_tags import Tag
from cfmesh_autogui.gui.design_tokens import ORANGE_500, ORANGE_600, ORANGE_400, APP_VERSION, STATUS_READY
from cfmesh_autogui.octopoda_local import octo
from cfmesh_autogui.gui.settings_migration import AppSettings

logger = logging.getLogger(__name__)


def _is_wall_patch(name: str) -> bool:
    """Heuristic: a patch is a wall if its name does NOT match known
    non-wall boundary types.

    Handles Italian names (ingresso/uscita), mixed case (Inlet, WALL_1),
    numeric suffixes (wall_1, inlet-02), and dotted prefixes (patch.wall).
    Anything that doesn't match a known non-wall keyword is treated as wall
    (body, fixed, blade, casing, housing, etc.).
    """
    name_lower = name.lower().replace("-", "_").replace(".", "_")
    # Strip trailing numeric suffixes: wall_1, wall_02, wall-003
    name_stripped = name_lower.rstrip("_0123456789")
    non_wall_keywords = (
        "inlet", "outlet", "ingresso", "uscita", "entrance", "exit",
        "symmetry", "opening", "farfield",
        "empty", "porous", "interface",
        "periodic", "cyclic", "freestream",
        "pressure", "velocity",
    )
    if any(kw in name_stripped for kw in non_wall_keywords):
        return False
    return True


def _check_drive_writable(drive: str) -> bool:
    """Check if a drive root (e.g. 'C:\\') is writable."""
    import os as _os
    try:
        test_path = _os.path.join(drive, ".cfmesh_write_test")
        with open(test_path, "w") as _f:
            _f.write("test")
        _os.remove(test_path)
        return True
    except (OSError, PermissionError):
        return False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CFMesh-AutoGUI")
        self.resize(1400, 900)

        self._of_config = OFConfig()
        self._meshes: list[trimesh.Trimesh] = []  # ✅ F-009
        self._original_shape: cq.Shape | None = None
        self._case_dir: Path | None = None
        self._runner = RetryRunner(self._of_config)
        self._run_id = 0
        self._quality_fix_attempts = 0
        self._poly_was_converted = False
        self._unscaled_meshes: list[trimesh.Trimesh] = []
        self._scaled_meshes: list[trimesh.Trimesh] | None = None
        self._current_scale: float = 1.0
        octo.log_event("main_window", "init", f"CFMesh-AutoGUI v{APP_VERSION} started")

        self._setup_ribbon()
        self._setup_ui()
        self._setup_menu()
        self._params.set_undo_stack(self._undo_stack)
        self._setup_status()
        self._setup_workflow_tree()
        self._restore_settings()

        self._params.unit_changed.connect(self._on_unit_changed)
        self._params.cancel_meshing.connect(self._on_cancel_meshing)
        self._params.suggestion_completed.connect(
            lambda msg: self._log.append_log(msg)
        )
        self._viewer.distance_measured.connect(
            lambda msg: self._log.append_log(f"[measure] {msg}")
        )

        if not self._of_config.validate():
            logger.warning("OpenFOAM not detected via WSL2.")
            self._log.append_log(f"{Tag.WARN} OpenFOAM not detected via WSL.")
            self._log.append_log(f"{Tag.WARN} Ensure WSL2 + OpenFOAM v2512 are installed.")
            self._status.showMessage("OpenFOAM not found — meshing disabled")
        else:
            logger.info("OpenFOAM v2512 detected via WSL2.")
            self._status.showMessage("Ready — OpenFOAM v2512 via WSL2")

        from cfmesh_autogui.core.disk_cleanup import auto_cleanup
        try:
            auto_cleanup(keep_last=20, max_days=60)
        except Exception as exc:
            logger.debug("Startup disk cleanup skipped: %s", exc)

    def _show_shortcuts(self):
        QMessageBox.information(
            self,
            "Keyboard Shortcuts",
            "Ctrl+O   Load Geometry (STEP or STL)\n"
            "Ctrl+R   Generate mesh (or Cancel if running)\n"
            "Ctrl+N   Reset all\n"
            "Ctrl+Q   Exit\n\n"
            "Drag & Drop  .step / .stp / .stl onto the window to load\n"
            "Viewer > Select Patch  click a face to rename the patch",
        )

    def _setup_menu(self):
        fm = self.menuBar().addMenu("&File")
        load_geom_action = fm.addAction("Load Geometry...\tCtrl+O", self._on_load_step)
        load_geom_action.setToolTip(
            "Load or replace the geometry in the current case, keeping "
            "your existing mesh settings. To start a brand-new project "
            "with guided setup, use New Case in the ribbon instead."
        )
        self._recent_menu = fm.addMenu("Recent Geometry Files")
        self._refresh_recent_menu()
        fm.addSeparator()
        export_menu = fm.addMenu("Export Mesh")
        export_menu.addAction("VTU (.vtu)...", lambda: self._on_export_mesh("vtu"))
        export_menu.addAction("CGNS (.cgns)...", lambda: self._on_export_mesh("cgns"))
        export_menu.addAction("SU2 (.su2)...", lambda: self._on_export_mesh("su2"))
        export_menu.addAction("GMSH (.msh)...", lambda: self._on_export_mesh("gmsh"))
        export_menu.addAction("Abaqus (.inp)...", lambda: self._on_export_mesh("abaqus"))
        export_menu.addSeparator()
        export_menu.addAction("BaramFlow Case Folder...", self._on_export_baramflow)
        export_menu.addAction("PDF Quality Report...", self._on_export_pdf)
        fm.addSeparator()
        fm.addAction("Set Case Directory...", self._on_set_case_dir)
        fm.addSeparator()
        fm.addAction("Exit\tCtrl+Q", self.close)

        self._undo_stack = QUndoStack(self)
        edit_menu = self.menuBar().addMenu("&Edit")
        undo_action = self._undo_stack.createUndoAction(self, "&Undo")
        redo_action = self._undo_stack.createRedoAction(self, "&Redo")
        undo_action.setShortcut(QKeySequence.Undo)
        redo_action.setShortcut(QKeySequence.Redo)
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)

        QShortcut("Ctrl+R", self, activated=self._params.trigger_meshing)
        QShortcut("Ctrl+N", self, activated=self._on_reset)
        QShortcut("Ctrl+M", self, activated=self._on_quick_mesh)
        QShortcut("Ctrl+Q", self, activated=self.close)

        hm = self.menuBar().addMenu("&Help")
        hm.addAction("Keyboard Shortcuts...", self._show_shortcuts)
        hm.addSeparator()
        hm.addAction("About CFMesh-AutoGUI...", self._show_about)

        vm = self.menuBar().addMenu("&View")
        theme_menu = vm.addMenu("Theme")
        self._theme_light_action = theme_menu.addAction("Light")
        self._theme_dark_action = theme_menu.addAction("Dark")
        self._theme_system_action = theme_menu.addAction("System (follow OS)")
        self._theme_light_action.setCheckable(True)
        self._theme_dark_action.setCheckable(True)
        self._theme_system_action.setCheckable(True)
        self._theme_light_action.triggered.connect(lambda: self._on_theme_change("light"))
        self._theme_dark_action.triggered.connect(lambda: self._on_theme_change("dark"))
        self._theme_system_action.triggered.connect(lambda: self._on_theme_change("system"))
        self._sync_theme_menu()

        tm = self.menuBar().addMenu("&Tools")
        tm.addAction("Launch ParaView", self._on_launch_paraview)
        tm.addSeparator()
        # A synthetic geometry generator for trying the app without a real
        # CAD file — it used to sit in the File menu right next to "Load
        # Geometry...", which read as a second, competing way to start a
        # real project rather than what it actually is: a demo/sample.
        tm.addAction("Load Sample Cylinder (demo geometry)", self._on_test_cylinder)
        tm.addSeparator()
        tm.addAction("Clean Up Old Case Directories...", self._on_cleanup_cases)

    def _on_theme_change(self, mode: str) -> None:
        from cfmesh_autogui.gui.theme import set_theme_mode, apply_theme
        set_theme_mode(mode)
        apply_theme(QApplication.instance())
        s = AppSettings()
        s.set_value("ui/theme_mode", mode)
        self._sync_theme_menu()
        self._status.showMessage(f"Theme: {mode}", 3000)

    def _sync_theme_menu(self) -> None:
        from cfmesh_autogui.gui.theme import current_request
        mode = current_request()
        self._theme_light_action.setChecked(mode == "light")
        self._theme_dark_action.setChecked(mode == "dark")
        self._theme_system_action.setChecked(mode == "system")

    def _show_about(self) -> None:
        from cfmesh_autogui.gui.about_dialog import AboutDialog
        AboutDialog(self).exec()

    # ------------------------------------------------------------------
    # ANSYS Ribbon Toolbar
    # ------------------------------------------------------------------
    def _setup_ribbon(self):
        ribbon = QToolBar("Ribbon", self)
        ribbon.setObjectName("ribbon_toolbar")
        ribbon.setMovable(False)
        ribbon.setIconSize(QSize(16, 16))
        ribbon.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        ribbon.setFloatable(False)

        def _make_ribbon_btn(text: str, icon_name: str, slot, checkable: bool = True) -> QToolButton:
            btn = QToolButton()
            btn.setText(text)
            from cfmesh_autogui.gui.branding import get_ribbon_icon
            icon = get_ribbon_icon(icon_name)
            if not icon.isNull():
                btn.setIcon(icon)
                btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            else:
                btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
            btn.setMinimumHeight(28)
            btn.setMinimumWidth(60)
            btn.setCheckable(checkable)
            if checkable:
                btn.setAutoExclusive(True)
            if slot:
                btn.clicked.connect(slot)
            ribbon.addWidget(btn)
            return btn

        # The left Workflow dock (see _setup_workflow_tree) already provides
        # step-by-step navigation with progress/lock state — a second,
        # independent set of tab-switching buttons here (Home/Geometry/
        # Mesh/Advanced/Quality), mirroring the right panel's own QTabWidget
        # tabs, was a third parallel navigation surface for the exact same
        # 4-5 stages, and none of the three reliably stayed in sync with
        # each other. Keeping only the ribbon's action buttons (not
        # navigation) removes that duplication instead of syncing it.
        self._ribbon_btns = {}
        self._ribbon_btns["new"] = _make_ribbon_btn("New Case", "home", self._on_new_case_wizard, checkable=False)
        self._ribbon_btns["new"].setToolTip(
            "Recommended starting point: guided setup that walks you "
            "through loading geometry and choosing initial mesh settings "
            "in one flow. Use File > Load Geometry instead if you just "
            "want to swap the geometry in an existing case."
        )
        self._ribbon_btns["template"] = _make_ribbon_btn("From Template", "template", self._on_template_selector, checkable=False)
        self._ribbon_btns["template"].setToolTip(
            "Start from a predefined case template (internal flow, "
            "external aero, CHT, etc.) with pre-configured settings."
        )
        self._ribbon_btns["quick"] = _make_ribbon_btn("Quick Mesh", "quick", self._on_quick_mesh, checkable=False)
        qm = self._ribbon_btns["quick"]
        qm.setCheckable(False)
        qm.setAutoExclusive(False)
        qm.setStyleSheet(
            f"QToolButton {{ background:{ORANGE_500}; color:white; border:1px solid {ORANGE_600}; "
            "border-radius:4px; padding:4px 16px; font-weight:700; }"
            f"QToolButton:hover {{ background:{ORANGE_400}; }}"
        )
        # The Mesh tab's "Generate Mesh" button already turns into "Cancel"
        # while a run is active, but that button lives on one specific tab
        # — if meshing was started from the ribbon's Quick Mesh while
        # looking at a different tab, there was no visible way to stop it.
        # This mirrors that same cancel action in the ribbon itself, always
        # reachable regardless of which tab is showing.
        self._ribbon_btns["cancel"] = _make_ribbon_btn(
            "Cancel", "cancel", self._on_cancel_meshing, checkable=False,
        )
        cancel_btn = self._ribbon_btns["cancel"]
        cancel_btn.setAutoExclusive(False)
        cancel_btn.setToolTip("Stop the meshing run in progress.")
        cancel_btn.setVisible(False)
        cancel_btn.setStyleSheet(
            f"QToolButton {{ background:{COLOR_DANGER}; color:white; "
            f"border:1px solid {COLOR_DANGER}; border-radius:4px; padding:4px 16px; "
            "font-weight:700; }"
        )
        ribbon.addSeparator()
        # Simple by default (just cell sizes + Generate Mesh); toggling
        # this reveals boundary layers, mesher choice, and the
        # parallel/polyhedral Advanced tab for users who want to tune them.
        self._ribbon_btns["expert"] = _make_ribbon_btn(
            "Advanced Mode", "advanced", self._on_toggle_expert_mode,
        )
        expert_btn = self._ribbon_btns["expert"]
        expert_btn.setAutoExclusive(False)
        expert_btn.setToolTip(
            "Off: just cell sizes and Generate Mesh — enough for most cases.\n"
            "On: also shows boundary layers, mesher choice, and multi-core/"
            "polyhedral options."
        )
        ribbon.addSeparator()
        self._ribbon_btns["bc"] = _make_ribbon_btn(
            "BC Editor", "bc", self._on_bc_editor, checkable=False,
        )
        self._ribbon_btns["bc"].setToolTip("Edit boundary conditions and export 0/ fields.")
        self._ribbon_btns["bc"].setAutoExclusive(False)

        self._ribbon_btns["solver"] = _make_ribbon_btn(
            "Solver Setup", "solver", self._on_solver_setup, checkable=False,
        )
        self._ribbon_btns["solver"].setToolTip("Configure solver, schemes, and turbulence model.")
        self._ribbon_btns["solver"].setAutoExclusive(False)

        self._ribbon_btns["fullauto"] = _make_ribbon_btn(
            "Full Auto", "run", self._on_full_auto, checkable=False,
        )
        fullauto = self._ribbon_btns["fullauto"]
        fullauto.setAutoExclusive(False)
        fullauto.setStyleSheet(
            f"QToolButton {{ background:{COLOR_PASS}; color:white; border:1px solid green; "
            "border-radius:4px; padding:4px 16px; font-weight:700; }"
            f"QToolButton:hover {{ background:{STATUS_READY}; }}"
        )
        fullauto.setToolTip(
            "Geometry -> Mesh -> BC -> Solver Setup -> Run — all in one click."
        )
        self.addToolBar(Qt.TopToolBarArea, ribbon)
        self._ribbon = ribbon

    def _on_toggle_expert_mode(self, checked: bool) -> None:
        self._params.set_expert_mode(checked)
        self._status.showMessage(
            "Advanced mode on" if checked else "Simple mode — advanced options hidden",
            3000,
        )

    # ------------------------------------------------------------------
    # ANSYS Workflow Tree (Project Schematic-style)
    # ------------------------------------------------------------------
    def _setup_workflow_tree(self):
        dock = QDockWidget("Workflow", self)
        dock.setObjectName("workflow_dock")
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setIndentation(12)
        tree.setRootIsDecorated(False)
        tree.setAnimated(True)
        tree.setFrameShape(QFrame.NoFrame)
        tree.setMinimumWidth(160)
        tree.itemClicked.connect(self._on_workflow_item_clicked)
        self._workflow_tree = tree

        def _wf_item(text: str, status: str = "pending") -> QTreeWidgetItem:
            item = QTreeWidgetItem([text])
            status_map = {"ready": "✅", "active": "▶", "pending": "○", "error": "✗", "done": "✓"}
            icon_text = status_map.get(status, "○")
            item.setText(0, f"{icon_text}  {text}")
            item.setData(0, Qt.UserRole, {"status": status, "stage": text.lower().replace(" ", "_")})
            return item

        self._wf_items = {}
        self._wf_items["geometry"] = _wf_item("Geometry", "active")
        self._wf_items["mesh"] = _wf_item("Mesh Settings", "pending")
        self._wf_items["advanced"] = _wf_item("Advanced", "pending")
        self._wf_items["generate"] = _wf_item("Generate Mesh", "pending")
        self._wf_items["quality"] = _wf_item("Quality Check", "pending")
        for k in ["geometry", "mesh", "advanced", "generate", "quality"]:
            tree.addTopLevelItem(self._wf_items[k])

        # Guided-workflow brain + a live "what to do next" hint. The hint is the
        # heart of "senza impazzimenti": the user always sees the single next
        # action, and never has to guess which button is safe to press.
        self._workflow = MeshingWorkflow()
        self._wf_hint = QLabel()
        self._wf_hint.setWordWrap(True)
        self._wf_hint.setStyleSheet("padding: 6px 8px; font-size: 11px;")
        self._wf_hint.setMinimumWidth(180)

        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(tree)
        v.addWidget(QLabel("Next step:"))
        v.addWidget(self._wf_hint)
        v.addStretch()

        dock.setWidget(container)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self._wf_dock = dock
        self._refresh_workflow()

    # Map workflow steps to the existing tree items and statuses to icons.
    _WF_STEP_ITEMS = {
        Step.GEOMETRY: "geometry",
        Step.SIZING: "mesh",
        Step.BOUNDARY_LAYERS: "advanced",
        Step.GENERATE: "generate",
        Step.QUALITY: "quality",
    }
    _WF_STATUS_ICON = {
        Status.LOCKED: "🔒",
        Status.READY: "▶",
        Status.DONE: "✓",
        Status.WARNING: "⚠",
    }

    def _refresh_workflow(self, **flags) -> None:
        """Update the workflow model from *flags* and repaint the dock.

        Kept defensive: any missing widget just means the dock isn't built yet.
        """
        wf = getattr(self, "_workflow", None)
        if wf is None:
            return
        for key, value in flags.items():
            if hasattr(wf, key):
                setattr(wf, key, value)

        for step, item_key in self._WF_STEP_ITEMS.items():
            item = getattr(self, "_wf_items", {}).get(item_key)
            if item is None:
                continue
            icon = self._WF_STATUS_ICON.get(wf.status_of(step), "○")
            label = item.text(0)[3:] if len(item.text(0)) > 3 else item.text(0)
            item.setText(0, f"{icon}  {label}")

        if getattr(self, "_wf_hint", None) is not None:
            done, total = wf.progress()
            self._wf_hint.setText(f"[{done}/{total}]  {wf.next_action()}")

    def _on_workflow_item_clicked(self, item: QTreeWidgetItem, col: int):
        stage = item.data(0, Qt.UserRole).get("stage", "")
        tab_map = {"geometry": 0, "mesh_settings": 1, "advanced": 2, "generate_mesh": 1, "quality_check": 3}
        idx = tab_map.get(stage, 0)
        self._params.set_current_tab(idx)

    def _set_workflow_stage(self, stage: str, status: str):
        if stage in self._wf_items:
            item = self._wf_items[stage]
            status_map = {"ready": "✅", "active": "▶", "pending": "○", "error": "✗", "done": "✓"}
            icon_text = status_map.get(status, "○")
            item.setText(0, f"{icon_text}  {item.text(0)[3:]}")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        h_splitter = QSplitter(Qt.Horizontal)
        # Without this, dragging a pane's edge past its content's natural
        # size snaps it to fully collapsed instead of stopping at a sane
        # minimum — the most common cause of a splitter feeling "broken".
        h_splitter.setChildrenCollapsible(False)

        self._viewer = ViewerWidget()
        self._viewer.face_picked.connect(self._on_face_picked)
        h_splitter.addWidget(self._viewer)

        right = QWidget()
        right.setMinimumWidth(260)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)

        self._params = ParamsPanel()
        self._params.run_meshing.connect(self._on_run_meshing)
        self._params.reset_all.connect(self._on_reset)
        self._params.load_step_requested.connect(self._on_load_step)
        rl.addWidget(self._params)

        self._cell_count_label = QLabel("Cells: --")
        self._cell_count_label.setStyleSheet(
            f"color: {COLOR_TEXT_DISABLED}; padding-right: 8px; font-weight: bold;"
        )

        self._checkmesh_thread: QThread | None = None
        self._checkmesh_worker: CheckMeshWorker | None = None

        h_splitter.addWidget(right)
        h_splitter.setSizes([900, 400])

        self._quality = QualityPanel()
        self._quality.fix_requested.connect(self._on_auto_fix_quality)

        self._log = LogPanel()
        self._log.setMinimumHeight(60)
        log_holder = QWidget()
        log_holder.setMinimumHeight(100)
        log_layout = QVBoxLayout(log_holder)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)
        log_layout.addWidget(self._quality)
        log_layout.addWidget(self._log, 1)

        v_splitter = QSplitter(Qt.Vertical)
        # See h_splitter above: without this the console pane can snap shut
        # instead of resizing smoothly when dragged near its minimum.
        v_splitter.setChildrenCollapsible(False)
        v_splitter.addWidget(h_splitter)
        v_splitter.addWidget(log_holder)
        v_splitter.setStretchFactor(0, 4)
        v_splitter.setStretchFactor(1, 1)
        v_splitter.setSizes([700, 200])

        main_layout.addWidget(v_splitter)

    def _setup_status(self):
        self._status = self.statusBar()
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedWidth(140)
        self._progress.setVisible(False)
        self._status.addPermanentWidget(self._progress)
        self._status.addPermanentWidget(self._cell_count_label)

    @contextlib.contextmanager
    def _busy(self, disable_run: bool = True):
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        if disable_run and hasattr(self._params, "_btn_run"):
            self._params._btn_run.setEnabled(False)
        try:
            yield
        finally:
            QGuiApplication.restoreOverrideCursor()
            self._progress.setRange(0, 100)
            self._progress.setVisible(False)
            if disable_run and hasattr(self._params, "_btn_run"):
                # Re-derive from the current patch list rather than
                # restoring a snapshot taken before this block ran: geometry
                # loading happens *inside* this context and calls
                # set_patches(), which is what should actually decide
                # whether "Generate Mesh" is enabled. Restoring a stale
                # pre-load snapshot (always False before geometry existed)
                # silently clobbered that back to disabled every time —
                # geometry loaded fine, patches showed up, "Ready to mesh"
                # displayed, but the button just sat there dead.
                self._params._btn_run.setEnabled(
                    bool(self._params.get_patch_names())
                )

    @staticmethod
    def _settings() -> AppSettings:
        return AppSettings()

    def _restore_settings(self):
        s = self._settings()
        size = s.get_value("window/size")
        pos = s.get_value("window/pos")
        if size is not None:
            self.resize(size)
        if pos is not None:
            self.move(pos)
        self._viewer.restore_background(s.raw)
        self._params.restore_params(s.raw)
        self._ribbon_btns["expert"].setChecked(self._params.is_expert_mode())

    def _load_geometry(self, shape: cq.Shape):
        self._original_shape = shape
        self._set_workflow_stage("geometry", "done")
        self._set_workflow_stage("mesh", "active")
        self._status.showMessage("Classifying faces...")
        QApplication.processEvents()
        n = len(list(shape.Faces()))
        self._log.append_log(f"[geom] Loaded: {n} faces, type: {shape.geomType()}")

        patches = classify_faces(shape)
        self._log.append_log(f"{Tag.GEOM} Patches: {[(n, len(f)) for n, f in patches]}")
        try:
            self._status.showMessage("Tessellating geometry (this may take a moment)...")
            QApplication.processEvents()
            self._unscaled_meshes = tessellate_patches(patches)
        except RuntimeError as e:
            logger.error("Tessellation error: %s", e)
            self._log.append_log(f"{Tag.ERROR} {e}")
            QMessageBox.critical(self, "Tessellation Error", str(e))
            return

        self._heal_geometry(self._unscaled_meshes)

        self._rebuild_scaled_meshes()

        names = [m.metadata.get("name", "?") for m in self._meshes]
        logger.info("Patches: %s", names)
        self._log.append_log(f"{Tag.GEOM} Patches: {', '.join(names)}")
        self._params.set_patches(names)
        self._params.set_suggest_meshes(self._meshes)
        self._viewer.show_cad(self._meshes)

        dx, dy, dz = compute_bbox_full(self._meshes)
        self._params.set_bbox(dx, dy, dz)
        logger.info("Domain bbox: %.4f x %.4f x %.4f", dx, dy, dz)
        self._log.append_log(f"{Tag.GEOM} Domain: {dx:.3f} \u00d7 {dy:.3f} \u00d7 {dz:.3f} m")

        # Geometry loaded and auto-sized; the watertight result is set inside.
        self._refresh_workflow(geometry_loaded=True, sizing_ready=True)
        self._check_watertight(self._unscaled_meshes)

    def _prepare_surface_with_features(self) -> str:
        """Return the surface file for cfMesh.

        Feature-edge extraction (FMS) is DISABLED by default because it
        causes more crashes than it fixes: on tessellated curves (pipes,
        cylinders, fillets) every facet edge gets detected as a "feature",
        producing a dense FMS that makes cartesianMesh crash trying to
        preserve them all.

        The plain STL produces slightly rounded corners but always works.
        Users who need crisp edges on true CAD models (cubes, brackets)
        can re-enable FMS by setting the constant below to True.
        """
        return "constant/triSurface/surface.stl"

    # Above either of these, ask before committing the user to a long run.
    # For 25M-cell support: confirm at 2M+ cells, or if the estimate says
    # it will take more than 10 minutes.
    LARGE_MESH_CELLS = 2_000_000
    LARGE_MESH_SECONDS = 600

    def _confirm_large_mesh(
        self, est_cells: int, est_seconds: float, max_cell: float, min_cell: float,
    ) -> bool:
        """Ask before starting a mesh that will take a long time.

        For meshes under 2M cells or 10 min estimate, proceeds silently.
        For 2M-25M cells, asks with a clear time estimate.
        For 25M+ cells, warns that this may exceed available resources.
        """
        if est_cells < self.LARGE_MESH_CELLS and est_seconds < self.LARGE_MESH_SECONDS:
            return True

        minutes = est_seconds / 60.0
        hours = minutes / 60.0

        if est_cells >= 25_000_000:
            msg = (
                f"Mesh molto grande: ~{est_cells:,} celle "
                f"(~{hours:.1f} ore stimate).\n\n"
                f"Dimensioni cella: max={max_cell:.4g} m, min={min_cell:.4g} m.\n\n"
                "Questa mesh potrebbe richiedere più di 8 GB di RAM e diverse ore. "
                "Assicurati che WSL2 abbia memoria sufficiente "
                "(esegui 'wsl --set-memory Ubuntu <quantità>' in PowerShell).\n\n"
                "Procedere?"
            )
        else:
            msg = (
                f"Questa mesh è stimata in ~{est_cells:,} celle "
                f"(~{minutes:.0f} min).\n\n"
                f"Dimensioni cella attuali: max={max_cell:.4g} m, min={min_cell:.4g} m.\n"
                "Aumentare la dimensione minima (o scegliere un livello di dettaglio "
                "più grossolano) riduce molto il tempo.\n\n"
                "Procedere comunque?"
            )

        reply = QMessageBox.question(
            self, "Large Mesh",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes if est_cells < 25_000_000 else QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    # Solvers whose fvSchemes/fvSolution case_setup.setup_case() actually
    # writes correctly (a fixed steady-RAS SIMPLE configuration). A template's
    # turbulence model is always safe to carry through — case_setup handles
    # every value (kOmegaSST/kEpsilon/laminar) correctly — but its *solver*
    # (e.g. chtMultiRegionFoam, interFoam, reactingFoam) is not: those need
    # entirely different scheme/solution dictionaries this function doesn't
    # generate, so silently swapping in `application` alone would produce an
    # internally inconsistent case (controlDict says one solver, fvSolution is
    # tuned for another) — worse than just staying on simpleFoam and saying so.
    _CASE_SETUP_SUPPORTED_SOLVERS = {"simpleFoam"}

    def _case_setup_kwargs(self) -> dict:
        """application/turbulence_model to pass to setup_case(), honouring
        whatever case template the user applied (if any) — never silently."""
        picked = self._params.get_template_solver_turbulence()
        if picked is None:
            return {}
        solver, turbulence = picked
        kwargs: dict = {"turbulence_model": turbulence}
        if solver in self._CASE_SETUP_SUPPORTED_SOLVERS:
            kwargs["application"] = solver
        else:
            self._log.append_log(
                f"{Tag.WARN} Template solver '{solver}' needs case files this "
                "version doesn't generate yet — applied turbulence model only "
                f"({turbulence}); solver/scheme setup for '{solver}' must be "
                "done manually."
            )
        return kwargs

    def _heal_geometry(self, meshes: list[trimesh.Trimesh]) -> None:
        """Repair small CAD defects right after loading, before anything else
        looks at the geometry.

        Degenerate-face removal / hole-filling / vertex-merging already ran
        automatically before every STL export (`stl_writer.heal_mesh`), but
        only at export time and only to a DEBUG-level log line the user never
        sees. That meant the watertight check and cell-size sizing upstream
        both ran on the RAW mesh: a CAD file with a couple of small holes
        healing would have fixed anyway got flagged "not watertight" here —
        a false alarm, exactly the kind of confusion this app should prevent.
        Healing here first means every later step sees the same (repaired)
        geometry the mesher will actually use; the export-time heal becomes a
        no-op repeat on an already-healed mesh (idempotent, so no regression).
        """
        try:
            from cfmesh_autogui.commercial.cad_healer import CADHealer
            reports = CADHealer().heal_meshes(meshes)
        except Exception as exc:
            logger.debug("CAD healing skipped: %s", exc)
            return

        total_ops = sum(len(r.operations) for r in reports)
        if total_ops == 0:
            return
        for mesh, report in zip(meshes, reports):
            name = mesh.metadata.get("name", "?")
            if report.operations:
                self._log.append_log(
                    f"{Tag.GEOM} Healed '{name}': {', '.join(report.operations)}"
                )

    def _check_watertight(self, meshes: list[trimesh.Trimesh]) -> None:
        """Warn early if the assembled patches don't form a closed volume,
        and automatically attempt to fix it.

        cfMesh/cartesianMesh needs a watertight domain \u2014 running the mesher
        on an open/leaky geometry fails only after minutes of WSL2 work,
        with a cryptic cfMesh error. Checking right after load surfaces the
        problem immediately, before the user commits to a full mesh run.

        The overwhelming majority of "open boundary edges" on a geometry
        that looks fine are not a real hole \u2014 they're each patch having
        been tessellated independently, so vertices meant to sit on a
        shared boundary are a tiny floating-point distance apart. A user
        seeing only "506 open boundary edges" with no next step has no way
        to tell that apart from a genuinely broken CAD file, and no tool to
        do anything about either case. See core/geometry_repair.py.
        """
        if not meshes:
            return
        try:
            combined = trimesh.util.concatenate(meshes)
            combined.merge_vertices()
            if combined.is_watertight:
                self._log.append_log(f"{Tag.GEOM} Watertight check: OK (closed volume).")
                self._refresh_workflow(watertight=True)
                return
        except Exception as exc:
            logger.debug("Watertight check skipped: %s", exc)
            return

        try:
            import trimesh.grouping as _grouping
            boundary_edges = combined.edges[
                _grouping.group_rows(combined.edges_sorted, require_count=1)
            ]
            n_open = len(boundary_edges)
        except Exception:
            n_open = 0

        self._log.append_log(
            f"{Tag.WARN} Watertight check: geometry has {n_open} open boundary edges. "
            "cfMesh needs a fully closed domain \u2014 attempting automatic repair..."
        )

        from cfmesh_autogui.core.geometry_repair import attempt_auto_repair
        bbox_dim = compute_bbox_dim(meshes)
        try:
            repaired, reports = attempt_auto_repair(meshes, bbox_dim)
        except Exception as exc:
            logger.exception("Auto-repair failed")
            self._log.append_log(f"{Tag.ERROR} Auto-repair crashed: {exc}")
            self._refresh_workflow(watertight=False)
            return

        for report in reports:
            for op in report.operations:
                self._log.append_log(f"{Tag.GEOM} [auto-fix/{report.method}] {op}")
            for warn in report.warnings:
                self._log.append_log(f"{Tag.WARN} [auto-fix/{report.method}] {warn}")

        final_report = reports[-1]
        if final_report.watertight_after:
            self._unscaled_meshes = repaired
            self._rebuild_scaled_meshes()
            self._params.set_patches([m.metadata.get("name", "?") for m in self._meshes])
            self._viewer.show_cad(self._meshes)
            self._log.append_log(
                f"{Tag.GEOM} Watertight check: fixed automatically, geometry is now closed."
            )
            self._refresh_workflow(watertight=True)
            return

        self._refresh_workflow(watertight=False)
        self._log.append_log(
            f"{Tag.WARN} Automatic repair could not fully close the geometry "
            f"({final_report.open_edges_after} open edges remain). This usually "
            "means a real missing face rather than a tessellation gap \u2014 check "
            "for a patch that doesn't share its full boundary with its "
            "neighbours in the 3D view, or re-export the CAD model with the "
            "gaps closed. Meshing may still fail or leak."
        )

    def _rebuild_scaled_meshes(self) -> None:
        """Rebuild ``self._meshes`` from ``self._unscaled_meshes`` using
        ``self._current_scale``.

        When the scale is 1.0 (the common case) no copy is made — ``_meshes``
        references ``_unscaled_meshes`` directly — avoiding a full in-memory
        duplicate for geometries with millions of triangles.
        """
        scale = self._params.get_scale_factor()
        self._current_scale = scale
        if abs(scale - 1.0) <= 1e-9:
            self._scaled_meshes = None
            self._meshes = self._unscaled_meshes
        else:
            self._scaled_meshes = [m.copy() for m in self._unscaled_meshes]
            scale_meshes(self._scaled_meshes, scale)
            self._meshes = self._scaled_meshes

    @Slot(str)
    def _on_unit_changed(self, unit: str):
        if not self._unscaled_meshes:
            return
        new_scale = unit_to_scale(unit)
        if abs(new_scale - self._current_scale) < 1e-9:
            return
        self._rebuild_scaled_meshes()
        names = [m.metadata.get("name", "?") for m in self._meshes]
        self._params.set_patches(names)
        self._params.set_suggest_meshes(self._meshes)
        self._viewer.show_cad(self._meshes)
        dx, dy, dz = compute_bbox_full(self._meshes)
        self._params.set_bbox(dx, dy, dz)
        logger.info("Re-scaled by %.6f (unit: %s), domain: %.4f\u00d7%.4f\u00d7%.4f", new_scale, unit, dx, dy, dz)
        self._log.append_log(f"{Tag.SCALE} Re-scaled \u00d7{new_scale:.6f} (CAD unit: {unit})")

    def _on_test_cylinder(self):
        logger.info("Creating test cylinder...")
        self._log.append_log(f"{Tag.GEOM} Creating test cylinder...")
        with self._busy():
            cyl = create_test_cylinder(radius=1.0, height=2.0)
            self._load_geometry(cyl.val())

    def _on_cleanup_cases(self):
        from cfmesh_autogui.core.disk_cleanup import list_cases, cases_disk_usage_mb, auto_cleanup
        from PySide6.QtWidgets import QMessageBox, QInputDialog
        cases = list_cases()
        usage = cases_disk_usage_mb()
        if not cases:
            QMessageBox.information(self, "Disk Cleanup", "No case directories found.")
            return
        keep, ok = QInputDialog.getInt(
            self, "Clean Up Old Cases",
            f"Found {len(cases)} case directories ({usage:.0f} MB total).\n\n"
            "Keep how many most recent cases?",
            value=10, min=1, max=max(len(cases), 10),
        )
        if not ok:
            return
        result = auto_cleanup(keep_last=keep)
        QMessageBox.information(
            self, "Disk Cleanup",
            f"Removed {result['removed_by_age'] + result['removed_by_keep']} case dirs.\n"
            f"Freed: {result['disk_freed_mb']:.1f} MB\n"
            f"Remaining: {result['remaining_mb']:.1f} MB",
        )

    def _on_load_step(self):
        s = self._settings()
        last_dir = s.get_value("geometry/last_step_dir", "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Geometry", last_dir,
            "Geometry Files (*.step *.stp *.stl);;STEP Files (*.step *.stp);;STL Files (*.stl);;All Files (*.*)",
        )
        if not path:
            return
        if Path(path).suffix.lower() not in {".step", ".stp", ".stl"}:
            self._log.append_log(f"[warn] Unsupported file format: {path}")
            return
        s.set_value("geometry/last_step_dir", str(Path(path).parent))
        octo.log_event("main_window", "load_geometry", path)
        self._load_geometry_from_path(path)

    def _load_geometry_from_path(self, path: str):
        import os as _os
        if not _os.path.isfile(path):
            self._log.append_log(f"{Tag.ERROR} Not a file: {path}")
            return
        size = _os.path.getsize(path)
        if size > MAX_STEP_FILE_BYTES:
            mb = size // 1024 // 1024
            self._log.append_log(
                f"{Tag.ERROR} File too large ({mb} MB; max {MAX_STEP_FILE_BYTES // 1024 // 1024} MB)"
            )
            QMessageBox.critical(
                self, "Geometry File Too Large",
                f"The file is {mb} MB. Maximum supported size is "
                f"{MAX_STEP_FILE_BYTES // 1024 // 1024} MB.",
            )
            return
        ext = Path(path).suffix.lower()
        logger.info("Loading geometry (%s): %s", ext, path)
        self._log.append_log(f"{Tag.GEOM} Loading {path}...")
        try:
            with self._busy():
                if ext in (".step", ".stp"):
                    self._loaded_step_path = path
                    self._status.showMessage("Importing STEP geometry...")
                    QApplication.processEvents()
                    shape = load_step(path)
                    self._load_geometry(shape)
                elif ext == ".stl":
                    self._loaded_step_path = path
                    self._unscaled_meshes = list(load_geometry(path))
                    self._rebuild_scaled_meshes()
                    names = [m.metadata.get("name", "?") for m in self._meshes]
                    logger.info("STL solids: %s", names)
                    self._log.append_log(
                        f"{Tag.GEOM} STL solids: {', '.join(names)}"
                    )
                    self._params.set_patches(names)
                    self._params.set_suggest_meshes(self._meshes)
                    self._viewer.show_cad(self._meshes)
                    dx, dy, dz = compute_bbox_full(self._meshes)
                    self._params.set_bbox(dx, dy, dz)
                    self._log.append_log(
                        f"{Tag.GEOM} Domain: {dx:.3f} \u00d7 {dy:.3f} \u00d7 {dz:.3f} m"
                    )
                else:
                    self._log.append_log(
                        f"{Tag.ERROR} Unsupported format: '{ext}'. "
                        "Use .step, .stp, or .stl."
                    )
                    return
        except Exception as e:
            logger.error("Failed to load %s: %s", ext, e)
            self._log.append_log(f"{Tag.ERROR} Failed to load {ext}: {e}")
            QMessageBox.critical(
                self, "Error", f"Failed to load {ext.upper()}:\n{e}"
            )
            return
        self._push_recent_step(path)

    def _push_recent_step(self, path: str):
        s = self._settings()
        existing = s.get_value("geometry/recent_files", []) or []
        if isinstance(existing, str):
            existing = [existing] if existing else []
        normalised = [p for p in existing if p != path]
        normalised.insert(0, path)
        s.set_value("geometry/recent_files", normalised[:MAX_RECENT_STEP_FILES])
        self._refresh_recent_menu()

    def _refresh_recent_menu(self):
        if not hasattr(self, "_recent_menu"):
            return
        self._recent_menu.clear()
        s = self._settings()
        recent = s.get_value("geometry/recent_files", []) or []
        if isinstance(recent, str):
            recent = [recent] if recent else []
        if not recent:
            empty = self._recent_menu.addAction("(empty)")
            empty.setEnabled(False)
            return
        for p in recent:
            action = self._recent_menu.addAction(p)
            action.triggered.connect(lambda _checked=False, pp=p: self._load_geometry_from_path(pp))
        self._recent_menu.addSeparator()
        clear = self._recent_menu.addAction("Clear Recent")
        clear.triggered.connect(self._clear_recent_step)

    def _clear_recent_step(self):
        s = self._settings()
        s.set_value("geometry/recent_files", [])
        self._refresh_recent_menu()

    @Slot(str)
    def _on_face_picked(self, patch_name: str):
        new_name, ok = QInputDialog.getText(
            self, f"Rename Patch '{patch_name}'",
            "New patch name:", text=patch_name,
        )
        if not ok or not new_name.strip() or new_name.strip() == patch_name:
            return
        new_name = new_name.strip()
        import re
        if re.search(r'[;{}"/\s]', new_name) or not new_name:
            QMessageBox.warning(self, "Invalid Name",
                "Patch name must not contain: ; { } \" / or whitespace.")
            return
        logger.info("Renaming patch '%s' -> '%s'.", patch_name, new_name)
        for mesh in self._meshes:
            if mesh.metadata.get("name") == patch_name:
                mesh.metadata["name"] = new_name
                break
        names = [m.metadata.get("name", "?") for m in self._meshes]
        self._params.set_patches(names)
        self._params.set_suggest_meshes(self._meshes)
        self._viewer.show_cad(self._meshes)
        self._viewer.highlight_patch(new_name)
        self._log.append_log(f"{Tag.PATCH} Renamed '{patch_name}' \u2192 '{new_name}'.")

    def _on_set_case_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Case Directory")
        if not path:
            return
        candidate = Path(path)
        valid, msg = OFConfig.validate_case_path(candidate)
        if not valid:
            QMessageBox.warning(
                self, "Case Directory Issue",
                msg,
            )
            return
        self._case_dir = candidate
        self._params.set_case_dir(str(self._case_dir))
        self._log.append_log(f"{Tag.MANUAL} User-set case directory: {self._case_dir}")

    def _on_reset(self):
        self._run_id += 1
        if self._runner.is_running:
            self._runner.terminate()
        # Back to a blank workflow — the hint returns to "load a geometry".
        self._workflow = MeshingWorkflow()
        self._refresh_workflow()
        self._meshes = []
        self._unscaled_meshes = []
        self._scaled_meshes = None
        self._original_shape = None
        self._case_dir = None
        self._current_scale = 1.0
        self._loaded_step_path = None
        self._params.set_patches([])
        self._params.set_suggest_meshes([])
        self._params.set_case_dir("")
        self._params.set_bbox(0, 0, 0)
        self._viewer.clear()
        self._log.clear_log()
        self._cell_count_label.setText("Cells: --")
        self._cell_count_label.setStyleSheet(
            f"color: {COLOR_TEXT_DISABLED}; padding-right: 8px; font-weight: bold;"
        )
        self._quality.clear_report()
        logger.info("State reset (run_id=%d).", self._run_id)
        self._log.append_log(f"{Tag.RESET} State cleared.")
        self._status.showMessage("Ready")

    def _resolve_auto_mesher(self) -> str:
        """"Automatic" mode: pick the best available mesher instead of
        making the user understand cfMesh vs. GMSH hybrid vs. GMSH direct.

        cfMesh (hex-dominant via WSL2/cartesianMesh) is the highest-quality
        option when it's available (ALGORITHM_INFO's own quality_rank=1);
        GMSH direct (pure tetra+prism, no WSL needed) is the fallback when
        it isn't — so the app always produces a mesh instead of stopping
        with "OpenFOAM Not Found" and leaving the user to figure out there's
        a manual dropdown they need to switch.
        """
        try:
            if self._of_config.validate():
                return "cfmesh"
        except Exception as exc:
            logger.debug("Automatic mesher: WSL check failed: %s", exc)
        return "gmsh_direct"

    def _on_run_meshing(self):
        if not self._meshes:
            QMessageBox.warning(
                self, "No Geometry",
                "No geometry loaded. Please load a STEP or CAD file first "
                "(use File \u2192 Load Geometry, drag & drop, or press Ctrl+O).",
            )
            return

        self._run_id += 1
        self._poly_was_converted = False
        my_id = self._run_id
        logger.info("Starting meshing run #%d.", my_id)

        if self._runner.is_running:
            QMessageBox.information(
                self, "Meshing in Progress",
                "A meshing job is already running. Please wait for it to finish.",
            )
            return

        mesher_type = self._params.get_mesher_type()
        if mesher_type == "auto":
            mesher_type = self._resolve_auto_mesher()
            self._log.append_log(f"{Tag.CASE} Automatic: using {mesher_type}.")
        octo.log_event("main_window", "run_meshing_start",
            f"run_id={my_id} mesher={mesher_type}")
        if mesher_type != "cfmesh":
            orig = getattr(self, "_loaded_step_path", None)
            if orig is None:
                orig = self._make_temp_geometry_for_gmsh()
            if orig is None:
                QMessageBox.warning(
                    self, "GMSH Needs Geometry",
                    "No geometry loaded. Load a STEP, STL, or use the "
                    "test cylinder first.",
                )
                return
            self._params.set_meshing_enabled(False)
            self._params.set_all_enabled(False)
            if mesher_type == "gmsh_hybrid":
                self._on_run_meshing_gmsh_hybrid(orig)
            else:
                self._on_run_meshing_gmsh_direct(orig)
            return

        # Clean up previous runner's thread before creating a new one,
        # otherwise the old QThread may still be running when the runner
        # gets garbage-collected, causing "QThread: Destroyed while
        # thread '' is still running" and potential crashes on subsequent
        # meshing runs.
        self._cleanup_runner()

        self._params.set_meshing_enabled(False)
        self._params.set_all_enabled(False)
        self._runner = RetryRunner(self._of_config)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        raw_candidates = [
            Path.home() / "cfmesh_cases",
            Path("C:/cfmesh_cases"),
            Path(str(Path(os.environ.get("TEMP", "C:\\temp"))).replace(" ", "_")) / "cfmesh_cases",
        ]
        candidates = [p for p in raw_candidates if " " not in str(p)]
        root = None
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                root = candidate
                break
            except OSError:
                continue
        if root is None:
            self._log.append_log(f"{Tag.ERROR} Cannot create case directory: no writable path found")
            self._params.set_all_enabled(True)
            return
        self._case_dir = root / f"case_{ts}"
        try:
            self._case_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(
                self, "Cannot Write Case Directory",
                f"Could not create the case directory:\n{self._case_dir}\n\n"
                f"Error: {exc}\n\n"
                "Check that the disk is not full and that you have write permissions.\n"
                "You can set a different case directory via File > Set Case Directory."
            )
            self._params.set_all_enabled(True)
            self._log.append_log(f"{Tag.ERROR} Cannot create case directory: {exc}")
            return
        self._params.set_case_dir(str(self._case_dir))

        valid, msg = OFConfig.validate_case_path(self._case_dir)
        if not valid:
            QMessageBox.warning(
                self, "Case Directory Issue",
                msg,
            )
            self._params.set_all_enabled(True)
            return

        # OFConfig.validate() launches wsl.exe and blocks until it responds.
        # WSL2 auto-shuts-down its VM after a period of inactivity, so the
        # NEXT invocation can trigger a full cold boot (systemd, snap
        # mounts, network services) taking well over a minute. Running that
        # call directly on the GUI thread used to freeze the whole window
        # for the entire wait — unresponsive, no repaint, indistinguishable
        # from a crash (this is almost certainly what "automatic meshing
        # doesn't work" reports were actually seeing). Run it on a
        # background QThread instead so the UI stays alive and the status
        # bar keeps showing feedback while WSL boots.
        self._log.append_log(
            f"{Tag.CASE} Checking OpenFOAM environment (WSL2)... "
            "this can take a minute if WSL just started."
        )
        self._status.showMessage("Checking OpenFOAM environment (WSL2)...")
        self._start_wsl_check(my_id)

    def _start_wsl_check(self, my_id: int) -> None:
        if getattr(self, "_wsl_check_thread", None) and self._wsl_check_thread.isRunning():
            self._wsl_check_thread.quit()
            self._wsl_check_thread.wait(3000)

        # my_id is stashed on self rather than captured in a lambda: a plain
        # Python lambda has no QObject thread affinity for PySide6 to queue
        # against, so a Qt.QueuedConnection to one can run on the emitting
        # (background) thread instead of the GUI thread — which is exactly
        # what happened here, crashing the process the moment the callback
        # touched the log widget's QTextDocument from the worker thread.
        # Connecting straight to a bound method of this QObject (as done
        # everywhere else in this file) queues correctly.
        self._wsl_check_run_id = my_id
        self._wsl_check_thread = QThread()
        self._wsl_check_worker = WslCheckWorker(self._of_config)
        self._wsl_check_worker.moveToThread(self._wsl_check_thread)
        self._wsl_check_thread.started.connect(self._wsl_check_worker.run)
        self._wsl_check_worker.finished.connect(self._on_wsl_check_finished, Qt.QueuedConnection)
        self._wsl_check_worker.finished.connect(self._wsl_check_thread.quit, Qt.QueuedConnection)
        self._wsl_check_worker.finished.connect(self._wsl_check_worker.deleteLater, Qt.QueuedConnection)
        self._wsl_check_thread.start()

    def _on_wsl_check_finished(self, of_available: bool) -> None:
        my_id = self._wsl_check_run_id
        if my_id != self._run_id:
            logger.debug("Stale WSL-check callback ignored (got %d, current %d).", my_id, self._run_id)
            return
        if not of_available:
            QMessageBox.warning(
                self, "OpenFOAM Not Found",
                "OpenFOAM v2512 not detected via WSL2.\n"
                "Install OpenFOAM v2512 in WSL2 Ubuntu and try again.",
            )
            self._params.set_all_enabled(True)
            return
        self._continue_run_meshing(my_id)

    def _continue_run_meshing(self, my_id: int) -> None:
        self._log.clear_log()
        self._log.append_log(f"{Tag.CASE} {self._case_dir}")
        self._quality.clear_report()

        self._log.append_log(f"{Tag.EXPORT} Writing surface STL...")
        try:
            export_surface_file(self._meshes, self._case_dir)
        except Exception as e:
            logger.error("STL export failed: %s", e)
            self._log.append_log(f"{Tag.ERROR} STL export failed: {e}")
            QMessageBox.critical(
                self, "STL Export Failed",
                f"Could not write the surface STL file for meshing:\n\n{e}\n\n"
                "Check that the case directory is writable and the disk is not full."
            )
            self._params.set_all_enabled(True)
            return

        surface_file = self._prepare_surface_with_features()

        bbox_dim = compute_bbox_dim(self._meshes)
        logger.info("Geometry bbox max dim: %.4f", bbox_dim)

        # Feature detection — geometry-aware cell sizing (Fix 1). Runs a
        # full GMSH 2D surface remesh internally just to derive curvature/
        # sharp-edge stats; on a complex or poorly-defeatured CAD model
        # that can take a long time (GMSH retrying "Splitting those edges"
        # on many surfaces) — confirmed live, the whole app showed "Not
        # Responding" for the entire duration when this ran directly here
        # on the GUI thread. Now on a background QThread.
        feature_step_path = getattr(self, '_loaded_step_path', None)
        if feature_step_path and Path(feature_step_path).exists():
            self._log.append_log("[feature] Analyzing geometry features...")
            self._start_feature_detect(my_id, feature_step_path, surface_file, bbox_dim)
            return

        self._continue_run_meshing_after_feature_detect(my_id, surface_file, bbox_dim)

    def _start_feature_detect(
        self, my_id: int, step_path: str, surface_file: str, bbox_dim: float,
    ) -> None:
        if getattr(self, "_feature_thread", None) and self._feature_thread.isRunning():
            self._feature_thread.quit()
            self._feature_thread.wait(3000)

        self._feature_thread = QThread()
        # Pass the SAME CAD-unit scale factor already applied to the loaded
        # meshes: the detector reads the raw CAD file, so without this its
        # suggestions come back in the file's own units (mm) and get used
        # as metres — confirmed live on a 3 m model authored in mm, where
        # it suggested a 150 m max cell size.
        self._feature_worker = FeatureDetectWorker(
            step_path, self._params.get_detail_level(), self._current_scale,
        )
        self._feature_worker.moveToThread(self._feature_thread)
        self._feature_thread.started.connect(self._feature_worker.run)
        self._feature_worker.finished.connect(
            lambda fm, err: self._on_feature_detect_finished(my_id, surface_file, bbox_dim, fm, err),
            Qt.QueuedConnection,
        )
        self._feature_worker.finished.connect(self._feature_thread.quit, Qt.QueuedConnection)
        self._feature_worker.finished.connect(self._feature_worker.deleteLater, Qt.QueuedConnection)
        self._feature_thread.start()

    def _on_feature_detect_finished(
        self, my_id: int, surface_file: str, bbox_dim: float, feature_map, error: str | None,
    ) -> None:
        if my_id != self._run_id:
            logger.debug("Stale feature-detect callback ignored (got %d, current %d).", my_id, self._run_id)
            return

        if error is not None:
            logger.warning("Feature detection failed (non-fatal): %s", error)
            self._log.append_log(f"[warn] Feature detection skipped: {error}")
        elif feature_map is not None:
            self._log.append_log(
                f"[feature] Detected: {len(feature_map.sharp_edges)} sharp edges, "
                f"{len(feature_map.gap_regions)} gaps, "
                f"curvature={feature_map.curvature_radius:.4f}"
            )
            suggested_max = feature_map.suggested_max_cell
            suggested_min = feature_map.suggested_min_cell
            self._log.append_log(
                f"[feature] Feature-based cell sizes: max={suggested_max:.4f} "
                f"min={suggested_min:.4f} (logged only — user's values preserved)"
            )
            # Feature detection values are logged for reference only.
            # Do NOT overwrite the user's spinbox values — they were already
            # set by Auto Suggest or manual input before the mesh started.

        self._continue_run_meshing_after_feature_detect(my_id, surface_file, bbox_dim)

    def _continue_run_meshing_after_feature_detect(
        self, my_id: int, surface_file: str, bbox_dim: float,
    ) -> None:
        if my_id != self._run_id:
            logger.debug(
                "Stale continue-run callback ignored (got %d, current %d).",
                my_id, self._run_id,
            )
            return

        # Local refinement: detect narrow passages via local thickness field
        self._throat_zones = None
        if self._params.get_auto_refine_enabled() and self._meshes:
            try:
                from cfmesh_autogui.core.throat_detector import detect_refinement_regions
                detail = self._params.get_detail_level()
                raw_max = self._params.get_mesh_params().get("max_cell_size", 0.05)
                zones = detect_refinement_regions(self._meshes, detail=detail, global_max_cell=raw_max)
                if zones:
                    self._throat_zones = zones
                    for z in zones:
                        self._log.append_log(
                            f"{Tag.DICT} Narrow passage at "
                            f"({z.centre[0]:.3f},{z.centre[1]:.3f},{z.centre[2]:.3f}), "
                            f"thickness={z.local_thickness:.4f}m, "
                            f"cellSize={z.cell_size:.5f}m (radius={z.radius:.4f}m)"
                        )
                    self._log.append_log(
                        f"{Tag.DICT} {len(zones)} auto refinement zone(s) detected."
                    )
                else:
                    logger.debug("Refinement detection: no narrow passages found.")
            except Exception as exc:
                logger.warning("Refinement detection skipped: %s", exc)

        try:
            self._do_meshing_pipeline(my_id, surface_file, bbox_dim)
        except Exception as e:
            import traceback
            tb = "".join(traceback.format_exc())
            logger.critical("Meshing pipeline crashed:\n%s", tb)
            self._log.append_log(f"{Tag.ERROR} Meshing failed: {e}")
            self._params.set_all_enabled(True)
            self._params.set_meshing_state(False)
            self._progress.setVisible(False)
            self._ribbon_btns["cancel"].setVisible(False)
            self._status.showMessage("Meshing crashed")
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Meshing Crash",
                f"Meshing pipeline crashed:\n\n{e}\n\n{tb[-500:]}")

    def _do_meshing_pipeline(
        self, my_id: int, surface_file: str, bbox_dim: float,
    ) -> None:
        """Actual meshing logic, wrapped by _continue_run_meshing_after_feature_detect
        for error handling."""
        p = self._params.get_mesh_params()
        self._log.append_log(f"{Tag.MESHING} Starting meshing pipeline...")
        from cfmesh_autogui.core.validation import validate_cell_size
        validation_result = validate_cell_size(
            max_cell=p["max_cell_size"],
            min_cell=p["min_cell_size"],
            bbox_dim=bbox_dim,
        )
        if not validation_result.valid:
            # A marginal min/max ratio violation (within ~2%) is almost
            # always the spinboxes' own 4-decimal rounding on an
            # auto-suggested value that was safe before rounding — not a
            # real user mistake. Verified live: a real auto-suggested pair
            # (0.09375/0.1875, exactly 50% before rounding) became
            # 0.0938/0.1875 = 50.03% after setValue()'s rounding and hard-
            # blocked meshing with no visible cause. Auto-correct silently
            # instead of showing a scary dialog for a rounding artifact;
            # still hard-block for a genuinely wrong setup (min_cell way
            # bigger than max_cell).
            ratio = p["min_cell_size"] / p["max_cell_size"] if p["max_cell_size"] > 0 else 999
            if ratio <= 0.52:
                fixed_min = round(p["max_cell_size"] * 0.49, 4)
                self._log.append_log(
                    f"{Tag.SAFEGUARD} min_cell {p['min_cell_size']:.4f} was "
                    f"marginally over 50% of max_cell after rounding — "
                    f"correcting in meshDict only, UI spinbox preserved."
                )
                p = self._params.get_mesh_params()
                # Use corrected min for meshDict, keep UI spinbox unchanged
                user_min = p["min_cell_size"]
                if p["max_cell_size"] > user_min:
                    p["min_cell_size"] = fixed_min
            else:
                logger.error("Cell size validation failed: %s", validation_result.message)
                QMessageBox.critical(self, "Cell Size Error", validation_result.message)
                self._params.set_all_enabled(True)
                return
        for w in validation_result.warnings:
            self._log.append_log(f"{Tag.SAFEGUARD} {w}")

        # Use the user's EXACT values for the meshDict — no clamping, no
        # auto-correction beyond what validate_cell_size already enforced.
        # validate_cell_sizes is called only for estimation/warnings, NOT
        # for the actual meshDict write, so the user's settings survive.
        raw_max = p["max_cell_size"]
        raw_min = p["min_cell_size"]

        try:
            safe_max, safe_min, warnings = validate_cell_sizes(
                bbox_dim, raw_max, raw_min,
            )
        except ValueError as e:
            logger.error("Cell size validation failed: %s", e)
            QMessageBox.critical(self, "Cell Size Error", str(e))
            self._params.set_all_enabled(True)
            return

        for w in warnings:
            self._log.append_log(f"{Tag.SAFEGUARD} {w}")

        self._log.append_log(f"{Tag.GEOM} Bbox: {bbox_dim:.4f} m, cells: {raw_max:.4f}/{raw_min:.4f} m")

        # Compute per-patch cell sizes EARLY so the estimate accounts for
        # local refinement (otherwise a mesh with wall=0.04 on a 3m bbox
        # gets estimated at 19K cells but actually produces 2.3M).
        detail = self._params.get_detail_level()
        patch_sizes, bc_size, bc_thick = compute_patch_cell_sizes(
            self._meshes, detail=detail,
        )
        if patch_sizes:
            self._log.append_log(
                f"{Tag.DICT} Per-patch cell sizes: "
                + ", ".join(f"{n}={s:.4f}" for n, s in patch_sizes.items())
            )

        # Use the smallest effective cell size for estimation
        eff_min = safe_min
        if patch_sizes:
            eff_min = min(eff_min, min(patch_sizes.values()))
        volume = compute_volume(self._meshes)
        lo, est, hi = estimate_cell_count(volume, safe_max, eff_min)
        logger.info(
            "Cell estimate: volume=%.4e, nominal=%d (range %d-%d).", volume, est, lo, hi
        )
        # Estimate meshing time: cartesianMesh throughput scales with
        # geometry complexity.  Simple boxes/pipe reach ~5000 cells/sec;
        # complex assemblies may drop to ~1000 cells/sec.
        rate = 5000 if est < 1_000_000 else 1800
        est_secs = est / rate
        if est_secs < 60:
            time_str = f"{est_secs:.0f}s"
        elif est_secs < 3600:
            time_str = f"{est_secs/60:.1f}min"
        else:
            time_str = f"{est_secs/3600:.1f}h"
        self._cell_count_label.setText(f"~{est:,} cells (~{time_str})")
        self._params.set_cell_estimate(f"~{est:,} cells (~{time_str}), range {lo:,}-{hi:,}")
        self._log.append_log(f"{Tag.EST} ~{est:,} cells (~{time_str}), range {lo:,}-{hi:,}")

        if not self._confirm_large_mesh(est, est_secs, safe_max, safe_min):
            self._log.append_log(f"{Tag.CANCELLED} Meshing cancelled before start.")
            self._params.set_all_enabled(True)
            return

        bl_params = self._params.get_bl_params()
        if bl_params is not None:
            all_names = [
                m.metadata.get("name", f"patch_{i}")
                for i, m in enumerate(self._meshes)
            ]
            apply_all = self._params.get_bl_apply_all()
            if apply_all:
                wall_patches = list(all_names)
                bl_log = f"all {len(wall_patches)} patches (user-forced)"
            else:
                wall_patches = [
                    n for n in all_names if _is_wall_patch(n)
                ]
                bl_log = (
                    f"{len(wall_patches)} wall patch(es): {wall_patches}"
                    if wall_patches
                    else "no patches named like 'wall' — will apply to ALL"
                )

            if not wall_patches:
                self._log.append_log(
                    f"{Tag.WARN} No wall-like patches detected — "
                    "disabling boundary layers to avoid BL on inlet/outlet."
                )
                bl_params = None
                self._params.set_bl_enabled(False)
            else:
                self._log.append_log(
                    f"{Tag.BL} BL enabled on {bl_log}"
                )
                bl_params = dict(bl_params)
                bl_params["wallPatches"] = wall_patches
            # bl_params comes from ParamsPanel.get_bl_params(), whose real
            # keys are nLayers/thicknessRatio/firstLayerThickness (see that
            # method's own contract comment) — there is no "expansionRatio"
            # key. This used to crash with KeyError on every meshing run
            # that had boundary layers enabled, confirmed live.
            logger.info(
                "BL params: nLayers=%d, thicknessRatio=%.4f, "
                "firstLayerThickness=%.6f, walls=%s",
                bl_params['nLayers'], bl_params['thicknessRatio'],
                bl_params['firstLayerThickness'], wall_patches,
            )
        else:
            self._log.append_log(f"{Tag.BL} Boundary layers: disabled.")

        # Build objectRefinements from auto-detected throats + manual zones
        object_refinements = None
        throat_zones = getattr(self, "_throat_zones", None)
        if throat_zones:
            object_refinements = [
                {"centre": z.centre, "radius": z.radius, "cell_size": z.cell_size}
                for z in throat_zones
            ]
            # BL throat compatibility check — auto-reduces nLayers/firstLayerThickness
            # when there isn't enough space in the narrowest passage, preventing
            # cfMesh from crashing or producing degenerate cells.
            if bl_params:
                try:
                    from cfmesh_autogui.core.throat_detector import check_bl_throat_compatibility
                    bl_warnings, adjusted_bl = check_bl_throat_compatibility(bl_params, throat_zones)
                    for w in bl_warnings:
                        self._log.append_log(f"{Tag.WARN} {w}")
                    # Apply auto-reduced BL params and update UI spinbox
                    if adjusted_bl.get("nLayers", bl_params.get("nLayers")) != bl_params.get("nLayers"):
                        self._params._bl_n_layers.setValue(adjusted_bl["nLayers"])
                    if adjusted_bl.get("firstLayerThickness", bl_params.get("firstLayerThickness")) != bl_params.get("firstLayerThickness"):
                        max_cell_val = self._params.get_max_cell()
                        if max_cell_val > 0:
                            self._params._bl_thick.setValue(adjusted_bl["firstLayerThickness"] / max_cell_val)
                    bl_params = adjusted_bl
                except Exception as exc:
                    logger.debug("BL throat check skipped: %s", exc)
        manual_refs = self._params.get_manual_refinements()
        if manual_refs:
            object_refinements = list(object_refinements or []) + manual_refs

        self._log.append_log(f"{Tag.DICT} Writing meshDict...")
        try:
            from cfmesh_autogui.core.meshdict_gen import write_meshdict
            self._log.append_log(
                f"[DEBUG] Writing meshDict with raw values: max={raw_max:.6f} min={raw_min:.6f}"
            )
            write_meshdict(
                self._case_dir, raw_max, raw_min,
                patch_cell_size=patch_sizes or None,
                boundary_cell_size=bc_size,
                boundary_refinement_thickness=bc_thick,
                bl_params=bl_params,
                patch_names=[m.metadata.get("name", "wall") for m in self._meshes],
                surface_file=surface_file,
                object_refinements=object_refinements,
            )
        except Exception as e:
            logger.error("meshDict failed: %s", e)
            self._log.append_log(f"{Tag.ERROR} meshDict failed: {e}")
            QMessageBox.critical(
                self, "Mesh Configuration Failed",
                f"Could not write the meshDict configuration file:\n\n{e}\n\n"
                "Check that the case directory is writable and the disk is not full."
            )
            self._params.set_all_enabled(True)
            return

        try:
            (self._case_dir / "system" / "controlDict").write_text(
                "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
                "application cartesianMesh;\n"
                "startFrom startTime; startTime 0;\n"
                "stopAt endTime; endTime 1000;\n"
                "deltaT 1;\n"
                "writeControl timeStep; writeInterval 1;\n"
                "writeFrequency 1;\n"
                "purgeWrite 0; writeFormat binary; writePrecision 6;\n"
                "writeCompression on; timeFormat general; timePrecision 6;\n"
                "runTimeModifiable true;\n",
                encoding="ascii",
            )
        except Exception as e:
            logger.error("controlDict write failed: %s", e)
            self._log.append_log(f"{Tag.ERROR} controlDict write failed: {e}")
            QMessageBox.critical(
                self, "Mesh Configuration Failed",
                f"Could not write the controlDict configuration file:\n\n{e}\n\n"
                "Check that the case directory is writable and the disk is not full."
            )
            self._params.set_all_enabled(True)
            return

        self._meshing_run_id = my_id
        self._status.showMessage("Meshing...")

        def guarded_finished(exit_code, output, attempts):
            if my_id != self._run_id:
                logger.debug("Stale callback ignored (got %d, current %d).", my_id, self._run_id)
                self._log.append_log(f"{Tag.STALE} Ignoring result from run #{my_id}")
                return
            self._on_meshing_finished(exit_code, output, attempts)

        try:
            self._params.set_meshing_state(True)
            self._progress.setRange(0, 0)
            self._progress.setVisible(True)
            self._ribbon_btns["cancel"].setVisible(True)

            parallel_enabled, n_cores = self._params.get_parallel_params()
            # For small meshes (< 500K cells), MPI overhead dwarfs any
            # parallel speedup — force serial.
            if est < 500_000:
                if parallel_enabled:
                    self._log.append_log(
                        f"{Tag.MESHING} Mesh too small for parallel "
                        f"(<500K cells, est={est}) — using serial."
                    )
                parallel_enabled = False
            if parallel_enabled and n_cores >= 2:
                self._log.append_log(
                    f"{Tag.MESHING} Running cartesianMesh across {n_cores} cores..."
                )
                # Stash params for _parallel_fallback so it uses the
                # SAME settings the parallel run was configured with,
                # not whatever the user changed in the UI during meshing.
                self._fallback_mesh_params = dict(p)
                self._fallback_safe_max = raw_max
                self._fallback_safe_min = raw_min
                self._fallback_bl_params = bl_params
                self._run_parallel_mesh(
                    raw_max, raw_min, n_cores, guarded_finished, bl_params,
                )
                return

            self._log.append_log(f"{Tag.MESHING} Running cartesianMesh...")
            self._connect_runner_signals()
            self._runner.run(
                self._case_dir,
                on_log=self._log.append_log,
                on_finished=guarded_finished,
                fix_action=self._make_fix_action(),
                bl_params=bl_params,
                max_cell=raw_max,
                min_cell=raw_min,
                patch_names=[m.metadata.get("name", "wall") for m in self._meshes],
            )
        except Exception as e:
            logger.error("Meshing launch failed: %s", e)
            self._log.append_log(f"{Tag.ERROR} Meshing launch failed: {e}")
            self._params.set_all_enabled(True)
            self._params.set_meshing_state(False)
            self._progress.setVisible(False)
            self._ribbon_btns["cancel"].setVisible(False)
            return

    def _run_parallel_mesh(
        self, max_cell: float, min_cell: float, n_cores: int, guarded_finished,
        bl_params: dict | None = None,
    ) -> None:
        """Run cartesianMesh across n_cores MPI ranks via ParallelMeshEngine,
        on a background QThread so the GUI stays responsive (the engine's
        run() is a long synchronous/blocking call).

        Reuses _on_meshing_finished() for the follow-up (boundary parsing,
        case setup, quality check) via the same (exit_code, output, attempts)
        contract the serial RetryRunner path uses — parallel vs. serial only
        differs in how constant/polyMesh got there.

        If the parallel attempt itself fails (timeout, WSL2 memory
        exhaustion, MPI error), automatically falls back to the serial
        RetryRunner instead of just reporting failure: "parallel meshing"
        as a feature should mean "faster when it can be, never worse than
        turning it off" — a user shouldn't lose a working mesh just because
        the multi-core path hit trouble their geometry/machine couldn't
        support this time.
        """
        # Disconnect old worker signals BEFORE creating a new one, so a
        # delayed finished/failed signal from the previous run can't
        # interfere with the current mesh.
        old_worker = getattr(self, "_parallel_worker", None)
        if old_worker is not None:
            try:
                old_worker.log_line.disconnect()
                old_worker.finished.disconnect()
                old_worker.failed.disconnect()
                old_worker.cancelled.disconnect()
            except (RuntimeError, TypeError):
                pass
            old_worker.deleteLater()

        if getattr(self, "_parallel_thread", None) and self._parallel_thread.isRunning():
            self._parallel_thread.quit()
            self._parallel_thread.wait(5000)

        my_id = self._run_id
        self._parallel_thread = QThread()
        self._parallel_worker = ParallelMeshWorker(
            self._case_dir, self._of_config,
            max_cell=max_cell, min_cell=min_cell, n_cores=n_cores,
            patch_names=[m.metadata.get("name", "wall") for m in self._meshes],
            bl_params=bl_params,
        )
        self._parallel_worker.moveToThread(self._parallel_thread)
        self._parallel_worker.log_line.connect(self._log.append_log, Qt.QueuedConnection)

        def on_finished(result):
            if my_id != self._run_id:
                return
            if result.cell_count == 0:
                QMetaObject.invokeMethod(
                    self, "_parallel_fallback",
                    Qt.QueuedConnection,
                    Q_ARG(str, "Parallel mesh produced 0 cells"),
                )
                return
            QMetaObject.invokeMethod(
                self, "_on_parallel_mesh_success",
                Qt.QueuedConnection,
                Q_ARG(int, my_id),
            )

        def on_failed(msg: str):
            if my_id != self._run_id:
                return
            QMetaObject.invokeMethod(
                self, "_parallel_fallback",
                Qt.QueuedConnection,
                Q_ARG(str, msg),
            )

        def _cleanup():
            if self._parallel_thread:
                self._parallel_thread.quit()
                self._parallel_thread.wait(5000)
            self._parallel_worker.deleteLater()
            self._parallel_worker = None
            if self._parallel_thread:
                self._parallel_thread.deleteLater()
                self._parallel_thread = None

        def on_cancelled():
            self._log.append_log(f"{Tag.CANCELLED} Parallel meshing stopped.")
            _cleanup()

        def _on_thread_finished():
            """Called when the QThread has fully exited."""
            _cleanup()

        self._parallel_worker.finished.connect(on_finished, Qt.QueuedConnection)
        self._parallel_worker.finished.connect(self._parallel_thread.quit, Qt.QueuedConnection)
        self._parallel_thread.finished.connect(_on_thread_finished, Qt.QueuedConnection)
        self._parallel_worker.failed.connect(on_failed, Qt.QueuedConnection)
        self._parallel_worker.failed.connect(self._parallel_thread.quit, Qt.QueuedConnection)
        self._parallel_worker.cancelled.connect(on_cancelled, Qt.QueuedConnection)
        self._parallel_worker.cancelled.connect(self._parallel_thread.quit, Qt.QueuedConnection)
        self._parallel_thread.started.connect(self._parallel_worker.run)
        self._parallel_thread.start()

    def _make_temp_geometry_for_gmsh(self) -> str | None:
        import os as _os
        from datetime import datetime as _dt
        _default_base = Path.home() / "cfmesh_cases" / ".gmsh_temp"
        if " " in str(_default_base):
            _default_base = Path("C:/cfmesh_cases") / ".gmsh_temp"
        base = self._case_dir or _default_base
        base.mkdir(parents=True, exist_ok=True)
        ts = _dt.now().strftime('%Y%m%d_%H%M%S')

        if self._original_shape is not None:
            tmp = str(base / f"gmsh_geometry_{ts}.step")
            try:
                import cadquery as cq
                cq.exporters.export(self._original_shape, tmp, exportType="STEP")
                logger.info("Exported original shape to STEP for GMSH: %s", tmp)
                return tmp
            except Exception as e:
                logger.warning("Failed to export STEP for GMSH: %s", e)
        if self._meshes:
            tmp = str(base / f"gmsh_geometry_{ts}.stl")
            try:
                from cfmesh_autogui.core.stl_writer import export_multisolid_stl
                export_multisolid_stl(self._meshes, tmp)
                logger.info("Exported meshes to STL for GMSH: %s", tmp)
                return tmp
            except Exception as e:
                logger.warning("Failed to export STL for GMSH: %s", e)
        return None

    def _on_run_meshing_gmsh_hybrid(self, geom_path: str):
        self._run_id += 1
        my_id = self._run_id
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = Path.home() / "cfmesh_cases"
        if " " in str(root):
            root = Path("C:/cfmesh_cases")
        self._case_dir = root / f"gmsh_hybrid_{ts}"
        self._case_dir.mkdir(parents=True, exist_ok=True)
        self._params.set_case_dir(str(self._case_dir))
        self._log.clear_log()
        self._log.append_log(f"{Tag.CASE} {self._case_dir} (GMSH hybrid)")

        detail = self._params.get_detail_level()
        bl_params = self._params.get_bl_params()
        self._log.append_log("[gmsh] Generating curvature-aware surface STL...")
        self._status.showMessage("GMSH: surface mesh...")
        QApplication.processEvents()

        try:
            tri_dir = self._case_dir / "constant" / "triSurface"
            tri_dir.mkdir(parents=True, exist_ok=True)
            stl_out = tri_dir / "surface.stl"
            names = gmsh_generate_surface_stl(
                geom_path, stl_out, detail=detail,
            )
            self._log.append_log(
                f"[gmsh] Surface STL: {stl_out} — patches: {names}"
            )
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error("GMSH surface failed: %s\n%s", e, tb)
            self._log.append_log(f"[ERROR] GMSH surface: {e}")
            self._params.set_all_enabled(True)
            return

        self._log.append_log("[gmsh] Volume fill via cfMesh...")
        self._status.showMessage("cfMesh: volume fill...")
        QApplication.processEvents()

        sizing = gmsh_compute_sizing(geom_path, detail=detail)
        safe_max = max(sizing.suggested_volume_size, 0.001)
        safe_min = max(sizing.suggested_surface_size, 0.0001)

        try:
            from cfmesh_autogui.core.meshdict_gen import write_meshdict
            write_meshdict(
                self._case_dir, safe_max, safe_min,
                patch_cell_size=sizing.patch_sizes or None,
                boundary_cell_size=sizing.suggested_surface_size,
                boundary_refinement_thickness=sizing.min_curvature_radius * 2,
                bl_params=bl_params,
                patch_names=names,
            )
        except Exception as e:
            logger.error("meshDict failed: %s", e)
            self._log.append_log(f"{Tag.ERROR} meshDict: {e}")
            self._params.set_all_enabled(True)
            return

        (self._case_dir / "system" / "controlDict").write_text(
            "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
            "application cartesianMesh;\n"
            "startFrom startTime; startTime 0;\n"
            "stopAt endTime; endTime 1000;\n"
            "deltaT 1;\n"
            "writeControl timeStep; writeInterval 1;\n"
            "writeFrequency 1;\n"
            "purgeWrite 0; writeFormat binary; writePrecision 6;\n"
            "writeCompression on; timeFormat general; timePrecision 6;\n"
            "runTimeModifiable true;\n",
            encoding="ascii",
        )

        self._runner = RetryRunner(self._of_config)
        self._connect_runner_signals()

        def guarded_finished(exit_code, output, attempts):
            if my_id != self._run_id:
                logger.debug("Stale gmsh_hybrid callback ignored")
                return
            self._on_meshing_finished(exit_code, output, attempts)

        self._runner.run(
            self._case_dir,
            on_log=self._log.append_log,
            on_finished=guarded_finished,
            bl_params=bl_params,
            max_cell=safe_max,
            min_cell=safe_min,
            patch_names=names,
        )

    def _on_run_meshing_gmsh_direct(self, step_path: str):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = Path.home() / "cfmesh_cases"
        if " " in str(root):
            root = Path("C:/cfmesh_cases")
        self._case_dir = root / f"gmsh_direct_{ts}"
        self._case_dir.mkdir(parents=True, exist_ok=True)
        self._params.set_case_dir(str(self._case_dir))
        self._log.clear_log()
        self._log.append_log(f"{Tag.CASE} {self._case_dir} (GMSH direct)")

        detail = self._params.get_detail_level()
        bl_params = self._params.get_bl_params()
        # get_bl_params()'s real keys are nLayers/thicknessRatio (the
        # growth ratio)/firstLayerThickness (absolute metres) — this used
        # to read "thicknessRatio" for the absolute thickness and a
        # nonexistent "expansionRatio" for the growth ratio, so the user's
        # actual BL settings were silently ignored on the GMSH-direct path
        # (same key-name bug that crashed the cfMesh path with KeyError).
        n_layers = bl_params.get("nLayers", 0) if bl_params else 0
        bl_thickness = bl_params.get("firstLayerThickness", 0.005) if bl_params else None
        bl_expansion = bl_params.get("thicknessRatio", 1.2) if bl_params else 1.2

        self._log.append_log("[gmsh] Generating volume mesh (tetra + BL)...")
        self._status.showMessage("GMSH: volume mesh...")
        QApplication.processEvents()

        bl_retried = False
        while True:
            try:
                msh_path = self._case_dir / "mesh.msh"
                msh_path, names = gmsh_generate_volume_mesh(
                    step_path, msh_path,
                    detail=detail,
                    n_layers=n_layers,
                    bl_thickness=bl_thickness,
                    bl_expansion=bl_expansion,
                )
                self._log.append_log(
                    f"[gmsh] Volume mesh: {msh_path} — patches: {names}"
                )
                break
            except Exception as e:
                if not bl_retried and bl_params and n_layers > 0:
                    bl_retried = True
                    self._log.append_log(
                        f"{Tag.WARN} GMSH volume mesh failed with BL — "
                        "retrying without boundary layers."
                    )
                    n_layers = 0
                    bl_thickness = None
                    bl_expansion = 1.2
                    continue
                import traceback
                tb = traceback.format_exc()
                logger.error("GMSH volume mesh failed: %s\n%s", e, tb)
                self._log.append_log(f"[ERROR] GMSH volume: {e}")
                self._params.set_all_enabled(True)
                return

        self._log.append_log("[gmsh] Converting to OpenFOAM polyMesh...")
        self._status.showMessage("GMSH: conversion...")
        QApplication.processEvents()
        try:
            from cfmesh_autogui.core.mesh_converter import msh_to_of_polymesh
            poly_dir = msh_to_of_polymesh(msh_path, self._case_dir)
            self._log.append_log(
                f"[gmsh] polyMesh: {poly_dir}"
            )
            try:
                from cfmesh_autogui.core.boundary_reader import (
                    count_cells, count_faces, count_points,
                )
                n_points = count_points(self._case_dir)
                n_faces = count_faces(self._case_dir)
                n_cells = count_cells(self._case_dir)
                self._log.append_log(
                    f"[gmsh] Stats: {n_points:,} points, {n_faces:,} faces, "
                    f"{n_cells:,} cells"
                )
            except Exception:
                pass
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error("MSH conversion failed: %s\n%s", e, tb)
            self._log.append_log(f"[ERROR] MSH conversion: {e}")
            self._params.set_all_enabled(True)
            return

        from cfmesh_autogui.core.boundary_reader import parse_boundary
        from cfmesh_autogui.core.case_setup import setup_case
        boundary_path = self._case_dir / "constant" / "polyMesh" / "boundary"
        if boundary_path.exists():
            try:
                patches = parse_boundary(boundary_path)
                setup_case(self._case_dir, patches, **self._case_setup_kwargs())
                self._log.append_log("[setup] Case files generated (0/, system/).")
            except Exception as e:
                logger.error("Case setup failed: %s", e)
                self._log.append_log(f"[ERROR] Case setup: {e}")
                self._params.set_all_enabled(True)
                return

        self._log.append_log(f"{Tag.DONE} Case: {self._case_dir}")
        self._viewer.show_mesh(self._case_dir)
        self._status.showMessage("GMSH direct mesh ready")
        self._params.set_all_enabled(True)

        poly_points = self._case_dir / "constant" / "polyMesh" / "points"
        poly_faces = self._case_dir / "constant" / "polyMesh" / "faces"
        if poly_points.exists() and poly_faces.exists():
            self._launch_checkmesh()

    @Slot(int)
    def _on_cell_count_found(self, count: int):
        from cfmesh_autogui.gui.design_tokens import SUCCESS
        self._cell_count_label.setText(f"Mesh cells: {count:,}")
        self._cell_count_label.setStyleSheet(
            f"color: {SUCCESS}; padding-right: 8px; font-weight: bold;"
        )
        self._params.set_real_cell_count(count)
        logger.info("Final cell count reported by cartesianMesh: %d", count)
        self._log.append_log(f"{Tag.MESHING} Cells: {count:,} (final)")

    @Slot(int)
    def _on_progress_update(self, value: int):
        if value == -1:
            self._progress.setRange(0, 0)
        elif value >= 0:
            self._progress.setRange(0, 100)
            self._progress.setValue(value)
        if value == 100:
            self._progress.setVisible(False)

    def _cleanup_runner(self) -> None:
        """Stop and clean up the current RetryRunner's thread."""
        runner = getattr(self, "_runner", None)
        if runner is not None and hasattr(runner, '_cleanup_previous'):
            try:
                runner._cleanup_previous()
            except Exception:
                pass

    def _kill_wsl_processes(self) -> None:
        """Best-effort kill of WSL2 mpirun/cartesianMesh processes.
        Called on cancel to ensure no orphaned ranks continue running."""
        try:
            import subprocess as _sp
            _sp.run(
                ["wsl.exe", "-d", self._of_config.wsl_distro, "--",
                 "bash", "-lc",
                 "pkill -9 -f cartesianMesh 2>/dev/null; "
                 "pkill -9 -f mpirun 2>/dev/null; "
                 "pkill -9 -f reconstructParMesh 2>/dev/null; true"],
                capture_output=True, text=True, timeout=10,
            )
        except Exception as exc:
            logger.debug("WSL process kill skipped: %s", exc)

    @Slot()
    @Slot(int)
    def _on_parallel_mesh_success(self, my_id: int = 0) -> None:
        """Called on the GUI thread when parallel meshing succeeds.
        Dispatched via QMetaObject.invokeMethod from the QThread."""
        if my_id != self._run_id:
            return
        def guarded(exit_code, output, attempts):
            if my_id != self._run_id:
                return
            self._on_meshing_finished(exit_code, output, attempts)
        guarded(0, "", 1)

    def _connect_runner_signals(self) -> None:
        """Connect RetryRunner signals to handlers, avoiding duplicates."""
        try:
            self._runner.cell_count_relay.disconnect(self._on_cell_count_found)
        except (RuntimeError, TypeError):
            pass
        try:
            self._runner.progress_update.disconnect(self._on_progress_update)
        except (RuntimeError, TypeError):
            pass
        self._runner.cell_count_relay.connect(self._on_cell_count_found)
        self._runner.progress_update.connect(self._on_progress_update)

    @Slot(str)
    @Slot(str)
    def _parallel_fallback(self, msg: str) -> None:
        """Serial fallback for parallel meshing. Runs on GUI thread
        via QMetaObject.invokeMethod so QObject creation is safe."""
        logger.warning("Parallel meshing failed (%s) — falling back to serial", msg)
        self._log.append_log(
            f"{Tag.WARN} Parallel meshing failed ({msg}) — "
            "falling back to single-core meshing for this run."
        )
        QMessageBox.information(
            self, "Parallel Meshing Fallback",
            f"Parallel meshing failed ({msg}).\n\n"
            "Falling back to single-core (serial) meshing automatically.\n"
            "The mesh will still be generated, it will just take longer.",
        )
        if self._case_dir:
            import glob as _glob
            for d in _glob.glob(str(self._case_dir / "processor*")):
                import shutil as _su
                try:
                    _su.rmtree(d)
                except Exception:
                    pass
        self._log.append_log(f"{Tag.MESHING} Running cartesianMesh (serial fallback)...")
        p = getattr(self, "_fallback_mesh_params", None) or self._params.get_mesh_params()
        bl_params = getattr(self, "_fallback_bl_params", None)
        max_cell = p.get("max_cell_size", 0.05)
        min_cell = p.get("min_cell_size", 0.01)
        self._connect_runner_signals()
        self._runner.run(
            self._case_dir,
            on_log=self._log.append_log,
            on_finished=self._make_guarded_finished(),
            fix_action=self._make_fix_action(),
            bl_params=bl_params,
            max_cell=max_cell,
            min_cell=min_cell,
            patch_names=[m.metadata.get("name", "wall") for m in self._meshes],
        )

    def _make_guarded_finished(self):
        """Build a guarded_finished closure bound to the current run_id."""
        my_id = self._run_id
        def guarded(exit_code, output, attempts):
            if my_id != self._run_id:
                logger.debug(
                    "Stale callback ignored (got %d, current %d).",
                    my_id, self._run_id,
                )
                return
            self._on_meshing_finished(exit_code, output, attempts)
        return guarded

    def _on_cancel_meshing(self):
        self._run_id += 1
        if getattr(self._runner, "is_running", False):
            self._runner.terminate()
        parallel_worker = getattr(self, "_parallel_worker", None)
        if parallel_worker is not None:
            parallel_worker.cancel()
        parallel_thread = getattr(self, "_parallel_thread", None)
        if parallel_thread is not None and parallel_thread.isRunning():
            parallel_thread.requestInterruption()
            if not parallel_thread.wait(5000):
                parallel_thread.terminate()
                parallel_thread.wait(3000)
        self._kill_wsl_processes()
        self._params.set_meshing_state(False)
        self._params.set_all_enabled(True)
        self._progress.setVisible(False)
        self._ribbon_btns["cancel"].setVisible(False)
        self._status.showMessage("Cancelled")
        self._log.append_log(f"{Tag.CANCELLED} Meshing cancelled by user.")

    @Slot(int, str, int)
    def _on_meshing_finished(self, exit_code: int, output: str, attempts: int = 1):
        # Guard against stale callbacks from previous meshing runs
        meshing_id = getattr(self, "_meshing_run_id", None)
        if meshing_id is not None and meshing_id != self._run_id:
            logger.debug("Stale _on_meshing_finished ignored (meshing_id=%d, run_id=%d)", meshing_id, self._run_id)
            return
        self._params.set_meshing_state(False)
        self._progress.setVisible(False)
        self._ribbon_btns["cancel"].setVisible(False)
        self._params.set_all_enabled(True)
        # Track retry count to avoid infinite retry loops
        fail_count = getattr(self, "_meshing_fail_count", 0) + 1
        self._meshing_fail_count = fail_count
        if exit_code != 0:
            self._set_workflow_stage("generate", "error")
            error = analyze_error(output)
            logger.error(
                "cartesianMesh failed: exit=%d attempt=%d/%d error=%s",
                exit_code, attempts, RetryRunner.MAX_RETRIES, error.error_type.value,
            )
            self._log.append_log(
                f"{Tag.ERROR} cartesianMesh failed (exit {exit_code}, "
                f"attempt {attempts}/{RetryRunner.MAX_RETRIES})"
            )
            self._log.append_log(f"[error] {error.message}")
            self._log.append_log(f"{Tag.SUGGESTION} {error.suggestion}")
            self._quality.set_raw_log(output)
            self._status.showMessage("Meshing failed")
            # Check if retry limit exceeded
            if fail_count >= 3:
                QMessageBox.warning(
                    self, "Meshing Failed Repeatedly",
                    f"cartesianMesh has failed {fail_count} times in a row.\n\n"
                    "Auto-recovery by loosening cell sizes has not resolved the issue.\n"
                    "The problem may be geometry-related (non-watertight surface, "
                    "invalid patches, or unsupported features).\n\n"
                    f"Last error: {error.message}\n"
                    f"Suggestion: {error.suggestion}\n\n"
                    "Check the log panel for details or try loading a different geometry."
                )
                self._meshing_fail_count = 0
                return
            # Offer auto-recovery: reduce cell sizes by 30% and retry
            retry = QMessageBox.question(
                self, "Meshing Failed",
                f"cartesianMesh failed (exit {exit_code}).\n\n"
                f"{error.message}\n\n"
                "Auto-recovery: reduce cell sizes by 30% and retry?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if retry == QMessageBox.Yes:
                max_cell = self._params.get_max_cell()
                min_cell = self._params.get_min_cell()
                self._params._max_cell.setValue(max_cell * 1.3)
                self._params._min_cell.setValue(min_cell * 1.3)
                self._log.append_log(
                    f"{Tag.FIX} Loosened cell sizes: max={max_cell*1.3:.4f} "
                    f"min={min_cell*1.3:.4f}, re-running..."
                )
                self._on_run_meshing()
            return
        # Reset fail count on success
        self._meshing_fail_count = 0

        self._set_workflow_stage("generate", "done")
        self._set_workflow_stage("quality", "active")
        self._refresh_workflow(mesh_generated=True)
        logger.info("cartesianMesh OK (attempt %d).", attempts)
        octo.log_event("main_window", "meshing_ok",
            f"attempt={attempts} case_dir={self._case_dir}")
        self._log.append_log(f"{Tag.MESHING} cartesianMesh OK (attempt {attempts}).")
        self._status.showMessage("Setting up case files...")

        boundary_path = self._case_dir / "constant" / "polyMesh" / "boundary"
        if boundary_path.exists():
            try:
                patches = parse_boundary(boundary_path)
                logger.info("Boundary parsed: %d patches.", len(patches))
                self._log.append_log(
                    f"{Tag.BOUNDARY} {len(patches)} patches: {[p.name for p in patches]}"
                )
                setup_case(self._case_dir, patches, **self._case_setup_kwargs())
                self._log.append_log(f"{Tag.SETUP} Case files generated (0/, system/).")
            except Exception as e:
                logger.error("Case setup failed: %s", e)
                self._log.append_log(f"{Tag.ERROR} Case setup failed: {e}")
                return
        else:
            logger.warning("boundary file not found in %s", self._case_dir)
            self._log.append_log(f"{Tag.WARN} boundary file not found.")
        self._viewer.show_mesh(self._case_dir)
        self._log.append_log(f"{Tag.DONE} Case: {self._case_dir}")

        poly_points = self._case_dir / "constant" / "polyMesh" / "points"
        if poly_points.exists():
            self._launch_checkmesh()
        else:
            self._log.append_log(
                f"{Tag.WARN} polyMesh/points not found. "
                "If using parallel mode, run 'reconstructParMesh -constant' "
                "manually in the case directory."
            )

    def _direct_remesh(self) -> None:
        """Re-mesh directly from current state, skipping WSL check
        and feature detect.  Used by quality auto-fix for speed."""
        if not self._meshes or not self._case_dir:
            self._log.append_log(f"{Tag.ERROR} Cannot remesh: no geometry loaded.")
            return
        self._run_id += 1
        my_id = self._run_id
        self._meshing_run_id = my_id
        logger.info("Direct re-mesh run #%d (quality fix).", my_id)
        from cfmesh_autogui.core.stl_writer import export_surface_file
        from cfmesh_autogui.core.meshdict_gen import write_meshdict
        p = self._params.get_mesh_params()
        raw_max = p["max_cell_size"]
        raw_min = p["min_cell_size"]
        bl_params = self._params.get_bl_params()
        all_names = [m.metadata.get("name", f"patch_{i}") for i, m in enumerate(self._meshes)]
        if bl_params is not None:
            wall_patches = [n for n in all_names if _is_wall_patch(n)]
            if wall_patches:
                bl_params = dict(bl_params)
                bl_params["wallPatches"] = wall_patches
            else:
                bl_params = None
        try:
            export_surface_file(self._meshes, self._case_dir)
            # Detect if FMS feature edges were previously generated
            fms_path = self._case_dir / "constant" / "triSurface" / "surface.fms"
            surface_file = "constant/triSurface/surface.fms" if fms_path.exists() else "constant/triSurface/surface.stl"
            write_meshdict(self._case_dir, raw_max, raw_min, bl_params=bl_params,
                           patch_names=all_names, surface_file=surface_file)
            from cfmesh_autogui.commercial.mesh_engine import _write_control_dict
            _write_control_dict(self._case_dir)
        except Exception as e:
            logger.error("Direct remesh setup failed: %s", e)
            self._log.append_log(f"{Tag.ERROR} Direct remesh setup failed: {e}")
            self._params.set_all_enabled(True)
            return

        def guarded(ec, out, att):
            if my_id != self._run_id:
                return
            self._on_meshing_finished(ec, out, att)

        try:
            self._params.set_meshing_state(True)
            self._progress.setRange(0, 0)
            self._progress.setVisible(True)
            self._ribbon_btns["cancel"].setVisible(True)
            parallel_enabled, n_cores = self._params.get_parallel_params()
            if parallel_enabled and n_cores >= 2:
                self._fallback_mesh_params = dict(p)
                self._fallback_safe_max = raw_max
                self._fallback_safe_min = raw_min
                self._fallback_bl_params = bl_params
                self._run_parallel_mesh(raw_max, raw_min, n_cores, guarded, bl_params)
                return
            self._log.append_log(f"{Tag.MESHING} Running cartesianMesh (direct re-mesh)...")
            self._connect_runner_signals()
            self._runner.run(self._case_dir, on_log=self._log.append_log,
                             on_finished=guarded, fix_action=self._make_fix_action(),
                             bl_params=bl_params, max_cell=raw_max, min_cell=raw_min,
                             patch_names=all_names)
        except Exception as e:
            logger.error("Direct remesh launch failed: %s", e)
            self._log.append_log(f"{Tag.ERROR} Direct remesh launch failed: {e}")
            self._params.set_all_enabled(True)
            self._params.set_meshing_state(False)
            self._progress.setVisible(False)
            self._ribbon_btns["cancel"].setVisible(False)

    def _launch_checkmesh(self):
        if not self._case_dir:
            return
        poly_points = self._case_dir / "constant" / "polyMesh" / "points"
        if not poly_points.exists():
            self._log.append_log(f"{Tag.WARN} checkMesh: no mesh in constant/polyMesh.")
            return
        self._checkmesh_run_id = self._run_id
        if self._checkmesh_thread and self._checkmesh_thread.isRunning():
            self._checkmesh_thread.quit()
            self._checkmesh_thread.wait(3000)
        self._checkmesh_thread = QThread()
        self._checkmesh_worker = CheckMeshWorker(self._case_dir, self._of_config)
        self._checkmesh_worker.moveToThread(self._checkmesh_thread)
        self._checkmesh_worker.log_line.connect(self._log.append_log, Qt.QueuedConnection)
        self._checkmesh_worker.finished.connect(self._on_checkmesh_finished, Qt.QueuedConnection)
        self._checkmesh_worker.finished.connect(self._checkmesh_thread.quit, Qt.QueuedConnection)
        self._checkmesh_worker.failed.connect(
            lambda msg: (
                self._log.append_log(f"{Tag.CHECKMESH} FAILED: {msg}"),
                QMessageBox.warning(
                    self, "Mesh Quality Check Failed",
                    f"checkMesh reported errors:\n\n{msg}\n\n"
                    "Check the Quality panel for details. "
                    "Try reducing cell sizes or enabling auto-fix."
                ),
            ),
            Qt.QueuedConnection,
        )
        self._checkmesh_worker.failed.connect(self._checkmesh_thread.quit, Qt.QueuedConnection)
        self._checkmesh_thread.started.connect(self._checkmesh_worker.run)
        self._checkmesh_thread.start()

    def _on_checkmesh_finished(self, report):
        # Stale guard: checkMesh was launched during a specific meshing
        # run; if a newer run already started, ignore this callback.
        check_run_id = getattr(self, "_checkmesh_run_id", self._run_id)
        if check_run_id != self._run_id:
            logger.debug("Stale checkMesh callback ignored (%d != %d).", check_run_id, self._run_id)
            return
        try:
            payload = report.to_dict()
        except Exception as exc:
            logger.exception("checkMesh report.to_dict() failed")
            self._log.append_log(f"{Tag.ERROR} checkMesh report parse failed: {exc}")
            self._quality.clear_report()
            return
        self._quality.show_report(payload)
        self._refresh_workflow(quality_passed=bool(report.passed))
        if report.cells:
            self._on_cell_count_found(report.cells)
        if report.passed:
            self._set_workflow_stage("quality", "done")
            self._log.append_log(f"{Tag.QUALITY} PASS checkMesh")
            self._status.showMessage("Ready — mesh complete")
            if self._params.get_poly_conversion():
                if not getattr(self, "_poly_was_converted", False):
                    self._launch_polydual()
            else:
                self._launch_decomposepar()
        else:
            self._set_workflow_stage("quality", "error")
            self._log.append_log(f"{Tag.QUALITY} {report.status}")
            self._status.showMessage("Mesh quality check failed")
            # Auto-fix: offer to relax cell sizes and re-mesh
            self._auto_quality_fix(report)

    def _auto_quality_fix(self, report) -> None:
        """Auto-fix poor quality by relaxing cell sizes and re-meshing.
        Runs at most 2 iterations to avoid infinite loops.
        Calls _direct_remesh() instead of _on_run_meshing() to skip
        the WSL check + feature detect pipeline.
        """
        n = self._quality_fix_attempts
        if n >= 2:
            self._log.append_log(
                f"{Tag.WARN} Quality auto-fix: max iterations (2) reached."
            )
            return
        self._quality_fix_attempts = n + 1
        max_cell = self._params.get_max_cell()
        min_cell = self._params.get_min_cell()
        self._params._max_cell.setValue(max_cell * 1.3)
        self._params._min_cell.setValue(max(min_cell * 0.7, 0.0001))
        self._log.append_log(
            f"{Tag.FIX} Quality auto-fix #{n + 1}: "
            f"relaxed cells max={max_cell*1.3:.4f} min={max(min_cell*0.7, 0.0001):.6f}"
        )
        self._direct_remesh()

    def _launch_polydual(self) -> None:
        if not self._case_dir:
            return
        poly_points = self._case_dir / "constant" / "polyMesh" / "points"
        if not poly_points.exists():
            self._log.append_log(
                f"{Tag.WARN} Poly conversion: no mesh found in constant/polyMesh."
            )
            return
        self._polydual_run_id = self._run_id
        if hasattr(self, '_polydual_thread') and self._polydual_thread and self._polydual_thread.isRunning():
            self._polydual_thread.quit()
            self._polydual_thread.wait(3000)
        self._log.append_log("[poly] Converting hex \u2192 polyhedral mesh (polyDualMesh)...")
        self._status.showMessage("Polyhedral conversion...")
        t = QThread()
        feature_angle = 45.0  # Higher = smoother polyhedral cells
        w = PolyDualWorker(self._case_dir, self._of_config,
                           feature_angle=feature_angle)
        w.moveToThread(t)
        w.log_line.connect(self._log.append_log, Qt.QueuedConnection)
        w.finished.connect(self._on_polydual_finished, Qt.QueuedConnection)
        w.finished.connect(t.quit, Qt.QueuedConnection)
        w.failed.connect(
            lambda msg: (
                self._log.append_log(f"[poly] FAILED: {msg}"),
                QMessageBox.warning(
                    self, "Polyhedral Conversion Failed",
                    f"polyDualMesh failed:\n\n{msg}\n\n"
                    "The hex mesh is still available. "
                    "Try a larger feature angle or skip polyhedral conversion."
                ),
            ),
            Qt.QueuedConnection,
        )
        w.failed.connect(t.quit, Qt.QueuedConnection)
        t.started.connect(w.run)
        self._polydual_thread = t
        self._polydual_worker = w
        t.start()

    def _on_polydual_finished(self, meshes) -> None:
        poly_run_id = getattr(self, "_polydual_run_id", self._run_id)
        if poly_run_id != self._run_id:
            logger.debug("Stale polyDual callback ignored (%d != %d).", poly_run_id, self._run_id)
            return
        self._log.append_log("[poly] Polyhedral conversion complete.")
        self._status.showMessage("Polyhedral mesh ready — running quality check...")
        self._viewer.show_mesh(self._case_dir)
        self._poly_was_converted = True
        self._launch_checkmesh()

    def _launch_decomposepar(self) -> None:
        """Run decomposePar to create processor dirs for parallel solving.
        Called after serial meshing when user has parallel mode enabled."""
        if not self._case_dir:
            return
        parallel_enabled, n_cores = self._params.get_parallel_params()
        if not parallel_enabled or n_cores < 2:
            return
        poly_points = self._case_dir / "constant" / "polyMesh" / "points"
        if not poly_points.exists():
            return
        self._log.append_log(
            f"{Tag.MESHING} Running decomposePar across {n_cores} cores "
            "for parallel solving..."
        )
        self._status.showMessage("Decomposing mesh for parallel solving...")
        self._cleanup_thread("_decompose_thread", "_decompose_worker")
        t = QThread()
        w = DecomposeParWorker(self._case_dir, self._of_config, n_cores)
        w.moveToThread(t)
        self._decompose_thread = t
        self._decompose_worker = w
        w.log_line.connect(self._log.append_log, Qt.QueuedConnection)
        w.finished.connect(lambda _: self._log.append_log(
            f"{Tag.MESHING} decomposePar OK — parallel solving ready."
        ), Qt.QueuedConnection)
        w.finished.connect(t.quit, Qt.QueuedConnection)
        w.finished.connect(w.deleteLater, Qt.QueuedConnection)
        w.failed.connect(lambda msg: self._log.append_log(
            f"{Tag.WARN} decomposePar FAILED: {msg} — mesh still usable for serial solving."
        ), Qt.QueuedConnection)
        w.failed.connect(t.quit, Qt.QueuedConnection)
        w.failed.connect(w.deleteLater, Qt.QueuedConnection)
        t.started.connect(w.run)
        t.start()

    def _make_fix_action(self):
        shape = self._original_shape
        current_scale = self._current_scale

        def fix(error_info, attempt: int) -> bool:
            logger.info("Retry fix for %s (attempt %d).", error_info.error_type.value, attempt)
            self._log.append_log(f"{Tag.FIX} {error_info.error_type.value} (attempt {attempt})")
            if error_info.error_type in (ErrorType.NON_WATERTIGHT, ErrorType.SURFACE_READ):
                try:
                    finer_tol = 0.01 / (2 ** attempt)
                    patches = classify_faces(shape)
                    self._unscaled_meshes = list(tessellate_patches(patches, tolerance=finer_tol, angle_tolerance=0.05))
                    self._scaled_meshes = None
                    if abs(current_scale - 1.0) > 1e-9:
                        self._scaled_meshes = [m.copy() for m in self._unscaled_meshes]
                        scale_meshes(self._scaled_meshes, current_scale)
                        self._meshes = self._scaled_meshes
                    else:
                        self._meshes = self._unscaled_meshes
                    export_surface_file(self._meshes, self._case_dir)
                    self._log.append_log(f"{Tag.FIX} Re-exported STL (tol={finer_tol})")
                    return True
                except Exception as e:
                    logger.warning("Fix failed: %s", e)
                    self._log.append_log(f"{Tag.FIX} {e}")
                    return False
            if error_info.error_type == ErrorType.PATCH_NOT_FOUND:
                try:
                    names = [m.metadata.get("name", "wall") for m in self._meshes]
                    p = self._params.get_mesh_params()
                    bl_retry = self._params.get_bl_params()
                    if bl_retry is not None:
                        wall_patches = [
                            n for n in names if _is_wall_patch(n)
                        ]
                        if not wall_patches:
                            bl_retry = None
                            self._log.append_log(
                                f"{Tag.WARN} Retry: no wall-like patches — "
                                "disabling BL."
                            )
                        else:
                            bl_retry = dict(bl_retry)
                            bl_retry["wallPatches"] = wall_patches
                    detail = self._params.get_detail_level()
                    ps_r, bc_r, bt_r = compute_patch_cell_sizes(
                        self._meshes, detail=detail,
                    )
                    write_meshdict(
                        self._case_dir,
                        max_cell_size=p["max_cell_size"],
                        min_cell_size=p["min_cell_size"],
                        patch_cell_size=ps_r or None,
                        boundary_cell_size=bc_r,
                        boundary_refinement_thickness=bt_r,
                        bl_params=bl_retry,
                        patch_names=names,
                    )
                    self._log.append_log(f"{Tag.FIX} Regenerated meshDict.")
                    return True
                except Exception as e:
                    logger.warning("Fix failed: %s", e)
                    self._log.append_log(f"{Tag.FIX} {e}")
                    return False
            return False
        return fix

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            from pathlib import Path as _Path
            for url in event.mimeData().urls():
                p = url.toLocalFile().lower()
                if p.endswith((".step", ".stp", ".stl")):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if not path.lower().endswith((".step", ".stp", ".stl")):
                continue
            self._load_geometry_from_path(path)
            return

    def _on_export_mesh(self, fmt: str):
        """Export the mesh via foamToVTK -> PyVista -> (triangulate) -> meshio.

        meshio (5.3.5, as shipped) has no OpenFOAM *reader*, so reading the
        case directly always raised ReadError — this export never worked at
        all. Separately, cfMesh's octree produces polyhedral cells at
        refinement transitions, and meshio's own VTU reader refuses mixed
        polyhedra + standard cells — so even routing through a plain VTU
        re-read fails on realistic (non-toy) geometry, not just simple boxes.
        `core.mesh_export.export_mesh` fixes both: PyVista (built on VTK, not
        meshio) reads any OpenFOAM/cfMesh cell type natively, and non-VTU
        formats triangulate every cell to tetrahedra first — verified against
        a real mesh with mixed hexahedron+polyhedron cells for every format
        below.
        """
        if not self._case_dir:
            QMessageBox.warning(self, "No Mesh", "Generate a mesh first.")
            return
        poly_dir = self._case_dir / "constant" / "polyMesh"
        if not poly_dir.exists():
            QMessageBox.warning(self, "No Mesh", "polyMesh directory not found.")
            return

        from cfmesh_autogui.core.mesh_export import EXPORT_FORMATS
        ext, _meshio_fmt, desc = EXPORT_FORMATS[fmt]
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export Mesh as {ext}",
            str(self._case_dir / f"mesh{ext}"),
            f"{desc} (*{ext})",
        )
        if not path:
            return
        try:
            from cfmesh_autogui.core.mesh_export import export_mesh
            out = export_mesh(self._case_dir, fmt, path, of_config=self._of_config)
            self._log.append_log(f"{Tag.EXPORT} Mesh exported ({fmt.upper()}): {out}")
            QMessageBox.information(self, "Export Complete", f"Mesh exported to:\n{out}")
        except Exception as e:
            logger.error("Export failed: %s", e)
            QMessageBox.critical(self, "Export Error", str(e))

    def _on_export_baramflow(self):
        """Export a self-contained OpenFOAM case folder ready to open in BaramFlow.

        BaramFlow opens native OpenFOAM cases directly — there is no special
        conversion step. "Exporting" means validating that constant/,
        system/ and the initial 0/ fields are all present and complete,
        then copying the case to a destination the user picks (so the
        live working case isn't disturbed by anything BaramFlow writes).
        """
        if not self._case_dir:
            QMessageBox.warning(self, "No Mesh", "Generate a mesh first.")
            return

        from cfmesh_autogui.core.baramflow_export import validate_case, export_case

        # Validate completeness AND mesh/field consistency up front, so the user
        # learns about a case BaramFlow can't open here, not after shipping it.
        validation = validate_case(self._case_dir)
        if not validation.ok:
            detail = ""
            if validation.missing_files:
                detail += "Missing files:\n" + "\n".join(
                    f"  • {m}" for m in validation.missing_files
                ) + "\n"
            if validation.issues:
                detail += "Consistency problems:\n" + "\n".join(
                    f"  • {i}" for i in validation.issues
                )
            QMessageBox.warning(
                self, "Case Not Ready",
                "This case is not ready to open in BaramFlow:\n\n" + detail
                + "\n\nGenerate the mesh and let the case setup complete first.",
            )
            return

        dest_parent = QFileDialog.getExistingDirectory(
            self, "Choose Destination Folder for BaramFlow Case",
            str(Path(self._case_dir).parent),
        )
        if not dest_parent:
            return

        dest = Path(dest_parent).resolve() / self._case_dir.name
        if dest == Path(self._case_dir).resolve():
            QMessageBox.warning(
                self, "Invalid Destination",
                "Choose a different folder than the current case directory.",
            )
            return
        if dest.exists():
            reply = QMessageBox.question(
                self, "Overwrite?",
                f"'{dest}' already exists. Overwrite its contents?",
            )
            if reply != QMessageBox.Yes:
                return

        try:
            out = export_case(self._case_dir, dest_parent)
            self._refresh_workflow(exported=True)
            self._log.append_log(
                f"{Tag.EXPORT} BaramFlow case exported: {out} "
                f"({len(validation.patches)} patches)"
            )
            QMessageBox.information(
                self, "Export Complete",
                f"Case exported to:\n{out}\n\nOpen this folder directly in BaramFlow.",
            )
        except Exception as e:
            logger.error("BaramFlow export failed: %s", e)
            QMessageBox.critical(self, "Export Error", str(e))

    def _on_export_pdf(self):
        if not self._case_dir:
            QMessageBox.warning(self, "No Mesh", "Generate a mesh first.")
            return
        from cfmesh_autogui.gui.pdf_report import MeshReportPDF
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PDF Quality Report",
            str(self._case_dir / "mesh_quality.pdf"),
            "PDF (*.pdf)",
        )
        if not path:
            return
        try:
            quality_data = getattr(self._quality, "_metrics", {})
            report = MeshReportPDF(self._case_dir)
            report.generate(Path(path), quality_data)
            self._log.append_log(f"[export] PDF report: {path}")
        except Exception as e:
            logger.error("PDF export failed: %s", e)
            QMessageBox.critical(self, "PDF Error", str(e))

    def _on_bc_editor(self):
        if not self._case_dir:
            QMessageBox.warning(self, "No Case", "Generate a mesh first.")
            return
        from cfmesh_autogui.gui.bc_dialog import BCEditorDialog
        dlg = BCEditorDialog(self._case_dir, self)
        dlg.bc_applied.connect(lambda: self._log.append_log("[bc] Fields exported."))
        dlg.exec()
        self._set_workflow_stage("export", "active")

    def _on_solver_setup(self):
        if not self._case_dir:
            QMessageBox.warning(self, "No Case", "Generate a mesh first.")
            return
        from cfmesh_autogui.commercial.solver_setup import SolverSetup, SolverConfig, SolverType
        from cfmesh_autogui.commercial.bc_editor import BCEditor
        bce = BCEditor()
        solver = SolverSetup()
        patches = []
        try:
            patches = bce.read_boundary(self._case_dir)
        except FileNotFoundError:
            pass
        cfg = SolverConfig(
            solver_type=SolverType.SIMPLE_FOAM,
            turbulence_model="kOmegaSST",
            scheme_preset="bilanciato",
        )
        solver.configure(cfg)
        solver.write_all(self._case_dir, patches)
        self._log.append_log(
            f"[solver] Setup complete: simpleFoam, kOmegaSST, bilanciato"
        )
        QMessageBox.information(
            self, "Solver Setup",
            f"Solver configuration written to {self._case_dir / 'system'}"
        )
        self._set_workflow_stage("export", "active")

    def _on_full_auto(self):
        if not self._meshes:
            QMessageBox.warning(self, "No Geometry", "Load a geometry first.")
            return
        self._log.append_log("[fullauto] Starting Full Auto pipeline...")
        self._set_workflow_stage("generate", "active")
        self._on_quick_mesh()

    def _on_template_selector(self):
        from cfmesh_autogui.gui.template_selector import TemplateSelectorDialog
        dlg = TemplateSelectorDialog(self)
        if dlg.exec() == QDialog.Accepted:
            preset = dlg.get_selected_template()
            if preset is None:
                return
            self._log.append_log(
                f"[template] Selected: {preset.metadata.name} "
                f"({preset.metadata.category})"
            )
            self._apply_template_preset(preset)

    def _apply_template_preset(self, preset):
        self._params._detail_slider.setValue(
            {"coarse": 1, "medium": 2, "fine": 3, "very_fine": 4}.get(
                preset.detail, 2
            )
        )
        if hasattr(self._params, '_algorithm_combo'):
            algo_map = {
                "CartesianHex": 0, "Tetrahedral": 1,
                "Polyhedral": 2, "HexCorePoly": 3,
            }
            idx = algo_map.get(preset.metadata.solver, 0)
            self._params._algorithm_combo.setCurrentIndex(idx)
        if hasattr(self._params, '_bl_checkbox'):
            self._params._bl_checkbox.setChecked(preset.bl_enabled)
        if hasattr(self._params, '_bl_n_layers'):
            self._params._bl_n_layers.setValue(preset.bl_n_layers)
        if preset.geometry_hint:
            self._log.append_log(
                f"[template] Geometry hint: {preset.geometry_hint}"
            )
        self._log.append_log(
            f"[template] Applied: {preset.metadata.solver} / "
            f"{preset.metadata.turbulence}"
        )

    def _on_new_case_wizard(self):
        from cfmesh_autogui.gui.new_case_wizard import NewCaseWizard
        wiz = NewCaseWizard(self)
        if wiz.exec() == QDialog.Accepted:
            params = wiz.get_params()
            geo_path = params.get("geometry_path", "")
            if geo_path:
                self._load_geometry_from_path(geo_path)
            # Apply wizard mesh settings to ParamsPanel
            detail_map = {"Coarse": 1, "Medium": 2, "Fine": 3, "Very Fine": 4}
            detail_str = params.get("detail", "Medium")
            if detail_str in detail_map:
                self._params._detail_slider.setValue(detail_map[detail_str])
            if "max_cell" in params:
                self._params._max_cell.setValue(float(params["max_cell"]))
            if "min_cell" in params:
                self._params._min_cell.setValue(float(params["min_cell"]))
            if "bl_enabled" in params:
                self._params._bl_checkbox.setChecked(bool(params["bl_enabled"]))
            if "bl_n" in params:
                self._params._bl_n_layers.setValue(int(params["bl_n"]))
            if "bl_thick" in params:
                self._params._bl_thick.setValue(float(params["bl_thick"]))
            if "bl_exp" in params:
                self._params._bl_exp.setValue(float(params["bl_exp"]))
            # If pipeline result available, log feature info
            pipe_result = params.get("pipeline_result")
            if pipe_result and pipe_result.success:
                g = pipe_result.geometry
                self._log.append_log(
                    f"[wizard] Geometry: {g.n_patches} patches, "
                    f"watertight={g.watertight}, unit={g.detected_unit}, "
                    f"bbox={g.bbox_max:.3f}m"
                )
                if pipe_result.healing:
                    h = pipe_result.healing
                    if h.holes_filled or h.gaps_stitched or h.slivers_removed:
                        self._log.append_log(
                            f"[wizard] Healing: {h.holes_filled} holes, "
                            f"{h.gaps_stitched} gaps, {h.slivers_removed} slivers"
                        )
                if pipe_result.features:
                    f = pipe_result.features
                    self._log.append_log(
                        f"[wizard] Features: {f.n_sharp_edges} sharp edges, "
                        f"curvature={f.min_curvature_radius:.4f}m"
                    )
            # Trigger Quick Mesh if user selected it
            if params.get("use_quick_mesh") and geo_path:
                self._log.append_log("[wizard] Starting Quick Mesh...")
                self._on_quick_mesh()
            else:
                self._set_workflow_stage("mesh", "active")

    def _on_quick_mesh(self):
        if not self._meshes:
            QMessageBox.warning(self, "No Geometry", "Load a geometry first.")
            return
        self._log.append_log("[quick] Running Quick Mesh...")
        self._set_workflow_stage("generate", "active")
        from cfmesh_autogui.commercial.quick_mesh import QuickMesh
        from cfmesh_autogui.commercial.mesh_engine import MeshEngine
        from cfmesh_autogui.core.geometry import suggest_cell_sizes, compute_bbox_dim
        detail = self._params.get_detail_level()
        # Auto-select algorithm via MeshEngine
        has_wsl = self._of_config.validate()
        n_wt = sum(1 for m in self._meshes if m.is_watertight)
        all_wt = n_wt == len(self._meshes)
        engine = MeshEngine()
        algo = engine.auto_select(
            patch_count=len(self._meshes), watertight=all_wt, has_wsl=has_wsl,
        )
        self._log.append_log(f"[quick] Algorithm: {algo.value}")
        # Auto cell sizes via QuickMesh helper
        bbox_dim = compute_bbox_dim(self._meshes)
        s_max, s_min = suggest_cell_sizes(self._meshes, detail=detail)
        quality_mult = {"coarse": 1.5, "medium": 1.0, "fine": 0.6, "very_fine": 0.4}
        detail_key = detail if detail in quality_mult else "medium"
        s_max *= quality_mult[detail_key]
        s_min *= quality_mult[detail_key]
        self._params._max_cell.setValue(s_max)
        self._params._min_cell.setValue(s_min)
        # Auto BL via QuickMesh
        qm = QuickMesh()
        bl_params = qm._auto_bl_params(self._meshes, bbox_dim, all_wt)
        if bl_params:
            self._params._bl_checkbox.setChecked(True)
            self._params._bl_n_layers.setValue(bl_params.get("nLayers", 3))
            self._log.append_log(
                f"[quick] Auto BL: {bl_params.get('nLayers')} layers, "
                f"growth {bl_params.get('thicknessRatio', 1.2):.2f}"
            )
        self._log.append_log(
            f"[quick] Suggested: max={s_max:.4f} min={s_min:.4f} "
            f"(bbox={bbox_dim:.4f})"
        )
        self._params._on_run()

    def _on_auto_fix_quality(self, _action: str):
        if not self._case_dir:
            QMessageBox.warning(self, "No Case", "Generate a mesh first.")
            return
        if getattr(self, '_quality_fix_thread', None) and self._quality_fix_thread and self._quality_fix_thread.isRunning():
            QMessageBox.information(self, "Already Running", "A quality fix cycle is already in progress.")
            return
        self._quality_fix_thread = QThread()
        self._quality_fix_worker = QualityFixWorker(self._of_config)
        self._quality_fix_worker.moveToThread(self._quality_fix_thread)
        self._quality_fix_worker.log_line.connect(self._log.append_log, Qt.QueuedConnection)

        def _on_qf_finished(code: int):
            self._log.append_log(f"[quality-fix] Completed (exit {code})")
            self._set_workflow_stage("quality", "done")
            self._launch_checkmesh()

        def _on_qf_failed(msg: str):
            self._log.append_log(f"[quality-fix] FAILED: {msg}")
            self._set_workflow_stage("quality", "error")

        self._quality_fix_worker.finished.connect(_on_qf_finished, Qt.QueuedConnection)
        self._quality_fix_worker.finished.connect(self._quality_fix_thread.quit, Qt.QueuedConnection)
        self._quality_fix_worker.finished.connect(self._quality_fix_worker.deleteLater, Qt.QueuedConnection)
        self._quality_fix_worker.failed.connect(_on_qf_failed, Qt.QueuedConnection)
        self._quality_fix_worker.failed.connect(self._quality_fix_thread.quit, Qt.QueuedConnection)
        self._quality_fix_worker.failed.connect(self._quality_fix_worker.deleteLater, Qt.QueuedConnection)
        max_cell = self._params.get_max_cell()
        min_cell = self._params.get_min_cell()
        bl_params = self._params.get_bl_params()
        case_dir = self._case_dir
        self._quality_fix_thread.started.connect(
            lambda: self._quality_fix_worker.run(
                case_dir,
                max_cell=max_cell,
                min_cell=min_cell,
                bl_params=bl_params,
            ),
        )
        self._quality_fix_thread.start()
        self._log.append_log("[quality-fix] Auto-fix cycle started...")

    def _on_undo(self):
        self._undo_stack.undo()

    def _on_redo(self):
        self._undo_stack.redo()

    def _on_launch_paraview(self):
        if not self._case_dir:
            QMessageBox.warning(self, "No Case", "Generate a mesh first.")
            return
        import subprocess
        try:
            subprocess.Popen(["paraview", str(self._case_dir)], shell=sys.platform == "win32")  # ✅ F-008
            self._log.append_log(f"[paraview] Launched ParaView for {self._case_dir}")
        except FileNotFoundError:
            QMessageBox.warning(
                self, "ParaView Not Found",
                "ParaView executable not found in PATH.\n"
                "Install ParaView and ensure 'paraview' is available.",
            )
        except Exception as e:
            logger.error("ParaView launch failed: %s", e)
            QMessageBox.critical(self, "Error", f"Failed to launch ParaView:\n{e}")

    def _cleanup_thread(self, attr_thread: str, attr_worker: str, timeout_ms: int = 3000):
        thread = getattr(self, attr_thread, None)
        worker = getattr(self, attr_worker, None)
        if thread and thread.isRunning():
            if worker:
                try:
                    worker.finished.disconnect()
                except (TypeError, RuntimeError):
                    pass
                worker.deleteLater()
            thread.quit()
            if not thread.wait(timeout_ms):
                thread.terminate()
                thread.wait(1000)
        for a in (attr_thread, attr_worker):
            if hasattr(self, a):
                setattr(self, a, None)

    def closeEvent(self, event):
        s = self._settings()
        s.set_value("window/size", self.size())
        s.set_value("window/pos", self.pos())
        # Purge stale keys to keep settings clean
        try:
            s.purge_stale_keys()
        except Exception:
            pass
        self._params.save_params(s.raw)
        self._viewer.save_background(s.raw)
        s.sync()
        if self._runner and self._runner.is_running:
            self._runner.terminate()
        # Clean up ALL background threads
        for attr_t, attr_w in [
            ("_wsl_check_thread", "_wsl_check_worker"),
            ("_feature_thread", "_feature_worker"),
            ("_parallel_thread", "_parallel_worker"),
            ("_polydual_thread", "_polydual_worker"),
            ("_checkmesh_thread", "_checkmesh_worker"),
            ("_quality_fix_thread", "_quality_fix_worker"),
            ("_decompose_thread", "_decompose_worker"),
        ]:
            self._cleanup_thread(attr_t, attr_w)
        from cfmesh_autogui.core.gmsh_wrapper import gmsh_shutdown
        gmsh_shutdown()
        octo.log_event("main_window", "close", "app closed")
        super().closeEvent(event)
