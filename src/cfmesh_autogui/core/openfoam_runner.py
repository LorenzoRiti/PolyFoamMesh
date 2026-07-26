"""OpenFOAM subprocess runner with retry, error classification, and quality reporting.

This module provides:
  - MeshWorker: interruptible subprocess runner for cartesianMesh via WSL2
  - RetryRunner: orchestrates cartesianMesh with up to 3 automatic retries
  - CheckMeshWorker / PolyDualWorker: quality-check and conversion workers
  - QualityFixWorker: auto-fix loop (mesh -> checkMesh -> fix -> re-mesh)
  - analyze_error / parse_checkmesh_output: error and quality parsers
"""

from __future__ import annotations

import re
import os
import signal
import subprocess
import threading
import time
import enum
import logging
logger = logging.getLogger(__name__)
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from cfmesh_autogui.commercial.parallel_mesh import ParallelMeshEngine

# Monotonic clock for elapsed-time measurement (immune to wall-clock jumps).
_now = time.monotonic

__all__ = [
    "ErrorType", "ErrorInfo", "analyze_error",
    "MeshWorker", "RetryRunner",
    "MeshQualityReport", "parse_checkmesh_output",
    "CheckMeshWorker", "PolyDualWorker", "QualityFixWorker", "ParallelMeshWorker",
    "DecomposeParWorker", "WslCheckWorker",
    "generate_fms",
]

try:
    from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt
except ImportError:
    raise ImportError(
        "PySide6 is required for cfmesh_autogui. "
        "Install with: pip install PySide6"
    )

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.octopoda_local import octo

OF_BASHRC = "/usr/lib/openfoam/openfoam2512/etc/bashrc"




# ------------------------------------------------------------------
# Error classification
# ------------------------------------------------------------------
class ErrorType(enum.Enum):
    NONE = "none"
    NON_WATERTIGHT = "non_watertight"
    SURFACE_READ = "surface_read"
    PATCH_NOT_FOUND = "patch_not_found"
    FOAM_FATAL = "foam_fatal"
    CRASH = "crash"
    NON_MAPPABLE = "non_mappable"
    BOUNDARY_NOT_FOUND = "boundary_not_found"


def _extract_detail(full_output: str, max_lines: int = 10) -> str:
    """Extract the last error-relevant lines from the output for detail context."""
    lines = [l.strip() for l in full_output.splitlines() if l.strip()]
    error_lines = [l for l in lines if any(kw in l.lower() for kw in ["error", "fatal", "fail", "exception", "crash", "segmentation", "abort"])]
    if error_lines:
        return "\n".join(error_lines[-max_lines:])
    # ✅ F-001: return multi-line string instead of single line
    return "\n".join(lines[-max_lines:]) if lines else ""


@dataclass
class ErrorInfo:
    error_type: ErrorType = ErrorType.NONE
    message: str = ""
    suggestion: str = ""
    detail: str = ""


def analyze_error(full_output: str) -> ErrorInfo:
    """Classifica l'errore di cartesianMesh usando pattern cfMesh specifici."""
    lo = full_output.lower()
    detail = _extract_detail(full_output)

    if "--> foam fatal error:" in lo or "foam fatal" in lo:
        return ErrorInfo(
            ErrorType.FOAM_FATAL,
            "OpenFOAM encountered a fatal error during meshing.",
            "Check the surface STL for issues. Try 'Auto-Suggest Cell Sizes' or repair the CAD geometry.",
            detail,
        )
    if "floating point exception" in lo or "segmentation fault" in lo:
        return ErrorInfo(
            ErrorType.CRASH,
            "cartesianMesh crashed (FPE or segfault).",
            "Likely cause: degenerate geometry or extreme cell size ratio. Reduce max/min cell size ratio.",
            detail,
        )
    if "non-mappable" in lo:
        return ErrorInfo(
            ErrorType.NON_MAPPABLE,
            "Some surface patches could not be mapped to mesh boundaries.",
            "This may produce a partial mesh. Check the log for which patches failed.",
            detail,
        )
    if "non-watertight" in lo or "self-intersect" in lo:
        return ErrorInfo(
            ErrorType.NON_WATERTIGHT,
            "Surface is not watertight or self-intersecting.",
            "Try repairing the CAD geometry (e.g. fill holes and merge close vertices).",
            detail,
        )
    if (
        "boundary file not found" in lo
        or "boundary region cannot be found" in lo
        or "unable to find boundary" in lo
    ):
        return ErrorInfo(
            ErrorType.BOUNDARY_NOT_FOUND,
            "OpenFOAM boundary file not found in constant/polyMesh/.",
            "The mesh was not created or is incomplete. Check the cartesianMesh log.",
            detail,
        )
    if ("cannot read" in lo or "cannot find file" in lo) and ("surface" in lo or ".stl" in lo):
        return ErrorInfo(
            ErrorType.SURFACE_READ,
            "Cannot read surface STL file.",
            "Check that the STL file exists and is not corrupted.",
            detail,
        )
    if "cannot find patch" in lo or "unknown patch" in lo:
        return ErrorInfo(
            ErrorType.PATCH_NOT_FOUND,
            "meshDict references a patch that does not exist in the STL.",
            "Regenerate meshDict with the correct patch names.",
            detail,
        )
    return ErrorInfo()


