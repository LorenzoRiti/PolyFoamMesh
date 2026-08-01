import sys
import time
sys.path.insert(0, "src")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox


def _no_block(parent, title, text, *a, **kw):
    print(f"[QMessageBox] {title}: {text}", flush=True)
    return QMessageBox.Ok


QMessageBox.critical = staticmethod(_no_block)
QMessageBox.warning = staticmethod(_no_block)

app = QApplication(sys.argv)

from cfmesh_autogui.gui.main_window import MainWindow

win = MainWindow()
win.show()

GEO_FILE = sys.argv[1]
state = {"start_time": None}


def step1_load():
    print(f"=== Loading geometry: {GEO_FILE} ===", flush=True)
    win._load_geometry_from_path(GEO_FILE)
    QTimer.singleShot(2000, step2_configure_and_run)


def step2_configure_and_run():
    win._params._mesher_combo.setCurrentText("cfMesh (hexa-dominant + WSL2)")
    win._params._poly_check.setChecked(True)
    state["start_time"] = time.monotonic()
    print("=== Calling _on_run_meshing (cfMesh + poly) ===", flush=True)
    win._on_run_meshing()
    QTimer.singleShot(1000, check_progress)


def check_progress():
    logs = win._log.toPlainText()
    elapsed = time.monotonic() - state["start_time"]
    if "[poly] Polyhedral conversion complete" in logs or "[ERROR]" in logs or "[QMessageBox]" in logs:
        print(f"\n=== DONE after {elapsed:.1f}s ===", flush=True)
        app.quit()
        return
    if elapsed > 300:
        print(f"\n=== TIMEOUT after {elapsed:.1f}s ===", flush=True)
        app.quit()
        return
    QTimer.singleShot(2000, check_progress)


QTimer.singleShot(500, step1_load)
app.exec()
print("\n--- Final log ---", flush=True)
sys.stdout.buffer.write(win._log.toPlainText().encode("utf-8", errors="replace"))
print(f"\n--- case_dir: {win._case_dir} ---", flush=True)
