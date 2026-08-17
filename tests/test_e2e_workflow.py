import sys
import shutil
from pathlib import Path

import pytest  # noqa: F401 — @pytest.mark.wsl below

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import os as _os
try:
    import OCP as _ocp
    _d = _os.path.dirname(_ocp.__file__)
    if _d not in _os.environ.get("PATH", ""):
        _os.environ["PATH"] = _d + _os.pathsep + _os.environ.get("PATH", "")
except Exception:
    pass

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.geometry import create_test_cylinder, classify_faces, tessellate_patches
from cfmesh_autogui.core.stl_writer import export_surface_file
from cfmesh_autogui.core.meshdict_gen import write_meshdict
from cfmesh_autogui.core.openfoam_runner import MeshWorker
from cfmesh_autogui.core.boundary_reader import parse_boundary
from cfmesh_autogui.core.case_setup import setup_case
from cfmesh_autogui.gui.viewer_widget import read_openfoam_mesh_patches

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QThread, Qt, QTimer

# Safety net: without this, a stuck/unresponsive WSL2 subprocess (no output,
# no exit) leaves the QEventLoop below spinning forever with no error —
# hanging the entire pytest run instead of just failing this one test.
E2E_TIMEOUT_SECONDS = 180


@pytest.mark.wsl
def test_full_workflow():
    of_config = OFConfig()
    if not of_config.validate():
        print("SKIP: OpenFOAM not available")
        return

    case_dir = Path("C:/cfmesh_e2e_test")
    if case_dir.exists():
        shutil.rmtree(str(case_dir), ignore_errors=True)
    case_dir.mkdir(parents=True)

    valid, msg = OFConfig.validate_case_path(case_dir)
    assert valid, f"Invalid case path: {msg}"

    print("[1/7] Creating cylinder geometry...")
    # 1000x larger than the "1.0/2.0" used elsewhere: create_test_cylinder
    # builds geometry directly in cadquery's native mm, and tessellate_patches
    # now converts mm->m (matching real STEP file imports) — this test's
    # hardcoded downstream cell sizes (0.5/0.1) assume a real 1m/2m cylinder,
    # so the input must be specified in mm to land there after conversion.
    cyl = create_test_cylinder(radius=1000.0, height=2000.0)
    patches = classify_faces(cyl.val())
    patch_names = [n for n, _ in patches]
    assert len(patches) == 3
    assert "inlet" in patch_names and "outlet" in patch_names and "wall" in patch_names

    print("[2/7] Tessellating patches...")
    meshes = tessellate_patches(patches)
    assert len(meshes) == 3

    print("[3/7] Exporting surface STL...")
    export_surface_file(meshes, case_dir)
    stl_path = case_dir / "constant" / "triSurface" / "surface.stl"
    assert stl_path.exists()

    print("[4/7] Writing meshDict...")
    write_meshdict(case_dir, max_cell_size=0.5, min_cell_size=0.1)
    assert (case_dir / "system" / "meshDict").exists()

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

    print("[5/7] Running cartesianMesh...")
    worker = MeshWorker(case_dir, of_config)
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
    watchdog.start(E2E_TIMEOUT_SECONDS * 1000)

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
        f"cartesianMesh did not finish within {E2E_TIMEOUT_SECONDS}s "
        f"(stuck/unresponsive WSL2 subprocess?). Last log lines:\n"
        + "\n".join(logs[-10:])
    )
    assert finished_data["code"] == 0, "Meshing failed:\n" + "\n".join(logs[-10:])
    print(f"   cartesianMesh completed ({len(logs)} log lines)")

    print("[6/7] Setting up case files...")
    boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
    assert boundary_path.exists()
    patches_info = parse_boundary(boundary_path)
    assert len(patches_info) == 3
    patch_info_names = [p.name for p in patches_info]
    assert "inlet" in patch_info_names
    assert "outlet" in patch_info_names
    assert "wall" in patch_info_names

    setup_case(case_dir, patches_info)
    assert (case_dir / "0" / "p").exists()
    assert (case_dir / "0" / "U").exists()
    assert (case_dir / "system" / "fvSchemes").exists()
    assert (case_dir / "system" / "fvSolution").exists()
    print(f"   Patches: {patch_info_names}")

    print("[7/7] Verifying mesh render...")
    mesh_patches = read_openfoam_mesh_patches(case_dir)
    assert mesh_patches is not None
    assert len(mesh_patches) >= 3
    total_pts = sum(pd.n_points for pd in mesh_patches.values())
    total_cells = sum(pd.n_cells for pd in mesh_patches.values())
    print(f"   Mesh: {total_pts} points, {total_cells} cells, {len(mesh_patches)} patches")

    log_meshing = case_dir / "log.meshing"
    if log_meshing.exists():
        print(f"   log.meshing exists ({log_meshing.stat().st_size} bytes)")

    print("\nFULL WORKFLOW TEST PASSED")
    shutil.rmtree(str(case_dir), ignore_errors=True)


if __name__ == "__main__":
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    test_full_workflow()

    app.quit()

