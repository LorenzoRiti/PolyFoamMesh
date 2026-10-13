"""Full-window README screenshots from the REAL app (real platform/GPU).

The earlier panel-only crops looked incoherent; these are whole-window
captures like the original public README used:
  1. mesher selector   (Mesh tab, CAD displayed in the viewer)
  2. boundary layers   (Advanced tab, BL enabled)
  3. output            (a real meshed case shown in the viewer)

Run on the desktop (a window appears briefly).  QT_QPA_PLATFORM must NOT
be offscreen: the viewer needs the real GPU render.
"""
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\Davide Valoroso\cfmesh-autogui")
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

STL = REPO / "benchmarks" / "geometries" / "cylinder.stl"
MESH_CASE = Path(r"C:\cfmesh_cases\gmsh_direct_20261009_085751")  # cone, Mesh OK
ASSETS = REPO / "docs" / "assets"
_LOG = Path(r"C:\cfmesh_bench\capture_trace.log")


def _log(msg: str) -> None:
    """Append-only checkpoint log (flushed): survives a killed run."""
    with open(_LOG, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")
        fh.flush()
    print(msg, flush=True)


def _pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.05)


def _pump_until(app, cond, timeout_s: float) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.1)
        try:
            if cond():
                return True
        except Exception:
            pass
    return False


def _grab(window, name: str) -> None:
    from PySide6.QtGui import QGuiApplication
    # grabWindow includes the OS window frame (title bar), like the
    # original screenshots; fall back to the widget grab if it fails.
    screen = window.screen() or QGuiApplication.primaryScreen()
    pix = screen.grabWindow(window.winId())
    if pix.isNull():
        pix = window.grab()
    pix.save(str(ASSETS / name), "PNG")
    print(f"{name}: {pix.width()}x{pix.height()}")


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    _log("start")
    app = QApplication.instance() or QApplication([])
    _log("qapp")

    # Apply the theme EXACTLY like app.main() does: without it the checked
    # ribbon buttons fall back to the native Windows style (light blue)
    # instead of the themed clay-orange (#d97757) of the real app.
    # "dark" matches the live app (user request: README shots in dark).
    from polyfoammesh.gui.theme import apply_theme, set_theme_mode

    set_theme_mode("dark")
    _log("mode set")
    apply_theme(app)
    _log("theme applied")

    from polyfoammesh.gui.main_window import MainWindow

    _log("constructing MainWindow")
    w = MainWindow()
    _log("constructed")
    w.resize(1460, 920)
    w.show()
    _log("shown")
    _pump(app, 1.5)
    _log("pumped")

    # 1) Mesher selector: CAD in the viewer, Mesh tab, scrolled so the
    #    Mesher group (with BOTH buttons) is fully visible.
    #    _load_geometry_from_path opens a MODAL "Patch splitting" dialog
    #    (dlg.exec()); a script cannot click it, so a timer auto-accepts
    #    any visible QDialog while the load runs (it runs inside the
    #    dialog's nested event loop, so timers still fire).
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog

    def _dismiss_dialogs():
        for top in app.topLevelWidgets():
            if isinstance(top, QDialog) and top.isVisible():
                top.accept()

    dialog_timer = QTimer()
    dialog_timer.timeout.connect(_dismiss_dialogs)
    dialog_timer.start(150)
    w._load_geometry_from_path(str(STL))
    ok = _pump_until(
        app, lambda: bool(w._viewer._cad_meshes), timeout_s=30.0,
    )
    dialog_timer.stop()
    _log(f"geometry displayed: {ok}")
    _pump(app, 1.0)
    w._params._tabs.setCurrentIndex(1)
    _pump(app, 0.7)
    from PySide6.QtWidgets import QScrollArea

    sa = w.findChild(QScrollArea)
    if sa is not None:
        sa.verticalScrollBar().setValue(sa.verticalScrollBar().maximum())
        _pump(app, 0.5)
    _grab(w, "mesher-selector.png")
    _log("shot 1 (mesher) done")

    # 2) Boundary layers: same Mesh tab (the BL group lives there, right
    #    under the fineness slider), BL enabled, scrolled to the top.
    w._params._bl_checkbox.setChecked(True)
    _pump(app, 0.7)
    sa = w.findChild(QScrollArea)
    if sa is not None:
        sa.verticalScrollBar().setValue(0)
        _pump(app, 0.5)
    _grab(w, "boundary-layers.png")
    _log("shot 2 (bl) done - finishing")

    # Skip close(): closeEvent can raise a modal "jobs in progress" confirm
    # while startup tasks linger.  Hide + quit the loop instead - no
    # closeEvent, clean process exit.
    w.hide()
    app.quit()
    _log("finished cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
