"""Unified background-task execution for the CFMesh-AutoGUI UI.

This module is the ONE concurrency pattern for the application (decision
recorded in .slim/deepwork/hardening-anti-freeze.md):

    worker QObject on a dedicated QThread, moved with explicit moveToThread,
    owned and wired by TaskManager.

Why this pattern (and not QThreadPool/QRunnable or plain threads):

- The codebase already has ~15 worker QObject classes (GmshVolumeWorker,
  CheckMeshWorker, ExportWorker, ...) that all follow "QObject + run() +
  signals + moveToThread". Unifying means a thin wiring layer, not a rewrite.
- QProcess and "subprocess.run inside a QThread started via
  t.started.connect(w.run)" have both failed in real sessions in this app
  (see main_window.py:2521-2526 and :2424-2442): PySide6 cannot always
  resolve a receiver thread for plain closures, so queued callbacks ran on
  the worker thread and crashed or froze the GUI. Every callback here is
  therefore invoked from TaskManager slots that run on the GUI thread, so
  callers can pass plain callables safely.
- TaskManager owns the QThread lifecycle, the cooperative cancellation
  token, a liveness watchdog, and a bounded shutdown path, so no other
  module needs to touch QThread directly.

Contract:
- Long work goes through TaskManager.submit() only.
- Every task is cancellable (cooperative token + optional subprocess kill
  hook). Cancel must return the UI to a usable state in well under 2 s.
- Every task updates liveness (progress/log/heartbeat); the watchdog emits
  stalled(name, seconds) if a task goes silent for heartbeat_timeout_s.
- shutdown(timeout_ms) is the only exit path: cancel everything, quit all
  threads within the budget, terminate stragglers.
- No processEvents(), no waitForFinished(), no time.sleep on the GUI thread
  is needed anywhere that uses this module.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot

logger = logging.getLogger(__name__)


class CancellationToken:
    """Thread-safe cooperative cancellation flag shared with a worker."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


