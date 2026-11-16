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
    # stall_s is deliberately ~6x the 0.5 s tick: under heavy suite load a
    # single tick can be delayed, and a tight margin made this test flaky
    # on the CI/Linux scheduler (the child is HEALTHY and must never be
    # killed, which is what the test is about).
    res = _run_child(_TICK_CHILD, (8, 0.5), stall_s=3.0, heartbeat_s=0.3)

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


def test_process_cpu_seconds_fallback_without_psutil(monkeypatch):
    """The stdlib fallback must work from a BARE pid, no psutil.

    Regression (blocked a release): the Windows fallback read
    ``proc._handle`` (only subprocess.Popen has it) and silently returned
    None for anything else - the CI runner has no psutil, so exactly this
    path ran there and the probe test failed.  Blocking psutil here makes
    a dev machine reproduce the CI condition.
    """
    import os as _os
    import sys as _sys

    monkeypatch.setitem(_sys.modules, "psutil", None)  # import -> ImportError

    class _Proc:
        pid = _os.getpid()

    cpu = ofr._process_cpu_seconds(_Proc())
    assert cpu is not None and cpu >= 0.0


# --- GmshVolumeWorker retry-or-fail (fake streaming loop) ------------------- #

def _fake_stream_factory(outcomes):
    """Return (fake_stream, calls). Each outcome is 'stall', 'signature',
    'error' or 'ok'."""
    calls: list[dict] = []
    queue = list(outcomes)

    def fake_stream(cmd, run_cwd, timeout_s, on_line, env=None, heartbeat_s=10,
                    proc_holder=None, stall_s=None, stall_holder=None,
                    signature_detector=None):
        calls.append({"env": dict(env or {}), "stall_s": stall_s,
                      "detector": signature_detector})
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
        if outcome == "signature":
            if stall_holder is not None:
                stall_holder["stalled"] = True
                stall_holder["signature"] = True
                stall_holder["evidence"] = (
                    "6524 elements remain invalid in surface 3 after 15 "
                    "split round(s) in 457s of 2D meshing (gate=magnitude)"
                )
                stall_holder["elapsed_s"] = 30.0
                stall_holder["no_output_s"] = 1.0
            on_line(
                "watchdog fired: non-convergence (6524 elements remain "
                "invalid in surface 3 after 15 split round(s)) - killing "
                "process tree"
            )
            return (None, [], [], False)
        if outcome == "error":
            payload = json.dumps({"success": False, "error": "GMSH fell over"})
            on_line("Info: about to fail")
            return (1, [payload], [], False)
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


def test_repeated_stalls_do_not_loop_forever(monkeypatch):
    """(c) normal + robust + manual all stall -> exactly three attempts, fail.

    Phase D adds ONE manual-equivalent (adaptive-OFF) attempt after the robust
    retry; the point here is that the chain stays finite — never a watchdog
    loop and never a bare error while a mesh might still be achievable.
    """
    calls, finished, failed, logs = _run_volume_worker(
        monkeypatch, outcomes=["stall", "stall", "stall"], stall_env=5,
        max_cells_target=200_000,
    )

    assert len(calls) == 3, f"watchdog looped: {len(calls)} attempts"
    assert calls[1]["env"][ofr.GMSH_ROBUST_ENV_VAR] == "1"
    assert calls[2]["env"][ofr.GMSH_MANUAL_EQUIV_ENV_VAR] == "1"
    assert not finished
    assert failed, "stage did not fail after the manual fallback stalled"
    assert "manual-equivalent" in failed[0].lower()
    joined = "\n".join(logs)
    assert "giving up" in joined


def test_gmsh_threads_env_is_a_reproducibility_pin(monkeypatch):
    """M2: GMSH parallel tessellation can differ run-to-run; opt-in pin."""
    monkeypatch.setenv(ofr.GMSH_THREADS_ENV_VAR, "1")
    calls, finished, failed, _logs = _run_volume_worker(
        monkeypatch, outcomes=["ok"],
    )
    assert finished and not failed
    assert calls[0]["env"].get("GMSH_NUM_THREADS") == "1"


def test_no_stall_runs_once_without_robust_env(monkeypatch):
    """Healthy volume run: one attempt, no robust flag, stage finishes."""
    calls, finished, failed, _logs = _run_volume_worker(
        monkeypatch, outcomes=["ok"],
    )

    assert len(calls) == 1
    assert ofr.GMSH_ROBUST_ENV_VAR not in calls[0]["env"]
    assert finished and not failed
    assert calls[0]["stall_s"] == ofr.GMSH_STALL_TIMEOUT_S