# ------------------------------------------------------------------
# MeshWorker — interruptible subprocess runner
# ------------------------------------------------------------------
class MeshWorker(QObject):
    """Runs cartesianMesh via WSL2 in a background QThread.

    Uses select.select() for non-blocking stdout reads so that
    thread interruption requests are checked even when the
    subprocess produces no output for a long time (B13 fix).
    """

    log_line = Signal(str)
    finished = Signal(int, str)
    cell_count_found = Signal(int)
    progress_update = Signal(int)

    # V1.1: scan stdout line for the cell count (F2)
    # cfMesh v2512 output looks like:
    #   21027 vertices
    #   53740 faces
    #   16400 cells            <- number FIRST, label SECOND
    # We also accept the label-first form (legacy / other formats).
    _CELLS_RE = re.compile(
        r"(?:\b(\d[\d,]*)\s+cells\b)|(?:\bcells\s+(\d[\d,]*))",
        re.IGNORECASE,
    )
    _PROGRESS_PCT_RE = re.compile(
        r"(?:[Pp]rogress|Done)\s*[:=]?\s*(\d{1,3})\s*%",
    )
    # V1.1: subprocess PID for fallback kill (S7)
    _PID_FILE = "process.pid"

    # Maximum wall-clock time for a single meshing run (hours:min:sec)
    # 4 hours supports ~25M cells at ~1800 cells/sec sustained throughput.
    MAX_RUNTIME_SECONDS = 14400  # 4 hours

    def __init__(self, case_dir: Path | str, of_config: OFConfig, parent=None):
        super().__init__(parent)
        self._case_dir = Path(case_dir).resolve()
        self._of_config = of_config
        self._pid: int | None = None
        self._cell_count_emitted: bool = False
        self._start_time: float = 0.0

    def _process_line(self, line: str, full_output: list[str]):
        """Append, forward to log panel, and detect the cell count + progress."""
        clean = line.rstrip("\n\r")
        full_output.append(clean)
        self.log_line.emit(clean)

        # Progress percentage from explicit OpenFOAM output
        m = self._PROGRESS_PCT_RE.search(clean)
        if m:
            try:
                pct = int(m.group(1))
                self.progress_update.emit(min(pct, 100))
            except (ValueError, AttributeError):
                pass

        # Estimate progress from line count (diminishing granularity after ~200 lines)
        if not m:
            n_lines = len(full_output)
            if n_lines < 20:
                pct = n_lines * 5
            elif n_lines < 100:
                pct = 20 + (n_lines - 20) * 0.5
            elif n_lines < 500:
                pct = 60 + (n_lines - 100) * 0.1
            else:
                pct = 95
            self.progress_update.emit(min(int(pct), 99))

        # Cell count detection (unchanged)
        if not self._cell_count_emitted:
            m = self._CELLS_RE.search(clean)
            if m:
                raw = m.group(1) or m.group(2)
                try:
                    n = int(raw.replace(",", ""))
                    self.cell_count_found.emit(n)
                    self._cell_count_emitted = True
                except (ValueError, AttributeError):
                    logger.debug("Could not parse cell count from line: %s", clean)

    @Slot()
    def run(self):
        valid, msg = OFConfig.validate_case_path(self._case_dir)
        if not valid:
            self.log_line.emit(f"ERROR: {msg}")
            self.finished.emit(1, msg)
            return

        cmd = self._of_config.build_command(self._case_dir)
        self.log_line.emit(f"[cmd] {' '.join(cmd)}")

        process = None
        full_output: list[str] = []
        interrupted = False
        self._pid = None
        _stderr_thread: threading.Thread | None = None
        _err_lines: list[str] = []

        self._start_time = _now()

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=4096,
            )
            self._pid = process.pid
            self.progress_update.emit(0)

            def _drain_stderr():
                if process and process.stderr:
                    for line in process.stderr:
                        _err_lines.append(line)

            _stderr_thread = threading.Thread(
                target=_drain_stderr, daemon=True
            )
            _stderr_thread.start()
        except FileNotFoundError:
            self.log_line.emit("ERROR: WSL not found. Is WSL2 installed?")
            self.finished.emit(1, "WSL not found")
            return

        try:
            if process.stdout:
                # V1.1: threading-based reader (Windows: select only works on sockets, not pipes)
                _stop_reader = threading.Event()
                _reader_done = threading.Event()

                def _reader():
                    try:
                        for line in process.stdout:
                            if _stop_reader.is_set():
                                break
                            self._process_line(line, full_output)
                            if len(full_output) % 3 == 0:
                                self.progress_update.emit(-1)
                    except Exception:
                        pass
                    finally:
                        _reader_done.set()

                _stdout_thread = threading.Thread(target=_reader, daemon=True)
                _stdout_thread.start()

                # Check interruption every 0.5s while reader is alive
                while not _reader_done.wait(timeout=0.5):
                    if QThread.currentThread().isInterruptionRequested():
                        self.log_line.emit(
                            "[cancelled] Interruption requested, killing process..."
                        )
                        _stop_reader.set()
                        interrupted = True
                        break
                    if _now() - self._start_time > self.MAX_RUNTIME_SECONDS:
                        self.log_line.emit(
                            f"[timeout] Meshing exceeded {self.MAX_RUNTIME_SECONDS}s "
                            "— killing process."
                        )
                        _stop_reader.set()
                        interrupted = True
                        break

                # Collect any remaining lines after reader finishes
                if not _stop_reader.is_set():
                    _stdout_thread.join(timeout=2)
                    if not _reader_done.is_set():
                        try:
                            for line in process.stdout:
                                if _stop_reader.is_set():
                                    break
                                self._process_line(line, full_output)
                        except Exception:
                            pass
        finally:
            if _stderr_thread is not None and _stderr_thread.is_alive():
                _stderr_thread.join(timeout=2)
            for line in _err_lines:
                full_output.append(f"[stderr] {line.rstrip()}")
            if process.poll() is None:
                process.kill()
                # V1.1: fallback kill via os.kill (S7 fix)
                if self._pid is not None:
                    try:
                        self._kill_process_tree(self._pid, self._of_config.wsl_distro)
                    except Exception as exc:
                        logger.debug("Fallback kill failed: %s", exc)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("Subprocess did not terminate within 5s after kill")

        returncode = process.returncode if process.returncode is not None else -1
        if interrupted:
            full_output.append("[cancelled] Process was interrupted by user request.")

        combined = "\n".join(full_output)

        log_path = self._case_dir / "log.meshing"
        try:
            log_path.write_text(combined + "\n", encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("Could not write log.meshing to %s", log_path)

        self.progress_update.emit(100)
        self.finished.emit(returncode, combined)

    def _kill_process_tree(self, pid: int, wsl_distro: str | None = None):
        """Kill process and its children recursively.

        Uses ``taskkill /T`` on Windows, ``os.killpg`` on POSIX.
        Falls back to ``wsl --terminate`` when the subprocess may be WSL.

        Args:
            pid: Process ID to terminate.
            wsl_distro: WSL distro name for fallback kill (e.g. ``"Ubuntu"``).
        """
        if os.name == "nt":
            with suppress(OSError, subprocess.TimeoutExpired):
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5,
                )
            if wsl_distro:
                with suppress(OSError, subprocess.TimeoutExpired):
                    subprocess.run(
                        ["wsl.exe", "--terminate", wsl_distro],
                        capture_output=True,
                        timeout=10,
                    )
        else:
            with suppress(OSError, ProcessLookupError):
                os.killpg(os.getpgid(pid), signal.SIGKILL)


