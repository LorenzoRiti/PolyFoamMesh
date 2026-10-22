"""W8: one watchdog infrastructure for every meshing phase.

Pure unit tests with a fake monotonic clock: no sleeps, no subprocesses.
The real-subprocess behavior is covered by tests/test_gmsh_watchdog.py,
which the refactor must keep green unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from polyfoammesh.core.phase_watchdog import (  # noqa: E402
    FtetWildProgressDetector,
    PhaseStalled,
    PhaseWatchdog,
    WatchdogAction,
)


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


def test_stall_fires_once_and_guard_raises():
    clock = FakeClock(100.0)
    events: list[WatchdogAction] = []
    wd = PhaseWatchdog(
        "dual", stall_s=10.0, heartbeat_s=5.0, clock=clock,
        on_event=events.append,
    )
    clock.advance(9.9)
    assert wd.check() is None
    clock.advance(0.2)
    action = wd.check()
    assert action is not None
    assert action.reason == "stall"
    assert action.policy == "abort"
    assert action.no_progress_s == pytest.approx(10.1)
    assert len(events) == 1
    # Polling again must not duplicate the event.
    assert wd.check() is action
    assert len(events) == 1

    with pytest.raises(PhaseStalled) as exc:
        wd.guard()
    assert exc.value.action is action


def test_beat_resets_stall_and_heartbeat_does_not():
    clock = FakeClock()
    beats: list[dict] = []
    wd = PhaseWatchdog(
        "bl", stall_s=10.0, heartbeat_s=2.0, clock=clock,
        on_heartbeat=beats.append,
    )
    clock.advance(2.1)
    assert wd.check() is None
    assert len(beats) == 1, "silence should emit one heartbeat"
    # The heartbeat itself must NOT make the stall clock look alive.
    clock.advance(8.0)  # 10.1 s since the last real beat
    action = wd.check()
    assert action is not None and action.reason == "stall"

    # A real beat restarts the stall window.
    clock2 = FakeClock()
    wd2 = PhaseWatchdog("bl", stall_s=10.0, heartbeat_s=2.0, clock=clock2)
    clock2.advance(9.0)
    wd2.beat("still working")
    clock2.advance(9.0)
    assert wd2.check() is None


def test_timeout_is_independent_of_stall():
    clock = FakeClock()
    wd = PhaseWatchdog(
        "tet", stall_s=None, timeout_s=5.0, heartbeat_s=1.0, clock=clock,
    )
    clock.advance(5.01)
    action = wd.check()
    assert action is not None
    assert action.reason == "timeout"
    assert action.policy == "abort"


def test_progress_sets_eta_and_is_monotonic():
    clock = FakeClock()
    emitted: list[tuple[float, str, float | None]] = []
    wd = PhaseWatchdog(
        "dual", stall_s=30.0, clock=clock,
        on_progress=lambda p, stage, eta: emitted.append((p, stage, eta)),
    )
    clock.advance(10.0)
    wd.progress(0.25, "build")
    assert emitted[-1][0] == pytest.approx(0.25)
    assert emitted[-1][1] == "build"
    assert emitted[-1][2] == pytest.approx(30.0)
    assert wd.eta_s() == pytest.approx(30.0)

    clock.advance(10.0)
    wd.progress(0.5, "quality")
    assert wd.eta_s() == pytest.approx(20.0)
    assert wd.progress_fraction == pytest.approx(0.5)


def test_progress_clamps_are_declared():
    wd = PhaseWatchdog("bl", stall_s=10.0)
    wd.progress(-2.0)
    assert wd.progress_fraction == 0.0
    wd.progress(2.0)
    assert wd.progress_fraction == 1.0
    assert len(wd.warnings) == 2


def test_external_abort_check_is_content_aware():
    clock = FakeClock()
    fired = {"value": False}
    wd = PhaseWatchdog(
        "gmsh", stall_s=None, timeout_s=100.0, clock=clock,
        abort_check=lambda: fired["value"],
        abort_evidence=lambda: "invalid elements 6524",
    )
    assert wd.check() is None
    fired["value"] = True
    action = wd.check()
    assert action is not None
    assert action.reason == "external"
    assert "6524" in action.evidence


def test_snapshot_contains_the_diagnosis_fields():
    clock = FakeClock(1.0)
    wd = PhaseWatchdog("convert", stall_s=5.0, timeout_s=60.0, clock=clock)
    clock.advance(3.0)
    wd.progress(0.4, "convert")
    snap = wd.snapshot()
    assert snap["phase"] == "convert"
    assert snap["elapsed_s"] == pytest.approx(3.0)
    assert snap["progress"] == pytest.approx(0.4)
    assert snap["stall_s"] == 5.0
    assert snap["timeout_s"] == 60.0
    assert snap["eta_s"] == pytest.approx(4.5)


def test_fetwild_progress_detector_parses_passes_and_energy():
    d = FtetWildProgressDetector(opt_iter=10)
    assert d.feed("Info: pass 0") is True
    assert d.pass_index == 0
    assert d.fraction == 0.0
    assert d.feed("pass 3, max_energy = 7.55, #v 12345, #t 67890") is True
    assert d.pass_index == 3
    assert d.max_energy == pytest.approx(7.55)
    assert d.n_vertices == 12345
    assert d.n_tets == 67890
    assert d.fraction == pytest.approx(0.3)
    # Unrelated lines must not move the state.
    assert d.feed("Reading surface...") is False
    assert d.pass_index == 3


def test_fetwild_progress_detector_without_opt_iter_is_indeterminate():
    d = FtetWildProgressDetector(opt_iter=0)
    d.feed("pass 4")
    assert d.pass_index == 4
    assert d.fraction is None