# --- Phase B: signature non-convergence detector (unit) --------------------- #

def _detector(**kw):
    return ofr.GmshSignatureStallDetector(**kw)


def test_detector_magnitude_gate_fires_on_large_invalid_count():
    d = _detector()
    assert d.feed("Info    : [ 20%] Meshing surface 3 (Cone, MeshAdapt)") is False
    assert d.feed("Warning : 2 elements remain invalid in surface 3") is False
    assert d.feed("Warning : 364 elements remain invalid in surface 3") is True

    assert d.fired is True
    assert d.max_invalid == 364
    assert d.max_invalid_surface == 3
    assert "364 elements remain invalid in surface 3" in d.evidence
    assert "gate=magnitude" in d.evidence
    # Fires once and stays fired; later smaller counts do not re-arm it.
    assert d.feed("Warning : 99 elements remain invalid in surface 4") is True


def test_detector_healthy_small_counts_do_not_fire():
    """The measured healthy venturi progression (max 14, 11 warnings)."""
    d = _detector()
    for c in (2, 2, 2, 6, 4, 2, 2, 4, 2, 14, 9, 2):
        assert d.feed(f"Warning : {c} elements remain invalid in surface 3") is False
    assert d.feed("Info    : [100%] Done meshing 2D (0.4 s)") is False
    assert d.fired is False


def test_detector_growth_gate_needs_splits_time_and_monotone_counts():
    clock = {"t": 100.0}
    d = _detector(clock=lambda: clock["t"])
    d.feed("Info    : [ 20%] Meshing surface 3 (Cone, MeshAdapt)")
    # 8 split rounds, monotone counts, but only ~8 s in the 2D phase: no fire.
    for i in range(8):
        d.feed("Info    : 8-| Splitting those edges and trying again")
        d.feed(f"Warning : {10 + i} elements remain invalid in surface 3")
        clock["t"] += 1.0
    assert d.fired is False, d.evidence
    # Past the 30 s floor with the counts still rising, the growth gate fires.
    clock["t"] += 40.0
    d.feed("Warning : 20 elements remain invalid in surface 3")
    assert d.fired is True
    assert "gate=growth" in d.evidence


def test_detector_growth_gate_ignores_non_monotone_counts():
    clock = {"t": 0.0}
    d = _detector(clock=lambda: clock["t"])
    d.feed("Info    : Meshing 2D")
    seq = [50, 40, 30]  # decreasing -> a recovering surface, not a runaway
    for i in range(9):
        d.feed("Info    : 8-| Splitting those edges and trying again")
        d.feed(f"Warning : {seq[i % 3]} elements remain invalid in surface 4")
        clock["t"] += 10.0
    assert d.fired is False


def test_detector_ignores_unrelated_lines():
    d = _detector()
    assert d.feed("Info    : All edges recovered after 1 iteration") is False
    assert d.feed("Warning : an element remains invalid somewhere") is False
    assert d.feed("") is False
    assert d.fired is False


# Real child that streams the runaway signature then stays alive, so the
# detector's kill wiring is exercised end-to-end (not just the parser).
_SIGNATURE_CHILD = (
    "import sys, time\n"
    "sys.stdout.write('Info    : [ 20%] Meshing surface 3 (Cone, MeshAdapt)\\n')\n"
    "sys.stdout.flush()\n"
    "for c in (2, 364, 2610, 5244, 6524):\n"
    "    sys.stdout.write('Warning : %d elements remain invalid in surface 3\\n' % c)\n"
    "    sys.stdout.flush()\n"
    "    time.sleep(0.05)\n"
    "time.sleep(60)\n"
)


def test_signature_detector_kills_runaway_via_stream_subprocess(monkeypatch):
    killed: list[int] = []
    real_kill = ofr._kill_pid_tree

    def _record_kill(pid):
        killed.append(pid)
        real_kill(pid)

    monkeypatch.setattr(ofr, "_kill_pid_tree", _record_kill)

    det = ofr.GmshSignatureStallDetector()
    cmd = [sys.executable, "-c", _SIGNATURE_CHILD]
    t0 = time.monotonic()
    _rc, _out, _err, timed_out = ofr._stream_subprocess(
        cmd, run_cwd=str(Path.cwd()), timeout_s=30,
        on_line=lambda _l: None, heartbeat_s=5.0, stall_s=None,
        stall_holder={}, signature_detector=det,
    )
    elapsed = time.monotonic() - t0

    assert det.fired, "detector never fired on the streamed runaway"
    assert killed, "process tree was never killed on signature fire"
    assert not timed_out
    assert elapsed < 20.0, f"kill was not prompt ({elapsed:.1f}s)"


