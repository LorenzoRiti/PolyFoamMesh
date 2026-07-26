import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import os as _os
try:
    import OCP as _ocp
    _d = _os.path.dirname(_ocp.__file__)
    if _d not in _os.environ.get("PATH", ""):
        _os.environ["PATH"] = _d + _os.pathsep + _os.environ.get("PATH", "")
except Exception:
    pass

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QThread, Qt, QTimer

# Safety net: without this, a stuck/unresponsive WSL2 subprocess (no output,
# no exit) leaves the QEventLoop below spinning forever with no error —
# hanging the entire pytest run instead of just failing this one test.
MESH_WORKER_TIMEOUT_SECONDS = 180

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.geometry import create_test_cylinder, classify_faces, tessellate_patches
from cfmesh_autogui.core.stl_writer import export_surface_file
from cfmesh_autogui.core.meshdict_gen import write_meshdict
from cfmesh_autogui.core.openfoam_runner import (
    MeshWorker,
    analyze_error,
    ErrorType,
)


def test_analyze_error():
    ei1 = analyze_error("Error: non-watertight surface detected")
    assert ei1.error_type == ErrorType.NON_WATERTIGHT, f"Got {ei1.error_type}"

    ei2 = analyze_error("cannot read file surface.stl")
    assert ei2.error_type == ErrorType.SURFACE_READ, f"Got {ei2.error_type}"

    ei3 = analyze_error("cannot find patch 'inletX'")
    assert ei3.error_type == ErrorType.PATCH_NOT_FOUND, f"Got {ei3.error_type}"

    ei4 = analyze_error("--> FOAM FATAL ERROR: something bad")
    assert ei4.error_type == ErrorType.FOAM_FATAL, f"Got {ei4.error_type}"

    ei5 = analyze_error("Mesh has 4312 cells. End")
    assert ei5.error_type == ErrorType.NONE, f"Got {ei5.error_type}"

    print("PASS: test_analyze_error")


def test_analyze_error_all_types():  # ✅ F-027
    # CRASH
    ei = analyze_error("floating point exception")
    assert ei.error_type == ErrorType.CRASH, f"Got {ei.error_type}"
    ei = analyze_error("segmentation fault")
    assert ei.error_type == ErrorType.CRASH, f"Got {ei.error_type}"

    # NON_MAPPABLE
    ei = analyze_error("non-mappable patch")
    assert ei.error_type == ErrorType.NON_MAPPABLE, f"Got {ei.error_type}"

    # BOUNDARY_NOT_FOUND
    ei = analyze_error("boundary region cannot be found")
    assert ei.error_type == ErrorType.BOUNDARY_NOT_FOUND, f"Got {ei.error_type}"
    ei = analyze_error("Unable to find boundary")
    assert ei.error_type == ErrorType.BOUNDARY_NOT_FOUND, f"Got {ei.error_type}"

    # SURFACE_READ
    ei = analyze_error("cannot read surface.stl")
    assert ei.error_type == ErrorType.SURFACE_READ, f"Got {ei.error_type}"

    # PATCH_NOT_FOUND
    ei = analyze_error("cannot find patch inlet")
    assert ei.error_type == ErrorType.PATCH_NOT_FOUND, f"Got {ei.error_type}"

    print("PASS: test_analyze_error_all_types")


def test_mesh_worker():
    if not OFConfig().validate():
        print("SKIP: OpenFOAM not available via WSL")
        return

    import tempfile, os
    _safe_tmp = Path(os.environ.get("SYSTEMDRIVE", "C:") + "/") / "cfmesh_test_tmp"
    _safe_tmp.mkdir(parents=True, exist_ok=True)
    case_dir = Path(tempfile.mkdtemp(prefix="cfmesh_test_", dir=str(_safe_tmp)))  # ✅ F-026

    cyl = create_test_cylinder(radius=1.0, height=2.0)
    patches = classify_faces(cyl.val())
    meshes = tessellate_patches(patches)
    export_surface_file(meshes, case_dir)
    write_meshdict(case_dir, max_cell_size=0.5, min_cell_size=0.1)

    ctrl = """\
FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }
application cartesianMesh;
startFrom startTime; startTime 0;
stopAt endTime; endTime 1000;
deltaT 1;
writeControl timeStep; writeInterval 1;
purgeWrite 0; writeFormat binary; writePrecision 6;
writeCompression on; timeFormat general; timePrecision 6;
runTimeModifiable true;
"""
    (case_dir / "system" / "controlDict").write_text(ctrl)

    worker = MeshWorker(case_dir, OFConfig())
    logs = []
    worker.log_line.connect(logs.append, Qt.QueuedConnection)

    finished_data = {"code": None, "output": None}

    def on_finished(code, output):
        finished_data["code"] = code
        finished_data["output"] = output

    worker.finished.connect(on_finished, Qt.QueuedConnection)

    loop = QEventLoop()
    worker.finished.connect(lambda *a: loop.quit(), Qt.QueuedConnection)

    timed_out = {"flag": False}

    def _on_timeout():
        timed_out["flag"] = True
        loop.quit()

    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(_on_timeout)
    watchdog.start(MESH_WORKER_TIMEOUT_SECONDS * 1000)

    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    thread.start()

    loop.exec()
    watchdog.stop()
    if timed_out["flag"]:
        thread.requestInterruption()

    thread.quit()
    thread.wait(5000)

    assert not timed_out["flag"], (
        f"cartesianMesh did not finish within {MESH_WORKER_TIMEOUT_SECONDS}s "
        f"(stuck/unresponsive WSL2 subprocess?). Last log lines:\n"
        + "\n".join(logs[-10:])
    )
    assert finished_data["code"] is not None, "Worker did not finish"
    assert finished_data["code"] == 0, (
        f"cartesianMesh failed with code {finished_data['code']}\n"
        + "\n".join(logs[-20:])
    )
    assert len(logs) > 0, "No log output"

    poly_mesh = case_dir / "constant" / "polyMesh"
    assert poly_mesh.exists(), f"polyMesh not found in {case_dir}"
    boundary_file = poly_mesh / "boundary"
    assert boundary_file.exists()
    boundary_content = boundary_file.read_text()
    assert "inlet" in boundary_content
    assert "outlet" in boundary_content
    assert "wall" in boundary_content

    print(f"PASS: test_mesh_worker ({len(logs)} log lines, mesh created)")


if __name__ == "__main__":
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    test_analyze_error()
    test_mesh_worker()

    app.quit()
    print("\nAll Fase 3 tests passed.")

