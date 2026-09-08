"""Tests for the parallel meshing engine."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest  # noqa: F401 — @pytest.mark.wsl below

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module, space_free_tmp_root

_mod = load_commercial_module("parallel_mesh")
DecomposeParams = _mod.DecomposeParams
ParallelMeshResult = _mod.ParallelMeshResult
ParallelMeshEngine = _mod.ParallelMeshEngine
DECOMP_METHODS = _mod.DECOMP_METHODS


def test_subprocess_with_cancel_drains_large_output():
    """Regression: _run_subprocess_with_cancel used to poll process.poll()
    without ever reading the stdout/stderr PIPE. A child emitting more than
    the ~64 KB OS pipe buffer (a real parallel cartesianMesh + reconstruct
    does) blocked on write and never exited, so wsl.exe never EOF'd and the
    run stalled until the 4 h wall-clock timeout — even though the mesh had
    finished in seconds (observed live). Draining threads fix it; this test
    reproduces the >64 KB case with no WSL needed."""
    pe = ParallelMeshEngine()
    script = (
        "import sys; [print(i) for i in range(40000)]; "
        "print('DONE', file=sys.stderr)"
    )
    t0 = time.monotonic()
    r = pe._run_subprocess_with_cancel([sys.executable, "-c", script], timeout=60)
    dt = time.monotonic() - t0
    assert r.returncode == 0
    assert r.stdout.count("\n") >= 40000, "stdout must be fully captured"
    assert "DONE" in r.stderr
    assert dt < 30, f"pipe-drain took too long: {dt:.1f}s (was hanging >4h)"


def test_decompose_params_defaults():
    p = DecomposeParams()
    assert p.method == "scotch"
    assert p.n_cores == 4
    assert p.overlap == 0


def test_decompose_params_custom():
    p = DecomposeParams(method="metis", n_cores=8, overlap=2)
    assert p.method == "metis"
    assert p.n_cores == 8
    assert p.overlap == 2


def test_parallel_mesh_result_defaults():
    r = ParallelMeshResult()
    assert not r.success
    assert r.n_cores == 0
    assert r.cell_count == 0
    assert r.cell_count_per_rank == []


def test_parallel_mesh_result_with_data():
    r = ParallelMeshResult(success=True, n_cores=4, cell_count=500000)
    assert r.success
    assert r.n_cores == 4
    assert r.cell_count == 500000


def test_engine_init():
    pe = ParallelMeshEngine()
    assert pe._max_cell == 0.05
    assert pe._min_cell == 0.01
    assert pe.MIN_CORES == 2
    assert pe.MAX_CORES == 128


def test_engine_set_cell_sizes_valid():
    pe = ParallelMeshEngine()
    pe.set_cell_sizes(0.05, 0.01)
    assert pe._max_cell == 0.05


def test_engine_set_cell_sizes_invalid():
    pe = ParallelMeshEngine()
    try:
        pe.set_cell_sizes(-0.05, 0.01)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_engine_setup_case_invalid():
    pe = ParallelMeshEngine()
    try:
        pe.setup_case("Z:\\nonexistent\\case", n_cores=4)
        assert False, "Should have raised error"
    except (FileNotFoundError, ValueError):
        pass


def _tmp_case_dir() -> Path:
    """Create a temp case dir in a path without spaces."""
    safe_dir = space_free_tmp_root() / "tmp_parallel_test"
    safe_dir.mkdir(parents=True, exist_ok=True)
    return safe_dir


def test_engine_setup_case_invalid_cores():
    pe = ParallelMeshEngine()
    d = _tmp_case_dir()
    try:
        pe.setup_case(d, n_cores=1)
        assert False, "Should have raised ValueError for < 2 cores"
    except ValueError:
        pass
    try:
        pe.setup_case(d, n_cores=200)
        assert False, "Should have raised ValueError for > 128 cores"
    except ValueError:
        pass
    _clean_tmp(d)


def test_engine_setup_case_invalid_method():
    pe = ParallelMeshEngine()
    d = _tmp_case_dir()
    try:
        pe.setup_case(d, n_cores=4, method="unknown")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    _clean_tmp(d)


def test_engine_setup_case_valid():
    pe = ParallelMeshEngine()
    d = _tmp_case_dir()
    (d / "system").mkdir(exist_ok=True)
    (d / "constant").mkdir(exist_ok=True)
    pe.setup_case(d, n_cores=4, method="scotch")
    assert pe._params.n_cores == 4
    assert pe._params.method == "scotch"
    _clean_tmp(d)


def _clean_tmp(d: Path) -> None:
    import shutil
    try:
        shutil.rmtree(d)
    except Exception:
        pass


def test_decomp_methods_available():
    assert "scotch" in DECOMP_METHODS
    assert "metis" in DECOMP_METHODS
    assert len(DECOMP_METHODS) >= 4


def test_estimate_speedup():
    est = ParallelMeshEngine.estimate_speedup(n_cores=8)
    assert est["speedup"] > 1.0
    assert 0 < est["efficiency"] <= 1.0
    assert est["serial_fraction"] == 0.05


def test_estimate_speedup_many_cores():
    est = ParallelMeshEngine.estimate_speedup(n_cores=128)
    assert est["speedup"] > 1.0
    # With 5% serial fraction, max speedup ~20x
    assert est["speedup"] < 30


def test_estimate_speedup_single_core():
    est = ParallelMeshEngine.estimate_speedup(n_cores=1)
    assert round(est["speedup"]) == 1.0


def test_decompose_params_string_rep():
    p = DecomposeParams(method="hierarchical", n_cores=16)
    assert "hierarchical" in str(p)
    assert "16" in str(p)


def test_engine_run_no_case():
    pe = ParallelMeshEngine()
    result = pe.run()
    assert not result.success
    assert len(result.errors) > 0


def _wsl_available() -> bool:
    try:
        from polyfoammesh.config import OFConfig
        return OFConfig().validate()
    except Exception:
        return False


@pytest.mark.wsl
def test_run_actually_meshes_in_parallel_on_real_wsl():
    """Real end-to-end run against WSL2 OpenFOAM, from genuinely fresh state
    (no pre-written meshDict). Pins down four independent bugs found live in
    this module, all previously silent:

    1. Used `decomposePar -force` before any mesh existed — decomposePar
       decomposes an EXISTING mesh/fields for parallel solving; it always
       failed ("Cannot find file 'points' in directory 'polyMesh'").
       cartesianMesh's own -parallel mode does geometry decomposition
       itself; it only needs processorN/ dirs with system/+constant/ copied
       in (confirmed live: fails with "cannot open case directory
       processorN" without them).
    2. reconstructParMesh was called with `-merge`, not a real option
       ("Invalid option: -merge" — confirmed via -help-full).
    3. cartesian_mesh_bin ("cartesianMesh", a plain command name) was run
       through _quoted_linux_path(), which is for FILE PATHS — it silently
       turned the binary name into a bogus absolute path
       (".../cfmesh-autogui/cartesianMesh"), so every mpirun invocation
       tried to exec a file that doesn't exist.
    4. set_cell_sizes() validated and stored values but nothing ever wrote
       them into a meshDict at all.

    Combined effect before any of these were fixed: run() always returned
    success=True with cell_count=0 in under 1.5s, regardless of the actual
    case — a silently-broken feature that looked like it worked.
    """
    import shutil
    import pytest as _pytest

    if not _wsl_available():
        _pytest.skip("OpenFOAM/WSL not available")

    import trimesh

    case = space_free_tmp_root() / "cfmesh_bench" / "parallel_mesh_regression_test"
    shutil.rmtree(case, ignore_errors=True)
    (case / "constant" / "triSurface").mkdir(parents=True)
    (case / "system").mkdir(parents=True)

    box = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    box.export(str(case / "constant" / "triSurface" / "surface.stl"))
    (case / "system" / "controlDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; "
        "object controlDict; }\n"
        "application cartesianMesh;\n"
        "startFrom startTime; startTime 0; stopAt endTime; endTime 1000; "
        "deltaT 1;\n"
        "writeControl timeStep; writeInterval 1; purgeWrite 0; "
        "writeFormat binary;\nwritePrecision 6; writeCompression on; "
        "timeFormat general; timePrecision 6;\nrunTimeModifiable true;\n",
        encoding="ascii",
    )

    from polyfoammesh.core.meshdict_gen import write_meshdict
    write_meshdict(case, 0.08, 0.02, patch_names=["wall"],
                   surface_file="constant/triSurface/surface.stl")

    pe = ParallelMeshEngine()
    pe.setup_case(case, n_cores=4, method="scotch")
    pe.set_cell_sizes(0.08, 0.02)
    pe.set_patch_names(["wall"])
    result = pe.run()

    assert result.success, result.errors
    assert result.cell_count > 0, "cell_count must reflect the real reconstructed mesh"
    shutil.rmtree(case, ignore_errors=True)


if __name__ == "__main__":
    test_decompose_params_defaults()
    test_decompose_params_custom()
    test_parallel_mesh_result_defaults()
    test_parallel_mesh_result_with_data()
    test_engine_init()
    test_engine_set_cell_sizes_valid()
    test_engine_set_cell_sizes_invalid()
    test_engine_setup_case_invalid()
    test_engine_setup_case_invalid_cores()
    test_engine_setup_case_invalid_method()
    test_engine_setup_case_valid()
    test_decomp_methods_available()
    test_estimate_speedup()
    test_estimate_speedup_many_cores()
    test_estimate_speedup_single_core()
    test_decompose_params_string_rep()
    test_engine_run_no_case()
    print("ALL PASS")


def test_clamp_cores_reduces_when_memory_insufficient(monkeypatch):
    """awk-based field extraction through the Windows->wsl.exe->bash -lc
    bridge proved unreliable (confirmed live: even a trivial
    `awk '{print $7}'` returned the whole input line, not one field), so
    the clamp parses plain `free -m` text in Python instead. This guards
    against ever reintroducing that quoting fragility."""
    import shutil
    import subprocess as _subprocess

    case = space_free_tmp_root() / "cfmesh_bench" / "parallel_clamp_test_case_1"
    shutil.rmtree(case, ignore_errors=True)
    (case / "system").mkdir(parents=True)
    (case / "constant" / "triSurface").mkdir(parents=True)
    (case / "constant" / "triSurface" / "surface.stl").write_bytes(b"x" * (2 * 1024 * 1024))

    pe = ParallelMeshEngine()
    pe.setup_case(case, n_cores=64)

    class _FakeResult:
        stdout = (
            "               total        used        free      shared  buff/cache   available\n"
            "Mem:           15542         750       13560           3        1432       14794\n"
            "Swap:           4096           0        4096\n"
        )
        stderr = ""
        returncode = 0

    monkeypatch.setattr(_subprocess, "run", lambda *a, **k: _FakeResult())

    clamped = pe._clamp_cores_to_available_memory()

    assert clamped < 64
    assert clamped >= pe.MIN_CORES
    assert len(pe._result.warnings) == 1
    assert "14794" in pe._result.warnings[0]


def test_clamp_cores_leaves_reasonable_request_untouched(monkeypatch):
    import shutil
    import subprocess as _subprocess

    case = space_free_tmp_root() / "cfmesh_bench" / "parallel_clamp_test_case_2"
    shutil.rmtree(case, ignore_errors=True)
    (case / "system").mkdir(parents=True)
    (case / "constant" / "triSurface").mkdir(parents=True)

    pe = ParallelMeshEngine()
    pe.setup_case(case, n_cores=4)

    class _FakeResult:
        stdout = "Mem:           15542         750       13560           3        1432       14794\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(_subprocess, "run", lambda *a, **k: _FakeResult())

    clamped = pe._clamp_cores_to_available_memory()

    assert clamped == 4
    assert pe._result.warnings == []


def test_clamp_cores_handles_query_failure_gracefully(monkeypatch):
    import shutil
    import subprocess as _subprocess

    case = space_free_tmp_root() / "cfmesh_bench" / "parallel_clamp_test_case_3"
    shutil.rmtree(case, ignore_errors=True)
    (case / "system").mkdir(parents=True)

    pe = ParallelMeshEngine()
    pe.setup_case(case, n_cores=4)

    def _raise(*a, **k):
        raise _subprocess.TimeoutExpired(cmd="wsl.exe", timeout=15)

    monkeypatch.setattr(_subprocess, "run", _raise)

    clamped = pe._clamp_cores_to_available_memory()  # must not raise

    assert clamped == 4  # left as requested when the query itself fails