# --- Phase C/D: attempt-chain wiring ---------------------------------------- #

def test_signature_stall_retries_robust_with_optimizer_flag(monkeypatch):
    """A signature kill -> robust retry WITH the optimizer env flag set."""
    calls, finished, failed, logs = _run_volume_worker(
        monkeypatch, outcomes=["signature", "ok"], stall_env=7,
        max_cells_target=200_000,
    )

    assert len(calls) == 2, calls
    assert ofr.GMSH_ROBUST_ENV_VAR not in calls[0]["env"]
    assert calls[1]["env"][ofr.GMSH_ROBUST_ENV_VAR] == "1"
    assert calls[1]["env"][ofr.GMSH_ROBUST_OPT_ENV_VAR] == "1"
    assert finished and not failed
    joined = "\n".join(logs)
    assert "non-convergence detected:" in joined
    assert "retrying robust" in joined


def test_no_output_stall_does_not_enable_signature_optimizer(monkeypatch):
    """Only the signature retry may turn the robust optimizer on."""
    calls, finished, failed, _logs = _run_volume_worker(
        monkeypatch, outcomes=["stall", "ok"], stall_env=7,
        max_cells_target=200_000,
    )

    assert len(calls) == 2, calls
    assert calls[1]["env"][ofr.GMSH_ROBUST_ENV_VAR] == "1"
    assert ofr.GMSH_ROBUST_OPT_ENV_VAR not in calls[1]["env"]
    assert finished and not failed


def test_robust_double_failure_runs_one_manual_equiv_fallback(monkeypatch):
    """robust fails (signature) -> ONE manual-equivalent (adaptive-OFF) run."""
    calls, finished, failed, logs = _run_volume_worker(
        monkeypatch, outcomes=["signature", "signature", "ok"],
        stall_env=7, max_cells_target=200_000,
    )

    assert len(calls) == 3, calls
    assert calls[1]["env"][ofr.GMSH_ROBUST_ENV_VAR] == "1"
    assert calls[2]["env"][ofr.GMSH_MANUAL_EQUIV_ENV_VAR] == "1"
    assert ofr.GMSH_ROBUST_ENV_VAR not in calls[2]["env"]
    assert finished and not failed
    joined = "\n".join(logs)
    assert "adaptive sizing did not converge on this geometry" in joined
    assert "meshed with the manual equivalent sizing" in joined


def test_robust_error_also_triggers_manual_equiv_fallback(monkeypatch):
    """A robust attempt that errors (not stalls) still gets the ONE fallback."""
    calls, finished, failed, _logs = _run_volume_worker(
        monkeypatch, outcomes=["stall", "error", "ok"],
        stall_env=7, max_cells_target=200_000,
    )

    assert len(calls) == 3, calls
    assert calls[2]["env"][ofr.GMSH_MANUAL_EQUIV_ENV_VAR] == "1"
    assert finished and not failed


def test_manual_equiv_failure_fails_honestly_without_looping(monkeypatch):
    calls, finished, failed, _logs = _run_volume_worker(
        monkeypatch, outcomes=["signature", "signature", "signature"],
        stall_env=7, max_cells_target=200_000,
    )

    assert len(calls) == 3, f"fallback looped: {len(calls)} attempts"
    assert not finished
    assert failed
    assert "manual-equivalent" in failed[0].lower()


def test_explicit_size_request_never_uses_manual_fallback(monkeypatch):
    """adaptive OFF -> no derived fallback; robust double-fail fails as before."""
    fake_stream, calls = _fake_stream_factory(["stall", "stall"])
    monkeypatch.setattr(ofr, "_stream_subprocess", fake_stream)

    worker = ofr.GmshVolumeWorker(
        step_path="geom.step", msh_path=Path("out.msh"), detail="medium",
        max_cell_size=0.01, min_cell_size=0.002, max_cells_target=0,
    )
    finished: list = []
    failed: list[str] = []
    worker.finished.connect(finished.append)
    worker.failed.connect(failed.append)
    worker.run()

    assert len(calls) == 2, calls
    assert all(
        ofr.GMSH_MANUAL_EQUIV_ENV_VAR not in c["env"] for c in calls
    ), calls
    assert failed and not finished
    # The pre-Phase-D honest failure text is preserved when no fallback exists.
    assert "stalled twice" in failed[0].lower()
    assert "robust" in failed[0].lower()


