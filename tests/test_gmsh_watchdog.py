"""GMSH stall-watchdog tests.

GMSH's surface-meshing loop can hang forever on a bad surface (confirmed
live: stuck at 40% on "surface 2"; a venturi looping on "Serializing surface
46") while emitting no output at all. A mesher that can hang forever is
unacceptable, so the volume worker tracks the time of the last output line
and, if nothing arrives for ``stall_s``: kills the process tree, retries ONCE
with POLYFOAMMESH_GMSH_ROBUST=1, and on a second stall fails the stage
honestly instead of looping or hanging.

Covered here:
(a) output resets the stall timer (long total run, short gaps -> no kill);
(b) silence > N kills the tree and the worker retries once with the env var;
(c) a second stall fails the stage (exactly two attempts, no loop);
(d) a healthy stream that keeps printing is never killed, however long;
(e) the threshold is configurable via POLYFOAMMESH_GMSH_STALL_S;
(f) the budget is size-scaled for large jobs and the heartbeat reports CPU.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from polyfoammesh.core import openfoam_runner as ofr


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


# --- real-subprocess helpers (exercise _stream_subprocess unchanged) -------- #

# Prints a line then sleeps, so the parent sees real output at a known cadence.
_TICK_CHILD = (
    "import sys, time\n"
    "n = int(sys.argv[1]); interval = float(sys.argv[2])\n"
    "for i in range(n):\n"
    "    print('tick %d' % i, flush=True)\n"
    "    time.sleep(interval)\n"
)


def _run_child(child_src, child_args, stall_s, heartbeat_s=5.0, timeout_s=60.0):
    live: list[str] = []
    stall_holder: dict = {}
    cmd = [sys.executable, "-c", child_src] + [str(a) for a in child_args]
    t0 = time.monotonic()
    rc, out, err, timed_out = ofr._stream_subprocess(
        cmd,
        run_cwd=str(Path.cwd()),
        timeout_s=timeout_s,
        on_line=live.append,
        heartbeat_s=heartbeat_s,
        stall_s=stall_s,
        stall_holder=stall_holder,
    )
    return {
        "rc": rc,
        "stdout": out,
        "stderr": err,
        "timed_out": timed_out,
        "stall": stall_holder,
        "live": live,
        "elapsed": time.monotonic() - t0,
    }


def test_output_resets_stall_timer():
    """(a) A run longer than stall_s but with regular output is NOT killed."""
    # Total ~2.4s > stall 1.5s, but each silent gap is only ~0.4s.
    res = _run_child(_TICK_CHILD, (6, 0.4), stall_s=1.5, heartbeat_s=5.0)

    assert not res["stall"].get("stalled"), res["stall"]
    assert res["rc"] == 0
    assert not res["timed_out"]
    assert "watchdog armed" in " ".join(res["live"])
    assert len(res["stdout"]) == 6


def test_healthy_long_stream_is_never_killed():
    """(d) Total run is long and heartbeats fire, but output keeps flowing."""
    res = _run_child(_TICK_CHILD, (8, 0.5), stall_s=1.2, heartbeat_s=0.3)

    assert res["elapsed"] > 2.0, res["elapsed"]
    assert not res["stall"].get("stalled"), res["stall"]
    assert res["rc"] == 0
    # Heartbeats coexist with the watchdog: they fire without killing.
    assert any("still running" in line for line in res["live"])
    # The heartbeat reports the child's CPU usage so a busy-but-silent process
    # is distinguishable from a dead one (probe may be psutil or the stdlib
    # fallback; either way the heartbeat line carries a CPU field).
    assert any("still running" in line and "CPU" in line for line in res["live"])
    assert len(res["stdout"]) == 8


def test_silent_child_is_killed_despite_heartbeats(monkeypatch):
    """(b, kill half) Silence > N kills the tree even while heartbeats reset.

    The heartbeat bumps its own clock; it must NOT reset the stall clock, or
    a silent child would never be killed.
    """
    killed: list[int] = []
    real_kill = ofr._kill_pid_tree

    def _record_kill(pid):
        killed.append(pid)
        real_kill(pid)

    monkeypatch.setattr(ofr, "_kill_pid_tree", _record_kill)

    res = _run_child(
        "import time; time.sleep(60)", (),
        stall_s=0.8, heartbeat_s=0.2, timeout_s=30.0,
    )

    assert res["stall"].get("stalled") is True, res["stall"]
    assert res["stall"]["no_output_s"] >= 0.8
    assert killed, "process tree was never killed on stall"
    assert any("watchdog fired" in line for line in res["live"])
    # Heartbeats still worked during the silence; they just don't mask it.
    assert any("still running" in line for line in res["live"])


# --- stall threshold configuration ------------------------------------------ #

def test_stall_threshold_default_and_env_override(monkeypatch):
    """(e) POLYFOAMMESH_GMSH_STALL_S overrides the module default."""
    monkeypatch.delenv(ofr.GMSH_STALL_ENV_VAR, raising=False)
    assert ofr._gmsh_stall_timeout_s() == ofr.GMSH_STALL_TIMEOUT_S
    assert ofr.GMSH_STALL_TIMEOUT_S == 300.0

    monkeypatch.setenv(ofr.GMSH_STALL_ENV_VAR, "42")
    assert ofr._gmsh_stall_timeout_s() == 42.0

    monkeypatch.setenv(ofr.GMSH_STALL_ENV_VAR, "1.5")
    assert ofr._gmsh_stall_timeout_s() == 1.5


@pytest.mark.parametrize("bad", ["abc", "0", "-3", ""])
def test_stall_threshold_invalid_values_fall_back(monkeypatch, bad):
    monkeypatch.setenv(ofr.GMSH_STALL_ENV_VAR, bad)
    assert ofr._gmsh_stall_timeout_s() == ofr.GMSH_STALL_TIMEOUT_S


def test_stall_budget_scales_with_job_size():
    """(f) The no-output budget ramps from the 300 s base to 900 s with size."""
    base = ofr.GMSH_STALL_TIMEOUT_S
    assert ofr.GMSH_STALL_MAX_TIMEOUT_S == 900.0
    # Unknown / small jobs keep the flat base.
    assert ofr._gmsh_stall_budget_s(base, None) == base
    assert ofr._gmsh_stall_budget_s(base, 0) == base
    assert ofr._gmsh_stall_budget_s(base, ofr.GMSH_STALL_SMALL_CELLS) == base
    # Large jobs reach the maximum and never exceed it.
    assert ofr._gmsh_stall_budget_s(base, ofr.GMSH_STALL_LARGE_CELLS) == 900.0
    assert ofr._gmsh_stall_budget_s(base, 20_000_000) == 900.0
    # Mid-size jobs land strictly between base and max (measured ramp).
    mid = ofr._gmsh_stall_budget_s(base, 1_000_000)
    assert base < mid < 900.0
    # A base above the ceiling is honoured as-is (never lowered).
    assert ofr._gmsh_stall_budget_s(1200.0, 20_000_000) == 1200.0


def test_stall_env_override_is_not_size_scaled(monkeypatch):
    """An explicit POLYFOAMMESH_GMSH_STALL_S pins the threshold exactly."""
    monkeypatch.setenv(ofr.GMSH_STALL_ENV_VAR, "42")
    assert ofr._gmsh_stall_env_overridden() is True
    assert ofr._gmsh_stall_timeout_s() == 42.0
    monkeypatch.delenv(ofr.GMSH_STALL_ENV_VAR)
    assert ofr._gmsh_stall_env_overridden() is False


def test_process_cpu_seconds_probe_returns_number_for_live_process():
    """The CPU probe used by the heartbeat works on this interpreter."""
    import os as _os

    class _Proc:
        pid = _os.getpid()

    cpu = ofr._process_cpu_seconds(_Proc())
    assert cpu is not None and cpu >= 0.0
    assert ofr._process_cpu_seconds(None) is None


# --- GmshVolumeWorker retry-or-fail (fake streaming loop) ------------------- #

def _fake_stream_factory(outcomes):
    """Return (fake_stream, calls). Each outcome is 'stall' or 'ok'."""
    calls: list[dict] = []
    queue = list(outcomes)

    def fake_stream(cmd, run_cwd, timeout_s, on_line, env=None, heartbeat_s=10,
                    proc_holder=None, stall_s=None, stall_holder=None):
        calls.append({"env": dict(env or {}), "stall_s": stall_s})
        outcome = queue.pop(0)
        if outcome == "stall":
            if stall_holder is not None:
                stall_holder["stalled"] = True
                stall_holder["elapsed_s"] = 5.0
                stall_holder["no_output_s"] = float(stall_s or 0.0)
            on_line(
                f"watchdog fired: no output for {int(stall_s or 0)}s "
                "(0s elapsed) - killing process tree"
            )
            return (None, [], [], False)
        if outcome == "ok":
            payload = json.dumps({"success": True, "msh_path": "out.msh"})
            on_line("Info: mesh written")
            return (0, [payload], [], False)
        raise AssertionError(f"unexpected outcome {outcome!r}")

    return fake_stream, calls


def _run_volume_worker(monkeypatch, outcomes, stall_env=None, max_cells_target=0):
    if stall_env is not None:
        monkeypatch.setenv(ofr.GMSH_STALL_ENV_VAR, str(stall_env))
    fake_stream, calls = _fake_stream_factory(outcomes)
    monkeypatch.setattr(ofr, "_stream_subprocess", fake_stream)

    worker = ofr.GmshVolumeWorker(
        step_path="geom.step", msh_path=Path("out.msh"), detail="medium",
        max_cells_target=max_cells_target,
    )
    finished: list = []
    failed: list[str] = []
    logs: list[str] = []
    worker.finished.connect(finished.append)
    worker.failed.connect(failed.append)
    worker.log_line.connect(logs.append)
    worker.run()
    return calls, finished, failed, logs


def test_stall_triggers_one_robust_retry_with_env_var(monkeypatch):
    """(b, retry half) First stall -> retry once with POLYFOAMMESH_GMSH_ROBUST."""
    calls, finished, failed, logs = _run_volume_worker(
        monkeypatch, outcomes=["stall", "ok"], stall_env=7,
    )

    assert len(calls) == 2, calls
    assert ofr.GMSH_ROBUST_ENV_VAR not in calls[0]["env"]
    assert calls[1]["env"][ofr.GMSH_ROBUST_ENV_VAR] == "1"
    # The configured threshold is what the worker passes to the watchdog.
    assert calls[0]["stall_s"] == 7.0
    # Env override is never size-scaled: robust keeps the exact pinned value.
    assert calls[1]["stall_s"] == 7.0

    assert finished, "robust retry succeeded but stage did not finish"
    assert not failed
    joined = "\n".join(logs)
    assert "stalled: no output for" in joined
    assert "retrying in robust mode" in joined
    assert "healing + Delaunay 2D" in joined


def test_robust_retry_gets_size_scaled_budget(monkeypatch):
    """(f) A large job raises only the robust retry's no-output budget."""
    # No env override -> size scaling applies. A large explicit target pushes
    # the robust budget to the 900 s ceiling while the normal attempt stays
    # at the flat 300 s base.
    calls, _finished, _failed, logs = _run_volume_worker(
        monkeypatch, outcomes=["stall", "ok"], max_cells_target=6_000_000,
    )

    assert len(calls) == 2, calls
    assert calls[0]["stall_s"] == ofr.GMSH_STALL_TIMEOUT_S
    assert calls[1]["stall_s"] == ofr.GMSH_STALL_MAX_TIMEOUT_S
    assert any("robust stall budget raised to 900s" in line for line in logs)


def test_second_stall_fails_stage_instead_of_looping(monkeypatch):
    """(c) Robust retry stalls too -> exactly two attempts, then failure."""
    calls, finished, failed, logs = _run_volume_worker(
        monkeypatch, outcomes=["stall", "stall"], stall_env=5,
    )

    assert len(calls) == 2, f"watchdog looped: {len(calls)} attempts"
    assert calls[1]["env"][ofr.GMSH_ROBUST_ENV_VAR] == "1"
    assert not finished
    assert failed, "stage did not fail after a second stall"
    msg = failed[0].lower()
    assert "stalled twice" in msg
    assert "robust" in msg
    joined = "\n".join(logs)
    assert "giving up" in joined


def test_no_stall_runs_once_without_robust_env(monkeypatch):
    """Healthy volume run: one attempt, no robust flag, stage finishes."""
    calls, finished, failed, _logs = _run_volume_worker(
        monkeypatch, outcomes=["ok"],
    )

    assert len(calls) == 1
    assert ofr.GMSH_ROBUST_ENV_VAR not in calls[0]["env"]
    assert finished and not failed
    assert calls[0]["stall_s"] == ofr.GMSH_STALL_TIMEOUT_S
