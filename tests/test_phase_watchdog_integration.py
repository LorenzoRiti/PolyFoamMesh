"""W8: the watchdog is actually used by dual/BL/convert, not just imported."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine  # noqa: E402
from polyfoammesh.core.phase_watchdog import (  # noqa: E402
    PhaseStalled,
    PhaseWatchdog,
)
from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_dual_converter_uses_the_phase_watchdog():
    clock = FakeClock()
    wd = PhaseWatchdog("dual", stall_s=10.0, clock=clock)
    conv = TetPolyDualConverter(Path("."), watchdog=wd)
    clock.advance(10.1)
    with pytest.raises(PhaseStalled) as exc:
        conv._check_cancel()
    assert exc.value.action.reason == "stall"


def test_bl_engine_uses_the_phase_watchdog_and_progress_lines():
    clock = FakeClock()
    wd = PhaseWatchdog("bl", stall_s=10.0, clock=clock)
    eng = PolyBoundaryLayerEngine(Path("."), log=lambda _m: None, watchdog=wd)
    eng._log_message("precompute: fade/min-edge 50%...")
    assert wd.progress_fraction == pytest.approx(0.05 + 0.25 * 0.5)
    eng._log_message("building prism side faces 50%...")
    assert wd.progress_fraction == pytest.approx(0.65)
    clock.advance(10.1)
    with pytest.raises(PhaseStalled):
        eng._check_watchdog()


def test_dual_worker_phase_watchdog_emits_gui_progress(app):
    from polyfoammesh.core.openfoam_runner import DualPolyWorker

    worker = DualPolyWorker(Path("."))
    progress: list[tuple[str, float]] = []
    worker.progress.connect(lambda stage, pct: progress.append((stage, pct)))
    wd = worker._phase_watchdog("dual", 10.0, 60.0)
    wd.progress(0.5, "build")
    assert progress == [("build", pytest.approx(50.0))]
    progress.clear()
    wd.progress(1.0, "done")
    assert progress[-1] == ("done", pytest.approx(100.0))


def test_polydual_worker_streams_and_has_a_cancel_path(monkeypatch):
    import polyfoammesh.core.openfoam_runner as ofr

    class _Cfg:
        def build_poly_dual_cmd(self, case_dir, feature_angle=90):
            return ["polyDualMesh", "90"]

    captured: dict = {}
    killed: list[int] = []

    class _Proc:
        pid = 4242

        def poll(self):
            return None

    def fake_stream(cmd, run_cwd, timeout_s, on_line, proc_holder=None,
                    heartbeat_s=10, **kwargs):
        captured["cmd"] = cmd
        captured["timeout"] = timeout_s
        if proc_holder is not None:
            proc_holder["proc"] = _Proc()
        on_line("polyDualMesh output")
        return 0, ["polyDualMesh output"], [], False

    monkeypatch.setattr(ofr, "_stream_subprocess", fake_stream)
    monkeypatch.setattr(ofr, "_kill_pid_tree", lambda pid: killed.append(pid))

    # Install a live child, then cancel it: the same proc_holder path the
    # streaming loop publishes must be reachable from the GUI thread.
    cancelling = ofr.PolyDualWorker(Path("."), _Cfg())
    cancelling._proc_holder["proc"] = _Proc()
    cancelling.cancel()
    assert killed == [4242]

    worker = ofr.PolyDualWorker(Path("."), _Cfg())
    logs: list[str] = []
    finished: list = []
    failed: list[str] = []
    worker.log_line.connect(logs.append)
    worker.finished.connect(finished.append)
    worker.failed.connect(failed.append)
    worker.run()
    assert captured["timeout"] == ofr.PolyDualWorker.TIMEOUT_S
    assert finished and not failed
    assert any("[polyDualMesh]" in line for line in logs)


def test_fetwild_worker_exposes_progress_and_heartbeat_signals(monkeypatch):
    import polyfoammesh.core.openfoam_runner as ofr

    captured: dict = {}

    def fake_stream(cmd, run_cwd, timeout_s, on_line, proc_holder=None,
                    stall_s=None, watchdog=None, **kwargs):
        captured["watchdog"] = watchdog
        captured["stall_s"] = stall_s
        on_line("pass 5, max_energy = 7.55, #t 1000")
        return 0, ['{"success": true}'], [], False

    monkeypatch.setattr(ofr, "_stream_subprocess", fake_stream)
    worker = ofr.FtetWildVolumeWorker(
        "geom.step", Path("out.msh"), "medium", opt_iter=10,
    )
    progress: list[tuple[str, float]] = []
    finished: list = []
    worker.progress.connect(lambda stage, pct: progress.append((stage, pct)))
    worker.finished.connect(finished.append)
    worker.run()

    assert captured["watchdog"] is not None
    assert captured["watchdog"].phase == "fTetWild"
    assert captured["stall_s"] is None  # the passed object owns the stall
    assert finished
    assert progress and progress[-1][0] == "fTetWild"
    assert progress[-1][1] == pytest.approx(50.0)
    assert worker.STALL_S > 0
    assert worker.VOLUME_TIMEOUT_S >= worker.STALL_S