# ------------------------------------------------------------------
# RetryRunner — orchestrates retries with BL fallback and exit-15 recovery
# ------------------------------------------------------------------
class RetryRunner(QObject):
    """Orchestrates cartesianMesh with up to 3 automatic retries on known errors.

    V1.1 additions:
      - F2: cell_count_relay for real cell count.
      - F6: automatic fallback without boundary layers if BL causes failure.
      - F7: exit-code 15 treated as non-fatal for unconnected regions.
    """

    MAX_RETRIES = 3
    cell_count_found = Signal(int)
    log_emitted = Signal(str)
    cell_count_relay = Signal(int)
    progress_update = Signal(int)

    _UNCONNECTED_RE = re.compile(r"unconnected|non-mappable", re.IGNORECASE)

    def __init__(self, of_config: OFConfig, parent=None):
        super().__init__(parent)
        self._of_config = of_config
        self._attempt = 0
        self._case_dir: Path = Path(".")
        self._thread: QThread | None = None
        self._worker: MeshWorker | None = None
        self._is_running = False
        self._fix_action: Callable[[ErrorInfo, int], bool] | None = None
        self._on_log: Callable[[str], None] | None = None
        self._on_finished: Callable[[int, str, int], None] | None = None
        self._bl_params: dict | None = None
        self._bl_retried: bool = False
        self._max_cell: float = 0.05
        self._min_cell: float = 0.01
        self._patch_cell_size: dict[str, float] | None = None
        self._patch_names: list[str] | None = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    def thread(self) -> QThread | None:
        return self._thread

    def request_stop(self):
        self._is_running = False
        if self._thread is not None:
            self._thread.requestInterruption()

    def run(
        self,
        case_dir: Path | str,
        on_log: Callable[[str], None] | None = None,
        on_finished: Callable[[int, str, int], None] | None = None,
        fix_action: Callable[[ErrorInfo, int], bool] | None = None,
        bl_params: dict | None = None,
        max_cell: float = 0.05,
        min_cell: float = 0.01,
        patch_cell_size: dict[str, float] | None = None,
        patch_names: list[str] | None = None,
    ):
        self._attempt = 0
        self._case_dir = Path(case_dir).resolve()
        self._on_log = on_log
        self._on_finished = on_finished
        self._fix_action = fix_action
        self._bl_params = bl_params
        self._bl_retried = False
        self._max_cell = max_cell
        self._min_cell = min_cell
        self._patch_cell_size = patch_cell_size
        self._patch_names = patch_names
        self._do_attempt()

    # ------------------------------------------------------------------
    # internal — B2 fix: no broad sig.disconnect() on relay signals
    # ------------------------------------------------------------------
    def _cleanup_previous(self):
        if self._worker is not None:
            try:
                self._worker.log_line.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                self._worker.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                self._worker.cell_count_found.disconnect()
            except (RuntimeError, TypeError):
                pass
            # V1.1: DO NOT delete worker before thread stops (B14 fix)
            self._worker = None
        if self._thread is not None:
            if self._thread.isRunning():
                self._thread.quit()
                if not self._thread.wait(30000):
                    logger.warning("QThread did not quit within 30s — forcing terminate")
                    self._thread.terminate()
                    self._thread.wait(3000)
            # V1.1: delete thread AFTER it has fully stopped (B14 fix)
            self._thread.deleteLater()
            self._thread = None
        # V1.1: call deleteLater on old worker AFTER thread is done (B14 fix)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _do_attempt(self):
        self._attempt += 1
        if self._attempt > 1 and self._on_log:
            self._on_log(f"[retry {self._attempt}/{self.MAX_RETRIES}]")
        self._cleanup_previous()
        self._is_running = True

        case_str = str(self._case_dir)
        octo.log_event("retry_runner", "attempt",
            f"attempt={self._attempt}/{self.MAX_RETRIES} case_dir={case_str}")

        self._thread = QThread()
        self._worker = MeshWorker(self._case_dir, self._of_config)
        self._worker.moveToThread(self._thread)

        # V1.1: relay connections — bounded to new worker, not disconnected externally (B2 fix)
        self._worker.log_line.connect(self.log_emitted, Qt.DirectConnection)
        self._worker.cell_count_found.connect(self.cell_count_relay, Qt.DirectConnection)
        if self._on_log:
            self._worker.log_line.connect(self._on_log, Qt.QueuedConnection)
        self._worker.progress_update.connect(self.progress_update, Qt.DirectConnection)
        self._worker.finished.connect(self._on_attempt_finished, Qt.QueuedConnection)
        self._worker.finished.connect(lambda *a: self._thread.quit(), Qt.QueuedConnection)
        self._worker.cell_count_found.connect(self.cell_count_found, Qt.QueuedConnection)
        self._thread.started.connect(self._worker.run)
        self._worker.destroyed.connect(self._thread.quit)
        self._thread.start()

    def _regenerate_meshdict_without_bl(self):
        """Regenerate meshDict without boundary layers.

        Was missing `patch_names=`, so `renameBoundary` never got emitted here
        — every inlet/outlet/wall patch silently reverted to cfMesh's default
        `wall` type on this fallback. Since this is the SAME RetryRunner the
        main "Generate Mesh" path uses (not just an unwired workflow), any real
        case whose boundary layers fail — common on complex or thin geometry —
        would silently lose correct patch typing on the automatic retry.
        """
        from cfmesh_autogui.core.meshdict_gen import write_meshdict
        write_meshdict(
            self._case_dir,
            max_cell_size=self._max_cell,
            min_cell_size=self._min_cell,
            patch_cell_size=self._patch_cell_size,
            patch_names=self._patch_names,
        )
        logger.info("Regenerated meshDict without boundary layers.")

    @Slot(int, str)
    def _on_attempt_finished(self, exit_code: int, output: str):
        self._is_running = False
        case_str = str(self._case_dir)

        octo.log_event("retry_runner", "attempt_finished",
            f"exit_code={exit_code} attempt={self._attempt}/{self.MAX_RETRIES} case_dir={case_str}")

        # V1.1: F7 — exit code 15 may be non-fatal for unconnected regions
        if exit_code == 15 and self._UNCONNECTED_RE.search(output):
            if self._on_log:
                self._on_log(
                    "[WARN] Exit code 15 with unconnected/non-mappable regions. "
                    "Checking for partial mesh..."
                )
            points_file = self._case_dir / "constant" / "polyMesh" / "points"
            if points_file.exists():
                logger.info(
                    "polyMesh/points found despite exit 15; treating as success (F7)."
                )
                if self._on_log:
                    self._on_log(
                        "[meshing] Exit 15 but polyMesh/points exists — "
                        "partial mesh generated, continuing."
                    )
                if self._on_finished:
                    self._on_finished(0, output, self._attempt)
                self._cleanup_previous()
                return
            else:
                if self._on_log:
                    self._on_log(
                        "[WARN] Exit 15 and no polyMesh/points found — "
                        "mesh generation incomplete."
                    )

        if exit_code == 0:
            if self._on_finished:
                self._on_finished(exit_code, output, self._attempt)
            self._cleanup_previous()
            return

        # V1.1: F6 — BL fallback: retry once without boundary layers
        if (
            self._bl_params is not None
            and self._bl_params.get("nLayers")
            and not self._bl_retried
            and exit_code != 0
        ):
            self._bl_retried = True
            if self._on_log:
                self._on_log(
                    "[WARN] Boundary layers failed. Retrying without BL..."
                )
                self._on_log("[fallback] Regenerating meshDict without boundaryLayers.")
            try:
                self._regenerate_meshdict_without_bl()
                logger.info(
                    "BL fallback: meshDict regenerated without BL, retrying (F6)."
                )
                octo.log_event("retry_runner", "bl_fallback",
                    f"attempt={self._attempt} case_dir={case_str}")
                self._do_attempt()
                return
            except Exception as exc:
                logger.error("BL fallback meshDict regeneration failed: %s", exc)
                if self._on_log:
                    self._on_log(f"[ERROR] BL fallback meshDict failed: {exc}")

        error = analyze_error(output)
        if error.error_type == ErrorType.NONE:
            if self._on_log:
                self._on_log(
                    "[error-detail] Nessun pattern specifico riconosciuto "
                    "— clicca 'Show Log' per il log completo."
                )
                self._on_log(
                    "[error-detail] Raw exit code: %d | Ultime 5 righe:\n  %s"
                    % (exit_code, "\n  ".join(output.strip().split("\n")[-5:]))
                )
            if self._on_finished:
                self._on_finished(exit_code, output, self._attempt)
            self._cleanup_previous()
            return
        if self._attempt < self.MAX_RETRIES and self._fix_action and self._fix_action(error, self._attempt):
            octo.log_event("retry_runner", "auto_fix_retry",
                f"error={error.error_type.value} attempt={self._attempt} case_dir={case_str}")
            self._do_attempt()
            return
        if self._on_log:
            self._on_log(f"[error] {error.message}")
            self._on_log(f"[suggestion] {error.suggestion}")
        if self._on_finished:
            self._on_finished(exit_code, output, self._attempt)
        self._cleanup_previous()

    def terminate(self):
        """Kill subprocess and cleanup. Call before starting a new run."""
        self._is_running = False
        if self._thread is not None and self._thread.isRunning():
            self._thread.requestInterruption()
            # V1.1: wait a bit for the select loop to pick up the interruption
            if not self._thread.wait(30000):
                self._thread.terminate()
                self._thread.wait(3000)
        self._cleanup_previous()



