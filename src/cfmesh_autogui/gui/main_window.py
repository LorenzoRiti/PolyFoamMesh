from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import cadquery as cq
import trimesh
from PySide6.QtCore import QSize, Qt, QTimer, Slot
from PySide6.QtGui import (
    QGuiApplication,
    QKeySequence,
    QShortcut,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFrame,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.boundary_reader import parse_boundary
from cfmesh_autogui.core.case_setup import setup_case
from cfmesh_autogui.core.feature_detector import FeatureDetectWorker
from cfmesh_autogui.core.geometry import (
    classify_faces,
    compute_bbox_dim,
    compute_bbox_full,
    compute_patch_cell_sizes,
    compute_volume,
    create_test_cylinder,
    estimate_cell_count_geometric,
    load_geometry,
    load_step,
    cfmesh_cell_budget,
    scale_meshes,
    suggest_cell_sizes,
    tessellate_patches,
    unit_to_scale,
    validate_cell_sizes,
)
from cfmesh_autogui.core.gmsh_wrapper import gmsh_shutdown
from cfmesh_autogui.core.meshdict_gen import write_meshdict
from cfmesh_autogui.core.openfoam_runner import (
    CheckMeshWorker,
    DecomposeParWorker,
    ErrorType,
    ParallelMeshWorker,
    PolyDualWorker,
    QualityFixWorker,
    RetryRunner,
    DualPolyWorker,
    WslCheckWorker,
    analyze_error,
)
from cfmesh_autogui.core.stl_writer import export_surface_file
from cfmesh_autogui.core.workflow import MeshingWorkflow, Status, Step
from cfmesh_autogui.gui.constants import MAX_RECENT_STEP_FILES, MAX_STEP_FILE_BYTES
from cfmesh_autogui.gui.design_tokens import APP_VERSION, STATUS_READY
from cfmesh_autogui.gui.log_panel import LogPanel
from cfmesh_autogui.gui.log_tags import Tag
from cfmesh_autogui.gui.params_panel import ParamsPanel
from cfmesh_autogui.gui.quality_panel import QualityPanel
from cfmesh_autogui.gui.settings_migration import AppSettings
from cfmesh_autogui.gui.style import COLOR_DANGER, COLOR_PASS, COLOR_TEXT_DISABLED
from cfmesh_autogui.gui.task_runner import FunctionWorker, TaskManager
from cfmesh_autogui.gui.viewer_widget import ViewerWidget
from cfmesh_autogui.octopoda_local import octo

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


def _free_ram_gb() -> float:
    """Free physical RAM in GB (best-effort, ctypes/GlobalMemoryStatusEx).

    Used by the pre-flight guard before a GMSH volume run: the reported
    freeze was the system thrashing into swap while GMSH + WSL2/OpenFOAM +
    the GUI competed for memory, not a bug in the meshing pipeline itself
    (GMSH always completed its .msh; the GUI main thread then starved and
    Windows killed it with an App Hang).
    """
    try:
        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return stat.ullAvailPhys / (1024 ** 3)
    except Exception:
        return 999.0  # unknown — never block on a probe failure


def _free_disk_gb(path: str | Path) -> float:
    """Free disk space in GB on the volume hosting ``path`` (best-effort)."""
    try:
        return shutil.disk_usage(str(path)).free / (1024 ** 3)
    except Exception:
        return 999.0


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
        # Set when a polyhedral mesh failed checkMesh and was rolled back to
        # the tet mesh — prevents re-converting the same tet mesh in a loop.
        self._poly_fallback_active = False
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

        self._status.showMessage("Checking OpenFOAM environment...")
        QTimer.singleShot(100, self._start_deferred_wsl_check)

        from cfmesh_autogui.core.disk_cleanup import auto_cleanup

        def _startup_cleanup():
            def work(worker):
                auto_cleanup(keep_last=20, max_days=60)
                return None

            # Disk scan + rmtree of old cases must not run on the UI thread
            # at startup; defer and run in a background task.
            self._submit_task(
                "startup_cleanup", FunctionWorker(work),
                heartbeat_timeout_s=600.0,
            )

        QTimer.singleShot(3000, _startup_cleanup)

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
        export_menu.addAction("Mesh (.msh)...", lambda: self._on_export_mesh("gmsh"))
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
        tm.addSeparator()
        # EXPERIMENTAL native cut-cell -> median dual (Fasi 1-6). Isolated
        # here in Tools, not in the mesher combo: it was there before
        # (commit 16e52e1) and confused users next to the CFD Poly GMSH
        # path. Kept explicitly experimental/opt-in.
        tm.addAction(
            "Experimental: Native Poly (100% poly, nativo)...",
            self._on_experimental_native_poly,
        )
        tm.addAction(
            "Experimental: Native Poly — help",
            self._on_experimental_native_poly_help,
        )

    def _on_theme_change(self, mode: str) -> None:
        from cfmesh_autogui.gui.theme import apply_theme, set_theme_mode
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
        # Adaptive (OODA) Mesh — runs the closed-loop meshing engine
        self._ribbon_btns["adaptive"] = _make_ribbon_btn(
            "Adaptive", "adaptive", self._on_adaptive_mesh, checkable=False,
        )
        adaptive_btn = self._ribbon_btns["adaptive"]
        adaptive_btn.setCheckable(False)
        adaptive_btn.setAutoExclusive(False)
        adaptive_btn.setToolTip(
            "OODA closed-loop adaptive meshing: auto-detects quality issues, "
            "applies local corrections, iterates until convergence."
        )
        # Solution-adaptive refinement (SAMR): solve -> refine where the FLOW
        # demands it -> remesh from CAD -> repeat. Distinct from the OODA
        # engine above, which remediates mesh QUALITY; this one needs a first
        # mesh to exist and refines from the actual velocity field.
        self._ribbon_btns["samr"] = _make_ribbon_btn(
            "Solve Adaptive", "adaptive", self._on_solution_adaptive,
            checkable=False,
        )
        samr_btn = self._ribbon_btns["samr"]
        samr_btn.setCheckable(False)
        samr_btn.setAutoExclusive(False)
        samr_btn.setToolTip(
            "Solution-adaptive refinement: runs a CFD solve, finds where the "
            "flow is under-resolved (velocity-gradient based), and re-meshes "
            "finer there automatically — no manual refinement zones needed."
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
        self._params.pick_refinement_requested.connect(self._on_pick_refinement)
        self._viewer.refinement_point_picked.connect(self._on_refinement_point_picked)
        self._params.add_box_requested.connect(self._on_add_refinement_box)
        self._params.refinements_changed.connect(self._on_refinements_changed)
        self._viewer.refinement_boxes_changed.connect(self._on_refinement_boxes_changed)
        rl.addWidget(self._params)

        self._cell_count_label = QLabel("Cells: --")
        self._cell_count_label.setStyleSheet(
            f"color: {COLOR_TEXT_DISABLED}; padding-right: 8px; font-weight: bold;"
        )

        self._checkmesh_worker: CheckMeshWorker | None = None

        h_splitter.addWidget(right)
        h_splitter.setSizes([900, 400])

        self._quality = QualityPanel()
        self._quality.fix_requested.connect(self._on_auto_fix_quality)

        self._log = LogPanel()
        self._log.setMinimumHeight(60)

        # Unified background-task manager: every long job (GMSH, gmshToFoam,
        # dual, checkMesh, export, feature detection, AMR, ...) runs through
        # this one pattern — see gui/task_runner.py.
        self._tasks = TaskManager(self)
        self._tasks.stalled.connect(self._on_task_stalled)
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

    @staticmethod
    def _pos_on_a_screen(pos) -> bool:
        """True if a title-bar-sized area at *pos* would land on some
        currently connected screen. Requires at least 40px so a window
        barely clipped at a screen edge still counts as "on screen" (the
        common case), while a position left over from a since-removed
        monitor or an old higher-resolution/DPI setup does not."""
        from PySide6.QtCore import QRect
        probe = QRect(pos.x(), pos.y(), 40, 40)
        return any(
            screen.availableGeometry().intersects(probe)
            for screen in QApplication.screens()
        )

    def _restore_settings(self):
        s = self._settings()
        size = s.get_value("window/size")
        pos = s.get_value("window/pos")
        if size is not None:
            self.resize(size)
        if pos is not None and self._pos_on_a_screen(pos):
            self.move(pos)
        # Otherwise leave Qt's own default placement — a saved position
        # from a monitor/resolution/DPI setup that no longer matches
        # (external monitor unplugged, scaling changed, etc.) previously
        # got applied unconditionally, opening the window partially off
        # the current screen every time.
        self._viewer.restore_background(s.raw)
        self._params.restore_params(s.raw)
        self._ribbon_btns["expert"].setChecked(self._params.is_expert_mode())

    def _load_geometry(self, shape: cq.Shape):
        """Load a CAD shape: classify/tessellate/heal in a background task
        so big CAD files never freeze the UI, then apply on the GUI thread."""
        self._original_shape = shape
        self._set_workflow_stage("geometry", "done")
        self._set_workflow_stage("mesh", "active")
        self._status.showMessage("Classifying faces...")

        def work(worker):
            n = len(list(shape.Faces()))
            patches = classify_faces(shape)
            worker.report_progress("Tessellating geometry...", 55.0)
            meshes = list(tessellate_patches(patches))
            worker.report_progress("Healing geometry...", 75.0)
            heal_lines: list[str] = []
            try:
                from cfmesh_autogui.commercial.cad_healer import CADHealer
                reports = CADHealer().heal_meshes(meshes)
                for mesh, report in zip(meshes, reports):
                    if report.operations:
                        name = mesh.metadata.get("name", "?")
                        heal_lines.append(
                            f"Healed '{name}': {', '.join(report.operations)}"
                        )
            except Exception as exc:
                logger.debug("CAD healing skipped: %s", exc)
            return {"n": n, "patches": patches, "meshes": meshes, "heal_lines": heal_lines}

        def on_done(_name: str, result: dict):
            n = result["n"]
            patches = result["patches"]
            self._log.append_log(f"[geom] Loaded: {n} faces, type: {shape.geomType()}")
            for line in result["heal_lines"]:
                self._log.append_log(f"{Tag.GEOM} {line}")
            self._log.append_log(f"{Tag.GEOM} Patches: {[(n_, len(f)) for n_, f in patches]}")
            self._unscaled_meshes = result["meshes"]
            self._apply_loaded_meshes("CAD")

        def on_failed(_name: str, msg: str):
            logger.error("Geometry loading failed: %s", msg)
            self._log.append_log(f"{Tag.ERROR} {msg}")
            QMessageBox.critical(self, "Tessellation Error", str(msg))

        self._submit_task(
            "geometry_load", FunctionWorker(work),
            on_finished=on_done, on_failed=on_failed,
            heartbeat_timeout_s=600.0,
        )

    def _load_stl_async(self, path: str):
        """Load an STL file in a background task (big STLs are heavy)."""
        self._set_workflow_stage("geometry", "done")
        self._set_workflow_stage("mesh", "active")
        self._status.showMessage("Loading STL...")

        def work(worker):
            worker.report_progress("Loading STL...", 30.0)
            return list(load_geometry(path))

        def on_done(_name: str, meshes):
            self._unscaled_meshes = meshes
            self._apply_loaded_meshes("STL")

        def on_failed(_name: str, msg: str):
            logger.error("Failed to load STL: %s", msg)
            self._log.append_log(f"{Tag.ERROR} Failed to load STL: {msg}")
            QMessageBox.critical(self, "Error", f"Failed to load STL:\n{msg}")

        self._submit_task(
            "geometry_load", FunctionWorker(work),
            on_finished=on_done, on_failed=on_failed,
            heartbeat_timeout_s=600.0,
        )

    def _apply_loaded_meshes(self, source: str) -> None:
        """Apply newly loaded unscaled meshes to the UI (GUI thread only)."""
        self._rebuild_scaled_meshes()
        names = [m.metadata.get("name", "?") for m in self._meshes]
        logger.info("%s solids: %s", source, names)
        self._log.append_log(f"{Tag.GEOM} {source} solids: {', '.join(names)}")
        self._params.set_patches(names)
        self._params.set_suggest_meshes(self._meshes)
        self._viewer.show_cad(self._meshes)
        dx, dy, dz = compute_bbox_full(self._meshes)
        self._params.set_bbox(dx, dy, dz)
        logger.info("Domain bbox: %.4f x %.4f x %.4f", dx, dy, dz)
        self._log.append_log(
            f"{Tag.GEOM} Domain: {dx:.3f} \u00d7 {dy:.3f} \u00d7 {dz:.3f} m"
        )
        self._refresh_workflow(geometry_loaded=True, sizing_ready=True)
        self._start_watertight_check(self._unscaled_meshes)

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

        The HARD limit is the real RAM-based budget (cfmesh_cell_budget),
        not a fixed 80M: WSL2's memory cap differs per machine, and a mesh
        above the budget gets OOM-killed inside Linux. Below the budget,
        for meshes under 2M cells or 10 min estimate, proceeds silently;
        otherwise asks with a clear time estimate.
        """
        # unit tests call this method on a stub without _params — default
        # to serial (no parallel peak) there
        parallel_enabled, n_cores = False, 1
        params = getattr(self, "_params", None)
        if params is not None:
            parallel_enabled, n_cores = params.get_parallel_params()
        budget = cfmesh_cell_budget(
            parallel_cores=n_cores if parallel_enabled else 1,
        )
        budget_cells = budget["max_cells"]
        if est_cells >= budget_cells:
            hours = est_seconds / 3600.0
            msg = (
                f"<b>Mesh oltre la memoria disponibile</b><br><br>"
                f"La stima è di ~{est_cells:,} celle, ma questo computer può "
                f"gestirne ~{budget_cells:,} (limite RAM).<br><br>"
                f"Dimensioni cella: max={max_cell:.4g} m, min={min_cell:.4g} m.<br><br>"
                "Il motore di mesh gira in WSL2, che ha un tetto di memoria "
                "proprio: oltre questo limite il sistema operativo uccide il "
                "processo (OOM killer) e il meshing crasha.<br><br>"
                "<b>Suggerimenti:</b><br>"
                "• Usa un livello di dettaglio più grossolano (es. Media o Grossolana)<br>"
                "• Riduci la dimensione massima della cella nel pannello Advanced<br>"
                "• Aumenta la memoria di WSL2: <code>wsl --set-memory Ubuntu 32G</code> "
                "poi riavvia WSL"
            )
            QMessageBox.critical(self, "Mesh Troppo Grande", msg)
            return False

        if est_cells < self.LARGE_MESH_CELLS and est_seconds < self.LARGE_MESH_SECONDS:
            return True

        minutes = est_seconds / 60.0
        hours = minutes / 60.0

        if est_cells >= budget_cells * 0.5:
            msg = (
                f"<b>Mesh molto grande:</b> ~{est_cells:,} celle "
                f"(~{hours:.1f} ore stimate).<br><br>"
                f"Dimensioni cella: max={max_cell:.4g} m, min={min_cell:.4g} m.<br><br>"
                "Questa mesh potrebbe richiedere più di 8 GB di RAM e diverse ore.<br>"
                "Assicurati che WSL2 abbia memoria sufficiente "
                "(esegui <code>wsl --set-memory Ubuntu {'quantità'}</code> in PowerShell).<br><br>"
                "Procedere?"
            )
        else:
            msg = (
                f"<b>Mesh grande:</b> ~{est_cells:,} celle "
                f"(~{minutes:.0f} min).<br><br>"
                f"Dimensioni cella: max={max_cell:.4g} m, min={min_cell:.4g} m.<br>"
                "Aumentare la dimensione minima (o scegliere un livello di dettaglio "
                "più grossolano) riduce molto il tempo.<br><br>"
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
        """application/turbulence_model to pass to setup_case()."""
        return {}

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

    def _start_watertight_check(self, meshes: list[trimesh.Trimesh]) -> None:
        """Check watertightness in a background subprocess to avoid GUI freeze
        during pymeshfix repair.

        Falls back to synchronous check if the subprocess cannot be launched
        (e.g. trimesh not in PATH for the subprocess Python).
        """
        if not meshes:
            return
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix="cfmesh_watertight_"))
        stl_paths = []
        try:
            for i, m in enumerate(meshes):
                p = tmp_dir / f"patch_{i}.stl"
                m.export(str(p))
                stl_paths.append(p)
        except Exception as exc:
            logger.debug("Watertight STL export failed, using sync path: %s", exc)
            self._check_watertight_sync(meshes)
            return

        from cfmesh_autogui.core.openfoam_runner import WatertightWorker
        self._cleanup_thread("_watertight_thread", "_watertight_worker")
        w = WatertightWorker(stl_paths)
        self._watertight_worker = w
        self._log.append_log(f"{Tag.GEOM} Checking watertightness...")

        def on_watertight_result(result: dict):
            watertight = result.get("watertight", False)
            n_open = result.get("n_open_edges", 0)
            reports_data = result.get("reports", [])
            repaired_paths = result.get("repaired_paths", [])

            if watertight:
                self._log.append_log(f"{Tag.GEOM} Watertight check: OK (closed volume).")
                self._refresh_workflow(watertight=True)
            else:
                self._log.append_log(
                    f"{Tag.WARN} Watertight check: {n_open} open boundary edges."
                )
                for r in reports_data:
                    for op in r.get("operations", []):
                        self._log.append_log(f"{Tag.GEOM} [auto-fix/{r.get('method','?')}] {op}")
                if repaired_paths:
                    try:
                        import trimesh
                        repaired = [trimesh.load(p) for p in repaired_paths]
                        self._unscaled_meshes = repaired
                        self._rebuild_scaled_meshes()
                        self._params.set_patches(
                            [m.metadata.get("name", "?") for m in self._meshes]
                        )
                    except Exception as exc:
                        logger.debug("Could not load repaired meshes: %s", exc)
                if watertight:
                    self._log.append_log(
                        f"{Tag.GEOM} Watertight check: closed by auto-repair."
                    )
                self._refresh_workflow(watertight=watertight)

        def on_watertight_failed(_name: str, msg: str):
            self._log.append_log(f"{Tag.WARN} Watertight check failed (background): {msg}")
            self._log.append_log(f"{Tag.GEOM} Watertight check: running synchronously...")
            self._check_watertight_sync(meshes)

        self._submit_task(
            "watertight", w,
            on_finished=lambda _n, result: on_watertight_result(result),
            on_failed=on_watertight_failed,
            heartbeat_timeout_s=600.0,
        )

    def _check_watertight_sync(self, meshes: list[trimesh.Trimesh]) -> None:
        """Synchronous fallback for watertight check — now run in a
        background task so the GUI never freezes on large meshes."""
        if not meshes:
            return

        def work(worker):
            try:
                combined = trimesh.util.concatenate(meshes)
                combined.merge_vertices()
            except Exception as exc:
                logger.debug("Watertight check skipped: %s", exc)
                return {"watertight": None}
            if combined.is_watertight:
                return {"watertight": True}
            import trimesh.grouping as _grouping
            boundary_edges = combined.edges[
                _grouping.group_rows(combined.edges_sorted, require_count=1)
            ]
            n_open = len(boundary_edges)
            from cfmesh_autogui.core.geometry_repair import attempt_auto_repair
            bbox_dim = compute_bbox_dim(meshes)
            repaired, reports = attempt_auto_repair(meshes, bbox_dim)
            return {"watertight": False, "n_open": n_open, "repaired": repaired, "reports": reports}

        def on_done(_name: str, result: dict):
            if result.get("watertight") is None:
                return  # concatenate/merge failed — old behavior: skip silently
            if result.get("watertight"):
                self._log.append_log(f"{Tag.GEOM} Watertight check: OK (closed volume).")
                self._refresh_workflow(watertight=True)
                return
            self._log.append_log(
                f"{Tag.WARN} Watertight check: geometry has {result['n_open']} open "
                "boundary edges. cfMesh needs a fully closed domain \u2014 attempting "
                "automatic repair..."
            )
            reports = result.get("reports") or []
            for report in reports:
                for op in report.operations:
                    self._log.append_log(f"{Tag.GEOM} [auto-fix/{report.method}] {op}")
                for warn in report.warnings:
                    self._log.append_log(f"{Tag.WARN} [auto-fix/{report.method}] {warn}")
            if reports and reports[-1].watertight_after:
                self._unscaled_meshes = result["repaired"]
                self._rebuild_scaled_meshes()
                self._params.set_patches([m.metadata.get("name", "?") for m in self._meshes])
                self._viewer.show_cad(self._meshes)
                self._log.append_log(
                    f"{Tag.GEOM} Watertight check: fixed automatically, geometry is now closed."
                )
                self._refresh_workflow(watertight=True)
                return
            self._refresh_workflow(watertight=False)
            n_rem = reports[-1].open_edges_after if reports else result.get("n_open", 0)
            self._log.append_log(
                f"{Tag.WARN} Automatic repair could not fully close the geometry "
                f"({n_rem} open edges remain). This usually means a real missing "
                "face rather than a tessellation gap \u2014 check for a patch that "
                "doesn't share its full boundary with its neighbours in the 3D "
                "view, or re-export the CAD model with the gaps closed. Meshing "
                "may still fail or leak."
            )

        def on_error(_name: str, msg: str):
            logger.exception("Auto-repair failed: %s", msg)
            self._log.append_log(f"{Tag.ERROR} Auto-repair crashed: {msg}")
            self._refresh_workflow(watertight=False)

        self._submit_task(
            "watertight_sync", FunctionWorker(work),
            on_finished=on_done, on_failed=on_error,
            heartbeat_timeout_s=600.0,
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
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        from cfmesh_autogui.core.disk_cleanup import (
            auto_cleanup,
            cases_disk_usage_mb,
            list_cases,
        )
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

    # ------------------------------------------------------------------
    # EXPERIMENTAL native poly mesher: cut-cell nativo -> dual (Fasi 1-6)
    # ------------------------------------------------------------------

    def _on_experimental_native_poly_help(self):
        """Explain the experimental native mesher (Fasi 1-6)."""
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(
            self, "Native Poly (sperimentale)",
            "Mesher sperimentale nativo (algoritmo nostro, puro Python, "
            "Fasi 1-6):\n\n"
            "1. cut-cell castellated dalla tessellazione CAD (core/native_mesher.py)\n"
            "2. dual mediano -> mesh 100% poliedrica (core/hex_poly_dual.py), "
            "con smoothing quality-driven\n\n"
            "Nessun mesher esterno per generare (algoritmo nostro, puro "
            "Python); WSL usato solo dalla validazione checkMesh.\n\n"
            "Limiti noti (misurati):\n"
            "- funziona bene su superfici curve (es. sfera);\n"
            "- su pareti parallele alla griglia (es. venturi) il dual puo' fallire: "
            "serve lo snapping (non ancora implementato per quel caso);\n"
            "- refinement locale su un dettaglio piccolo interno: non c'e' "
            "(serve un vero octree, non implementato — solo la banda di "
            "margine attorno al pezzo puo' essere graduata);\n"
            "- lo strato limite (boundary layer) e' solo un calcolo di "
            "raccomandazione (y+/spessore), non vengono ancora inserite "
            "celle prismatiche;\n"
            "- la qualita' (skewness/non-ortho) del dual nativo e' in "
            "genere peggiore di quella del dual Polymesh (barycentric).\n\n"
            "Il mesh cut-cell viene conservato in "
            "constant/polyMesh_hex_native; il mesh 100% poly e' in "
            "constant/polyMesh.",
        )

    def _on_experimental_native_poly(self):
        """Run the EXPERIMENTAL native cut-cell -> median-dual mesher.

        Isolated: uses only the loaded geometry + case dir; the standard
        cfMesh/quick-mesh flow is untouched. Never escalates to another
        mesher — a failure is reported, not substituted.
        """
        from PySide6.QtWidgets import QMessageBox

        if not self._meshes:
            QMessageBox.warning(self, "No Geometry", "Load a geometry first.")
            return
        self._params.set_meshing_enabled(False)
        self._params.set_all_enabled(False)
        self._run_id += 1
        self._start_native_poly_worker(list(self._meshes), self._run_id)

    def _start_native_poly_worker(self, meshes: list, my_id: int):
        """Run the experimental native cut-cell -> median-dual mesher."""
        from cfmesh_autogui.commercial.native_poly_bridge import (
            NativePolyParams,
            run_native_poly,
        )

        # _on_checkmesh_finished is SHARED with the standard cfMesh/GMSH
        # flow and decides whether to auto-launch a poly conversion pass
        # (_launch_polydual / _launch_gmsh_poly_dual) based on
        # _current_mesher_type / _poly_was_converted, both otherwise left
        # over from whatever mesher the combo was last set to (e.g.
        # "cfmesh", the default) — that stale state made checkMesh, after
        # a native-poly run, launch cfMesh's OWN polyDualMesh on TOP of a
        # mesh that is already 100% poly, corrupting it. native-poly's
        # dual conversion already happened inside run_native_poly, so mark
        # it done up front and label the mesher explicitly.
        self._current_mesher_type = "native_poly"
        self._poly_was_converted = True
        self._poly_converter_name = "hex_poly_dual (nativo)"
        self._poly_fallback_active = False
        self._log.append_log(
            "[native-poly] cut-cell nativo -> dual 100% poly (sperimentale)...")
        self._status.showMessage("native-poly: cut-cell + dual ...")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = self._resolve_case_root()
        self._case_dir = root / f"native_poly_{ts}"
        self._case_dir.mkdir(parents=True, exist_ok=True)
        self._params.set_case_dir(str(self._case_dir))

        detail = self._params.get_detail_level()
        params = NativePolyParams(detail_level=detail)

        self._cleanup_thread("_native_poly_thread", "_native_poly_worker")

        def worker_fn(worker):
            try:
                def progress_cb(pct: int, stage: str, msg: str):
                    worker.report_progress(f"{stage}: {msg}", float(pct))
                return run_native_poly(meshes, self._case_dir, params,
                                       progress=progress_cb)
            except Exception as e:
                raise RuntimeError(str(e)) from e

        self._native_poly_worker = worker_fn
        self._native_poly_start_time = time.time()

        def on_progress(_name, stage, pct):
            self._progress.setRange(0, 100)
            self._progress.setValue(int(pct))
            self._log.append_log(f"[native-poly] {stage}")
            self._status.showMessage(f"native-poly: {stage} ({pct:.0f}%)")

        def on_finished(_name, result):
            self._on_native_poly_finished(result, my_id)

        def on_failed(_name, msg):
            self._on_native_poly_failed(msg, my_id)

        self._submit_task(
            "native_poly", FunctionWorker(worker_fn),
            on_finished=on_finished,
            on_failed=on_failed,
            on_progress=on_progress,
            heartbeat_timeout_s=600.0,
        )

    def _on_native_poly_finished(self, result, my_id: int):
        """Handle successful native-poly meshing completion."""
        if my_id != self._run_id:
            return
        self._progress.setVisible(False)
        elapsed = time.time() - getattr(
            self, "_native_poly_start_time", time.time())
        if result.success:
            self._log.append_log(
                f"[native-poly] DONE: {result.n_cells:,} celle 100% poly "
                f"({result.n_hex_cells:,} hex + {result.n_cut_cells:,} cut "
                f"primali) in {elapsed:.1f}s — "
                f"skew={result.max_skewness:.2f} "
                f"nonOrtho={result.max_non_ortho:.1f}° "
                f"defects={result.defects}"
            )
            if result.bl_recommendation:
                bl = result.bl_recommendation
                self._log.append_log(
                    f"[native-poly] BL sizing (raccomandazione, celle non "
                    f"inserite): {bl.get('n_layers')} layer, primo "
                    f"{bl.get('first_layer_height'):.3g} m"
                )
            self._status.showMessage(
                f"native-poly: {result.n_cells:,} cells ready")
            self._params.set_all_enabled(True)
            self._params.set_real_cell_count(result.n_cells)
            poly_dir = self._case_dir / "constant" / "polyMesh"
            try:
                from cfmesh_autogui.core.boundary_reader import parse_boundary
                from cfmesh_autogui.core.case_setup import setup_case
                boundary_path = poly_dir / "boundary"
                if boundary_path.exists():
                    patches = parse_boundary(boundary_path)
                    setup_case(self._case_dir, patches,
                               **self._case_setup_kwargs())
                    self._log.append_log(
                        "[setup] Case files generated (0/, system/).")
            except Exception as e:  # noqa: BLE001
                logger.warning("Case setup after native-poly: %s", e)
            self._viewer.show_mesh(self._case_dir)
            if poly_dir.exists():
                self._launch_checkmesh()
        else:
            self._log.append_log(
                f"[native-poly] FAILED: {result.message}")
            self._params.set_all_enabled(True)
            if result.errors:
                QMessageBox.critical(
                    self, "Native Poly Failed",
                    f"Native Poly fallito:\n\n{result.message}\n\n"
                    + "\n".join(result.errors))

    def _on_native_poly_failed(self, msg: str, my_id: int):
        """Handle native-poly failure."""
        if my_id != self._run_id:
            return
        self._log.append_log(f"[native-poly] ERROR: {msg}")
        self._progress.setVisible(False)
        self._params.set_all_enabled(True)
        QMessageBox.critical(self, "Native Poly Failed",
                             f"Native Poly fallito:\n\n{msg}")

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
        if ext in (".step", ".stp", ".stl"):
            self._loaded_step_path = path
            # Heavy loading (STEP import / STL parse / tessellation) runs in
            # background tasks — loading used to block the UI thread here.
            if ext in (".step", ".stp"):
                self._load_step_async(path)
            else:
                self._load_stl_async(path)
            self._push_recent_step(path)
            return
        self._log.append_log(
            f"{Tag.ERROR} Unsupported format: '{ext}'. "
            "Use .step, .stp, or .stl."
        )

    def _load_step_async(self, path: str):
        """Load a STEP file in a background task, then tessellate via
        _load_geometry (itself async)."""
        self._status.showMessage("Importing STEP geometry...")

        def work(worker):
            worker.report_progress("Importing STEP geometry...", 20.0)
            return load_step(path)

        def on_done(_name: str, shape):
            self._load_geometry(shape)

        def on_failed(_name: str, msg: str):
            logger.error("Failed to load STEP: %s", msg)
            self._log.append_log(f"{Tag.ERROR} Failed to load STEP: {msg}")
            QMessageBox.critical(self, "Error", f"Failed to load STEP:\n{msg}")

        self._submit_task(
            "geometry_step", FunctionWorker(work),
            on_finished=on_done, on_failed=on_failed,
            heartbeat_timeout_s=600.0,
        )

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

    def _on_pick_refinement(self):
        """Start 3D picking mode for refinement zone centre."""
        self._viewer.start_refinement_pick()
        self._log.append_log(
            f"{Tag.GEOM} Click a point on the mesh to set the refinement zone centre..."
        )

    @Slot(float, float, float)
    def _on_refinement_point_picked(self, x: float, y: float, z: float):
        """Handle a picked point: add refinement zone at that location."""
        self._params.add_refinement_from_pick(x, y, z)
        self._log.append_log(
            f"{Tag.GEOM} Refinement zone at ({x:.4f}, {y:.4f}, {z:.4f})"
        )

    # --- 3D refinement boxes -------------------------------------------------
    def _refinement_boxes(self) -> list:
        return [
            dict(b) for b in self._params.get_manual_refinements()
            if isinstance(b, dict) and b.get("type") == "box"
        ]

    def _refinement_box_base_size(self) -> float:
        """Level-1 base cell size: the cross-section bulk size for the current
        detail, falling back to the Max Cell Size field."""
        from cfmesh_autogui.core.gmsh_wrapper import _GMSH_DETAIL
        from cfmesh_autogui.core.refinement_boxes import base_bulk_size
        detail = self._params.get_detail_level() or "medium"
        cells_across = (_GMSH_DETAIL.get(detail) or {}).get("cells_across", 20)
        cross = 0.0
        if self._meshes:
            ext = []
            for m in self._meshes:
                try:
                    b = m.bounds
                    ext.append([b[1][i] - b[0][i] for i in range(3)])
                except Exception:
                    continue
            if ext:
                dims = [max(ext[i][k] for i in range(len(ext))) for k in range(3)]
                dims.sort()
                cross = dims[1] if len(dims) == 3 else (dims[0] if dims else 0.0)
        fallback = self._params.get_max_cell() or 0.01
        return base_bulk_size(cross, cells_across, fallback=fallback)

    def _mesh_bbox(self):
        lo = [1e30] * 3
        hi = [-1e30] * 3
        for m in self._meshes:
            try:
                b = m.bounds
                for i in range(3):
                    lo[i] = min(lo[i], float(b[0][i]))
                    hi[i] = max(hi[i], float(b[1][i]))
            except Exception:
                continue
        return (lo, hi) if hi[0] > lo[0] else (None, None)

    def _on_add_refinement_box(self):
        """Add a 3D refinement box at the model centre, edited with the box
        gizmo shown in the viewer (drag its handles). Only choice: the level."""
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        if not self._meshes:
            QMessageBox.warning(self, "No Geometry", "Load a geometry first.")
            return
        lo, hi = self._mesh_bbox()
        if lo is None:
            QMessageBox.warning(self, "No Geometry",
                                "Could not compute the model bounds.")
            return
        base = self._refinement_box_base_size()
        level, ok = QInputDialog.getInt(
            self, "Box Refinement Level",
            "Livello di raffinamento:\n"
            "  1 = dimensione base (nessun raffinamento extra)\n"
            "  2 = meta delle celle\n"
            "  3 = un quarto delle celle\n"
            "  ... ogni livello dimezza la dimensione precedente\n\n"
            "Poi trascina le maniglie sul box nel viewer per dimensionarlo.",
            2, 1, 6, 1,
        )
        if not ok:
            return
        try:
            from cfmesh_autogui.core.refinement_boxes import (
                suggest_box, apply_level,
            )
            box = suggest_box(
                lo, hi, fraction=0.5, level=level, base_size=base,
            )
            box["base_size"] = base
            box = apply_level(box, level, base)
            self._params.add_refinement_box(box)
            self._log.append_log(
                f"[refine] added box L{level} -> cell size "
                f"{box['cell_size']:.5g} m - trascina il box nel viewer"
            )
            self._refresh_box_viewer()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Add refinement box failed")
            self._log.append_log(f"{Tag.ERROR} Add box failed: {exc}")

    def _refresh_box_viewer(self):
        boxes = self._refinement_boxes()
        if self._viewer is None:
            return
        self._viewer.show_refinement_boxes(boxes)
        if boxes:
            # Enter drag-edit mode so the arrows are immediately live
            # (refinement_boxes_changed is already connected in __init__).
            self._viewer.start_refinement_box_drag()

    @Slot(object)
    def _on_refinement_boxes_changed(self, boxes: list):
        """A box's arrows were dragged to a new size — persist and refresh."""
        if not boxes:
            return
        self._params.set_refinement_boxes(boxes)
        self._log.append_log(
            f"[refine] box resized: "
            f"{boxes[-1].get('xmax', 0) - boxes[-1].get('xmin', 0):.3g} x "
            f"{boxes[-1].get('ymax', 0) - boxes[-1].get('ymin', 0):.3g} x "
            f"{boxes[-1].get('zmax', 0) - boxes[-1].get('zmin', 0):.3g} m"
        )

    @Slot(object)
    def _on_refinements_changed(self, _all: object):
        """Level changed in the panel — refresh the box rendering."""
        self._refresh_box_viewer()

    def _gmsh_refinement_zones(self) -> list:
        """Zones passed to the GMSH volume mesher: throat-detected spheres plus
        every manual zone (3D boxes and legacy spheres)."""
        manual = self._params.get_manual_refinements()
        spheres = [m for m in manual if isinstance(m, dict) and "centre" in m]
        boxes = [b for b in self._refinement_boxes()]
        return list(getattr(self, "_throat_zones", None) or []) + spheres + boxes

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
            # Non-blocking stop (terminate() can block the UI for 30 s).
            self._runner.request_stop()
            self._kill_wsl_processes()
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
        """"Automatic" mode: pick the best available mesher so the user
        never has to think about mesher internals.

        Cartesian cfMesh (hex-dominant via WSL2/cartesianMesh) is the
        highest-quality option when WSL2/OpenFOAM is available;
        otherwise our Polymesh (native polyhedral barycentric dual, no WSL)
        is the fallback — it always produces a 100% polyhedral mesh.
        """
        # Uses the cached startup WSL availability instead of calling
        # OFConfig.validate() here — that launches wsl.exe and can block
        # for minutes on a cold WSL2 boot, which used to freeze the whole
        # UI the moment "Automatic" was selected.
        wsl_ok = getattr(self, "_wsl_available", None)
        if wsl_ok is None:
            # Startup check still in flight: assume available; the async
            # WslCheckWorker path will surface the real state before any
            # meshing command actually runs.
            wsl_ok = True
        try:
            if wsl_ok:
                return "cfmesh"
        except Exception as exc:
            logger.debug("Automatic mesher: WSL check failed: %s", exc)
        try:
            from cfmesh_autogui.commercial.autopoly_bridge import create_mesher
            if create_mesher() is not None:
                return "autopoly"
        except Exception as exc:
            logger.debug("Automatic mesher: autopoly unavailable: %s", exc)
        return "gmsh_direct_poly"  # our Polymesh — always polyhedral

    def _on_run_meshing(self):
        if not self._meshes:
            QMessageBox.warning(
                self, "No Geometry",
                "No geometry loaded. Please load a STEP or CAD file first "
                "(use File \u2192 Load Geometry, drag & drop, or press Ctrl+O).",
            )
            return

        # GMSH/feature-detect/checkMesh run as separate tasks and do not set
        # RetryRunner.is_running.  Without this guard repeated clicks can
        # start overlapping native GMSH processes, making the UI appear stuck
        # and corrupting which run owns the callback.
        for task_name, label in (
            ("gmsh_volume", "_gmsh_worker"),
            ("feature_detect", "_feature_worker"),
            ("checkmesh", "_checkmesh_worker"),
        ):
            if self._tasks.is_running(task_name):
                logger.warning("Meshing request ignored: %s is still running", label)
                self._log.append_log(
                    f"{Tag.WARN} A meshing stage is still running ({label}); "
                    "cancel it before starting another run."
                )
                return

        self._run_id += 1
        self._poly_was_converted = False
        self._poly_fallback_active = False
        self._low_ram_gmsh_threads = False
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
            _friendly = {
                "cfmesh": "Cartesian cfMesh (WSL2)",
                "autopoly": "Autopoly",
                "gmsh_direct_poly": "Polymesh (our poly mesher)",
                "gmsh_direct": "FEM Tetra (GMSH)",
            }
            self._log.append_log(
                f"{Tag.CASE} Automatic: using {_friendly.get(mesher_type, mesher_type)}."
            )
            if mesher_type == "cfmesh" and not self._params.get_poly_conversion():
                # "Automatic" should mean "give me a solid polyhedral mesh"
                # without an extra manual step — cfMesh alone produces a
                # hex-dominant mesh; polyDualMesh conversion is what
                # actually makes it polyhedral (verified: clean checkMesh
                # pass on the same curved geometry autopoly still struggles
                # with). Only auto-enables it here, never overriding an
                # explicit user choice when cfMesh is picked directly.
                self._params._poly_check.setChecked(True)
                logger.info(
                    "Automatic mesher resolved to cfmesh — auto-enabling polyDualMesh conversion."
                )
                self._log.append_log(
                    f"{Tag.CASE} Automatic: enabling polyhedral conversion (polyDualMesh)."
                )
            # NOTE: _resolve_auto_mesher() now returns "gmsh_direct_poly"
            # (our Polymesh) as its final fallback, never plain
            # "gmsh_direct" — so Automatic always produces a polyhedral
            # mesh; the explicit "FEM Tetra (GMSH)" button is the only way
            # to ask for pure tetrahedra, and that explicit choice is
            # respected (no auto-enabling of the poly conversion).
        self._current_mesher_type = mesher_type
        adaptive_on = self._params.get_adaptive_sizing_enabled()
        poly_on = self._params.get_poly_conversion()
        logger.info(
            "Meshing run #%d: mesher=%s poly_conversion=%s adaptive=%s",
            my_id, mesher_type, poly_on, adaptive_on,
        )
        # This state decides more about the outcome than anything else in
        # the run (whether poly conversion happens at all, whether sizing
        # follows the geometry or a fixed manual value) — confirmed live
        # that its absence from the visible log was the actual blocker
        # when a user couldn't tell why a run finished as a pure tet mesh:
        # the equivalent logger.info() line above only ever reached the
        # Python console/file log, never the in-app log panel actually
        # being read.
        self._log.append_log(
            f"{Tag.CASE} Mesher: {mesher_type} | poly conversion: "
            f"{'ON' if poly_on else 'OFF'} | adaptive sizing: "
            f"{'ON' if adaptive_on else 'OFF (manual Max/Min Cell Size)'}"
        )
        if mesher_type == "gmsh_direct" and poly_on is False:
            self._log.append_log(
                f"{Tag.WARN} Tetrahedral (FEM) selected: this run will "
                "produce a pure tet mesh, no polyhedral conversion. "
                "Select 'Polyhedral (CFD)' in the Mesh tab for a poly mesh."
            )
        octo.log_event("main_window", "run_meshing_start",
            f"run_id={my_id} mesher={mesher_type}")
        if mesher_type == "autopoly":
            orig = getattr(self, "_loaded_step_path", None)
            if orig is None:
                orig = self._make_temp_geometry_for_gmsh()
            if orig is None:
                QMessageBox.warning(
                    self, "autopoly Needs Geometry",
                    "No geometry loaded. Load a STEP, STL, or use the test cylinder first.",
                )
                return
            self._autopoly_geom_path = orig
            self._params.set_meshing_enabled(False)
            self._params.set_all_enabled(False)
            self._start_autopoly_worker(orig, my_id)
            return
        if mesher_type != "cfmesh":
            # No auto-enable needed here: for gmsh_direct/gmsh_direct_poly
            # the mesher combo's own _on_mesher_changed already forces the
            # poly checkbox to match the choice (checked+hidden for
            # "Polyhedral (CFD)", unchecked+hidden for "Tetrahedral (FEM)")
            # the moment the user selects it — see params_panel.py. An
            # earlier version of this block re-forced it to checked
            # whenever mesher_type == "gmsh_direct" specifically, which
            # (once "Tetrahedral (FEM)" and "Polyhedral (CFD)" became
            # distinct mesher_type values instead of one shared "gmsh_direct"
            # entry) meant picking "Tetrahedral (FEM)" — meant to be
            # poly-off — got its poly conversion silently re-enabled here
            # at Run time regardless.
            # BL is now fully supported on the poly path: the boundary-layer
            # engine (`core/bl_poly.py`) adds prism layers to the poly mesh
            # AFTER the dual conversion, so the converter still only ever
            # sees pure tetrahedra. Nothing to disable here.
            orig = getattr(self, "_loaded_step_path", None)
            if orig is None:
                orig = self._make_temp_geometry_for_gmsh()
            if orig is None:
                QMessageBox.warning(
                    self, "Mesher Needs Geometry",
                    "No geometry loaded. Load a STEP, STL, or use the "
                    "test cylinder first.",
                )
                return
            self._params.set_meshing_enabled(False)
            self._params.set_all_enabled(False)
            logger.info("Dispatching GMSH worker: run_id=%d mesher=%s", my_id, mesher_type)
            self._start_gmsh_volume_worker(orig, my_id)
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

    def _start_deferred_wsl_check(self) -> None:
        """Deferred WSL check on startup — allows UI to render first."""
        self._start_wsl_check(self._run_id)

    def _start_wsl_check(self, my_id: int) -> None:
        # my_id is stashed on self rather than captured in a lambda: a plain
        # Python lambda has no QObject thread affinity for PySide6 to queue
        # against, so a Qt.QueuedConnection to one can run on the emitting
        # (background) thread instead of the GUI thread — which is exactly
        # what happened here, crashing the process the moment the callback
        # touched the log widget's QTextDocument from the worker thread.
        # Connecting straight to a bound method of this QObject (as done
        # everywhere else in this file) queues correctly.
        self._wsl_check_run_id = my_id
        w = WslCheckWorker(self._of_config)
        self._wsl_check_worker = w
        self._submit_task(
            "wsl_check", w,
            on_finished=lambda _n, available: self._on_wsl_check_finished(available),
            heartbeat_timeout_s=60.0,
        )

    def _on_wsl_check_finished(self, of_available: bool) -> None:
        self._wsl_available = of_available
        my_id = getattr(self, "_wsl_check_run_id", -1)
        if my_id not in (0, self._run_id):
            logger.debug("Stale WSL-check callback ignored (got %d, current %d).", my_id, self._run_id)
            return
        if my_id == 0:
            # Deferred startup check — no message box
            if of_available:
                logger.info("OpenFOAM v2512 detected via WSL2.")
                self._status.showMessage("Ready — OpenFOAM v2512 via WSL2")
            else:
                logger.warning("OpenFOAM not detected via WSL2.")
                self._log.append_log(f"{Tag.WARN} OpenFOAM not detected via WSL.")
                self._log.append_log(f"{Tag.WARN} Ensure WSL2 + OpenFOAM v2512 are installed.")
                self._status.showMessage("OpenFOAM not found — meshing disabled")
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
        # STL export of a finely-tessellated part can be hundreds of MB —
        # run it in a background task under a nested event loop so the UI
        # stays responsive during the write.
        meshes_for_export = list(self._meshes)
        ok, err = self._run_ui_worker_blocking(
            "surface_stl_export",
            lambda w: export_surface_file(meshes_for_export, self._case_dir),
            timeout_s=600.0,
        )
        if not ok:
            logger.error("STL export failed: %s", err)
            self._log.append_log(f"{Tag.ERROR} STL export failed: {err}")
            QMessageBox.critical(
                self, "STL Export Failed",
                f"Could not write the surface STL file for meshing:\n\n{err}\n\n"
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
        # Pass the SAME CAD-unit scale factor already applied to the loaded
        # meshes: the detector reads the raw CAD file, so without this its
        # suggestions come back in the file's own units (mm) and get used
        # as metres — confirmed live on a 3 m model authored in mm, where
        # it suggested a 150 m max cell size.
        self._feature_worker = FeatureDetectWorker(
            step_path, self._params.get_detail_level(), self._current_scale,
        )
        self._submit_task(
            "feature_detect", self._feature_worker,
            on_finished=lambda _n, payload: self._on_feature_detect_finished(
                my_id, surface_file, bbox_dim, *payload,
            ),
            heartbeat_timeout_s=600.0,
            signal_shapes={"finished": 2},
        )

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

        # Local refinement: detect narrow passages via local thickness field.
        #
        # "Rifinitura automatica" (get_adaptive_sizing_enabled, checked by
        # default) only ever drove GMSH's own internal sizing field
        # (generate_volume_mesh's adaptive path, gated further down by
        # mesher_type == "gmsh_direct"/poly) — it has NO effect at all for
        # cfMesh/cartesianMesh, which has no equivalent internal field and
        # instead needs EXPLICIT objectRefinements boxes to refine a narrow
        # passage (confirmed live: cartesianMesh output was byte-identical
        # whether minCellSize was the old uniform value or the new
        # feature-aware one — cfMesh's own automatic-refinement heuristic
        # did not consider the passage a "feature" worth refining on its
        # own). detect_refinement_regions/throat_detector.py is exactly the
        # mechanism that provides this for cfMesh, wired here off the SAME
        # visible "Rifinitura automatica" toggle the user already expects
        # to control this (the previous separate, hidden, off-by-default
        # get_auto_refine_enabled() checkbox was dead UI — removed
        # 2026-08-14, see params_panel.py).
        self._throat_zones = None
        _cfmesh_needs_explicit_refinement = (
            getattr(self, "_current_mesher_type", "") == "cfmesh"
            and self._params.get_adaptive_sizing_enabled()
        )
        if _cfmesh_needs_explicit_refinement and self._meshes:
            try:
                from cfmesh_autogui.core.throat_detector import (
                    detect_refinement_regions,
                )
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
                    # Show detected zones as semi-transparent spheres in the viewer
                    try:
                        self._viewer.show_refinement_zones(zones)
                    except Exception as exc:
                        logger.debug("Could not display refinement zones: %s", exc)
                else:
                    logger.debug("Refinement detection: no narrow passages found.")
            except Exception as exc:
                logger.warning("Refinement detection skipped: %s", exc)

        # Graded refinement field (cfMesh only): detect_refinement_regions
        # above only ever emits the few "significant minima" zones it
        # finds (2-3, typically) — a real fix for "there's an unrefined
        # gap" but nowhere near GMSH's continuous, smoothly-graded local
        # sizing field, which cfMesh has no equivalent of. This builds a
        # much denser approximation: many small boxes, growth-rate graded,
        # covering the WHOLE surface's local thickness variation rather
        # than just its sharpest few minima — see
        # geometry.build_graded_refinement_boxes's own docstring for the
        # full method. Kept separate from self._throat_zones (which also
        # feeds the BL/throat compatibility check and the viewer's sphere
        # display — RefinementZone objects, a different shape than these
        # plain box dicts) and merged into object_refinements alongside it
        # further down.
        # DISABLED (2026-08-14): a real user reported this hangs/crashes
        # cartesianMesh on a real (non-synthetic) geometry — up to 200
        # extra objectRefinements boxes plus a second full-surface
        # ray-cast pass on top of throat_detector's own was only ever
        # stress-tested on a small synthetic venturi, not a real CAD part
        # with many patches/faces. Left in place (function still exists
        # in geometry.py, tests still pass) but not wired to run
        # automatically until it's been fixed and re-verified against a
        # real, complex geometry — do not re-enable this block without
        # that.
        self._graded_refinement_boxes = None

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
        lo, est, hi = estimate_cell_count_geometric(
            self._meshes, volume, safe_max, patch_sizes,
        )
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
        self._params.set_geometry_cell_estimate(lo, est, hi)
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
                    from cfmesh_autogui.core.throat_detector import (
                        check_bl_throat_compatibility,
                    )
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
        graded_boxes = getattr(self, "_graded_refinement_boxes", None)
        if graded_boxes:
            object_refinements = list(object_refinements or []) + graded_boxes
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

            # Apply OpenMP thread configuration before launching
            openmp_mode, openmp_threads = self._params.get_openmp_params()
            if openmp_mode == "custom" and openmp_threads is not None:
                self._of_config.set_openmp_threads(openmp_threads)
                self._log.append_log(
                    f"{Tag.MESHING} OpenMP: {openmp_threads} thread(s) (custom mode)."
                )
            else:
                self._of_config.set_openmp_threads(None)  # auto via OpenMPAccel
                mpi_ranks = n_cores if parallel_enabled else 0
                self._log.append_log(
                    f"{Tag.MESHING} OpenMP mode: {openmp_mode}"
                    f"{f' ({mpi_ranks} MPI ranks)' if mpi_ranks else ''}."
                )

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

        my_id = self._run_id
        self._parallel_worker = ParallelMeshWorker(
            self._case_dir, self._of_config,
            max_cell=max_cell, min_cell=min_cell, n_cores=n_cores,
            patch_names=[m.metadata.get("name", "wall") for m in self._meshes],
            bl_params=bl_params,
        )

        def on_finished(_name, result):
            if my_id != self._run_id:
                return
            if result.cell_count == 0:
                self._parallel_fallback("Parallel mesh produced 0 cells")
                return
            self._on_parallel_mesh_success(my_id)

        def on_failed(_name, msg):
            if my_id != self._run_id:
                return
            self._parallel_fallback(msg)

        def on_cancelled(_name, msg):
            self._log.append_log(f"{Tag.CANCELLED} Parallel meshing stopped.")

        self._submit_task(
            "parallel", self._parallel_worker,
            on_finished=on_finished,
            on_failed=on_failed,
            on_cancelled=on_cancelled,
            signal_shapes={"cancelled": 0},
            # MPI runs emit no live output for many minutes — a short
            # watchdog budget would stall-fire and auto-cancel a healthy run.
            heartbeat_timeout_s=3600.0,
        )

    def _make_temp_geometry_for_gmsh(self) -> str | None:
        from datetime import datetime as _dt
        _default_base = Path.home() / "cfmesh_cases" / ".gmsh_temp"
        if " " in str(_default_base):
            _default_base = Path("C:/cfmesh_cases") / ".gmsh_temp"
        base = self._case_dir or _default_base
        base.mkdir(parents=True, exist_ok=True)
        ts = _dt.now().strftime('%Y%m%d_%H%M%S')

        # Exporting a complex CAD part to STEP (or a large tessellation to
        # STL) can take minutes — run it in a background task under a
        # nested event loop so the UI stays responsive.
        shape = self._original_shape
        meshes = list(self._meshes)

        def _work(worker):
            if shape is not None:
                tmp = str(base / f"gmsh_geometry_{ts}.step")
                try:
                    import cadquery as cq
                    cq.exporters.export(shape, tmp, exportType="STEP")
                    logger.info("Exported original shape to STEP for GMSH: %s", tmp)
                    return tmp
                except Exception as e:
                    logger.warning("Failed to export STEP for GMSH: %s", e)
            if meshes:
                tmp = str(base / f"gmsh_geometry_{ts}.stl")
                try:
                    from cfmesh_autogui.core.stl_writer import export_multisolid_stl
                    export_multisolid_stl(meshes, tmp)
                    logger.info("Exported meshes to STL for GMSH: %s", tmp)
                    return tmp
                except Exception as e:
                    logger.warning("Failed to export STL for GMSH: %s", e)
            return None

        ok, result = self._run_ui_worker_blocking(
            "gmsh_geometry_export", _work, timeout_s=900.0,
        )
        if not ok:
            logger.error("GMSH geometry export failed: %s", result)
        return result if ok else None

    def _resolve_case_root(self) -> Path:
        root = Path.home() / "cfmesh_cases"
        if " " in str(root):
            root = Path("C:/cfmesh_cases")
            if not _check_drive_writable("C:\\"):
                root = Path(str(root).replace(" ", "_"))
        return root

    def _start_gmsh_volume_worker(self, step_path: str, my_id: int):
        from cfmesh_autogui.core.openfoam_runner import GmshVolumeWorker
        logger.info(
            "GMSH stage entered: run_id=%d step=%s mesher=%s",
            my_id, step_path, getattr(self, "_current_mesher_type", ""),
        )

        # Pre-flight guard: GMSH volume meshing is RAM- and disk-hungry, and
        # on a machine where WSL2/OpenFOAM + the GUI already compete for
        # memory a big mesh makes the system thrash into swap — the main
        # thread stops being scheduled, Windows reports "non risponde" and
        # kills the whole app (confirmed from a real session: every CFD Poly
        # run produced a 0.7-1.1 GB .msh, then the GUI was App-Hang-killed).
        # Block the clearly-hopeless cases and warn on the borderline ones
        # instead of letting the user watch a freeze.
        try:
            free_ram = _free_ram_gb()
            free_disk = _free_disk_gb(self._resolve_case_root())
            if free_ram < 5.0:
                self._log.append_log(
                    f"{Tag.ERROR} RAM libera insufficiente ({free_ram:.1f} GB) — "
                    "GMSH avrebbe saturato il sistema e bloccato l'app."
                )
                QMessageBox.critical(
                    self, "Memoria insufficiente",
                    f"RAM libera: {free_ram:.1f} GB (serve almeno 5 GB).\n\n"
                    "GMSH volume meshing avrebbe fatto impallare il sistema.\n"
                    "Chiudi altre applicazioni, oppure riduci la memoria "
                    "riservata a WSL2 (file .wslconfig) e riprova.",
                )
                self._params.set_all_enabled(True)
                return
            if free_disk < 1.0:
                self._log.append_log(
                    f"{Tag.ERROR} Disco insufficiente ({free_disk:.1f} GB liberi) "
                    "— il file .msh non ci starebbe."
                )
                QMessageBox.critical(
                    self, "Disco pieno",
                    f"Spazio libero su disco: {free_disk:.1f} GB (serve almeno 1 GB).\n\n"
                    "Libera spazio e riprova.",
                )
                self._params.set_all_enabled(True)
                return
            if free_ram < 10.0 or free_disk < 3.0:
                warnings = []
                if free_ram < 10.0:
                    warnings.append(f"RAM libera: {free_ram:.1f} GB")
                if free_disk < 3.0:
                    warnings.append(f"disco libero: {free_disk:.1f} GB")
                ret = QMessageBox.warning(
                    self, "Risorse limitate",
                    "Risorse basse prima del meshing GMSH:\n\n"
                    + "\n".join(f"• {w}" for w in warnings)
                    + "\n\nIl meshing può saturare il sistema e rallentare "
                    "o bloccare l'app. Continuare comunque?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if ret != QMessageBox.Yes:
                    self._log.append_log(f"{Tag.CANCELLED} Meshing GMSH annullato dall'utente.")
                    self._params.set_all_enabled(True)
                    return
                # Continue with a reduced thread budget to keep the OS alive.
                if free_ram < 10.0:
                    os.environ["GMSH_NUM_THREADS"] = "4"
                    self._low_ram_gmsh_threads = True
                    self._log.append_log(
                        f"{Tag.WARN} RAM bassa: GMSH limitato a 4 thread per "
                        "mantenere il sistema reattivo."
                    )
        except Exception as exc:
            # A probe failure must never block meshing.
            logger.debug("Pre-flight resource check skipped: %s", exc)

        self._log.append_log("[gmsh] Starting volume mesh generation in background...")
        self._status.showMessage("GMSH: volume mesh...")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = self._resolve_case_root()
        self._case_dir = root / f"gmsh_direct_{ts}"
        self._case_dir.mkdir(parents=True, exist_ok=True)
        # gmshToFoam (and checkMesh/polyDualMesh after it) need a real
        # OpenFOAM case skeleton — system/controlDict at minimum. Every
        # other mesher path gets this from write_meshdict() or similar;
        # this one never got it at all. Uncovered by fixing the freeze
        # above: gmshToFoam failed immediately with "cannot find file
        # .../system/controlDict" — previously invisible because nothing
        # downstream of the freeze ever ran.
        (self._case_dir / "system").mkdir(parents=True, exist_ok=True)
        (self._case_dir / "constant").mkdir(parents=True, exist_ok=True)
        # Single shared writer (core.case_setup.write_control_dict) — no
        # longer reached through commercial.mesh_engine.
        from cfmesh_autogui.core.case_setup import write_control_dict
        write_control_dict(self._case_dir)
        (self._case_dir / "system" / "fvSchemes").write_text(
            "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
            "ddtSchemes { default steadyState; }\n"
            "gradSchemes { default Gauss linear; }\n"
            "divSchemes { default Gauss linear; }\n"
            "laplacianSchemes { default Gauss linear corrected; }\n"
            "interpolationSchemes { default linear; }\n"
            "snGradSchemes { default corrected; }\n",
            encoding="ascii",
        )
        (self._case_dir / "system" / "fvSolution").write_text(
            "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
            "solvers { p { solver PCG; preconditioner DIC; tolerance 1e-6; relTol 0.1; } }\n",
            encoding="ascii",
        )
        self._params.set_case_dir(str(self._case_dir))
        msh_path = self._case_dir / "mesh.msh"
        detail = self._params.get_detail_level()
        bl_params = self._params.get_bl_params()
        n_layers = bl_params.get("nLayers", 0) if bl_params else 0
        bl_thickness = bl_params.get("firstLayerThickness", 0.005) if bl_params else None
        bl_expansion = bl_params.get("thicknessRatio", 1.2) if bl_params else 1.2
        # Adaptive sizing toggle (Mesh tab, next to the Mesh Fineness
        # slider): this is now the ONLY thing deciding which sizing path
        # GMSH takes. Previously `max_cells_target > 0` also forced the
        # adaptive path regardless of the checkbox — but the slider's
        # target is NEVER zero (it maps slider position 0..20 to
        # 10K..20M cells, see _slider_to_cells), so that condition was
        # always true and the checkbox was unreachable dead UI: there was
        # no way to actually get literal/manual sizing from the GUI.
        # max_cells_target itself is only meaningful to the adaptive path
        # (generate_volume_mesh ignores it whenever an explicit max_cell
        # is passed), so it is only forwarded when adaptive is on.
        adaptive = self._params.get_adaptive_sizing_enabled()
        if adaptive:
            max_cell = 0
            min_cell = 0
            max_cells_target = self._params.get_max_cells_target()
        else:
            max_cell = self._params.get_max_cell()
            min_cell = self._params.get_min_cell()
            max_cells_target = 0

        # Propagate the user's OpenMP thread selection to the GMSH
        # subprocess so a "Custom" thread count also applies to GMSH
        # meshing (GMSH_NUM_THREADS is honoured by gmsh_wrapper). When the
        # user leaves the auto/balanced modes, no override is set and
        # gmsh_wrapper's own capped default keeps the system responsive
        # instead of GMSH pinning every logical core (the "mesh di gmsh
        # impalla il sistema" freeze). A custom count is honoured but capped
        # at the physical core count: oversubscribing logical cores (2x on
        # hyperthreaded CPUs) is exactly the saturation that makes the app
        # white-screen while GMSH runs.
        _openmp_mode, _openmp_threads = self._params.get_openmp_params()
        if _openmp_mode == "custom" and _openmp_threads is not None:
            try:
                _physical = os.cpu_count() or _openmp_threads
            except Exception:
                _physical = _openmp_threads
            capped = min(_openmp_threads, _physical)
            if capped != _openmp_threads:
                self._log.append_log(
                    f"{Tag.WARN} GMSH threads capped: {_openmp_threads} -> "
                    f"{capped} (physical cores) to keep the system responsive."
                )
            os.environ["GMSH_NUM_THREADS"] = str(capped)
        elif getattr(self, "_low_ram_gmsh_threads", None):
            # Pre-flight guard already capped threads for a RAM-tight
            # machine — keep its override instead of clearing it.
            pass
        else:
            os.environ.pop("GMSH_NUM_THREADS", None)

        self._cleanup_thread("_gmsh_thread", "_gmsh_worker")
        w = GmshVolumeWorker(step_path, msh_path, detail, n_layers, bl_thickness, bl_expansion,
                             refinement_zones=self._gmsh_refinement_zones(),
                             max_cell_size=max_cell, min_cell_size=min_cell,
                             max_cells_target=max_cells_target)
        self._gmsh_worker = w
        self._gmsh_vol_ctx = {
            "step_path": step_path, "msh_path": msh_path, "detail": detail,
            "bl_params": bl_params, "n_layers": n_layers,
            "bl_thickness": bl_thickness, "bl_expansion": bl_expansion,
            # Fase 3 P3.3: snapshot the ACTUAL values used in this attempt so
            # the no-BL retry reuses them instead of re-reading the spinboxes
            # (which the user may have changed in the meantime).
            "refinement_zones": self._gmsh_refinement_zones(),
            "max_cell": max_cell, "min_cell": min_cell,
            "max_cells_target": max_cells_target,
            "bl_retried": False, "detail_retried": False, "my_id": my_id,
        }

        self._submit_task(
            "gmsh_volume", w,
            on_finished=lambda _n, result: self._on_gmsh_volume_result(result),
            on_failed=lambda _n, msg: self._on_gmsh_volume_failed(msg),
            heartbeat_timeout_s=600.0,
        )
        logger.info("GMSH worker task started: run_id=%d pid_pending=true", my_id)

    def _on_gmsh_volume_result(self, result: dict):
        """Bound method (not a closure) so Qt.QueuedConnection reliably
        delivers this on the main thread — see _start_gmsh_volume_worker's
        comment for the full story of why that distinction matters here."""
        ctx = self._gmsh_vol_ctx
        my_id = ctx["my_id"]
        if my_id != self._run_id:
            return
        msh_path_result = Path(result["path"])
        names = result["names"]
        self._log.append_log(f"[gmsh] Volume mesh: {msh_path_result} — patches: {names}")
        if result.get("poly_dual_risk"):
            self._log.append_log(
                f"{Tag.WARN} Geometry has {result.get('n_surfaces', '?')} surfaces / "
                f"{result.get('n_gaps', '?')}+ tight gaps — polyDualMesh is known to "
                "produce incorrectly-oriented faces on this class of geometry "
                "(confirmed on real parts this complex, unrelated to mesh quality). "
                "The tetrahedral mesh will still be clean; check the final poly "
                "checkMesh report carefully before trusting it for a solver run."
            )
        self._continue_gmsh_direct(my_id, msh_path_result, names)

    def _on_gmsh_volume_failed(self, msg: str):
        """Bound method — see _on_gmsh_volume_result."""
        from cfmesh_autogui.core.openfoam_runner import GmshVolumeWorker
        ctx = self._gmsh_vol_ctx
        my_id = ctx["my_id"]
        if my_id != self._run_id:
            return
        if not ctx["bl_retried"] and ctx["bl_params"] and ctx["n_layers"] > 0:
            ctx["bl_retried"] = True
            self._log.append_log(f"{Tag.WARN} GMSH volume failed with BL — retrying without layers.")
            # Fase 3 P3.3: reuse the exact parameters of the first attempt
            # (snapshot in ctx) instead of re-reading the spinboxes.
            w2 = GmshVolumeWorker(ctx["step_path"], ctx["msh_path"], ctx["detail"], 0, None, 1.2,
                                  refinement_zones=ctx["refinement_zones"],
                                  max_cell_size=ctx["max_cell"],
                                  min_cell_size=ctx["min_cell"],
                                  max_cells_target=ctx["max_cells_target"])
            self._gmsh_worker = w2
            self._submit_task(
                "gmsh_volume", w2,
                on_finished=lambda _n, result: self._on_gmsh_volume_result(result),
                on_failed=lambda _n, m: self._on_gmsh_volume_failed(m),
                heartbeat_timeout_s=600.0,
            )
            return
        # Generic failure (not the BL-specific path above): retry ONCE at
        # the next coarser detail level, instead of giving up immediately.
        # Before this, a generic GMSH crash/exception (e.g. a real CAD
        # part with a periodic-surface parametrization degeneracy — see
        # gmsh_wrapper.py's Mesh.Algorithm comment) reported the EXACT
        # same error twice: nothing about the retry actually changed, so
        # it failed identically both times (confirmed directly from a
        # user's log). A coarser mesh changes the local element size near
        # the problematic surface, which measurably helps this class of
        # GMSH robustness issue (see the same comment) — not guaranteed,
        # but strictly better than repeating an attempt that cannot
        # possibly succeed differently.
        _DETAIL_COARSER = {
            "very_fine": "fine", "fine": "medium",
            "medium": "coarse", "coarse": "very_coarse",
        }
        next_detail = _DETAIL_COARSER.get(ctx["detail"])
        if not ctx["detail_retried"] and next_detail:
            ctx["detail_retried"] = True
            ctx["detail"] = next_detail
            self._log.append_log(
                f"{Tag.WARN} GMSH volume failed — retrying at a coarser "
                f"detail level ({next_detail})."
            )
            w2 = GmshVolumeWorker(
                ctx["step_path"], ctx["msh_path"], next_detail,
                ctx["n_layers"], ctx["bl_thickness"], ctx["bl_expansion"],
                refinement_zones=ctx["refinement_zones"],
                max_cell_size=ctx["max_cell"], min_cell_size=ctx["min_cell"],
                max_cells_target=ctx["max_cells_target"],
            )
            self._gmsh_worker = w2
            self._submit_task(
                "gmsh_volume", w2,
                on_finished=lambda _n, result: self._on_gmsh_volume_result(result),
                on_failed=lambda _n, m: self._on_gmsh_volume_failed(m),
                heartbeat_timeout_s=600.0,
            )
            return
        self._log.append_log(f"[ERROR] GMSH volume: {msg}")
        self._params.set_all_enabled(True)
        QMessageBox.critical(self, "GMSH Failed", f"Volume mesh failed:\n{msg}")

    def _continue_gmsh_direct(self, my_id: int, msh_path: Path, names: list[str]):
        """Convert MSH → OpenFOAM via gmshToFoam.

        Runs in a TaskManager task (gui/task_runner.py) with a cancellable
        Popen loop, so Cancel stops the conversion promptly. The result is
        delivered back to the GUI thread via the task callbacks — no raw
        threading.Thread/queue/QTimer polling here anymore.
        """
        self._log.append_log("[gmsh] Converting to OpenFOAM polyMesh...")
        self._status.showMessage("GMSH: conversion...")
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)

        case_dir = self._case_dir
        msh_name = msh_path.name

        def worker_fn(worker):
            # Dispatch through a fresh gmsh_wrapper.py subprocess (same
            # pattern GmshVolumeWorker already uses reliably), rather than
            # calling wsl.exe directly from this long-lived GUI process —
            # see gmsh_wrapper.py's "convert_to_foam" CLI branch for why.
            import json
            import subprocess
            import traceback
            from cfmesh_autogui.core.openfoam_runner import _gmsh_wrapper_script_cmd
            try:
                args = ["convert_to_foam", str(case_dir), msh_name]
                cmd, run_cwd = _gmsh_wrapper_script_cmd(args, "--gmsh-convert-to-foam")
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, cwd=run_cwd,
                )
                deadline = time.monotonic() + 300
                while proc.poll() is None:
                    if worker.is_cancelled():
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        return None  # cancelled — caller restores the UI
                    if time.monotonic() > deadline:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        proc.wait(10)
                        raise TimeoutError("gmshToFoam timed out after 300s")
                    time.sleep(0.2)
                stdout, stderr = proc.communicate(timeout=10)
                try:
                    payload = json.loads(stdout.strip().splitlines()[-1])
                except Exception:
                    payload = None
                if payload is not None:
                    return (
                        0 if payload.get("success") else 1,
                        payload.get("stdout", ""),
                        payload.get("stderr") or payload.get("error", ""),
                    )
                return (proc.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                return ("error", "gmshToFoam timed out after 300s")
            except Exception as e:
                # Full traceback, not just str(e) — a bare exception message
                # here previously gave no way to diagnose what actually
                # went wrong when this step failed in a real session.
                tb = traceback.format_exc()
                logger.error("MSH conversion worker thread crashed:\n%s", tb)
                return ("error", f"{e}\n\n{tb}")

        def fail(msg: str):
            self._log.append_log(f"[ERROR] MSH conversion: {msg}")
            self._progress.setVisible(False)
            self._params.set_all_enabled(True)
            QMessageBox.critical(self, "Conversion Failed", f"MSH to OpenFOAM failed:\n{msg}")

        def on_conv_finished(_name: str, item):
            if my_id != self._run_id:
                return  # cancelled or superseded by a newer run
            if item is None:
                self._log.append_log(f"{Tag.CANCELLED} MSH conversion cancelled.")
                self._progress.setVisible(False)
                self._params.set_all_enabled(True)
                return
            if item[0] == "error":
                fail(item[1])
                return
            exit_code, stdout, stderr = item
            for line in (stdout or "").splitlines()[-20:]:
                self._log.append_log(f"[gmshToFoam] {line}")
            if exit_code != 0:
                tail = (stderr or stdout or "")[-500:]
                fail(f"gmshToFoam failed (exit {exit_code}): {tail}")
                return
            verify_polymesh()

        def on_conv_failed(_name: str, msg: str):
            fail(msg)

        def on_conv_cancelled(_name: str, msg: str):
            self._log.append_log(f"{Tag.CANCELLED} MSH conversion cancelled.")
            self._progress.setVisible(False)
            self._params.set_all_enabled(True)

        def verify_polymesh(retry: int = 0):
            # gmshToFoam writes through WSL2's 9P filesystem — Windows
            # may not see the new files for a few hundred ms after the
            # process exits (same delay already documented/handled in
            # viewer_widget._poly_dir_state for cartesianMesh output).
            # Retry a few times before concluding it genuinely failed —
            # checking once immediately after exit produced a false
            # "no polyMesh found" on a real run.
            poly_points = case_dir / "constant" / "polyMesh" / "points"
            if not poly_points.exists():
                if my_id != self._run_id:
                    return
                if retry < 6:
                    QTimer.singleShot(300, lambda: verify_polymesh(retry + 1))
                    return
                fail(
                    "gmshToFoam reported success but no polyMesh found in "
                    f"{case_dir / 'constant' / 'polyMesh'}"
                )
                return
            self._log.append_log(f"[gmsh] polyMesh: {case_dir / 'constant' / 'polyMesh'}")
            self._log.append_log("[gmsh] Conversion complete.")
            self._progress.setVisible(False)

            try:
                from cfmesh_autogui.core.boundary_reader import parse_boundary
                from cfmesh_autogui.core.case_setup import setup_case
                boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
                if boundary_path.exists():
                    patches = parse_boundary(boundary_path)
                    setup_case(case_dir, patches, **self._case_setup_kwargs())
                    self._log.append_log("[setup] Case files generated (0/, system/).")
                self._log.append_log(f"{Tag.DONE} Case: {case_dir}")
                self._viewer.show_mesh(case_dir)
                self._status.showMessage("GMSH direct mesh ready")
                self._params.set_all_enabled(True)
                poly_faces = case_dir / "constant" / "polyMesh" / "faces"
                if poly_points.exists() and poly_faces.exists():
                    self._launch_checkmesh()
            except Exception as e:
                logger.error("Post-conversion setup failed: %s", e)
                self._log.append_log(f"[ERROR] Setup: {e}")
                self._params.set_all_enabled(True)

        self._submit_task(
            "gmsh_conv", FunctionWorker(worker_fn),
            on_finished=on_conv_finished,
            on_failed=on_conv_failed,
            on_cancelled=on_conv_cancelled,
            heartbeat_timeout_s=360.0,
        )

    # -----------------------------------------------------------------------
    # autopoly native polyhedral mesher
    # -----------------------------------------------------------------------

    def _start_autopoly_worker(self, geom_path: str, my_id: int):
        """Run autopoly polyhedral meshing in a background thread."""
        from cfmesh_autogui.commercial.autopoly_bridge import (
            AutopolyParams,
            run_autopoly,
        )

        self._log.append_log("[autopoly] Starting CVT-based polyhedral meshing...")
        self._status.showMessage("autopoly: polyhedral mesh...")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = self._resolve_case_root()
        self._case_dir = root / f"autopoly_{ts}"
        self._case_dir.mkdir(parents=True, exist_ok=True)
        self._params.set_case_dir(str(self._case_dir))

        detail = self._params.get_detail_level()
        params = AutopolyParams.from_detail_level(detail)

        bl_params = self._params.get_bl_params()
        if bl_params:
            params.bl_enabled = bool(bl_params.get("enabled", False))
            params.bl_layers = int(bl_params.get("nLayers", 3))
            params.bl_first_height = float(bl_params.get("firstLayerThickness", 0.0005))
            params.bl_growth_rate = float(bl_params.get("thicknessRatio", 1.2))
            params.bl_max_thickness = float(bl_params.get("maxThickness", 0.01))

        self._cleanup_thread("_autopoly_thread", "_autopoly_worker")

        def worker_fn(worker):
            try:
                def progress_cb(pct: int, stage: str, msg: str):
                    # Worker thread — safe: only emits Qt signals through
                    # the TaskManager relay (never touches widgets here).
                    worker.report_progress(f"{stage}: {msg}", float(pct))
                result = run_autopoly(geom_path, self._case_dir, params, progress=progress_cb)
                return result
            except Exception as e:
                raise RuntimeError(str(e)) from e

        self._autopoly_worker = worker_fn
        self._autopoly_start_time = time.time()

        def on_progress(_name, stage, pct):
            self._progress.setRange(0, 100)
            self._progress.setValue(int(pct))
            self._log.append_log(f"[autopoly] {stage}")
            self._status.showMessage(f"autopoly: {stage} ({pct:.0f}%)")

        def on_finished(_name, result):
            self._on_autopoly_finished(result, my_id)

        def on_failed(_name, msg):
            self._on_autopoly_failed(msg, my_id)

        self._submit_task(
            "autopoly", FunctionWorker(worker_fn),
            on_finished=on_finished,
            on_failed=on_failed,
            on_progress=on_progress,
            heartbeat_timeout_s=120.0,
        )

    def _on_autopoly_finished(self, result, my_id: int):
        """Handle successful autopoly meshing completion."""
        if my_id != self._run_id:
            return
        self._progress.setVisible(False)
        elapsed = time.time() - getattr(self, "_autopoly_start_time", time.time())
        if result.success:
            self._log.append_log(
                f"[autopoly] DONE: {result.n_cells:,} polyhedral cells "
                f"in {elapsed:.1f}s — "
                f"skew={result.max_skewness:.2f} "
                f"nonOrtho={result.max_non_ortho:.1f}° "
                f"ar={result.max_aspect_ratio:.0f}"
            )
            self._status.showMessage(f"autopoly: {result.n_cells:,} cells ready")
            self._params.set_all_enabled(True)
            self._params.set_real_cell_count(result.n_cells)
            # Generate case setup (system/controlDict etc.) BEFORE handing the
            # case to the viewer/checkMesh — matches the cfMesh completion
            # path (_on_meshing_finished), where setup_case() always runs
            # first. The viewer's mesh render kicks off OpenFOAM's own
            # foamToVTK utility via WSL, which requires system/controlDict
            # to exist; calling show_mesh() first left a window where that
            # file was still missing.
            poly_dir = self._case_dir / "constant" / "polyMesh"
            try:
                from cfmesh_autogui.core.boundary_reader import parse_boundary
                from cfmesh_autogui.core.case_setup import setup_case
                boundary_path = poly_dir / "boundary"
                if boundary_path.exists():
                    patches = parse_boundary(boundary_path)
                    setup_case(self._case_dir, patches, **self._case_setup_kwargs())
                    self._log.append_log("[setup] Case files generated (0/, system/).")
            except Exception as e:
                logger.warning("Case setup after autopoly: %s", e)
            self._viewer.show_mesh(self._case_dir)
            # Launch checkMesh if OpenFOAM available
            if poly_dir.exists():
                self._launch_checkmesh()
        else:
            self._log.append_log(f"[autopoly] FAILED: {result.message}")
            self._params.set_all_enabled(True)
            if result.errors:
                QMessageBox.critical(
                    self, "autopoly Failed",
                    f"Polyhedral meshing failed:\n\n{result.message}\n\n"
                    + "\n".join(result.errors)
                )

    def _on_autopoly_failed(self, msg: str, my_id: int):
        """Handle autopoly failure."""
        if my_id != self._run_id:
            return
        self._log.append_log(f"[autopoly] ERROR: {msg}")
        self._progress.setVisible(False)
        self._params.set_all_enabled(True)
        QMessageBox.critical(self, "autopoly Failed",
                             f"Polyhedral meshing failed:\n\n{msg}")

    # ------------------------------------------------------------------
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
        self._runner.cell_count_relay.connect(self._on_cell_count_found, Qt.QueuedConnection)
        self._runner.progress_update.connect(self._on_progress_update, Qt.QueuedConnection)

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
            # Deleting decomposed processor dirs can be GBs of I/O — never
            # on the UI thread. The serial fallback doesn't write processor
            # dirs, so the deletion can run concurrently on a daemon thread.
            import glob as _glob
            case_dir = self._case_dir
            stale = [d for d in _glob.glob(str(case_dir / "processor*"))]

            def _cleanup_processor_dirs():
                import shutil as _su
                for d in stale:
                    try:
                        _su.rmtree(d)
                    except Exception:
                        pass

            import threading as _thr
            _thr.Thread(target=_cleanup_processor_dirs, daemon=True).start()
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
        # Kill WSL subprocesses FIRST, before stopping workers — and do it
        # on a daemon thread so the UI responds to Cancel in well under 2s
        # (the wsl.exe probe itself can block for up to 10s).
        import threading as _thr
        _thr.Thread(target=self._kill_wsl_processes, daemon=True).start()
        if getattr(self._runner, "is_running", False):
            # request_stop is non-blocking (MeshWorker checks
            # isInterruptionRequested); the daemon WSL kill above unblocks
            # its subprocess read loop. terminate() would block the UI up
            # to 30s — forbidden.
            self._runner.request_stop()

        # Ask every known worker to cancel its own subprocess (idempotent
        # if the task already finished). The TaskManager below is the
        # authoritative teardown: tokens + kill hooks + bounded cleanup.
        for w_attr in (
            "_parallel_worker", "_gmsh_worker", "_gmsh_conv_worker",
            "_wsl_check_worker", "_feature_worker", "_polydual_worker",
            "_checkmesh_worker", "_quality_fix_worker", "_decompose_worker",
            "_watertight_worker", "_export_worker", "_samr_worker",
        ):
            worker = getattr(self, w_attr, None)
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception as exc:
                    logger.debug("cancel() failed for %s: %s", w_attr, exc)

        if hasattr(self, "_tasks"):
            self._tasks.cancel_all()

        # foamToVTK viewer process (not a WSL meshing process, but it can
        # still be mid-run and must not be left running after cancel).
        viewer_cancel = getattr(self._viewer, "cancel_vtk_process", None)
        if callable(viewer_cancel):
            try:
                viewer_cancel()
            except Exception as exc:
                logger.debug("viewer cancel failed: %s", exc)

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
        if exit_code != 0:
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
            # Offer auto-recovery: increase cell sizes by 30% and retry
            retry = QMessageBox.question(
                self, "Meshing Failed",
                f"cartesianMesh failed (exit {exit_code}).\n\n"
                f"{error.message}\n\n"
                "Auto-recovery: increase cell sizes by 30% and retry?",
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
        logger.info(
            "Post-meshing: polyMesh/points exists=%s case_dir=%s",
            poly_points.exists(), self._case_dir,
        )
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
        from cfmesh_autogui.core.meshdict_gen import write_meshdict
        from cfmesh_autogui.core.stl_writer import export_surface_file
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
            from cfmesh_autogui.core.case_setup import write_control_dict
            write_control_dict(self._case_dir)
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
        logger.info("Launching checkMesh for case_dir=%s", self._case_dir)
        self._checkmesh_run_id = self._run_id
        w = CheckMeshWorker(self._case_dir, self._of_config)
        self._checkmesh_worker = w

        def on_failed(_name: str, msg: str):
            self._log.append_log(f"{Tag.CHECKMESH} FAILED: {msg}")
            QMessageBox.warning(
                self, "Mesh Quality Check Failed",
                f"checkMesh reported errors:\n\n{msg}\n\n"
                "Check the Quality panel for details. "
                "Try reducing cell sizes or enabling auto-fix."
            )

        self._submit_task(
            "checkmesh", w,
            on_finished=lambda _n, report: self._on_checkmesh_finished(report),
            on_failed=on_failed,
        )

    def _on_checkmesh_finished(self, report):
        # Stale guard: checkMesh was launched during a specific meshing
        # run; if a newer run already started, ignore this callback.
        check_run_id = getattr(self, "_checkmesh_run_id", self._run_id)
        if check_run_id != self._run_id:
            logger.debug("Stale checkMesh callback ignored (%d != %d).", check_run_id, self._run_id)
            return
        logger.info(
            "checkMesh finished: passed=%s cells=%d poly_conversion=%s poly_was_converted=%s",
            report.passed, report.cells,
            self._params.get_poly_conversion(),
            getattr(self, "_poly_was_converted", False),
        )
        try:
            payload = report.to_dict()
        except Exception as exc:
            logger.exception("checkMesh report.to_dict() failed")
            self._log.append_log(f"{Tag.ERROR} checkMesh report parse failed: {exc}")
            self._quality.clear_report()
            return
        self._quality.show_report(payload)
        # Fase 3 P3.5: explicit poly quality gate — converter name, cells
        # before -> after, checkMesh verdict and key numbers in ONE line,
        # for both PASS and FAIL of the polyhedral mesh.
        if getattr(self, "_poly_was_converted", False):
            converter = getattr(self, "_poly_converter_name", "polyDualMesh (cfMesh)")
            cells_before = getattr(self, "_cells_before_poly", 0)
            verdict = "PASS" if report.passed else "FAIL"
            self._log.append_log(
                f"[poly] Quality gate: converter={converter} cells "
                f"{cells_before} -> {report.cells} checkMesh={verdict} "
                f"(skew={report.max_skewness:.2f}, "
                f"non-ortho={report.max_non_ortho:.1f}, neg={report.neg_cells})"
            )
        self._refresh_workflow(quality_passed=bool(report.passed))
        if report.cells:
            self._on_cell_count_found(report.cells)
        if report.passed:
            self._set_workflow_stage("quality", "done")
            self._log.append_log(f"{Tag.QUALITY} PASS checkMesh")
            self._status.showMessage("Ready — mesh complete")

            # Polyhedral conversion — DECREASES cells (terminal-face) or
            # increases them (polyDualMesh), depending on path.
            poly_conv = self._params.get_poly_conversion()
            poly_done = getattr(self, "_poly_was_converted", False)
            is_gmsh_tet = getattr(self, "_current_mesher_type", "") in {
                "gmsh_direct", "gmsh_direct_poly",
            }
            logger.info(
                "Poly decision: get_poly_conversion()=%s _poly_was_converted=%s "
                "is_gmsh_tet=%s",
                poly_conv, poly_done, is_gmsh_tet,
            )
            if poly_conv and not poly_done and not getattr(
                self, "_poly_fallback_active", False
            ):
                if is_gmsh_tet:
                    # polyDualMesh has a confirmed structural defect on
                    # complex real geometry, and terminal-face merging only
                    # reaches ~80% polyural coverage — this pipeline's poly
                    # path for GMSH tet meshes is the barycentric dual
                    # rebuild, which is 100% poly and the best measured.
                    logger.info("Launching barycentric dual conversion...")
                    self._launch_gmsh_poly_dual()
                else:
                    logger.info("Launching polyDualMesh conversion...")
                    self._launch_polydual()
                return
            if poly_conv and poly_done:
                logger.info("Poly conversion already done, skipping.")
                self._poly_was_converted = True

            self._launch_decomposepar()
            self._viewer.show_mesh(self._case_dir)
        else:
            self._set_workflow_stage("quality", "error")
            self._log.append_log(f"{Tag.QUALITY} {report.status}")
            self._status.showMessage("Mesh quality check failed")
            if getattr(self, "_poly_was_converted", False):
                # The POLYHEDRAL mesh failed checkMesh.  Do NOT fall through
                # to _auto_quality_fix: that re-meshes the tet at a coarser
                # detail, silently discarding the polyhedral conversion (the
                # exact silent-swap class of bug that cost earlier sessions a
                # day).  User decision: keep the polyhedral mesh and show it
                # in the viewer anyway, with a visible warning, so the user
                # can inspect it even though it did not pass checkMesh.
                self._log.append_log(
                    f"{Tag.WARN} Polyhedral mesh failed checkMesh "
                    f"({report.status}). Keeping the polyhedral mesh and "
                    "showing it anyway (it did not pass the quality check)."
                )
                self._viewer.show_mesh(self._case_dir)
                return
            # Auto-fix: offer to relax cell sizes and re-mesh
            self._auto_quality_fix(report)

    def _auto_quality_fix(self, report) -> None:
        """Auto-fix poor quality by relaxing cell sizes and re-meshing.
        Runs at most 2 iterations to avoid infinite loops.
        Calls _direct_remesh() instead of _on_run_meshing() to skip
        the WSL check + feature detect pipeline.

        _direct_remesh() always runs cfMesh cartesianMesh — if the mesh
        that failed the quality check came from autopoly, falling through
        to it would silently replace the user's chosen polyhedral mesh
        with an unrelated hex mesh. Retry with autopoly itself instead,
        at a coarser detail level, when that was the original mesher.
        """
        n = self._quality_fix_attempts
        if n >= 2:
            self._log.append_log(
                f"{Tag.WARN} Quality auto-fix: max iterations (2) reached."
            )
            return
        self._quality_fix_attempts = n + 1

        if getattr(self, "_current_mesher_type", None) == "autopoly":
            geom_path = getattr(self, "_autopoly_geom_path", None)
            if geom_path is None:
                self._log.append_log(
                    f"{Tag.WARN} Quality auto-fix: no autopoly geometry path saved, skipping."
                )
                return
            slider = self._params._detail_slider
            slider.setValue(max(0, slider.value() - 1))
            self._log.append_log(
                f"{Tag.FIX} Quality auto-fix #{n}: retrying autopoly at coarser detail "
                f"({self._params.get_detail_level()})"
            )
            self._run_id += 1
            self._poly_was_converted = False
            self._poly_fallback_active = False
            self._params.set_meshing_enabled(False)
            self._params.set_all_enabled(False)
            self._start_autopoly_worker(geom_path, self._run_id)
            return

        if getattr(self, "_current_mesher_type", None) in ("gmsh_direct", "gmsh_direct_poly"):
            # Same reasoning as the autopoly branch above, missing here
            # until now: falling through to _direct_remesh() always runs
            # cfMesh cartesianMesh, reading the Manual Max/Min Cell Size
            # fields — which sit disabled/stale under Adaptive sizing
            # (the default) and have nothing to do with whatever Max
            # cells target or detail level actually drove the GMSH mesh
            # that just failed. Confirmed live: a user with Adaptive on
            # and Max cells target=9,000,000 saw their mesh silently
            # replaced by an 80-then-320-cell cfMesh mesh after a poly
            # quality-check failure — a completely different mesher, at
            # completely unrelated cell sizes, with no indication the
            # switch had happened. Retry the same GMSH pipeline instead,
            # one detail level coarser, same as autopoly's own retry.
            step_path = getattr(self, "_loaded_step_path", None)
            if step_path is None:
                self._log.append_log(
                    f"{Tag.WARN} Quality auto-fix: no source geometry path saved, skipping."
                )
                return
            slider = self._params._detail_slider
            slider.setValue(max(0, slider.value() - 1))
            self._log.append_log(
                f"{Tag.FIX} Quality auto-fix #{n}: retrying GMSH at coarser detail "
                f"({self._params.get_detail_level()})"
            )
            self._run_id += 1
            # Confirmed live root cause of a "poly never happens on retry"
            # report: this dispatches a BRAND NEW GMSH tet mesh into a new
            # case_dir, which has never had terminal-face conversion
            # applied — but without this reset, the stale True left over
            # from the PREVIOUS (failed) case's successful conversion made
            # _on_checkmesh_finished's "already done" guard skip poly
            # conversion entirely for the new mesh, leaving it pure tet
            # forever with no error or warning anywhere.
            self._poly_was_converted = False
            self._poly_fallback_active = False
            self._params.set_meshing_enabled(False)
            self._params.set_all_enabled(False)
            self._start_gmsh_volume_worker(step_path, self._run_id)
            return

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
        # Save cell count before poly conversion for comparison
        try:
            from cfmesh_autogui.core.of_reader import of_list_count
            self._cells_before_poly = of_list_count(
                self._case_dir / "constant" / "polyMesh" / "owner"
            )
        except Exception:
            self._cells_before_poly = 0
        self._polydual_run_id = self._run_id
        # Fase 3 P3.5: converter name for the explicit poly quality gate log.
        self._poly_converter_name = "polyDualMesh (cfMesh)"
        feature_angle = 90  # Higher = smoother polyhedral cells
        logger.info(
            "Launching polyDualMesh: case_dir=%s feature_angle=%g cells_before=%d",
            self._case_dir, feature_angle, self._cells_before_poly,
        )
        self._log.append_log("[poly] Converting hex \u2192 polyhedral mesh (polyDualMesh)...")
        self._status.showMessage("Polyhedral conversion...")
        w = PolyDualWorker(self._case_dir, self._of_config,
                           feature_angle=feature_angle)
        self._polydual_worker = w
        self._polydual_start_time = time.monotonic()
        # polyDualMesh's own WSL command buffers all output until it
        # exits (piped through `tail -20` in build_poly_dual_cmd), so
        # PolyDualWorker has nothing to stream while it runs \u2014 on a big
        # mesh this can take a couple of minutes with zero log output,
        # which reads as a frozen app. A periodic heartbeat line is a
        # much smaller change than restructuring that command to stream
        # live, and gives the same reassurance that it's still working.
        heartbeat = QTimer(self)
        heartbeat.setInterval(8000)
        heartbeat.timeout.connect(self._on_polydual_heartbeat)
        heartbeat.start()
        self._polydual_heartbeat = heartbeat
        self._submit_task(
            "polydual", w,
            on_finished=lambda _n, meshes: self._on_polydual_finished(meshes),
            on_failed=lambda _n, msg: self._on_polydual_failed(msg),
            heartbeat_timeout_s=600.0,
        )

    def _on_polydual_heartbeat(self) -> None:
        elapsed = time.monotonic() - getattr(self, "_polydual_start_time", time.monotonic())
        self._log.append_log(f"[poly] ...still converting ({elapsed:.0f}s elapsed, this can take a while on large meshes)")

    def _stop_polydual_heartbeat(self) -> None:
        hb = getattr(self, "_polydual_heartbeat", None)
        if hb is not None:
            hb.stop()
            self._polydual_heartbeat = None

    def _on_polydual_failed(self, msg: str) -> None:
        self._stop_polydual_heartbeat()
        self._log.append_log(f"[poly] FAILED: {msg}")
        QMessageBox.warning(
            self, "Polyhedral Conversion Failed",
            f"polyDualMesh failed:\n\n{msg}\n\n"
            "The hex mesh is still available. "
            "Try a larger feature angle or skip polyhedral conversion."
        )

    def _on_polydual_finished(self, meshes) -> None:
        self._stop_polydual_heartbeat()
        poly_run_id = getattr(self, "_polydual_run_id", self._run_id)
        if poly_run_id != self._run_id:
            logger.debug("Stale polyDual callback ignored (%d != %d).", poly_run_id, self._run_id)
            return
        logger.info("polyDualMesh finished successfully.")
        self._log.append_log("[poly] Polyhedral conversion complete.")
        self._status.showMessage("Polyhedral mesh ready — running quality check...")
        self._viewer.show_mesh(self._case_dir)
        self._poly_was_converted = True
        # Compare cell counts from the mesh files
        try:
            from cfmesh_autogui.core.of_reader import of_list_count
            owner = self._case_dir / "constant" / "polyMesh" / "owner"
            cells_after = of_list_count(owner)
            cells_before = getattr(self, "_cells_before_poly", 0)
            if cells_before > 0 and cells_after > 0:
                pct = round((cells_after / cells_before - 1) * 100, 1)
                logger.info(
                    "Poly conversion: cells_before=%d cells_after=%d (%+.1f%%)",
                    cells_before, cells_after, pct,
                )
                self._log.append_log(
                    f"[poly] Cells: {cells_before} \u2192 {cells_after} ({pct:+.1f}%). "
                    "Non-orthogonality should improve."
                )
        except Exception:
            pass
        self._launch_checkmesh()

    def _launch_gmsh_poly_dual(self) -> None:
        """Barycentric-dual tet -> polyhedral conversion for GMSH-direct
        tetrahedral meshes — the one and only tet->poly converter this
        pipeline uses (100% polyhedral, best measured on every axis; the
        merge-based terminal-face converter is retired and unreachable)."""
        if not self._case_dir:
            return
        poly_points = self._case_dir / "constant" / "polyMesh" / "points"
        if not poly_points.exists():
            self._log.append_log(
                f"{Tag.WARN} Poly conversion: no mesh found in constant/polyMesh."
            )
            return
        try:
            from cfmesh_autogui.core.of_reader import of_list_count
            self._cells_before_poly = of_list_count(
                self._case_dir / "constant" / "polyMesh" / "owner"
            )
        except Exception:
            self._cells_before_poly = 0
        self._polydual_run_id = self._run_id
        # Fase 3 P3.5: converter name for the explicit poly quality gate log.
        self._poly_converter_name = "barycentric dual (tet_poly_dual)"
        logger.info(
            "Launching barycentric dual poly conversion: case_dir=%s cells_before=%d",
            self._case_dir, self._cells_before_poly,
        )
        # Always name the converter in the visible log: the dual rebuilds one
        # cell per primal vertex, so the cell count drops ~5.5x vs the input
        # tets, and a silent swap here is exactly what made earlier runs
        # impossible to interpret.
        self._log.append_log(
            "[poly] Converting tet → polyhedral mesh (barycentric dual, "
            "100% polyhedral)..."
        )
        bl_params = None
        if self._params.get_bl_enabled():
            bl_params = self._params.get_bl_params()
            if bl_params is not None:
                self._log.append_log(
                    "[poly] Boundary layers enabled: "
                    f"{bl_params['nLayers']} layers, first "
                    f"{bl_params['firstLayerThickness']:.6g} m, "
                    f"growth {bl_params['thicknessRatio']} — prism layers "
                    "will be added to the poly mesh after conversion."
                )
        w = DualPolyWorker(self._case_dir, bl_params=bl_params)
        self._status.showMessage("Polyhedral conversion...")
        self._polydual_worker = w
        self._polydual_start_time = time.monotonic()
        heartbeat = QTimer(self)
        heartbeat.setInterval(8000)
        heartbeat.timeout.connect(self._on_polydual_heartbeat)
        heartbeat.start()
        self._polydual_heartbeat = heartbeat
        self._submit_task(
            "polydual", w,
            on_finished=lambda _n, result: self._on_gmsh_polydual_finished(result),
            on_failed=lambda _n, msg: self._on_gmsh_polydual_failed(msg),
            heartbeat_timeout_s=600.0,
        )

    def _on_gmsh_polydual_failed(self, msg: str) -> None:
        self._stop_polydual_heartbeat()
        self._log.append_log(f"[poly] FAILED: {msg}")
        QMessageBox.warning(
            self, "Polyhedral Conversion Failed",
            f"Polyhedral conversion failed (barycentric dual):\n\n{msg}\n\n"
            "The tetrahedral mesh is still available. "
            "You can skip polyhedral conversion and use it directly."
        )

    def _on_gmsh_polydual_finished(self, result) -> None:
        self._stop_polydual_heartbeat()
        poly_run_id = getattr(self, "_polydual_run_id", self._run_id)
        if poly_run_id != self._run_id:
            logger.debug(
                "Stale gmsh-polydual callback ignored (%d != %d).",
                poly_run_id, self._run_id,
            )
            return
        # DualPolyResult: read `success`/`errors` first, then the counts.
        if not result.success:
            err = "; ".join(result.errors) if result.errors else "unknown error"
            self._log.append_log(f"[poly] FAILED: {err}")
            QMessageBox.warning(
                self, "Polyhedral Conversion Failed",
                f"Polyhedral conversion failed (barycentric dual):\n\n{err}\n\n"
                "The tetrahedral mesh is still available. "
                "You can skip polyhedral conversion and use it directly."
            )
            return
        cells_before = getattr(self, "_cells_before_poly", 0)
        cells_after = result.n_cells_after
        logger.info(
            "Barycentric dual conversion finished: %d tets -> %d poly cells",
            cells_before, cells_after,
        )
        self._log.append_log("[poly] Polyhedral conversion complete.")
        if getattr(result, "bl_prism_cells", 0) > 0:
            self._log.append_log(
                f"[poly] Boundary layers OK: {result.bl_prism_cells:,} prism "
                f"cells added (total thickness "
                f"{getattr(result, 'bl_thickness', 0.0):.6g} m)."
            )
        elif getattr(result, "bl_warning", None):
            self._log.append_log(
                f"{Tag.WARN} Boundary layers skipped for the poly mesh — "
                f"{result.bl_warning}"
            )
        self._status.showMessage("Polyhedral mesh ready — running quality check...")
        self._viewer.show_mesh(self._case_dir)
        self._poly_was_converted = True
        if cells_before > 0 and cells_after > 0:
            pct = round((cells_after / cells_before - 1) * 100, 1)
            self._log.append_log(
                f"[poly] Cells: {cells_before} \u2192 {cells_after} ({pct:+.1f}%). "
                "The barycentric dual rebuilds one polyhedral cell per primal "
                "vertex, so the cell count drops ~5.5x \u2014 the normal, desirable "
                "gain of a polyhedral mesh. For a target resolution, mesh finer "
                "upstream (the tet mesh) to compensate."
            )
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
        w = DecomposeParWorker(self._case_dir, self._of_config, n_cores)
        self._decompose_worker = w
        self._submit_task(
            "decompose", w,
            on_finished=lambda _n, _r: self._log.append_log(
                f"{Tag.MESHING} decomposePar OK — parallel solving ready."
            ),
            on_failed=lambda _n, msg: self._log.append_log(
                f"{Tag.WARN} decomposePar FAILED: {msg} — mesh still usable for serial solving."
            ),
            heartbeat_timeout_s=1800.0,
        )

    def _make_fix_action(self):
        shape = self._original_shape
        current_scale = self._current_scale

        def fix(error_info, attempt: int) -> bool:
            logger.info("Retry fix for %s (attempt %d).", error_info.error_type.value, attempt)
            self._log.append_log(f"{Tag.FIX} {error_info.error_type.value} (attempt {attempt})")
            if error_info.error_type in (ErrorType.NON_WATERTIGHT, ErrorType.SURFACE_READ):
                # Heavy retessellation + STL export run in a background task
                # under a nested event loop: the synchronous bool contract is
                # preserved but the UI stays responsive during the retry.
                try:
                    finer_tol = 0.01 / (2 ** attempt)

                    def _retessellate_work(worker):
                        if shape is not None:
                            patches = classify_faces(shape)
                            unscaled = list(tessellate_patches(
                                patches, tolerance=finer_tol, angle_tolerance=0.05,
                            ))
                        else:
                            # STL-only workflow: no CAD shape to re-tessellate,
                            # re-export the existing meshes
                            unscaled = None
                        if unscaled is None:
                            meshes = list(self._meshes)
                        elif abs(current_scale - 1.0) > 1e-9:
                            scaled = [m.copy() for m in unscaled]
                            scale_meshes(scaled, current_scale)
                            meshes = scaled
                        else:
                            meshes = unscaled
                        export_surface_file(meshes, self._case_dir)
                        return {"unscaled": unscaled, "meshes": meshes}

                    ok, result = self._run_ui_worker_blocking(
                        "fix_retessellate", _retessellate_work, timeout_s=300.0,
                    )
                    if not ok:
                        raise RuntimeError(str(result))
                    if result["unscaled"] is not None:
                        self._unscaled_meshes = result["unscaled"]
                    self._scaled_meshes = None
                    self._meshes = result["meshes"]
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
                    # compute_patch_cell_sizes is numpy-heavy on big meshes —
                    # run it off the UI thread (the meshDict write below is
                    # a tiny file and stays here).
                    meshes_for_sizing = list(self._meshes)
                    ok, result = self._run_ui_worker_blocking(
                        "fix_cell_sizes",
                        lambda w: compute_patch_cell_sizes(meshes_for_sizing, detail=detail),
                        timeout_s=300.0,
                    )
                    if not ok:
                        raise RuntimeError(str(result))
                    ps_r, bc_r, bt_r = result
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
            if error_info.error_type == ErrorType.BL_FAILURE:
                self._log.append_log(
                    f"{Tag.FIX} Boundary layers failed — disabling BL and retrying."
                )
                self._params.set_bl_enabled(False)
                try:
                    # NB: no local "from ... import write_meshdict" here.
                    # write_meshdict is already imported at module level; a
                    # second import INSIDE this method rebinds the name as a
                    # function-scoped local, which made the *earlier*
                    # recovery branch above (the meshDict regeneration for a
                    # non-BL failure) raise UnboundLocalError before ever
                    # reaching its own call — silently swallowed by the
                    # surrounding "except Exception", so auto-recovery just
                    # looked like it "didn't help". Found by ruff F823.
                    from cfmesh_autogui.core.stl_writer import export_surface_file
                    names = [m.metadata.get("name", "wall") for m in self._meshes]
                    meshes_for_export = list(self._meshes)
                    ok, result = self._run_ui_worker_blocking(
                        "fix_stl_export",
                        lambda w: export_surface_file(meshes_for_export, self._case_dir),
                        timeout_s=300.0,
                    )
                    if not ok:
                        raise RuntimeError(str(result))
                    p = self._params.get_mesh_params()
                    write_meshdict(
                        self._case_dir,
                        max_cell_size=p["max_cell_size"],
                        min_cell_size=p["min_cell_size"],
                        bl_params=None,
                        patch_names=names,
                    )
                    self._log.append_log(f"{Tag.FIX} Re-ran meshDict without BL.")
                    return True
                except Exception as e:
                    logger.warning("BL fix failed: %s", e)
                    self._log.append_log(f"{Tag.FIX} BL fix error: {e}")
                    return False
            if error_info.error_type in (ErrorType.FOAM_FATAL, ErrorType.CRASH):
                # Smarter retry: coarser cells + reduced ratio
                factor = 1.5 if attempt == 1 else 2.0
                p = self._params.get_mesh_params()
                new_max = p["max_cell_size"] * factor
                new_min = max(p["min_cell_size"] * 1.2, new_max * 0.1)
                self._log.append_log(
                    f"{Tag.FIX} Crash/fatal — coarsening cells: "
                    f"max {p['max_cell_size']:.4g} → {new_max:.4g}, "
                    f"min {p['min_cell_size']:.4g} → {new_min:.4g}"
                )
                try:
                    names = [m.metadata.get("name", "wall") for m in self._meshes]
                    bl = self._params.get_bl_params()
                    write_meshdict(
                        self._case_dir,
                        max_cell_size=new_max,
                        min_cell_size=new_min,
                        bl_params=bl,
                        patch_names=names,
                    )
                    self._params._max_cell.setValue(new_max)
                    self._params._min_cell.setValue(new_min)
                    return True
                except Exception as e:
                    logger.warning("Coarsen fix failed: %s", e)
                    self._log.append_log(f"{Tag.FIX} Coarsen fix error: {e}")
                    return False
            if error_info.error_type == ErrorType.NON_MAPPABLE:
                # Too many cells or bad topology — coarsen aggressively
                p = self._params.get_mesh_params()
                new_max = p["max_cell_size"] * 2.0
                self._log.append_log(
                    f"{Tag.FIX} Non-mappable — doubling max cell: "
                    f"{p['max_cell_size']:.4g} → {new_max:.4g}"
                )
                try:
                    names = [m.metadata.get("name", "wall") for m in self._meshes]
                    bl = self._params.get_bl_params()
                    write_meshdict(
                        self._case_dir,
                        max_cell_size=new_max,
                        min_cell_size=p["min_cell_size"],
                        bl_params=bl,
                        patch_names=names,
                    )
                    self._params._max_cell.setValue(new_max)
                    return True
                except Exception as e:
                    logger.warning("Coarsen fix failed: %s", e)
                    self._log.append_log(f"{Tag.FIX} Coarsen fix error: {e}")
                    return False
            return False
        return fix

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
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

        self._export_result_path = path
        self._export_format = fmt
        self._status.showMessage(f"Exporting mesh ({fmt})...")
        self._log.append_log(f"{Tag.EXPORT} Starting mesh export ({fmt})...")
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)

        from cfmesh_autogui.core.openfoam_runner import ExportWorker
        self._export_worker = ExportWorker(self._case_dir, fmt, path, self._of_config)
        self._submit_task(
            "export", self._export_worker,
            on_finished=lambda _n, out_path: self._on_export_finished(out_path),
            on_failed=lambda _n, msg: self._on_export_error(msg),
            heartbeat_timeout_s=600.0,
        )

    def _on_export_finished(self, out_path: str):
        self._progress.setVisible(False)
        self._status.showMessage("Export complete")
        self._log.append_log(f"{Tag.EXPORT} Mesh exported ({self._export_format.upper()}): {out_path}")
        QMessageBox.information(self, "Export Complete", f"Mesh exported to:\n{out_path}")

    def _on_export_error(self, msg: str):
        self._progress.setVisible(False)
        self._status.showMessage("Export failed")
        logger.error("Export failed: %s", msg)
        QMessageBox.critical(self, "Export Error", msg)

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

        from cfmesh_autogui.core.baramflow_export import export_case, validate_case

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
            # export_case copies the whole case (can be hundreds of MB) —
            # run it in a background task with progress feedback.
            self._status.showMessage("Exporting BaramFlow case...")
            self._progress.setRange(0, 0)
            self._progress.setVisible(True)
            self._ribbon_btns["cancel"].setVisible(True)
            case_dir = self._case_dir

            def _work(worker):
                return export_case(case_dir, dest_parent)

            def _done(_name, out):
                self._progress.setVisible(False)
                self._ribbon_btns["cancel"].setVisible(False)
                self._refresh_workflow(exported=True)
                self._log.append_log(
                    f"{Tag.EXPORT} BaramFlow case exported: {out} "
                    f"({len(validation.patches)} patches)"
                )
                QMessageBox.information(
                    self, "Export Complete",
                    f"Case exported to:\n{out}\n\nOpen this folder directly in BaramFlow.",
                )

            def _bad(_name, msg):
                self._progress.setVisible(False)
                self._ribbon_btns["cancel"].setVisible(False)
                logger.error("BaramFlow export failed: %s", msg)
                self._log.append_log(f"{Tag.ERROR} BaramFlow export failed: {msg}")
                QMessageBox.critical(self, "Export Failed", str(msg))

            self._submit_task(
                "baramflow_export", FunctionWorker(_work),
                on_finished=_done, on_failed=_bad,
                heartbeat_timeout_s=600.0,
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
        self._set_workflow_stage("quality", "done")

    def _on_solver_setup(self):
        if not self._case_dir:
            QMessageBox.warning(self, "No Case", "Generate a mesh first.")
            return
        from cfmesh_autogui.commercial.bc_editor import BCEditor
        from cfmesh_autogui.commercial.solver_setup import (
            SolverConfig,
            SolverSetup,
            SolverType,
        )
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
            "[solver] Setup complete: simpleFoam, kOmegaSST, bilanciato"
        )
        QMessageBox.information(
            self, "Solver Setup",
            f"Solver configuration written to {self._case_dir / 'system'}"
        )
        self._set_workflow_stage("quality", "done")

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
        from cfmesh_autogui.commercial.mesh_engine import MeshEngine
        from cfmesh_autogui.commercial.quick_mesh import QuickMesh
        from cfmesh_autogui.core.geometry import compute_bbox_dim
        detail = self._params.get_detail_level()
        # Auto-select algorithm via MeshEngine. Uses the cached startup
        # WSL availability (never OFConfig.validate() — a cold WSL2 boot
        # used to freeze the UI here for minutes).
        has_wsl = getattr(self, "_wsl_available", True)
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

    def _on_adaptive_mesh(self):
        """Run the OODA closed-loop adaptive meshing engine."""
        if not self._meshes:
            QMessageBox.warning(self, "No Geometry", "Load a geometry first.")
            return

        self._log.append_log("[adaptive] Starting OODA adaptive loop...")
        self._set_workflow_stage("generate", "active")

        # Show OODA panel
        if getattr(self, '_ooda_panel', None) is None:
            from cfmesh_autogui.gui.ooda_panel import OODAPanel
            self._ooda_panel = OODAPanel()
            from PySide6.QtWidgets import QStatusBar
            if isinstance(self.statusBar(), QStatusBar):
                self.statusBar().addPermanentWidget(self._ooda_panel)
        self._ooda_panel.show_running()

        case_dir = self._case_dir or Path(".")
        of_config = self._of_config

        # Build inline worker QObject
        from PySide6.QtCore import QObject, Signal

        class _OODAWorker(QObject):
            progress = Signal(str, float)
            log_line = Signal(str)
            finished = Signal(bool)

            def __init__(self, of_cfg, c_dir, meshes, geo_path):
                super().__init__()
                self._of_cfg = of_cfg
                self._c_dir = c_dir
                self._meshes = meshes
                self._geo_path = geo_path

            def run(self):
                from cfmesh_autogui.commercial.adaptive_integration import (
                    OODAWorkflowAdapter,
                )
                adapter = OODAWorkflowAdapter(self._of_cfg)
                adapter.configure(self._c_dir, self._meshes, self._geo_path)
                result = adapter.run(
                    callback=lambda msg, frac: self.progress.emit(msg, frac),
                )
                if result.success:
                    self.log_line.emit(
                        f"[adaptive] Done: {result.cell_count} cells, "
                        f"skew={result.max_skewness:.3f} "
                        f"({result.n_ooda_iterations} iterations)"
                    )
                else:
                    self.log_line.emit(
                        "[adaptive] FAILED — see quality report for details"
                    )
                self.finished.emit(result.success)

        self._adaptive_worker = _OODAWorker(
            of_config, case_dir, self._meshes, self._geometry_path,
        )

        def on_adaptive_done(_name, success: bool):
            self._ooda_panel.show_done(success)
            self._set_workflow_stage("quality", "done" if success else "error")
            if success:
                self._launch_checkmesh()

        self._submit_task(
            "adaptive", self._adaptive_worker,
            on_finished=on_adaptive_done,
            on_progress=lambda _n, stage, pct: self._ooda_panel.update_progress(stage, pct),
            heartbeat_timeout_s=900.0,
        )

    # ------------------------------------------------------------------
    # Solution-adaptive refinement (SAMR)
    # ------------------------------------------------------------------
    def _on_solution_adaptive(self):
        """One-click solution-adaptive mesh refinement.

        Runs the solve -> indicator -> remesh loop from
        ``core.solution_adaptive`` via ``SolutionAdaptiveWorker``. The remesh
        half reuses the exact same out-of-process GMSH path the app already
        uses (``core.gmsh_subprocess``), so adaptive results never diverge from
        normal meshes. No manual refinement zones required — the refinement
        map comes from the flow itself.
        """
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        if not self._meshes:
            QMessageBox.warning(self, "No Geometry", "Load a geometry first.")
            return
        step_path = getattr(self, "_loaded_step_path", None)
        if not step_path or not Path(step_path).exists():
            QMessageBox.warning(
                self, "No CAD", "No source CAD path saved — re-load the geometry."
            )
            return
        if not self._case_dir or not (
            self._case_dir / "constant" / "polyMesh" / "points"
        ).exists():
            QMessageBox.warning(
                self, "No Mesh",
                "Build a mesh first (Generate Mesh / Quick Mesh), then run "
                "Solve Adaptive on it.",
            )
            return
        if self._tasks.is_running("samr"):
            QMessageBox.information(
                self, "Already Running",
                "Solution-adaptive refinement is already running.",
            )
            return

        vel, ok1 = QInputDialog.getDouble(
            self, "Inlet velocity", "Inlet velocity magnitude [m/s]:",
            self._samr_default_inlet_velocity(), 0.001, 1000.0, 4,
        )
        if not ok1:
            return
        cycles, ok2 = QInputDialog.getInt(
            self, "Refinement cycles", "Number of solve-remesh cycles:",
            3, 1, 6, 1,
        )
        if not ok2:
            return
        budget, ok3 = QInputDialog.getInt(
            self, "Cell budget", "Max total cells (loop stops if exceeded):",
            3_000_000, 100_000, 30_000_000, 100_000,
        )
        if not ok3:
            return

        from cfmesh_autogui.core.case_setup import (
            mesh_bounds, suggest_flow_direction,
        )
        from cfmesh_autogui.core.gmsh_subprocess import assemble_runnable_case
        from cfmesh_autogui.core.openfoam_runner import SolutionAdaptiveWorker
        from cfmesh_autogui.core.solution_adaptive import AdaptiveParams

        fd = suggest_flow_direction(self._case_dir)
        inlet = (vel * fd[0], vel * fd[1], vel * fd[2])
        self._log.append_log(
            f"[adaptive] Solution-adaptive refinement: inlet U={inlet}, "
            f"cycles={cycles}, budget={budget:,} cells"
        )

        # Make the existing mesh a runnable case (inlet velocity, wall
        # functions, roles) so the first cycle can actually solve it.
        # Runs in a background task: this launches WSL subprocesses and
        # would otherwise freeze the UI on a slow WSL cold boot.
        ok, err = self._run_ui_worker_blocking(
            "samr_setup",
            lambda w: assemble_runnable_case(
                self._case_dir, inlet_velocity=inlet, end_time=400,
                on_line=w.log,
            ),
        )
        if not ok:
            logger.exception("Could not make the initial case runnable")
            QMessageBox.critical(
                self, "SAMR Error", f"Could not set up the solve case:\n{err}"
            )
            return

        bounds = mesh_bounds(self._case_dir)
        root = self._resolve_case_root()
        amr_root = root / f"{Path(self._case_dir).name}_samr"
        amr_root.mkdir(parents=True, exist_ok=True)
        self._samr_amr_root = amr_root
        detail = self._params.get_detail_level()

        # Auto-select parallelism: on meshes big enough that the fixed
        # decompose/reconstruct overhead pays off, use up to 8 solve cores
        # (the single biggest wall-time lever in the loop). GMSH meshing
        # threads measured NO benefit on this mesher (valve medium 268 s
        # single-thread vs 274 s with 8) so they stay off.
        _cores = os.cpu_count() or 4
        from cfmesh_autogui.core.boundary_reader import count_cells as _cc
        n_cells_now = _cc(self._case_dir)
        use_par = n_cells_now > 250_000
        solve_cores = min(_cores, 8) if use_par else 1
        mesh_threads = 1
        self._log.append_log(
            f"[adaptive] parallelism: solve on {solve_cores} core(s) "
            f"({n_cells_now:,} cells)"
        )

        def _remesh(size_field: Path, cycle: int):
            from cfmesh_autogui.core.gmsh_subprocess import remesh_from_cad
            case_dir = amr_root / f"cycle_{cycle}"
            case_dir.mkdir(parents=True, exist_ok=True)
            return remesh_from_cad(
                step_path, case_dir, detail, size_field, inlet, end_time=400,
                threads=mesh_threads, on_line=self._log.append_log,
            )

        params = AdaptiveParams(
            inlet_velocity=inlet, max_cycles=cycles, max_cells=budget,
            solver_iterations=300, final_solver_iterations=600,
            solve_cores=solve_cores,
        )

        self._run_id += 1
        my_id = self._run_id
        self._params.set_meshing_state(True)
        self._params.set_all_enabled(False)
        self._ribbon_btns["cancel"].setVisible(True)
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        self._status.showMessage("Solution-adaptive refinement...")

        self._cleanup_thread("_samr_thread", "_samr_worker")
        w = SolutionAdaptiveWorker(
            case_dir=self._case_dir, bounds=bounds, remesh_fn=_remesh,
            params=params, of_config=self._of_config,
        )
        self._samr_worker = w
        self._samr_my_id = my_id

        def on_samr_finished(_name, result):
            self._on_samr_finished(result)
            self._on_samr_thread_done()

        self._submit_task(
            "samr", w,
            on_finished=on_samr_finished,
            on_failed=lambda _n, msg: self._on_samr_failed(msg),
            on_notify=lambda _n, payload: self._on_samr_cycle_done(*payload),
            heartbeat_timeout_s=900.0,
        )

    @staticmethod
    def _samr_default_inlet_velocity() -> float:
        return 1.0

    def _on_samr_cycle_done(self, cycle: int, n_cells: int):
        self._log.append_log(
            f"[adaptive] cycle {cycle} remesh produced {n_cells:,} cells"
        )
        self._status.showMessage(f"Solution-adaptive: cycle {cycle} done "
                                 f"({n_cells:,} cells)")

    @Slot(object)
    def _on_samr_finished(self, result):
        self._log.append_log(f"{Tag.DONE} Solution-adaptive refinement complete.")
        try:
            summary = result.summary()
            self._log.append_log(f"[adaptive] {summary}")
        except Exception:  # noqa: BLE001
            pass
        # Show the final cycle's mesh in the viewer. The last remesh lives in
        # the highest-numbered cycle dir under the SAMR root.
        try:
            amr_root = getattr(self, "_samr_amr_root", None)
            case_dir = None
            if amr_root:
                cyc = sorted(
                    (d for d in amr_root.glob("cycle_*") if d.name[6:].isdigit()),
                    key=lambda d: int(d.name[6:]),
                )
                if cyc:
                    case_dir = cyc[-1]
            case_dir = case_dir or self._case_dir
            from cfmesh_autogui.core.boundary_reader import count_cells
            if case_dir and (case_dir / "constant" / "polyMesh" / "points").exists():
                n = count_cells(case_dir)
                self._log.append_log(
                    f"[adaptive] final mesh: {case_dir} ({n:,} cells)"
                )
                try:
                    self._viewer.show_mesh(case_dir)
                except Exception:  # noqa: BLE001
                    logger.exception("Viewer failed to show SAMR mesh")
        except Exception:  # noqa: BLE001
            logger.exception("SAMR finish handling failed")

    def _on_samr_failed(self, msg: str):
        self._log.append_log(f"{Tag.ERROR} Solution-adaptive refinement failed: {msg}")

    def _on_samr_thread_done(self):
        self._params.set_meshing_state(False)
        self._params.set_all_enabled(True)
        self._progress.setVisible(False)
        self._ribbon_btns["cancel"].setVisible(False)
        self._status.showMessage("Done")

    def _on_auto_fix_quality(self, _action: str):
        if not self._case_dir:
            QMessageBox.warning(self, "No Case", "Generate a mesh first.")
            return
        if self._tasks.is_running("quality_fix"):
            QMessageBox.information(self, "Already Running", "A quality fix cycle is already in progress.")
            return
        my_id = self._run_id
        w = QualityFixWorker(self._of_config)
        self._quality_fix_worker = w

        def _on_qf_finished(_name, code: int):
            if my_id != self._run_id:
                return
            self._log.append_log(f"[quality-fix] Completed (exit {code})")
            self._set_workflow_stage("quality", "done")
            self._launch_checkmesh()

        def _on_qf_failed(_name, msg: str):
            if my_id != self._run_id:
                return
            self._log.append_log(f"[quality-fix] FAILED: {msg}")
            self._set_workflow_stage("quality", "error")

        max_cell = self._params.get_max_cell()
        min_cell = self._params.get_min_cell()
        bl_params = self._params.get_bl_params()
        case_dir = self._case_dir
        self._submit_task(
            "quality_fix", w,
            on_finished=_on_qf_finished,
            on_failed=_on_qf_failed,
            run_kwargs={
                "case_dir": case_dir,
                "max_cell": max_cell,
                "min_cell": min_cell,
                "bl_params": bl_params,
            },
            heartbeat_timeout_s=900.0,
        )
        self._log.append_log("[quality-fix] Auto-fix cycle started...")

    @staticmethod
    def _resolve_paraview_exe() -> str | None:
        """Find the ParaView executable.

        Most Windows ParaView installs don't add themselves to PATH, so
        `shutil.which` alone isn't enough — also glob the standard
        Program Files install locations for the versioned folder name
        (e.g. "ParaView 6.1.0"). Returns None if nothing is found.
        """
        import shutil
        exe = shutil.which("paraview") or (
            shutil.which("paraview.exe") if sys.platform == "win32" else None
        )
        if exe:
            return exe
        if sys.platform == "win32":
            import glob
            for pf in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
                if not pf:
                    continue
                matches = sorted(glob.glob(os.path.join(pf, "ParaView*", "bin", "paraview.exe")))
                if matches:
                    return matches[-1]
        return None

    def _on_launch_paraview(self):
        if not self._case_dir:
            QMessageBox.warning(self, "No Case", "Generate a mesh first.")
            return
        import subprocess
        exe = self._resolve_paraview_exe()
        if exe is None:
            QMessageBox.warning(
                self, "ParaView Not Found",
                "ParaView executable not found in PATH or in the standard "
                "Program Files install location.\n"
                "Install ParaView and ensure 'paraview' is available.",
            )
            return
        # ParaView's OpenFOAM reader is keyed off a "<name>.foam" marker
        # file inside the case dir (the community-standard convention) —
        # more reliably auto-detected across versions than pointing it at
        # the bare case directory.
        foam_marker = Path(self._case_dir) / f"{Path(self._case_dir).name}.foam"
        try:
            if not foam_marker.exists():
                foam_marker.touch()
            # shell=True previously swallowed a not-found executable as a
            # silent no-op on Windows (cmd.exe prints "not recognized" to
            # its own invisible console and exits 0 instead of Popen
            # raising FileNotFoundError) — always launch the resolved exe
            # directly, no shell involved.
            subprocess.Popen([exe, str(foam_marker)])
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

    def _submit_task(
        self, name: str, worker, *,
        on_finished=None, on_failed=None, on_cancelled=None,
        on_progress=None, on_log=None,
        heartbeat_timeout_s: float | None = None,
        run_args: tuple = (), run_kwargs: dict | None = None,
        signal_shapes: dict | None = None,
    ) -> bool:
        """Submit a long job through the unified TaskManager.

        All callbacks run on the GUI thread (TaskManager lives there), so
        they may touch widgets freely. ``on_log`` defaults to the app log
        panel. Returns False if a task with the same name is running.
        """
        if on_log is None:
            on_log = lambda _name, msg: self._log.append_log(msg)
        return self._tasks.submit(
            name, worker,
            on_finished=on_finished, on_failed=on_failed,
            on_cancelled=on_cancelled, on_progress=on_progress,
            on_log=on_log,
            heartbeat_timeout_s=heartbeat_timeout_s,
            run_args=run_args, run_kwargs=run_kwargs,
            signal_shapes=signal_shapes,
        )

    def _run_ui_worker_blocking(self, name: str, fn, timeout_s: float = 120.0):
        """Run ``fn(worker)`` in a background task while keeping the GUI
        responsive, then return its result.

        Used where a synchronous bool/result contract must be preserved
        (e.g. the RetryRunner fix_action path) without freezing the UI: a
        nested event loop pumps the GUI until the task finishes, fails or
        is cancelled. Returns (ok: bool, result_or_error).
        """
        from PySide6.QtCore import QEventLoop
        box: dict = {}
        loop = QEventLoop()

        def _done(name_, result):
            box["result"] = result
            box["ok"] = True
            loop.quit()

        def _bad(name_, msg):
            box["error"] = msg
            box["ok"] = False
            loop.quit()

        safety = QTimer(self)
        safety.setSingleShot(True)
        safety.timeout.connect(lambda: (_bad("timeout", "timed out"), loop.quit()))
        safety.start(int(timeout_s * 1000))
        # The watchdog budget must cover the whole blocking call: these
        # tasks emit no progress while a native library does the work, so a
        # 30s default would stall-fire and cancel a legitimately busy export.
        self._submit_task(
            name, FunctionWorker(fn),
            on_finished=_done, on_failed=_bad, on_cancelled=_bad,
            heartbeat_timeout_s=timeout_s,
        )
        loop.exec()
        safety.stop()
        return box.get("ok", False), box.get("result", box.get("error"))

    def _on_task_stalled(self, name: str, silent_s: float) -> None:
        """Watchdog fired: a task stopped reporting liveness. Cancel it and
        restore the UI to a usable state instead of freezing."""
        logger.error("Task '%s' stalled (no liveness for %.0fs) — cancelling", name, silent_s)
        self._log.append_log(
            f"{Tag.ERROR} Task '{name}' appears stuck (no progress for {silent_s:.0f}s) "
            "— cancelling and restoring the UI."
        )
        self._tasks.cancel(name, reason="stalled")
        self._restore_after_job("Recovered from stalled task")

    def _restore_after_job(self, message: str = "Ready") -> None:
        """Safely restore the UI after any job ends (success/fail/cancel)."""
        try:
            self._params.set_all_enabled(True)
            self._params.set_meshing_state(False)
        except Exception:
            pass
        self._progress.setVisible(False)
        self._ribbon_btns["cancel"].setVisible(False)
        self._status.showMessage(message)

    def _cleanup_thread(self, attr_thread: str, attr_worker: str, timeout_ms: int = 3000):
        thread = getattr(self, attr_thread, None)
        worker = getattr(self, attr_worker, None)
        if thread and thread.isRunning():
            if worker:
                for sig_name in ("finished", "failed", "cancelled", "log_line", "error_occurred"):
                    sig = getattr(worker, sig_name, None)
                    if sig is not None:
                        try:
                            sig.disconnect()
                        except (TypeError, RuntimeError):
                            pass
            thread.quit()
            if not thread.wait(timeout_ms):
                # Kill WSL subprocesses BEFORE terminating the QThread, otherwise
                # the thread's finally block (which kills the process) never runs
                # and WSL processes become orphaned.
                self._kill_wsl_processes()
                thread.terminate()
                thread.wait(1000)
            if worker:
                worker.deleteLater()
            thread.deleteLater()
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

        # Running jobs: confirm, then tear everything down within a hard
        # budget (< 3 s). The TaskManager shutdown cancels every task,
        # quits every QThread and terminates stragglers; the daemon WSL
        # kill unblocks subprocess readers without blocking the UI.
        running = self._tasks.running if hasattr(self, "_tasks") else []
        if running:
            proceed = QMessageBox.question(
                self, "Jobs in Progress",
                f"{len(running)} background job(s) still running:\n"
                + "\n".join(f"  - {n}" for n in running)
                + "\n\nClose anyway? Running jobs will be cancelled.",
                QMessageBox.Yes | QMessageBox.No,
            )
            if proceed != QMessageBox.Yes:
                event.ignore()
                return
        import threading as _thr
        _thr.Thread(target=self._kill_wsl_processes, daemon=True).start()
        if self._runner and self._runner.is_running:
            self._runner.request_stop()
        if hasattr(self, "_tasks"):
            self._tasks.shutdown(timeout_ms=2500)
            stragglers = self._tasks.running + self._tasks.zombies
            if stragglers:
                logger.warning(
                    "closeEvent: %d task(s) still running — forcing exit",
                    len(stragglers),
                )
                # A worker that ignored cancellation (e.g. a native call
                # holding the GIL) would otherwise be destroyed with its
                # QThread at interpreter teardown — Qt fail-fast (BEX64) —
                # or deadlock the process on the GIL. Skip Python/Qt
                # teardown entirely; the OS reaps the straggler threads.
                os._exit(1)
        gmsh_shutdown()
        octo.log_event("main_window", "close", "app closed")
        super().closeEvent(event)