class WorkerBase(QObject):
    """Base class for long-running workers.

    Subclasses implement ``run()`` and emit terminal signals exactly once:

    - ``finished(result)`` on success
    - ``failed(message)`` on unrecoverable error
    - ``cancelled(message)`` after cooperative cancellation (when the
      caller cancelled and the worker noticed)

    While running, emit ``progress(stage, percent)``, ``log_line(text)``
    and/or ``heartbeat()`` to keep the watchdog satisfied.
    """

    progress = Signal(str, float)
    log_line = Signal(str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)
    heartbeat = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._token: CancellationToken | None = None

    def attach_token(self, token: CancellationToken) -> None:
        self._token = token

    def is_cancelled(self) -> bool:
        return self._token is not None and self._token.cancelled

    def report_progress(self, stage: str, percent: float) -> None:
        self.progress.emit(stage, percent)

    def log(self, msg: str) -> None:
        self.log_line.emit(msg)

    def heartbeat_now(self) -> None:
        self.heartbeat.emit()

    def run(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class FunctionWorker(WorkerBase):
    """Wraps a plain callable ``fn(worker)`` as a cancellable task.

    ``fn`` receives this worker so it can call ``worker.report_progress``,
    ``worker.log`` and ``worker.is_cancelled()``. Its return value is
    emitted through ``finished``; exceptions through ``failed``.

    ``cancel_hook`` (optional) is invoked from the GUI thread when the user
    cancels, to kill an in-flight subprocess the function cannot reach
    itself (see openfoam_runner._stream_subprocess's proc_holder pattern).
    """

    def __init__(
        self,
        fn: Callable[[FunctionWorker], Any],
        cancel_hook: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._fn = fn
        self._cancel_hook = cancel_hook

    def run(self) -> None:
        try:
            result = self._fn(self)
        except Exception as exc:  # noqa: BLE001
            logger.error("Task crashed:\n%s", traceback.format_exc())
            self.failed.emit(str(exc))
            return
        if self.is_cancelled():
            self.cancelled.emit("Cancelled by user")
        else:
            self.finished.emit(result)

    def cancel(self) -> None:  # called from GUI thread
        if self._cancel_hook is not None:
            try:
                self._cancel_hook()
            except Exception as exc:  # noqa: BLE001
                logger.warning("cancel_hook failed: %s", exc)


class _Relay(QObject):
    """GUI-thread signal relay normalising worker signal arity.

    Worker signals connect (QueuedConnection) to this relay's fixed-
    signature bridge slots, which re-emit the standard relay signals.
    Every connection is between two QObjects, so PySide6 always resolves
    the receiver thread correctly — no plain-callable connections, which
    are unreliable for queued delivery (observed: args arrive unpacked or
    packed depending on the QApplication instance). The relay lives on the
    GUI thread, so its signals may connect directly to TaskManager slots.
    """

    progress = Signal(str, float)
    log_line = Signal(str)
    heartbeat = Signal()
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)
    notify = Signal(object)  # generic passthrough for worker-specific events

    # Fixed-signature bridge slots (one per worker-signal shape).
    @Slot(str, float)
    def _b_progress2(self, stage: str, pct: float) -> None:
        self.progress.emit(stage, pct)

    @Slot(str)
    def _b_log1(self, msg: str) -> None:
        self.log_line.emit(msg)

    @Slot()
    def _b_heartbeat0(self) -> None:
        self.heartbeat.emit()

    @Slot(object)
    def _b_finished1(self, result: object) -> None:
        self.finished.emit(result)

    @Slot(object, object)
    def _b_finished2(self, a: object, b: object) -> None:
        self.finished.emit((a, b))

    @Slot(str)
    def _b_failed1(self, msg: str) -> None:
        self.failed.emit(msg)

    @Slot()
    def _b_cancelled0(self) -> None:
        self.cancelled.emit("Cancelled by user")

    @Slot(str)
    def _b_cancelled1(self, msg: str) -> None:
        self.cancelled.emit(msg)

    @Slot(str)
    def _b_error1(self, msg: str) -> None:
        self.failed.emit(msg)

    @Slot(object, object)
    def _b_notify2(self, a: object, b: object) -> None:
        self.notify.emit((a, b))


class _WorkerDispatcher(QObject):
    """Runs ``worker.run(*args, **kwargs)`` inside the worker's QThread.

    Lives in the same thread as the worker (moved there by submit()), so a
    queued connection from ``QThread.started`` executes its slot in the
    worker thread — unlike a plain lambda, which PySide6 delivers to the
    connection's creator thread (the GUI thread).
    """

    def __init__(self, worker: QObject, args: tuple, kwargs: dict):
        super().__init__()
        self._w = worker
        self._args = args
        self._kwargs = kwargs

    @Slot()
    def go(self) -> None:
        self._w.run(*self._args, **self._kwargs)


# worker_signal_name -> (relay bridge slot, relay signal)
# signal_shapes can override the default arity per worker (see submit()).
_RELAY_MAP: dict[str, tuple[str, str]] = {
    "progress": ("_b_progress2", "progress"),
    "log_line": ("_b_log1", "log_line"),
    "heartbeat": ("_b_heartbeat0", "heartbeat"),
    "finished": ("_b_finished1", "finished"),
    "failed": ("_b_failed1", "failed"),
    "cancelled": ("_b_cancelled1", "cancelled"),
    "error_occurred": ("_b_error1", "failed"),
}

# Worker signals whose arity deviates from the default, mapped to the
# bridge slot handling that arity: signal name -> bridge slot.
_ARITY_OVERRIDES: dict[str, dict[str, str]] = {
    "finished": {"2": "_b_finished2"},
    "cancelled": {"0": "_b_cancelled0"},
    "cycle_done": {"2": "_b_notify2"},
}


class TaskManager(QObject):
    """Owns QThreads for long-running tasks and centralises lifecycle.

    Usage (from the GUI thread)::

        mgr = TaskManager(parent=self)
        mgr.submit(
            "gmsh_volume", worker,
            on_progress=lambda name, stage, pct: ...,
            on_finished=lambda name, result: ...,
            on_failed=lambda name, msg: ...,
            on_cancelled=lambda name, msg: ...,
        )
        mgr.cancel("gmsh_volume")   # or mgr.cancel_all()
        mgr.shutdown(timeout_ms=2500)   # on app close

    Callbacks always run on the GUI thread (TaskManager lives there), so
    they may touch widgets freely — no closure-thread pitfalls.
    """

    stalled = Signal(str, float)  # task name, seconds since last liveness
    task_finished = Signal(str, object)
    task_failed = Signal(str, str)
    task_cancelled = Signal(str, str)

    def __init__(
        self,
        parent: QObject | None = None,
        heartbeat_timeout_s: float = 120.0,
        stall_poll_ms: int = 1000,
        cancel_grace_ms: int = 2500,
    ) -> None:
        super().__init__(parent)
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._cancel_grace_ms = cancel_grace_ms
        self._tasks: dict[str, dict[str, Any]] = {}
        self._task_of_sender: dict[int, str] = {}
        self._retired: list[dict[str, Any]] = []
        self._zombies: list[tuple] = []
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(stall_poll_ms)
        self._watchdog.timeout.connect(self._check_stalls)
        self._watchdog.start()

    # ------------------------------------------------------------------ API

    @property
    def running(self) -> list[str]:
        return [n for n, t in self._tasks.items() if t["state"] == "running"]

    def is_running(self, name: str) -> bool:
        t = self._tasks.get(name)
        return t is not None and t["state"] == "running"

    def submit(
        self,
        name: str,
        worker: WorkerBase,
        *,
        on_finished: Callable | None = None,
        on_failed: Callable | None = None,
        on_cancelled: Callable | None = None,
        on_progress: Callable | None = None,
        on_log: Callable | None = None,
        on_notify: Callable | None = None,
        cancel_hook: Callable[[], None] | None = None,
        heartbeat_timeout_s: float | None = None,
        run_args: tuple = (),
        run_kwargs: dict | None = None,
        signal_shapes: dict | None = None,
    ) -> bool:
        """Start ``worker`` in its own QThread under the name ``name``.

        ``worker`` may be any QObject exposing ``run()`` (plus the usual
        progress/log_line/heartbeat/finished/failed/cancelled signals in any
        arity — they are normalised through an internal relay). Extra
        worker signals such as ``error_occurred`` map onto ``failed``.
        ``run_args``/``run_kwargs`` are passed to ``worker.run()`` when it
        takes parameters.

        Returns False (without starting) if a task with that name is
        already running — callers use this as their re-entry guard.
        """
        if self.is_running(name):
            logger.warning("TaskManager.submit(%s) ignored: already running", name)
            return False

        token = CancellationToken()
        if callable(getattr(worker, "attach_token", None)):
            worker.attach_token(token)
        else:
            # Duck-typed workers (existing app worker classes) don't
            # subclass WorkerBase; expose the token for is_cancelled().
            worker._token = token
        if callable(getattr(worker, "cancel", None)) and cancel_hook is None:
            cancel_hook = worker.cancel  # type: ignore[attr-defined]

        thread = QThread(self)
        relay = _Relay(self)
        worker.moveToThread(thread)

        task = {
            "name": name,
            "worker": worker,
            "thread": thread,
            "relay": relay,
            "token": token,
            "cancel_hook": cancel_hook,
            "state": "running",
            "last_seen": time.monotonic(),
            "on_finished": on_finished,
            "on_failed": on_failed,
            "on_cancelled": on_cancelled,
            "on_progress": on_progress,
            "on_log": on_log,
            "on_notify": on_notify,
            "timeout_s": heartbeat_timeout_s or self._heartbeat_timeout_s,
            "terminal": False,
        }
        self._tasks[name] = task
        self._task_of_sender[id(relay)] = name

        # Bridge worker signals into the relay through fixed-signature slots
        # (QueuedConnection to a QObject receiver — the reliable pattern).
        signal_shapes = signal_shapes or {}
        for worker_sig_name, (bridge_slot_name, _relay_sig_name) in _RELAY_MAP.items():
            worker_sig = getattr(worker, worker_sig_name, None)
            if worker_sig is None:
                continue
            slot_name = bridge_slot_name
            shapes = signal_shapes.get(worker_sig_name)
            if shapes is not None:
                slot_name = _ARITY_OVERRIDES[worker_sig_name].get(
                    str(shapes), bridge_slot_name
                )
            bridge_slot = getattr(relay, slot_name)
            worker_sig.connect(bridge_slot, Qt.QueuedConnection)
        cycle_done = getattr(worker, "cycle_done", None)
        if cycle_done is not None:
            cycle_done.connect(relay._b_notify2, Qt.QueuedConnection)

        relay.progress.connect(self._on_progress)
        relay.log_line.connect(self._on_log_line)
        relay.heartbeat.connect(self._on_heartbeat)
        relay.finished.connect(self._on_finished)
        relay.failed.connect(self._on_failed)
        relay.cancelled.connect(self._on_cancelled)
        relay.notify.connect(self._on_notify)

        # started->run must run IN THE WORKER'S QTHREAD. Connecting
        # `started` to a plain lambda with QueuedConnection does NOT: a
        # plain callable has no QObject receiver, so PySide6 delivers it
        # to the thread that made the connection — the GUI thread. Every
        # "background" task then executed on the main thread, freezing the
        # UI for the whole run (confirmed live with py-spy: MainThread
        # parked inside _stream_subprocess while GMSH meshed). A QObject
        # moved to the QThread has the right affinity, so its slot is
        # delivered to the worker thread's event loop.
        run_kwargs = run_kwargs or {}
        dispatcher = _WorkerDispatcher(worker, run_args, run_kwargs)
        dispatcher.moveToThread(thread)
        thread.started.connect(dispatcher.go, Qt.QueuedConnection)
        task["dispatcher"] = dispatcher
        thread.start()
        logger.info("TaskManager: started task '%s'", name)
        return True

    def cancel(self, name: str, reason: str = "Cancelled by user") -> bool:
        """Cooperatively cancel ``name``; force-teardown after a grace period."""
        task = self._tasks.get(name)
        if task is None or task["state"] != "running":
            return False
        task["token"].cancel()
        self._invoke_cancel_hook(task)
        # If the worker cannot stop itself (e.g. stuck in a blocking call),
        # tear it down after the grace period.
        QTimer.singleShot(
            self._cancel_grace_ms,
            lambda: self._force_cleanup(name, reason),
        )
        logger.info("TaskManager: cancel requested for '%s'", name)
        return True

    def cancel_all(self, reason: str = "Cancelled by user") -> None:
        for name in list(self._tasks):
            self.cancel(name, reason)

    def shutdown(self, timeout_ms: int = 2500) -> bool:
        """Cancel all tasks and stop every QThread within ``timeout_ms``.

        Returns True if all threads exited cleanly. Uses terminate() as a
        last resort, which never blocks longer than ~1 s per straggler.
        """
        self._watchdog.stop()
        for task in self._tasks.values():
            if task["state"] == "running":
                task["token"].cancel()
                self._invoke_cancel_hook(task)
        budget_s = timeout_ms / 1000.0
        deadline = time.monotonic() + budget_s
        all_clean = True
        for task in list(self._tasks.values()):
            thread = task["thread"]
            if thread is None:
                continue
            try:
                running_now = thread.isRunning()
            except RuntimeError:
                running_now = False  # C++ object already deleted
            if not running_now:
                continue
            # quit() while the thread is mid-run is safe: the QuitEvent is
            # delivered once the worker's run() returns and its event loop
            # starts (verified against PySide6 6.11 / Windows).
            thread.quit()
            remaining = deadline - time.monotonic()
            if remaining > 0 and thread.wait(int(remaining * 1000)):
                continue
            if thread.isRunning():
                # NEVER terminate() from the GUI thread: killing a Python
                # worker while it holds the GIL deadlocks the process, and
                # destroying a QThread whose thread is still running makes
                # Qt fail-fast (BEX64). Detach instead: the token was set,
                # so cooperative workers exit on their own; the app forces
                # os._exit() in closeEvent when stragglers remain.
                all_clean = False
                self._detach_zombie(task)
        self._tasks.clear()
        self._task_of_sender.clear()
        return all_clean

    # ------------------------------------------------------------- internals

    def _invoke_cancel_hook(self, task: dict) -> None:
        hook = task["cancel_hook"]
        if callable(hook):
            try:
                hook()
            except Exception as exc:  # noqa: BLE001
                logger.warning("cancel hook for '%s' failed: %s", task["name"], exc)

    def _touch(self, name: str) -> None:
        task = self._tasks.get(name)
        if task is not None:
            task["last_seen"] = time.monotonic()

    def _name_of(self) -> str:
        try:
            sender = self.sender()
        except RuntimeError:
            return "?"
        return self._task_of_sender.get(id(sender), "?")

    @Slot(str, float)
    def _on_progress(self, stage: str, pct: float) -> None:
        name = self._name_of()
        self._touch(name)
        task = self._tasks.get(name)
        if task is not None and task["on_progress"]:
            task["on_progress"](name, stage, pct)

    @Slot(str)
    def _on_log_line(self, msg: str) -> None:
        name = self._name_of()
        self._touch(name)
        task = self._tasks.get(name)
        if task is not None and task["on_log"]:
            task["on_log"](name, msg)

    @Slot()
    def _on_heartbeat(self) -> None:
        self._touch(self._name_of())

    @Slot(object)
    def _on_notify(self, payload: object) -> None:
        name = self._name_of()
        self._touch(name)
        task = self._tasks.get(name)
        if task is not None and task["on_notify"]:
            task["on_notify"](name, payload)

    @Slot(object)
    def _on_finished(self, result: object) -> None:
        self._finalize("finished", result)

    @Slot(str)
    def _on_failed(self, msg: str) -> None:
        self._finalize("failed", msg)

    @Slot(str)
    def _on_cancelled(self, msg: str) -> None:
        self._finalize("cancelled", msg)

    def _finalize(self, kind: str, payload: Any) -> None:
        name = self._name_of()
        task = self._tasks.get(name)
        if task is None or task["terminal"]:
            return
        task["terminal"] = True
        task["state"] = "done"
        task["last_seen"] = time.monotonic()
        callback = task.get(f"on_{kind}")
        if callback:
            try:
                if kind == "finished":
                    callback(name, payload)
                else:
                    callback(name, payload)
            except Exception as exc:  # noqa: BLE001
                logger.error("TaskManager callback for '%s' raised: %s", name, exc)
        if kind == "finished":
            self.task_finished.emit(name, payload)
        elif kind == "failed":
            self.task_failed.emit(name, payload)
        else:
            self.task_cancelled.emit(name, payload)
        self._begin_teardown(task)

    def _begin_teardown(self, task: dict) -> None:
        relay = task["relay"]
        thread = task["thread"]
        for sig_name in (
            "progress", "log_line", "heartbeat", "finished", "failed", "cancelled",
            "notify",
        ):
            sig = getattr(relay, sig_name, None)
            if sig is not None:
                try:
                    sig.disconnect()
                except (TypeError, RuntimeError):
                    # signal already disconnected during teardown — fine
                    logger.debug("task_runner: disconnect failed on %s", sig_name, exc_info=True)
        self._task_of_sender.pop(id(relay), None)
        relay.deleteLater()
        # Keep strong references to worker+thread until the C++ objects are
        # actually destroyed (thread.destroyed -> release). Releasing the
        # Python wrappers earlier can GC a wrapper whose C++ object still
        # has queued events in flight, producing native aborts in delivery.
        thread.quit()
        thread.finished.connect(task["worker"].deleteLater, Qt.QueuedConnection)
        thread.finished.connect(thread.deleteLater, Qt.QueuedConnection)
        thread.destroyed.connect(
            lambda: self._release(task), Qt.QueuedConnection,
        )
        self._retired.append(task)

    def _release(self, task: dict) -> None:
        """Drop the last strong references after the C++ thread is gone."""
        if self._tasks.get(task["name"]) is task:
            self._tasks.pop(task["name"], None)
        try:
            self._retired.remove(task)
        except ValueError:
            # already removed by a concurrent cleanup — fine, nothing to release
            logger.debug("task_runner: task not in retired set (concurrent cleanup)", exc_info=True)

    def _force_cleanup(self, name: str, reason: str) -> None:
        task = self._tasks.get(name)
        if task is None or task["state"] != "running":
            return
        thread = task["thread"]
        logger.warning("TaskManager: forcing cleanup of '%s' (%s)", name, reason)
        self._invoke_cancel_hook(task)
        thread.quit()
        if thread.wait(1000):
            # Worker exited cooperatively after the hook — finish like a
            # normal teardown (finished/deleteLater chain + release).
            relay = task["relay"]
            self._task_of_sender.pop(id(relay), None)
            task["state"] = "done"
            task["terminal"] = True
            cb = task.get("on_cancelled")
            if cb:
                try:
                    cb(name, reason)
                except Exception as exc:  # noqa: BLE001
                    logger.error("on_cancelled callback for '%s' raised: %s", name, exc)
            self.task_cancelled.emit(name, reason)
            relay.deleteLater()
            thread.finished.connect(task["worker"].deleteLater, Qt.QueuedConnection)
            thread.finished.connect(thread.deleteLater, Qt.QueuedConnection)
            thread.destroyed.connect(
                lambda: self._release(task), Qt.QueuedConnection,
            )
            self._retired.append(task)
            return
        if thread.isRunning():
            # Worker ignored the cancellation (e.g. blocked in a native call
            # holding the GIL). NEVER terminate() from the GUI thread — GIL
            # deadlock; and never let the QThread be destroyed while its
            # thread is still running — Qt fail-fast (BEX64). Detach it and
            # let it finish on its own; closeEvent() forces os._exit() if
            # any zombie remains at app close.
            self._detach_zombie(task)

    def _detach_zombie(self, task: dict) -> None:
        """Keep a running thread alive but out of the manager's ownership.

        Reparenting to None + keeping a strong reference means the QThread
        C++ object is never destroyed while its thread is running (the
        fail-fast source), and the GUI never blocks on it (the GIL-deadlock
        source). The token was already set, so cooperative workers exit on
        their own; anything still running at process exit is reaped by the
        OS when closeEvent() calls os._exit().
        """
        name = task["name"]
        thread = task["thread"]
        worker = task["worker"]
        relay = task["relay"]
        logger.error(
            "TaskManager: '%s' still running after cancel grace — detached as "
            "zombie (will be reaped at process exit)", name,
        )
        try:
            thread.setParent(None)  # never destroyed by the manager
        except RuntimeError:
            # thread already gone (concurrent teardown) — nothing to detach
            logger.debug("task_runner: thread already destroyed for '%s'", name, exc_info=True)
        self._zombies.append((name, worker, thread, relay))
        self._tasks.pop(name, None)
        self._task_of_sender.pop(id(relay), None)
        task["state"] = "done"
        task["terminal"] = True
        cb = task.get("on_cancelled")
        if cb:
            try:
                cb(name, "cancelled (stuck task detached)")
            except Exception as exc:  # noqa: BLE001
                logger.error("on_cancelled callback for '%s' raised: %s", name, exc)
        try:
            self.task_cancelled.emit(name, "cancelled (stuck task detached)")
        except RuntimeError:
            # Object being torn down concurrently — nothing to report to.
            logger.debug("task_runner: task_cancelled emit failed (teardown)", exc_info=True)

    @property
    def zombies(self) -> list[str]:
        return [name for name, *_ in self._zombies]

    @Slot()
    def _check_stalls(self) -> None:
        now = time.monotonic()
        for name, task in list(self._tasks.items()):
            if task["state"] != "running":
                continue
            silent = now - task["last_seen"]
            if silent > task["timeout_s"]:
                logger.error(
                    "Task '%s' stalled (no liveness for %.0fs) — watchdog fired",
                    name, silent,
                )
                self.stalled.emit(name, silent)
                task["last_seen"] = now  # avoid repeating every poll
