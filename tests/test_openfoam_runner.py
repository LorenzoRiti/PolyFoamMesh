import sys
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

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QThread, Qt, QTimer

# Safety net: without this, a stuck/unresponsive WSL2 subprocess (no output,
# no exit) leaves the QEventLoop below spinning forever with no error —
# hanging the entire pytest run instead of just failing this one test.
MESH_WORKER_TIMEOUT_SECONDS = 180

from polyfoammesh.config import OFConfig
from _test_helpers import space_free_tmp_root
from polyfoammesh.core.geometry import create_test_cylinder, classify_faces, tessellate_patches
from polyfoammesh.core.stl_writer import export_surface_file
from polyfoammesh.core.meshdict_gen import write_meshdict
from polyfoammesh.core.openfoam_runner import (
    MeshWorker,
    analyze_error,
    ErrorType,
    parse_checkmesh_output,
    poly_geo_wall_patch_names,
    poly_wall_patch_names,
)


def test_poly_wall_patch_names_default_targets(tmp_path):
    """FASE 1: the default BL target set is wall-typed / wall-named patches;
    GMSH ``surface_N`` patches are excluded (they fall back to all with a
    warning in the worker)."""
    import polyfoammesh.core.foam_mesh_io as fio

    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    fio.write_boundary(poly / "boundary", [
        {"name": "wall", "type": "wall", "nFaces": 10, "startFace": 0},
        {"name": "inlet", "type": "patch", "nFaces": 5, "startFace": 10},
        {"name": "outlet", "type": "patch", "nFaces": 5, "startFace": 15},
        {"name": "wall_side", "type": "patch", "nFaces": 8, "startFace": 20},
        {"name": "surface_1", "type": "patch", "nFaces": 3, "startFace": 28},
    ])
    names = poly_wall_patch_names(tmp_path)
    assert "wall" in names            # type == wall
    assert "wall_side" in names       # wall-like NAME (type patch)
    assert "inlet" not in names
    assert "outlet" not in names
    assert "surface_1" not in names   # GMSH-style name -> excluded


def test_poly_wall_patch_names_empty_on_raw_gmsh(tmp_path):
    """Raw gmshToFoam output (every patch type 'patch', surface_N names)
    yields an empty set — the worker then falls back to ALL patches."""
    import polyfoammesh.core.foam_mesh_io as fio

    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    fio.write_boundary(poly / "boundary", [
        {"name": f"surface_{i}", "type": "patch", "nFaces": 4, "startFace": 4 * i}
        for i in range(3)
    ])
    assert poly_wall_patch_names(tmp_path) == []


def test_poly_geo_wall_patch_names_recovers_raw_gmsh_walls(tmp_path):
    """FASE 1 geometric fallback: a raw GMSH case (surface_N, type 'patch')
    with identifiable inlet/outlet extremes yields the wall-role patches —
    so the default BL never touches inlet/outlet even when no patch is
    explicitly named/typed wall.  Box pipe: x in [0,1], y/z in [-0.1,0.1];
    surface_1 = 4 side walls, surface_2 = inlet cap (x=0), surface_3 =
    outlet cap (x=1)."""
    import polyfoammesh.core.foam_mesh_io as fio

    corners = [
        (0.0, -0.1, -0.1), (0.0, -0.1, 0.1), (0.0, 0.1, -0.1), (0.0, 0.1, 0.1),
        (1.0, -0.1, -0.1), (1.0, -0.1, 0.1), (1.0, 0.1, -0.1), (1.0, 0.1, 0.1),
    ]
    box_faces = [
        (0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
        (2, 3, 7, 6), (2, 0, 1, 3), (6, 7, 5, 4),
    ]
    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)

    def _header(obj: str, n: int, body: str) -> str:
        return (
            f"FoamFile {{ version 2.0; format ascii; class {obj}; "
            f"object {obj}; }}\n{n}\n(\n{body})\n"
        )

    pts = "\n".join(f"({x} {y} {z})" for x, y, z in corners)
    (poly / "points").write_text(_header("vectorField", 8, pts), encoding="ascii")
    faces = "\n".join(f"4({a} {b} {c} {d})" for a, b, c, d in box_faces)
    (poly / "faces").write_text(_header("faceList", 6, faces), encoding="ascii")
    fio.write_boundary(poly / "boundary", [
        {"name": "surface_1", "type": "patch", "nFaces": 4, "startFace": 0},
        {"name": "surface_2", "type": "patch", "nFaces": 1, "startFace": 4},
        {"name": "surface_3", "type": "patch", "nFaces": 1, "startFace": 5},
    ])
    names = poly_geo_wall_patch_names(tmp_path)
    assert "surface_1" in names       # the 4 side walls
    assert "surface_2" not in names   # inlet cap
    assert "surface_3" not in names   # outlet cap


def test_checkmesh_failed_checks_are_not_passed():
    report = parse_checkmesh_output(
        "Mesh stats\n    cells: 10\n"
        "Mesh non-orthogonality Max: 68 average: 12\n"
        "Failed 1 mesh checks.\n"
    )
    assert not report.passed


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