# --- Phase F: partition-loop circuit breaker -------------------------------- #
#
# Measured live on DN1000 ('dn1000 aperta.stp'): the volume attempt printed
# "Partitioning face 4 with 2 triangles that all have the same partition -
# changing tolerance to X" thousands of times while the Level partition
# counter climbed past 500k attempts, and the no-output watchdog stayed mute
# (output KEEPS arriving). The breaker counts the identical warning and fires;
# the live log shows one summary per N lines instead of the flood.

_SPAM_LINE = (
    "Warning : Partitioning face 4 with 2 triangles that all have the same "
    "partition - changing tolerance to 2.72995"
)


def test_partition_breaker_fires_at_threshold_and_reports_evidence():
    b = ofr.PartitionLoopBreaker(fire_after=3, summary_every=2)
    assert b.feed("Info    : Meshing volume 1 (Delaunay)") is False
    assert b.feed(_SPAM_LINE) is False
    assert b.feed(_SPAM_LINE.replace("2.72995", "67.505")) is False
    assert b.count == 2
    assert b.feed(_SPAM_LINE.replace("2.72995", "1.03")) is True

    assert b.fired is True
    assert b.count == 3
    assert "partition loop:" in b.evidence
    assert "face 4 x3" in b.evidence
    assert "tolerance 1.03..67.505" in b.evidence
    assert "non-convergent 2D partitioning" in b.evidence
    # Fires once and stays fired: further spam does not re-arm it.
    assert b.feed(_SPAM_LINE) is True
    assert b.count == 3


def test_partition_breaker_healthy_log_never_fires():
    b = ofr.PartitionLoopBreaker()
    for line in (
        "Info    : Meshing curve 1 (Line)",
        "Info    : [ 20%] Meshing surface 3 (Cone, MeshAdapt)",
        "Warning : 2 elements remain invalid in surface 3",
        "Info    : Done meshing 2D (0.4 s)",
        "Warning : Tolerance too large - aborting partitioning",
        "",
    ):
        assert b.feed(line) is False
    assert b.fired is False and b.count == 0


def test_partition_breaker_gui_line_consumes_spam_and_summarizes():
    b = ofr.PartitionLoopBreaker(fire_after=100, summary_every=3)
    # Non-spam lines always pass through untouched.
    assert b.gui_line("Info    : meshing volume") == "Info    : meshing volume"
    for _ in range(6):
        b.feed(_SPAM_LINE)
    # The first two forwarded spam lines are consumed...
    assert b.gui_line(_SPAM_LINE) is None
    assert b.gui_line(_SPAM_LINE) is None
    # ...the third is replaced by ONE summary of ALL spam seen so far.
    summary = b.gui_line(_SPAM_LINE)
    assert summary is not None
    assert "[partition loop]" in summary
    assert "6 identical" in summary
    assert "face 4 x6" in summary
    assert b.gui_line(_SPAM_LINE) is None
    # After firing, spam stays silent (the watchdog message explains the kill)
    # while non-spam lines still reach the log.
    for _ in range(95):
        b.feed(_SPAM_LINE)
    assert b.fired is True
    assert b.gui_line(_SPAM_LINE) is None
    assert b.gui_line("watchdog fired: non-convergence") == (
        "watchdog fired: non-convergence"
    )


def test_partition_breaker_counts_tolerance_range_across_faces():
    b = ofr.PartitionLoopBreaker(fire_after=4)
    for tol in ("100.758", "1.03", "45.3367", "14.1368"):
        b.feed(_SPAM_LINE.replace("2.72995", tol))
    b.feed(_SPAM_LINE.replace("face 4", "face 7"))
    assert b.fired is True
    assert "face 4 x4" in b.evidence
    assert "tolerance 1.03..100.758" in b.evidence


def test_composite_detector_fans_out_and_reports_first_evidence():
    det = ofr.GmshSignatureStallDetector()
    breaker = ofr.PartitionLoopBreaker(fire_after=3)
    comp = ofr._CompositeSignatureDetector([det, breaker])
    comp.feed("Info    : all fine")
    assert comp.fired is False and comp.evidence == ""
    for _ in range(3):
        comp.feed(_SPAM_LINE)
    assert comp.fired is True
    assert "partition loop:" in comp.evidence
    # The other child is unaffected and still receives its lines.
    assert det.fired is False
    assert det.feed("Warning : 364 elements remain invalid in surface 3") is True
    assert "364 elements" in comp.evidence


