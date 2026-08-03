"""Unit tests for the unified task manager (gui/task_runner.py).

Covers: submit lifecycle, progress/log routing on the GUI thread, success,
error path, cooperative cancellation (<2 s), re-entry guard, watchdog stall
detection, and bounded shutdown.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication

from cfmesh_autogui.gui.task_runner import (
    FunctionWorker,
    TaskManager,
)


@pytest.fixture(scope="module")
def qapp():
    # QApplication (not QCoreApplication): other test modules construct
    # widgets, and a QApplication instance satisfies every consumer.
    return QApplication.instance() or QApplication([])


def _pump(ms: int = 50) -> None:
    """Pump the Qt event loop briefly."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _wait_until(pred, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        _pump(20)
    return pred()


def test_submit_success_runs_callback_on_gui_thread(qapp):
    mgr = TaskManager()
    main_thread = qapp.thread()
    results: list = []
    threads: list = []

    def fn(worker):
        worker.report_progress("phase", 42.0)
        return {"ok": 1}

    ok = mgr.submit(
        "t_success", FunctionWorker(fn),
        on_progress=lambda name, stage, pct: results.append(("p", stage, pct)),
        on_finished=lambda name, result: (
            results.append(("f", result)),
            threads.append(QThread.currentThread()),
        ),
    )
    assert ok
    assert mgr.is_running("t_success")
    assert _wait_until(lambda: ("f", {"ok": 1}) in results)
    assert ("p", "phase", 42.0) in results
    assert threads and threads[0] is main_thread
    assert mgr.running == []
    mgr.shutdown()


def test_error_path_emits_failed(qapp):
    mgr = TaskManager()
    errors: list = []

    def fn(worker):
        raise RuntimeError("boom")

    mgr.submit("t_error", FunctionWorker(fn), on_failed=lambda name, msg: errors.append(msg))
    assert _wait_until(lambda: len(errors) == 1)
    assert "boom" in errors[0]
    mgr.shutdown()


def test_cancellation_cooperative_and_fast(qapp):
    mgr = TaskManager()
    cancelled: list = []
    finished: list = []

    def fn(worker):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if worker.is_cancelled():
                return None
            worker.heartbeat_now()
            time.sleep(0.01)
        return {"done": True}

    mgr.submit(
        "t_cancel", FunctionWorker(fn),
        on_finished=lambda name, result: finished.append(result),
        on_cancelled=lambda name, msg: cancelled.append(msg),
    )
    assert _wait_until(lambda: mgr.is_running("t_cancel"))
    t0 = time.monotonic()
    assert mgr.cancel("t_cancel") is True
    assert _wait_until(lambda: cancelled, timeout_s=2.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 2.0
    assert finished == []
    assert not mgr.is_running("t_cancel")
    mgr.shutdown()


def test_reentry_guard_rejects_duplicate_name(qapp):
    mgr = TaskManager()

    def fn(worker):
        time.sleep(1.0)

    first = mgr.submit("t_dup", FunctionWorker(fn))
    second = mgr.submit("t_dup", FunctionWorker(fn))
    assert first is True
    assert second is False
    mgr.shutdown()


def test_watchdog_detects_stall(qapp):
    mgr = TaskManager(heartbeat_timeout_s=0.3, stall_poll_ms=50)
    stalled: list = []

    def fn(worker):
        time.sleep(0.8)  # no heartbeat at all

    mgr.submit("t_stall", FunctionWorker(fn))
    mgr.stalled.connect(lambda name, secs: stalled.append((name, secs)))
    assert _wait_until(lambda: len(stalled) == 1, timeout_s=3.0)
    assert stalled[0][0] == "t_stall"
    mgr.shutdown()


def test_shutdown_terminates_running_tasks_within_budget(qapp):
    mgr = TaskManager()

    def fn(worker):
        # Cooperative worker that only exits when cancelled (realistic
        # contract: every task must honour the token). terminate() is a
        # documented backstop for hostile workers but is racy with the
        # GIL on Windows, so it is not exercised here.
        while not worker.is_cancelled():
            time.sleep(0.05)

    mgr.submit("t_shut", FunctionWorker(fn))
    assert _wait_until(lambda: mgr.is_running("t_shut"))
    t0 = time.monotonic()
    clean = mgr.shutdown(timeout_ms=1500)
    elapsed = time.monotonic() - t0
    assert clean is True
    assert elapsed < 3.0
    assert mgr.running == []


def test_subprocess_kill_hook_invoked_on_cancel(qapp):
    mgr = TaskManager()
    killed: list = []

    def fn(worker):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if worker.is_cancelled():
                return
            time.sleep(0.01)
        return

    def kill():
        killed.append(True)

    mgr.submit("t_kill", FunctionWorker(fn, cancel_hook=kill))
    assert _wait_until(lambda: mgr.is_running("t_kill"))
    mgr.cancel("t_kill")
    assert _wait_until(lambda: killed)
    mgr.shutdown()


class _FakeWorker(QObject):
    """Duck-typed worker mimicking existing app worker classes: 2-arg
    finished (like FeatureDetectWorker), no heartbeat, no progress."""

    finished = Signal(object, object)
    log_line = Signal(str)
    failed = Signal(str)
    cancelled = Signal(str)

    def run(self):
        self.finished.emit({"feature": 1}, None)


def test_duck_typed_worker_arity_normalisation(qapp):
    mgr = TaskManager()
    results: list = []

    def on_finished(name, payload):
        results.append(("f", payload))

    mgr.submit(
        "t_duck", _FakeWorker(), on_finished=on_finished,
        signal_shapes={"finished": 2},
    )
    assert _wait_until(lambda: results)
    # 2-arg finished arrives as a single tuple payload
    assert results[0] == ("f", ({"feature": 1}, None))
    mgr.shutdown()


def test_duck_typed_worker_cancel_method_used_as_hook(qapp):
    import threading

    mgr = TaskManager()
    hook_called: list = []
    cancelled: list = []

    stop = threading.Event()

    class _SlowFake(QObject):
        finished = Signal(object, object)
        failed = Signal(str)
        cancelled = Signal(str)

        def run(self):
            while not stop.is_set():
                time.sleep(0.02)
            self.cancelled.emit("stopped")

        def cancel(self):
            hook_called.append(True)
            stop.set()

    mgr.submit("t_duck_cancel", _SlowFake(), on_cancelled=lambda n, m: cancelled.append(m))
    assert _wait_until(lambda: mgr.is_running("t_duck_cancel"))
    mgr.cancel("t_duck_cancel")
    assert _wait_until(lambda: hook_called)
    assert _wait_until(lambda: cancelled)
    mgr.shutdown()