# ------------------------------------------------------------------
# V1.1: MeshQualityReport + checkMesh parsing
# ------------------------------------------------------------------
@dataclass
class MeshQualityReport:
    """Structured output from checkMesh."""
    cells: int = 0
    faces: int = 0
    points: int = 0
    max_non_ortho: float = 0.0
    avg_non_ortho: float = 0.0
    max_skewness: float = 0.0
    avg_skewness: float = 0.0
    max_aspect_ratio: float = 0.0
    neg_cells: int = 0
    min_volume: float = 0.0
    has_fatal: bool = False
    raw_output: str = ""

    @property
    def passed(self) -> bool:
        if self.has_fatal or self.neg_cells > 0:
            return False
        return True

    @property
    def status(self) -> str:
        if self.has_fatal:
            return "❌ checkMesh FATAL"
        if self.neg_cells > 0:
            return f"❌ {self.neg_cells} negative-volume cells"
        warns = []
        if self.max_non_ortho > 65:
            warns.append(f"non-ortho={self.max_non_ortho:.1f}")
        if self.max_skewness > 4:
            warns.append(f"skewness={self.max_skewness:.2f}")
        if self.max_aspect_ratio > 1000:
            warns.append(f"aspect={self.max_aspect_ratio:.0f}")
        if warns:
            return f"⚠️ {', '.join(warns)}"
        return "✅ PASS"

    def to_dict(self) -> dict:
        return {
            "metrics": {
                "cells": self.cells,
                "faces": self.faces,
                "points": self.points,
                "max_non_ortho": self.max_non_ortho,
                "avg_non_ortho": self.avg_non_ortho,
                "max_skewness": self.max_skewness,
                "avg_skewness": self.avg_skewness,
                "max_aspect_ratio": self.max_aspect_ratio,
                "neg_cells": self.neg_cells,
                "min_volume": self.min_volume,
            },
            "passed": self.passed,
            "status": self.status,
            "raw_output": self.raw_output,
        }