def test_analyze_error_detects_cfmesh_abort():
    """A cfMesh core dump must NOT be classified as "no error".

    cfMesh throws bare `char const*` exceptions that nothing catches, so
    the process dies via std::terminate -> SIGABRT (shell: "Aborted (core
    dumped)", exit 134). Before this was handled, analyze_error() fell
    through to the empty ErrorInfo(), i.e. the app reported no recognised
    error while the mesher had in fact crashed.

    The text below is the real tail captured from cartesianMesh dying on
    the bf_roundtrip cylinder fixture.
    """
    real_output = (
        "Found 24 boundary faces \n"
        "Smoothing mesh surface before mapping.\n"
        "terminate called after throwing an instance of 'char const*'\n"
    )
    ei = analyze_error(real_output)
    assert ei.error_type == ErrorType.CRASH, f"Got {ei.error_type}"
    assert ei.message, "a crash must carry a message"
    assert ei.suggestion, "a crash must carry an actionable suggestion"

    # The shell-level wording alone must be enough too (stderr-only case).
    ei2 = analyze_error("bash: line 1: 2540 Aborted (core dumped) cartesianMesh")
    assert ei2.error_type == ErrorType.CRASH, f"Got {ei2.error_type}"


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


@pytest.mark.wsl
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


def test_mesh_worker_always_emits_finished_even_on_unexpected_error(monkeypatch):
    """run() must emit `finished` on EVERY path, including an unexpected one.

    Regression for a real hang: the subprocess-reading block had a `finally`
    but no `except`, so any unexpected exception escaped run() and skipped
    the terminating `self.finished.emit(...)`. Callers block on that signal
    (the GUI progress flow, and the QEventLoop in test_e2e_workflow), so the
    symptom was not an error message — it was meshing that never finished
    and never explained why.

    Here _process_line is forced to raise mid-read; the worker must still
    report completion, with a non-zero code and the error text in the output.
    """
    import shutil
    import tempfile

    import polyfoammesh.core.openfoam_runner as ofr

    boom = RuntimeError("synthetic reader failure")

    def _raise(self, line, full_output):
        raise boom

    monkeypatch.setattr(ofr.MeshWorker, "_process_line", _raise, raising=True)

    # NOT pytest's tmp_path: it lives under the user profile, which on this
    # machine contains a space, and MeshWorker legitimately refuses such a
    # case dir up front (OpenFOAM cannot handle spaces in paths). That guard
    # would short-circuit the run before the reader is ever reached.
    case_dir = Path(tempfile.mkdtemp(prefix="cfmesh_emit_test_",
                                     dir=str(space_free_tmp_root())))
    cfg = OFConfig()
    # A command that succeeds fast and prints something to read, so the
    # patched _process_line is reached without needing WSL/OpenFOAM.
    monkeypatch.setattr(
        cfg, "build_command", lambda case_dir, **kw: [sys.executable, "-c", "print('x')"],
        raising=False,
    )

    try:
        worker = ofr.MeshWorker(case_dir, cfg)
        seen = {}
        worker.finished.connect(lambda code, out: seen.update(code=code, out=out))
        worker.run()  # synchronous call: no thread, no event loop needed
    finally:
        shutil.rmtree(case_dir, ignore_errors=True)

    assert "code" in seen, "finished was never emitted -> the caller would hang forever"
    assert seen["code"] != 0, f"an unexpected error must not report success: {seen['code']}"
    assert "synthetic reader failure" in seen["out"], (
        "the cause must reach the caller, not vanish: " + seen["out"][-300:]
    )

# ------------------------------------------------------------------
# P2: stage-based progress + ETA (no WSL needed - pure _process_line)
# ------------------------------------------------------------------
def test_match_stage_recognizes_cfmesh_stages():
    """cfMesh v2512 stage lines map to the expected (name, bucket)."""
    worker = MeshWorker(Path("."), OFConfig())
    cases = {
        "Reading surface from file": ("read", 0),
        "Creating octree": ("octree", 10),
        "Refining the octree": ("refine", 40),
        "Smoothing the surface": ("smooth", 60),
        "Checking the mesh": ("check", 80),
        "Writing mesh": ("write", 90),
    }
    for line, expected in cases.items():
        assert worker._match_stage(line) == expected, line
    assert worker._match_stage("some unrelated line") is None


def test_process_line_stage_advances_progress():
    """A stage line advances the bar to its bucket start."""
    worker = MeshWorker(Path("."), OFConfig())
    progress = []
    worker.progress_update.connect(progress.append)
    worker._process_line("Reading surface from file", [])
    assert progress and progress[-1] == 0
    worker._process_line("Refining the octree", [])
    assert progress[-1] == 40


def test_process_line_explicit_pct_emits_eta():
    """An explicit % line emits an ETA (never invented without data)."""
    worker = MeshWorker(Path("."), OFConfig())
    worker._start_time = 100.0  # fake elapsed baseline
    etas = []
    worker.eta_update.connect(etas.append)
    worker._process_line("Progress: 50%", [])
    assert etas, "expected an ETA to be emitted"
    assert "ETA" in etas[0]
