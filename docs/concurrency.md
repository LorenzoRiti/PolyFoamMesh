# Concurrency architecture — CFMesh-AutoGUI

This document is the authoritative description of how the application runs
long work off the UI thread. It supersedes all ad-hoc threading patterns that
existed before the anti-freeze hardening pass.

## The one pattern

Every long job (GMSH volume, gmshToFoam, cartesianMesh, poly dual conversion,
checkMesh, export, feature detection, watertight check, autopoly, SAMR, OODA
adaptive, PDF/BaramFlow exports, geometry load/tessellation) runs as:

    worker QObject on a dedicated QThread (explicit moveToThread),
    owned and wired by TaskManager (gui/task_runner.py)

Why this pattern:

- The codebase already had ~15 worker classes following exactly this shape
  (`GmshVolumeWorker`, `CheckMeshWorker`, `ExportWorker`, ...), so the
  migration was additive wiring, not a rewrite.
- Both alternatives failed historically in this app: `QProcess`
  (main_window.py:2521-2526) and `subprocess.run` inside a QThread started
  via `t.started.connect(w.run)` with plain-closure receivers
  (main_window.py:2424-2442) — PySide6 cannot always resolve a receiver
  thread for plain callables, so queued callbacks ran on the worker thread
  and crashed or froze the GUI.
- `threading.Thread + queue.Queue + QTimer` polling worked, but it is
  non-Qt and manual; it has been replaced by `FunctionWorker` tasks.

## TaskManager API

```python
tasks = TaskManager(parent)              # one per widget that owns jobs
tasks.stalled.connect(handler)           # watchdog: (name, silent_seconds)

ok = tasks.submit(
    "task_name",                         # re-entry guard: one running per name
    worker,                              # QObject with run() + signals
    on_finished=lambda name, result: ...,
    on_failed=lambda name, msg: ...,
    on_cancelled=lambda name, msg: ...,
    on_progress=lambda name, stage, pct: ...,
    on_log=lambda name, msg: ...,
    on_notify=lambda name, payload: ...,  # worker-specific events (cycle_done)
    cancel_hook=...,                       # kills an in-flight subprocess
    heartbeat_timeout_s=...,               # per-task watchdog budget
    run_args=(), run_kwargs={},            # passed to worker.run(...)
    signal_shapes={...},                   # arity hints for non-standard workers
)

tasks.cancel("task_name")   # cooperative token + hook; force-teardown after grace
tasks.cancel_all()
tasks.shutdown(timeout_ms=2500)  # the ONLY exit path for the app
```

### Callbacks

All callbacks run on the GUI thread: worker signals are bridged into a
GUI-thread relay through fixed-signature slots (the proven
`QObject → QObject` queued pattern), so callbacks may touch widgets freely.
Never connect worker signals to plain lambdas with `Qt.QueuedConnection` —
argument delivery to plain callables is unreliable across QApplication
instances.

### Worker signals

Standard: `progress(str, float)`, `log_line(str)`, `heartbeat()`,
`finished(object)`, `failed(str)`, `cancelled(str)`. Non-standard shapes
(`FeatureDetectWorker.finished(object, object)`,
`ParallelMeshWorker.cancelled()`, `ExportWorker.error_occurred(str)`,
`SolutionAdaptiveWorker.cycle_done(int, int)`) are bridged with
`signal_shapes` hints or the generic `notify` channel.

### Cancellation contract

- Every task gets a cooperative `CancellationToken` (`worker.is_cancelled()`).
- Workers that own a subprocess expose `cancel()` (kills the subprocess) or
  accept a `cancel_hook`; subprocess-based workers must use `Popen` +
  poll loops (never `subprocess.run`) so they can be interrupted.
- Cancel response is < 2 s: `cancel()` is non-blocking; a grace timer
  force-tears-down (terminate as last resort) if the worker ignores the token.
- WSL subprocess kills run on a daemon thread — `wsl.exe` probes can block
  for 10 s and must never run on the GUI thread.

### Watchdog

`TaskManager` tracks liveness per task (any signal counts). If a task stays
silent for `heartbeat_timeout_s` (default 30 s, per-task overrides up to
15 min for long-running meshers), `stalled(name, seconds)` fires; the main
window cancels the task and restores the UI instead of freezing.

### Exit

`closeEvent` asks for confirmation when jobs are running, then calls
`tasks.shutdown(2500)`: cancel all → quit threads → terminate stragglers —
a bounded, hang-free exit (< 3 s).

## What runs where today

| Area | Mechanism |
|------|-----------|
| GMSH volume, gmshToFoam, checkMesh, poly dual, decompose, export, feature detect, watertight, autopoly, SAMR, OODA | TaskManager tasks |
| cartesianMesh (RetryRunner/MeshWorker) | Same worker-QObject-on-QThread pattern, internal lifecycle (not manager-owned); cancel via `request_stop()` + async WSL kill |
| Viewer foamToVTK + VTU reads/decimation, patch loads | Viewer-local TaskManager tasks |
| Geometry load / tessellate / heal, BaramFlow export, PDF export, startup cleanup, retry-fix tessellation | FunctionWorker tasks (fix path uses a nested event loop to preserve the synchronous bool contract) |
| ParaView launch | fire-and-forget Popen (no wait) |

## Rules for new code

1. Long work goes through `TaskManager.submit()` — no raw `QThread`,
   `threading.Thread`, `QProcess`, or `processEvents()`.
2. No `subprocess.run` on the UI thread; use `Popen` + poll loop inside a
   worker with a `cancel_hook`.
3. Every worker checks `is_cancelled()` in its loops and emits progress or
   heartbeat regularly.
4. No `time.sleep` / `waitForFinished` / busy polling on the UI thread.
5. Connect worker signals to TaskManager callbacks (GUI thread) — never
   touch widgets from worker code.