# Regexes for checkMesh output
_CHECK_CELLS_RE = re.compile(r"cells:\s+(\d+)", re.IGNORECASE)
_CHECK_FACES_RE = re.compile(r"faces:\s+(\d+)", re.IGNORECASE)
_CHECK_POINTS_RE = re.compile(r"points:\s+(\d+)", re.IGNORECASE)
# A float that does NOT swallow a trailing sentence period. checkMesh writes
# "Min volume = 6.30328e-06. Max volume = ..." and a `[\d.eE+-]+` class ate the
# final '.', so float() raised ValueError on real output.
_F = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"

# Real OpenFOAM v2512 phrasing differs from the older form these regexes were
# written against, and the metrics don't always carry an average:
#   "Mesh non-orthogonality Max: 61.5453 average: 15.0436"   (colon form)
#   "Max skewness = 0.657556 OK."                            (no average)
#   "Max aspect ratio = 39.5564 OK."                         (no average)
# Requiring "average = " with re.DOTALL meant these silently never matched, so
# quality was reported as 0/perfect regardless of the real mesh. Accept both
# spellings, make the average optional, and stay within one line ([^\n]) so a
# missing average can't drag the match onto an unrelated later line.
_CHECK_NONORTHO_RE = re.compile(
    rf"(?:Mesh non-orthogonality\s+Max:|Max non-orthogonality\s*=)\s*({_F})"
    rf"(?:[^\n]*?average\s*[:=]\s*({_F}))?",
    re.IGNORECASE,
)
_CHECK_SKEW_RE = re.compile(
    rf"Max skewness\s*[:=]\s*({_F})(?:[^\n]*?average\s*[:=]\s*({_F}))?",
    re.IGNORECASE,
)
_CHECK_ASPECT_RE = re.compile(
    rf"Max aspect ratio\s*[:=]\s*({_F})(?:[^\n]*?average\s*[:=]\s*({_F}))?",
    re.IGNORECASE,
)
_CHECK_NEGVOL_RE = re.compile(
    r"There are\s+(\d+)\s+cells[^\n]*?negative volume"
    r"|Writing\s+(\d+)\s+cells with negative volume",
    re.IGNORECASE,
)
_CHECK_MINVOL_RE = re.compile(rf"Min volume\s*=\s*({_F})", re.IGNORECASE)
_CHECK_MESH_OK_RE = re.compile(r"^\s*Mesh OK\.", re.IGNORECASE | re.MULTILINE)
_CHECK_FATAL_RE = re.compile(
    r"FOAM FATAL|FATAL ERROR|--> FOAM FATAL", re.IGNORECASE
)


def parse_checkmesh_output(text: str) -> MeshQualityReport:
    """Parse checkMesh stdout into a structured report."""
    r = MeshQualityReport(raw_output=text)
    r.has_fatal = bool(_CHECK_FATAL_RE.search(text))

    m = _CHECK_CELLS_RE.search(text)
    if m:
        r.cells = int(m.group(1))
    m = _CHECK_FACES_RE.search(text)
    if m:
        r.faces = int(m.group(1))
    m = _CHECK_POINTS_RE.search(text)
    if m:
        r.points = int(m.group(1))

    m = _CHECK_NONORTHO_RE.search(text)
    if m:
        r.max_non_ortho = float(m.group(1))
        if m.group(2) is not None:
            r.avg_non_ortho = float(m.group(2))

    m = _CHECK_SKEW_RE.search(text)
    if m:
        r.max_skewness = float(m.group(1))
        if m.group(2) is not None:
            r.avg_skewness = float(m.group(2))

    m = _CHECK_ASPECT_RE.search(text)
    if m:
        r.max_aspect_ratio = float(m.group(1))

    m = _CHECK_NEGVOL_RE.search(text)
    if m:
        r.neg_cells = int(m.group(1) or m.group(2))

    m = _CHECK_MINVOL_RE.search(text)
    if m:
        r.min_volume = float(m.group(1))

    return r