# Real child that streams the partition-loop spam then stays alive, so the
# breaker's kill wiring is exercised end-to-end (not just the parser).
_PARTITION_CHILD = (
    "import sys, time\n"
    "sys.stdout.write('Info    : Meshing volume 1 (Delaunay)\\n')\n"
    "sys.stdout.flush()\n"
    "for i in range(2100):\n"
    "    sys.stdout.write('Warning : Partitioning face 4 with 2 triangles '\n"
    "                     'that all have the same partition - changing '\n"
    "                     'tolerance to %d\\n' % (i % 97 + 1))\n"
    "sys.stdout.flush()\n"
    "time.sleep(60)\n"
)


def test_partition_breaker_kills_runaway_via_stream_subprocess(monkeypatch):
    killed: list[int] = []
    real_kill = ofr._kill_pid_tree

    def _record_kill(pid):
        killed.append(pid)
        real_kill(pid)

    monkeypatch.setattr(ofr, "_kill_pid_tree", _record_kill)

    breaker = ofr.PartitionLoopBreaker()
    stall: dict = {}
    cmd = [sys.executable, "-c", _PARTITION_CHILD]
    t0 = time.monotonic()
    _rc, _out, _err, timed_out = ofr._stream_subprocess(
        cmd, run_cwd=str(Path.cwd()), timeout_s=30,
        on_line=lambda _l: None, heartbeat_s=5.0, stall_s=None,
        stall_holder=stall, signature_detector=breaker,
    )
    elapsed = time.monotonic() - t0

    assert breaker.fired, "breaker never fired on the streamed spam"
    assert breaker.count >= ofr.GMSH_PARTITION_FIRE_THRESHOLD
    assert killed, "process tree was never killed on breaker fire"
    assert not timed_out
    assert elapsed < 20.0, f"kill was not prompt ({elapsed:.1f}s)"
    # The watchdog routes the fire through the same stall channel the retry
    # chain reads: named, with the breaker's evidence.
    assert stall.get("stalled") is True
    assert stall.get("signature") is True
    assert "partition loop" in str(stall.get("evidence", ""))


def test_volume_worker_wires_partition_breaker_into_stream(monkeypatch):
    """run() passes a composite detector holding the live breaker instance."""
    fake_stream, calls = _fake_stream_factory(["ok"])
    monkeypatch.setattr(ofr, "_stream_subprocess", fake_stream)

    worker = ofr.GmshVolumeWorker(
        step_path="geom.step", msh_path=Path("out.msh"), detail="medium",
    )
    finished: list = []
    failed: list[str] = []
    worker.finished.connect(finished.append)
    worker.failed.connect(failed.append)
    worker.run()

    assert finished and not failed
    det = calls[0]["detector"]
    assert isinstance(det, ofr._CompositeSignatureDetector)
    children = det.children
    assert len(children) == 2
    assert isinstance(children[0], ofr.GmshSignatureStallDetector)
    assert isinstance(children[1], ofr.PartitionLoopBreaker)
    # The same breaker instance _on_stream_line consults for the live filter.
    assert children[1] is worker._partition_breaker


def test_volume_worker_live_log_consumes_partition_spam():
    """_on_stream_line replaces the spam flood with periodic summaries."""
    worker = ofr.GmshVolumeWorker(
        step_path="geom.step", msh_path=Path("out.msh"), detail="medium",
    )
    logs: list[str] = []
    worker.log_line.connect(logs.append)
    breaker = ofr.PartitionLoopBreaker(summary_every=4)
    worker._partition_breaker = breaker
    # Mirror the real pump order: _stream_subprocess feeds every line to the
    # detector first, then forwards it to on_line.
    for _ in range(6):
        breaker.feed(_SPAM_LINE)
        worker._on_stream_line(_SPAM_LINE)
    # 6 spam arrivals (count=6) -> ONE summary at the 4th forward, 5 consumed.
    assert len(logs) == 1
    assert logs[0].startswith("[tet:gmsh] [partition loop]")
    assert "4 identical" in logs[0]
    assert "face 4 x4" in logs[0]
    # Non-spam lines still reach the log untouched, with the [tet:gmsh] tag.
    worker._on_stream_line("Info    : meshing volume")
    assert logs[-1] == "[tet:gmsh] Info    : meshing volume"
    # Progress parsing is unaffected: a % on a non-spam line still emits.
    events: list[tuple] = []
    worker.progress.connect(lambda name, pct: events.append((name, pct)))
    worker._on_stream_line("Info    : [ 50%] Meshing volume")
    assert events and events[0] == ("gmsh", 50.0)