# ------------------------------------------------------------------
# FMS (Feature Mesh Surface) generation via surfaceFeatureEdges
# ------------------------------------------------------------------

def generate_fms(
    case_dir: Path | str,
    angle: float = 60.0,
    timeout: int = 60,
) -> Path | None:
    """Run surfaceFeatureEdges to produce an .fms file from the surface STL.

    Wraps the WSL2 call to ``surfaceFeatureEdges -angle <angle>``,
    run from within the case directory so that output paths are relative
    and OpenFOAM can find its config files.

    The FMS file is written to ``constant/triSurface/surface.fms``.

    Args:
        case_dir: OpenFOAM case directory (must contain
            ``constant/triSurface/surface.stl``).
        angle: Feature angle threshold in degrees (default 30).
        timeout: Max wall-clock seconds for the WSL call.

    Returns:
        Path to the generated ``.fms`` file, or ``None`` on failure.
    """
    case_dir = Path(case_dir).resolve()
    stl_path = case_dir / "constant" / "triSurface" / "surface.stl"
    fms_path = case_dir / "constant" / "triSurface" / "surface.fms"
    if not stl_path.exists():
        logger.warning("generate_fms: %s not found", stl_path)
        return None

    linux = _to_wsl_path_wsl(case_dir)
    in_stl = "constant/triSurface/surface.stl"
    out_fms = "constant/triSurface/surface.fms"
    cmd = (
        f"cd {linux} && "
        f"surfaceFeatureEdges -angle {angle} {in_stl} {out_fms} 2>&1"
    )
    try:
        r = subprocess.run(
            ["wsl.exe", "-e", "bash", "-lc", f". {OF_BASHRC} && {cmd}"],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode == 0 and fms_path.exists():
            # Check if the FMS contains actual feature vertices.
            # An FMS with 0 feature vertices (only the patch header) does not
            # help and may degrade quality on curved surfaces (external_aero).
            try:
                fms_text = fms_path.read_text(encoding="ascii", errors="replace")
                # Format: first non-header line is patch count, second is
                # vertex count. If vertex count >= 3 there are real features.
                lines = [l.strip() for l in fms_text.splitlines() if l.strip()]
                if len(lines) >= 2:
                    try:
                        n_verts = int(lines[1])
                        if n_verts < 3:
                            logger.info(
                                "generate_fms: %s has %d feature vertices "
                                "(< 3), skipping FMS",
                                fms_path, n_verts,
                            )
                            return None
                    except (ValueError, IndexError):
                        pass
            except OSError:
                pass
            logger.info("generate_fms: created %s (angle=%.1f°)", fms_path, angle)
            return fms_path
        logger.warning(
            "generate_fms: surfaceFeatureEdges rc=%d", r.returncode,
        )
        return None
    except subprocess.TimeoutExpired:
        logger.warning("generate_fms: timed out (%ds)", timeout)
        return None
    except FileNotFoundError:
        logger.warning("generate_fms: WSL not found")
        return None


def _to_wsl_path_wsl(win_path: Path) -> str:
    """Convert a Windows absolute path to a shell-quoted WSL2 /mnt/ path."""
    win = win_path.resolve()
    drive = win.drive[0].lower()
    rel = str(win).split(":", 1)[1].replace("\\", "/")
    raw = f"/mnt/{drive}{rel}"
    import shlex
    return shlex.quote(raw)


# ------------------------------------------------------------------
# V1.1: CheckMeshWorker — runs checkMesh in a background thread
# ------------------------------------------------------------------
class CheckMeshWorker(QObject):
    """Runs checkMesh via WSL2 in a QThread and returns a quality report."""

    log_line = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, case_dir: Path | str, of_config: OFConfig, parent=None):
        super().__init__(parent)
        self._case_dir = Path(case_dir).resolve()
        self._of_config = of_config

    @Slot()
    def run(self):
        cmd = self._of_config.build_check_mesh_cmd(self._case_dir)

        self.log_line.emit(f"[checkMesh] {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
            )
            full = result.stdout + "\n" + result.stderr
        except subprocess.TimeoutExpired:
            self.log_line.emit("[checkMesh] TIMEOUT (600s)")
            self.failed.emit("checkMesh timed out after 600s")
            return
        except FileNotFoundError:
            self.log_line.emit("[checkMesh] WSL not found")
            self.failed.emit("WSL not found")
            return
        except Exception as exc:
            self.log_line.emit(f"[checkMesh] ERROR: {exc}")
            self.failed.emit(str(exc))
            return

        report = parse_checkmesh_output(full)

        self.log_line.emit(
            f"[checkMesh] cells={report.cells} faces={report.faces} "
            f"nonOrtho={report.max_non_ortho:.1f} "
            f"skewness={report.max_skewness:.2f} "
            f"aspectRatio={report.max_aspect_ratio:.0f}"
        )
        if not report.passed:
            self.log_line.emit(f"[checkMesh] {report.status}")

        self.finished.emit(report)


# ------------------------------------------------------------------
# PolyDualWorker — polyhedral mesh conversion
# ------------------------------------------------------------------
class PolyDualWorker(QObject):
    """Runs polyDualMesh via WSL2 in a background QThread.

    polyDualMesh converts the hexahedral mesh from cartesianMesh into
    an arbitrary polyhedral mesh (reduces cell count while preserving
    surface quality). It writes output to the same case directory.
    """

    log_line = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, case_dir: Path | str, of_config: OFConfig, parent=None):
        super().__init__(parent)
        self._case_dir = Path(case_dir).resolve()
        self._of_config = of_config

    @Slot()
    def run(self):
        cmd = self._of_config.build_poly_dual_cmd(self._case_dir)

        self.log_line.emit(f"[polyDualMesh] {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.returncode == 0:
                self.log_line.emit("[polyDualMesh] Conversion OK")
                self.finished.emit(self._case_dir)
            else:
                self.log_line.emit(f"[polyDualMesh] Return code {result.returncode}")
                self.failed.emit(f"polyDualMesh returned {result.returncode}")
        except subprocess.TimeoutExpired:
            self.log_line.emit("[polyDualMesh] TIMEOUT (180s)")
            self.failed.emit("polyDualMesh timed out after 180s")
        except FileNotFoundError:
            self.log_line.emit("[polyDualMesh] WSL not found")
            self.failed.emit("WSL not found")
        except Exception as exc:
            self.log_line.emit(f"[polyDualMesh] ERROR: {exc}")
            self.failed.emit(str(exc))


class DecomposeParWorker(QObject):
    """Runs decomposePar -force in a background QThread to decompose an
    existing mesh into processorN/ directories for parallel SOLVING."""

    log_line = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, case_dir: Path | str, of_config: OFConfig,
                 n_cores: int, method: str = "scotch", parent=None):
        super().__init__(parent)
        self._case_dir = Path(case_dir).resolve()
        self._of_config = of_config
        self._n_cores = n_cores
        self._method = method

    @Slot()
    def run(self):
        try:
            cmd = self._of_config.build_decompose_par_cmd(
                self._case_dir, self._n_cores, method=self._method,
            )
            self.log_line.emit(f"[decomposePar] {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                self.log_line.emit("[decomposePar] Decomposition OK")
                self.finished.emit(self._case_dir)
            else:
                self.log_line.emit(f"[decomposePar] Return code {result.returncode}")
                self.failed.emit(f"decomposePar returned {result.returncode}")
        except subprocess.TimeoutExpired:
            self.log_line.emit("[decomposePar] TIMEOUT (120s)")
            self.failed.emit("decomposePar timed out")
        except FileNotFoundError:
            self.log_line.emit("[decomposePar] WSL not found")
            self.failed.emit("WSL not found")
        except Exception as exc:
            self.log_line.emit(f"[decomposePar] ERROR: {exc}")
            self.failed.emit(str(exc))


class ParallelMeshWorker(QObject):
    """Runs ParallelMeshEngine.run() (synchronous, MPI-based) in a
    background QThread so it doesn't freeze the GUI's own event loop.

    Supports cancellation via cancel(): sets an event that the engine's
    polling subprocess runner checks every 0.5s and kills the MPI ranks."""

    log_line = Signal(str)
    finished = Signal(object)  # ParallelMeshResult
    failed = Signal(str)
    cancelled = Signal()  # emitted when user cancels mid-run

    def __init__(
        self, case_dir: Path | str, of_config: OFConfig,
        max_cell: float, min_cell: float, n_cores: int,
        patch_names: list[str] | None = None,
        method: str = "scotch", parent=None,
        bl_params: dict | None = None,
    ):
        super().__init__(parent)
        self._case_dir = Path(case_dir).resolve()
        self._of_config = of_config
        self._max_cell = max_cell
        self._min_cell = min_cell
        self._n_cores = n_cores
        self._patch_names = patch_names
        self._method = method
        self._bl_params = bl_params
        self._engine: ParallelMeshEngine | None = None

    def cancel(self) -> None:
        """Request cancellation of the running parallel meshing.
        Safe to call from any thread; triggers a process-tree kill
        inside WSL2 on the next 0.5s poll cycle."""
        if self._engine is not None:
            self._engine.cancel()

    @Slot()
    def run(self):
        try:
            from cfmesh_autogui.commercial.parallel_mesh import (
                ParallelMeshEngine, CancelledError,
            )
            self.log_line.emit(
                f"[parallel] Meshing across {self._n_cores} cores..."
            )
            self._engine = ParallelMeshEngine(self._of_config)
            self._engine.setup_case(self._case_dir, n_cores=self._n_cores, method=self._method)
            self._engine.set_cell_sizes(self._max_cell, self._min_cell)
            self._engine.set_patch_names(self._patch_names)
            self._engine.set_bl_params(self._bl_params)
            result = self._engine.run()
            if result.success:
                self.log_line.emit(
                    f"[parallel] Done: {result.cell_count:,} cells "
                    f"({result.wall_time_seconds:.1f}s wall time, "
                    f"{self._n_cores} cores)"
                )
                self.finished.emit(result)
            else:
                msg = "; ".join(result.errors) or "unknown error"
                self.log_line.emit(f"[parallel] FAILED: {msg}")
                self.failed.emit(msg)
        except CancelledError:
            self.log_line.emit("[parallel] Cancelled by user.")
            self.cancelled.emit()
        except Exception as exc:
            self.log_line.emit(f"[parallel] ERROR: {exc}")
            self.failed.emit(str(exc))


class WslCheckWorker(QObject):
    """Runs OFConfig.validate() (blocking wsl.exe call) in a background
    QThread. WSL2 auto-shuts-down its VM after inactivity, so this can
    take well over a minute on a cold boot — running it on the GUI
    thread freezes the whole window with no feedback, indistinguishable
    from a crash."""

    finished = Signal(bool)

    def __init__(self, of_config: OFConfig, parent=None):
        super().__init__(parent)
        self._of_config = of_config

    @Slot()
    def run(self):
        try:
            ok = self._of_config.validate()
        except Exception:
            ok = False
        self.finished.emit(ok)


# ------------------------------------------------------------------
# V2.0: QualityFixWorker — checkMesh → auto-fix → re-run loop
# ------------------------------------------------------------------
class QualityFixWorker(QObject):
    """Runs meshing + checkMesh + auto-fix loop with max 3 iterations.

    Uses RetryRunner internally for the meshing step, then runs
    checkMesh. If quality checks fail, applies meshDict fixes and
    re-runs.
    """

    MAX_FIX_ATTEMPTS = 3

    log_line = Signal(str)
    finished = Signal(int)
    failed = Signal(str)

    def __init__(self, of_config: OFConfig, parent=None):
        super().__init__(parent)
        self._of_config = of_config
        self._fix_attempts = 0
        self._runner: RetryRunner | None = None
        self._checkmesh_worker: CheckMeshWorker | None = None
        self._checkmesh_thread: QThread | None = None

    @Slot()
    def run(self, case_dir: Path, **kwargs):
        self._case_dir = Path(case_dir).resolve()
        self._kwargs = kwargs
        self._fix_attempts = 0
        self._do_meshing_step()

    def _do_meshing_step(self):
        self.log_line.emit(f"[quality-fix] Meshing step (fix attempt {self._fix_attempts})...")
        self._runner = RetryRunner(self._of_config)

        def on_finished(exit_code, output, attempts):
            if exit_code != 0:
                self.log_line.emit(f"[quality-fix] Meshing failed (exit {exit_code})")
                self.failed.emit(f"Meshing failed: exit {exit_code}")
                return
            self._do_checkmesh_step()

        self._runner.run(
            case_dir=self._case_dir,
            on_log=lambda msg: self.log_line.emit(msg),
            on_finished=on_finished,
            **self._kwargs,
        )

    def _do_checkmesh_step(self):
        self.log_line.emit("[quality-fix] Running checkMesh...")
        self._checkmesh_thread = QThread()
        self._checkmesh_worker = CheckMeshWorker(self._case_dir, self._of_config)
        self._checkmesh_worker.moveToThread(self._checkmesh_thread)
        self._checkmesh_worker.log_line.connect(self.log_line)
        self._checkmesh_worker.finished.connect(self._on_checkmesh_result)
        self._checkmesh_worker.failed.connect(lambda msg: self.failed.emit(msg))
        self._checkmesh_worker.finished.connect(self._checkmesh_thread.quit)
        self._checkmesh_worker.failed.connect(self._checkmesh_thread.quit)
        self._checkmesh_thread.started.connect(self._checkmesh_worker.run)
        self._checkmesh_thread.start()

    def _on_checkmesh_result(self, report):
        if report.passed:
            self.log_line.emit("[quality-fix] checkMesh PASSED")
            self.finished.emit(0)
            return

        self._fix_attempts += 1
        if self._fix_attempts >= self.MAX_FIX_ATTEMPTS:
            self.log_line.emit(
                f"[quality-fix] Max fix attempts ({self.MAX_FIX_ATTEMPTS}) reached."
            )
            self.failed.emit(f"Quality not met after {self.MAX_FIX_ATTEMPTS} attempts")
            return

        self.log_line.emit(
            f"[quality-fix] checkMesh FAILED ({report.status}). "
            f"Applying fix #{self._fix_attempts}..."
        )

        # Auto-fix: use QualityEngine's targeted fix strategies
        try:
            from cfmesh_autogui.commercial.quality_engine import QualityEngine, QualityMetrics

            qe = QualityEngine()
            qm = QualityMetrics(
                max_skewness=report.max_skewness,
                max_non_orthogonality=report.max_non_ortho,
                max_aspect_ratio=report.max_aspect_ratio,
                neg_cells=report.neg_cells,
                cells=report.cells,
            )
            fixes = qe._decide_fixes(qm)

            if not fixes:
                self.log_line.emit("[quality-fix] No applicable fix strategy.")
                self.failed.emit("No fix strategy for current quality failure")
                return

            applied_text = self._case_dir / "system" / "meshDict"
            if not applied_text.exists():
                self.failed.emit("meshDict not found")
                return

            text = applied_text.read_text(encoding="ascii")
            for fix in fixes:
                if fix.action == "relax":
                    text = QualityEngine._relax_cell_sizes(text, factor=1.2)
                elif fix.action == "reduce_bl":
                    text = QualityEngine._reduce_boundary_layers(text)
                elif fix.action == "disable_bl":
                    text = QualityEngine._disable_boundary_layers(text)
                elif fix.action == "remesh":
                    text = QualityEngine._coarsen_mesh(text, factor=1.3)
                elif fix.action == "split":
                    text = QualityEngine._reduce_max_cell(text, factor=0.7)
                self.log_line.emit(
                    f"[quality-fix] {fix.action}: {fix.detail}"
                )

            applied_text.write_text(text, encoding="ascii")
            self.log_line.emit(f"[quality-fix] meshDict updated ({len(fixes)} fix(es))")
            self._do_meshing_step()
        except Exception as e:
            self.log_line.emit(f"[quality-fix] Fix failed: {e}")
            self.failed.emit(str(e))

    def terminate(self):
        if self._runner and self._runner.is_running:
            self._runner.terminate()
        if self._checkmesh_thread and self._checkmesh_thread.isRunning():
            self._checkmesh_thread.quit()
            self._checkmesh_thread.wait(3000)

