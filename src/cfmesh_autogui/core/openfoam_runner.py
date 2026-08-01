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
import shlex
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

OF_BASHRC = OFConfig().env_script




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
    BL_FAILURE = "boundary_layer_failure"


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
    """Classify cartesianMesh errors using cfMesh-specific patterns."""
    lo = full_output.lower()
    detail = _extract_detail(full_output)

    bl_keywords = ["boundary layer", "prism layer", "near-wall layer"]
    if any(kw in lo for kw in bl_keywords) and (
            "failed" in lo or "collaps" in lo or "uncovered" in lo or "degenerate" in lo
    ):
        return ErrorInfo(
            ErrorType.BL_FAILURE,
            "Boundary layer generation failed (prism layers collapsed or degenerate).",
            "Reducing or disabling boundary layers and retrying.",
            detail,
        )
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
        except (OSError, PermissionError) as exc:
            self.log_line.emit(f"ERROR: Failed to launch meshing process: {exc}")
            self.finished.emit(1, f"Process launch failed: {exc}")
            return
        except Exception as exc:
            self.log_line.emit(f"ERROR: Unexpected meshing launch error: {exc}")
            self.finished.emit(1, f"Unexpected error: {exc}")
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
        # OpenFOAM can finish with return code 0 while reporting failed
        # topology/geometry checks.  Do not show a misleading PASS banner.
        if re.search(r"Failed\s+\d+\s+mesh\s+checks?", self.raw_output, re.IGNORECASE):
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
    from cfmesh_autogui.config import OFConfig

    cfg = OFConfig()
    case_dir = Path(case_dir).resolve()
    stl_path = case_dir / "constant" / "triSurface" / "surface.stl"
    fms_path = case_dir / "constant" / "triSurface" / "surface.fms"
    if not stl_path.exists():
        logger.warning("generate_fms: %s not found", stl_path)
        return None

    linux = cfg._quoted_linux_path(case_dir)
    in_stl = "constant/triSurface/surface.stl"
    out_fms = "constant/triSurface/surface.fms"
    cmd = (
        f"source {shlex.quote(cfg.env_script)} 2>/dev/null; "
        f"cd {linux} && "
        f"surfaceFeatureEdges -angle {angle} {in_stl} {out_fms} 2>&1"
    )
    try:
        r = subprocess.run(
            cfg._build_wsl_cmd(cmd),
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
        self._proc_holder: dict = {}
        self._cancelled = False

    @Slot()
    def cancel(self):
        """Kill the live subprocess (wsl.exe), if any is running right
        now. See GmshSurfaceWorker.cancel for why this exists — same gap
        here even though _on_cancel_meshing's _kill_wsl_processes() gives
        this one partial coverage already, killing wsl.exe directly by
        PID is faster and doesn't depend on process-name matching."""
        self._cancelled = True
        proc = self._proc_holder.get("proc")
        if proc is not None and proc.poll() is None:
            _kill_pid_tree(proc.pid)

    @Slot()
    def run(self):
        cmd = self._of_config.build_check_mesh_cmd(self._case_dir)

        self.log_line.emit(f"[checkMesh] {' '.join(cmd)}")

        try:
            returncode, stdout_lines, stderr_lines, timed_out = _stream_subprocess(
                cmd, None, 600,
                on_line=lambda ln: self.log_line.emit(f"[checkMesh] {ln}"),
                proc_holder=self._proc_holder,
            )
            full = "\n".join(stdout_lines) + "\n" + "\n".join(stderr_lines)
        except FileNotFoundError:
            self.log_line.emit("[checkMesh] WSL not found")
            self.failed.emit("WSL not found")
            return
        except Exception as exc:
            if not self._cancelled:
                self.log_line.emit(f"[checkMesh] ERROR: {exc}")
                self.failed.emit(str(exc))
            return

        if self._cancelled:
            self.log_line.emit("[checkMesh] Cancelled.")
            return

        if timed_out:
            self.log_line.emit("[checkMesh] TIMEOUT (600s)")
            self.failed.emit("checkMesh timed out after 600s")
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

    def __init__(self, case_dir: Path | str, of_config: OFConfig,
                 feature_angle: float = 90, parent=None):
        super().__init__(parent)
        self._case_dir = Path(case_dir).resolve()
        self._of_config = of_config
        self._feature_angle = feature_angle

    @Slot()
    def run(self):
        cmd = self._of_config.build_poly_dual_cmd(
            self._case_dir, feature_angle=self._feature_angle,
        )

        self.log_line.emit(f"[polyDualMesh] {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
            # Always log stdout/stderr so we can diagnose silent failures
            stdout_text = (result.stdout or "").strip()
            stderr_text = (result.stderr or "").strip()
            if stdout_text:
                for line in stdout_text.splitlines()[-20:]:
                    self.log_line.emit(f"[polyDualMesh] {line}")
            if stderr_text:
                for line in stderr_text.splitlines()[-10:]:
                    self.log_line.emit(f"[polyDualMesh:err] {line}")
            if result.returncode == 0:
                # Verify the mesh actually changed by checking owner file
                owner_path = self._case_dir / "constant" / "polyMesh" / "owner"
                if owner_path.exists():
                    logger.info(
                        "polyDualMesh OK (rc=0), owner file exists: %s",
                        owner_path,
                    )
                else:
                    logger.warning(
                        "polyDualMesh returned rc=0 but owner file NOT found at %s",
                        owner_path,
                    )
                self.log_line.emit(
                    f"[polyDualMesh] Conversion OK (featureAngle={self._feature_angle})"
                )
                self.finished.emit(self._case_dir)
            else:
                logger.error(
                    "polyDualMesh FAILED (rc=%d): %s",
                    result.returncode,
                    stdout_text[-500:] if stdout_text else "(no stdout)",
                )
                self.log_line.emit(f"[polyDualMesh] Return code {result.returncode}")
                self.failed.emit(f"polyDualMesh returned {result.returncode}")
        except subprocess.TimeoutExpired:
            self.log_line.emit("[polyDualMesh] TIMEOUT (300s)")
            self.failed.emit("polyDualMesh timed out after 300s")
        except FileNotFoundError:
            self.log_line.emit("[polyDualMesh] WSL not found")
            self.failed.emit("WSL not found")
        except Exception as exc:
            self.log_line.emit(f"[polyDualMesh] ERROR: {exc}")
            self.failed.emit(str(exc))


class TerminalFaceWorker(QObject):
    """Runs TerminalFaceConverter (tet -> polyhedral, Salinas et al. 2023)
    in a background QThread — this pipeline's poly path for GMSH-direct
    tetrahedral meshes, replacing polyDualMesh there. polyDualMesh has a
    confirmed structural defect on complex real geometry (600+
    incorrectly-oriented faces reproduced across every tet-generation
    strategy tried against the real Parte4 valve — see the poly_dual_risk
    comment in gmsh_wrapper.py's _configure_adaptive_sizing), which is
    why it's skipped for gmsh_direct/gmsh_direct_poly upstream of this.

    Pure Python + numpy/scipy — unlike GMSH surface/volume generation,
    it doesn't touch cadquery/OCP's OpenCASCADE, so it runs directly in
    a QThread rather than needing subprocess isolation.
    """

    log_line = Signal(str)
    finished = Signal(object)  # TerminalFaceResult
    failed = Signal(str)

    def __init__(self, case_dir: Path | str, parent=None):
        super().__init__(parent)
        self._case_dir = Path(case_dir).resolve()

    @Slot()
    def run(self):
        from cfmesh_autogui.core.terminal_face import TerminalFaceConverter

        self.log_line.emit("[poly] Converting tet -> polyhedral mesh (terminal-face)...")
        try:
            converter = TerminalFaceConverter(self._case_dir)
            result = converter.run()
        except Exception as exc:
            self.log_line.emit(f"[poly] ERROR: {exc}")
            self.failed.emit(str(exc))
            return

        if not result.success:
            msg = "; ".join(result.errors) or "Unknown terminal-face conversion error"
            self.log_line.emit(f"[poly] FAILED: {msg}")
            self.failed.emit(msg)
            return

        self.log_line.emit(
            f"[poly] Conversion OK: {result.n_tets_before} tets -> "
            f"{result.n_polyhedra} polyhedra ({result.wall_time_s:.1f}s)"
        )
        self.finished.emit(result)


class DualPolyWorker(QObject):
    """Runs TetPolyDualConverter (tet -> polyhedral by barycentric dual) in a
    background QThread.

    Unlike TerminalFaceWorker above, this does not merge tetrahedra: it builds
    the dual complex of the tet mesh (one cell per primal vertex), so poly
    coverage is 100% by construction and the boundary is an exact subdivision
    of the original surface triangles. See docs/poly_dual_converter.md for the
    measured comparison against the terminal-face path on identical inputs.

    Pure Python + numpy, so — like TerminalFaceWorker — it runs directly in a
    QThread rather than needing subprocess isolation.
    """

    log_line = Signal(str)
    finished = Signal(object)  # DualPolyResult
    failed = Signal(str)

    def __init__(self, case_dir: Path | str, parent=None):
        super().__init__(parent)
        self._case_dir = Path(case_dir).resolve()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self):
        from cfmesh_autogui.core.tet_poly_dual import TetPolyDualConverter

        self.log_line.emit(
            "[poly] Converting tet -> polyhedral mesh (barycentric dual)..."
        )
        # Back the pristine tet mesh up BEFORE converting, so a later
        # checkMesh failure on the poly mesh can be rolled back to the tet
        # (the quality-gate policy is a product decision, but the rollback
        # must always be possible — the converter overwrites polyMesh).
        try:
            import shutil

            poly_dir = self._case_dir / "constant" / "polyMesh"
            backup = self._case_dir / "constant" / "polyMesh_tet_backup"
            if poly_dir.exists() and not backup.exists():
                shutil.copytree(poly_dir, backup)
                self.log_line.emit(
                    "[poly] Tet mesh backed up to constant/polyMesh_tet_backup "
                    "(rollback available)"
                )
        except Exception as exc:  # noqa: BLE001 - backup is best-effort
            self.log_line.emit(f"[poly] WARN: tet backup failed: {exc}")
        try:
            converter = TetPolyDualConverter(
                self._case_dir,
                log=self.log_line.emit,
                cancel=lambda: self._cancelled,
            )
            result = converter.run()
        except Exception as exc:
            self.log_line.emit(f"[poly] ERROR: {exc}")
            self.failed.emit(str(exc))
            return

        if not result.success:
            msg = "; ".join(result.errors) or "Unknown dual conversion error"
            self.log_line.emit(f"[poly] FAILED: {msg}")
            self.failed.emit(msg)
            return

        self.log_line.emit(
            f"[poly] Conversion OK: {result.n_tets_before:,} tets -> "
            f"{result.n_cells_after:,} polyhedra, residual tetrahedra "
            f"{result.n_residual_tets} ({result.stage_times.get('total', 0)}s)"
        )
        self.finished.emit(result)


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


def _gmsh_wrapper_script_cmd(
    args: list[str], frozen_flag: str, script_name: str = "gmsh_wrapper.py",
) -> tuple[list[str], str | None]:
    """Build a subprocess command that runs a core/ module's CLI directly
    by file path rather than via `python -m cfmesh_autogui.core.<module>`.

    `-m` first fully imports the parent package (cfmesh_autogui.core),
    whose __init__.py already imports several of these modules — so by
    the time Python's runpy machinery goes to execute one as __main__,
    it's already sitting in sys.modules under its real dotted name. That
    triggers Python's own "found in sys.modules ... prior to execution;
    this may result in unpredictable behaviour" RuntimeWarning and
    re-executes the whole module a second time in the same process —
    confirmed from a real run where that warning ended up as the ONLY
    stderr content on a non-zero exit, masking whatever the actual
    failure was. Running the file directly skips package init entirely.
    """
    import sys
    frozen = getattr(sys, "frozen", False)
    if frozen:
        return [sys.executable, frozen_flag] + args[1:], None
    script = str(Path(__file__).resolve().with_name(script_name))
    run_cwd = str(Path(__file__).resolve().parents[2])
    return [sys.executable, script] + args, run_cwd


def _kill_pid_tree(pid: int) -> None:
    """Kill a process and its children by PID. Windows-only helper for
    the GMSH-subprocess workers (WSL processes go through
    MeshWorker._kill_process_tree / _kill_wsl_processes instead — this
    is for the plain sys.executable child _stream_subprocess launches).
    """
    import subprocess
    from contextlib import suppress
    with suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=5,
        )


def _stream_subprocess(cmd, run_cwd, timeout_s, on_line, env=None, heartbeat_s=10, proc_holder=None):
    """Run cmd via Popen, streaming stdout/stderr line-by-line as they
    arrive instead of buffering everything until the process exits.

    subprocess.run(capture_output=True) blocks with zero output for the
    ENTIRE run — confirmed live: a user watching the log during a slow
    (not hung, just genuinely producing far more cells than expected for
    the geometry) GMSH run saw nothing for 49s and reasonably assumed a
    freeze. This streams stdout as it's produced and, if GMSH itself goes
    quiet for heartbeat_s (it commonly does mid-phase, e.g. "Computing
    interpolated mesh sizes..."), emits a synthetic "still running" line
    through the same callback so silence never looks like a hang.

    proc_holder: optional mutable dict the caller can inspect from another
    thread while this call is still blocking — populated with the live
    Popen object as {"proc": proc} immediately after launch. Without this,
    nothing outside this function can ever reach the subprocess: Cancel in
    the GUI only tore down the QThread that called this (and only after a
    3s wait, since the thread is blocked in this loop and never processes
    the quit() event) — the actual GMSH child process, spawned here via
    Popen, was never touched and kept running orphaned. Confirmed live:
    Cancel visually reset the UI but the process stayed alive, and
    starting a new run then meant two GMSH processes running at once.

    Returns (returncode, stdout_lines, stderr_lines, timed_out).
    """
    import os
    import subprocess
    import threading
    import time

    # Decoding our end as UTF-8 isn't enough on its own: the child is a
    # separate Python process (launched fresh via sys.executable), and on
    # Windows it defaults to encoding ITS OWN stdout/stderr as cp1252 —
    # confirmed live, a plain em dash in a logger.warning() call came
    # through as a single mangled byte no matter how this end decoded it,
    # because it was already the wrong bytes by the time we read them.
    # PYTHONIOENCODING forces the child's own text streams to UTF-8
    # regardless of the platform's console codepage.
    child_env = dict(env) if env is not None else dict(os.environ)
    child_env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="backslashreplace",
        cwd=run_cwd, env=child_env, bufsize=1,
    )
    if proc_holder is not None:
        proc_holder["proc"] = proc

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    last_activity = [time.monotonic()]

    def _pump(stream, sink, prefix):
        try:
            for line in iter(stream.readline, ""):
                line = line.rstrip("\n")
                sink.append(line)
                last_activity[0] = time.monotonic()
                if line.strip():
                    on_line(prefix + line if prefix else line)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    # Both streams echoed live, not just stdout: Python's logging module
    # sends WARNING+ to stderr by default (no handler configured in the
    # gmsh_wrapper.py subprocess), so a logger.warning() call — e.g. the
    # domain-scale sizing clamp's own warning — landed in stderr_lines
    # and was silently swallowed until/unless the run failed, never
    # reaching the live GUI log on an otherwise-successful run. The
    # opposite of what a "show everything, always" log is supposed to do.
    t_out = threading.Thread(target=_pump, args=(proc.stdout, stdout_lines, ""), daemon=True)
    t_err = threading.Thread(target=_pump, args=(proc.stderr, stderr_lines, "[stderr] "), daemon=True)
    t_out.start()
    t_err.start()

    start = time.monotonic()
    timed_out = False
    while True:
        ret = proc.poll()
        if ret is not None:
            break
        now = time.monotonic()
        if now - start > timeout_s:
            timed_out = True
            proc.kill()
            break
        if now - last_activity[0] > heartbeat_s:
            on_line(f"... still running ({int(now - start)}s elapsed, "
                     f"no output for {int(now - last_activity[0])}s)")
            last_activity[0] = now
        time.sleep(0.5)

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    t_out.join(timeout=2)
    t_err.join(timeout=2)

    return proc.returncode, stdout_lines, stderr_lines, timed_out


class GmshSurfaceWorker(QObject):
    """Runs GMSH surface STL generation + sizing in a subprocess.

    GMSH's own bundled OpenCASCADE conflicts with cadquery/OCP's OpenCASCADE
    when both are loaded in the same process, causing hard native crashes.
    Running GMSH in a separate subprocess isolates this completely.
    """

    finished = Signal(object)  # dict with sizing results
    log_line = Signal(str)
    failed = Signal(str)

    SURFACE_TIMEOUT_S = 180

    def __init__(self, geom_path: str, stl_out: Path, detail: str, parent=None):
        super().__init__(parent)
        self._geom_path = geom_path
        self._stl_out = stl_out
        self._detail = detail
        self._proc_holder: dict = {}
        self._cancelled = False

    @Slot()
    def cancel(self):
        """Kill the live GMSH subprocess, if any is running right now.

        Cancel in the GUI previously only tore down the QThread this runs
        on — it never reached the actual GMSH child process spawned by
        _stream_subprocess, which kept running orphaned (confirmed live:
        Cancel visually reset the UI but the process stayed alive, and a
        new run then meant two GMSH processes running at once).
        """
        self._cancelled = True
        proc = self._proc_holder.get("proc")
        if proc is not None and proc.poll() is None:
            _kill_pid_tree(proc.pid)

    @Slot()
    def run(self):
        import json
        args = ["surface", self._geom_path, str(self._stl_out), self._detail]
        cmd, run_cwd = _gmsh_wrapper_script_cmd(args, "--gmsh-surface")
        self.log_line.emit("[gmsh] Running surface STL generation in subprocess...")
        try:
            returncode, stdout_lines, stderr_lines, timed_out = _stream_subprocess(
                cmd, run_cwd, self.SURFACE_TIMEOUT_S,
                on_line=lambda ln: self.log_line.emit(f"[gmsh] {ln}"),
                proc_holder=self._proc_holder,
            )
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))
            return

        if self._cancelled:
            self.log_line.emit("[gmsh] Surface generation cancelled.")
            return

        if timed_out:
            self.failed.emit(f"GMSH surface exceeded {self.SURFACE_TIMEOUT_S}s")
            return

        # Parse stdout first — see _gmsh_wrapper_script_cmd's docstring and
        # GmshVolumeWorker.run() for why returncode must not gate this.
        result = None
        non_empty = [ln for ln in stdout_lines if ln.strip()]
        if non_empty:
            try:
                result = json.loads(non_empty[-1])
            except json.JSONDecodeError:
                result = None

        if result is not None:
            if not result.get("success"):
                self.failed.emit(result.get("error", "Unknown GMSH error"))
                return
            self.finished.emit(result)
            return

        if returncode != 0:
            self.failed.emit("\n".join(stderr_lines).strip() or f"GMSH surface failed (exit {returncode})")
            return

        self.failed.emit("GMSH surface: no JSON output produced")


class GmshVolumeWorker(QObject):
    """Runs GMSH volume mesh generation in a subprocess."""

    finished = Signal(object)  # dict with path + names
    log_line = Signal(str)
    failed = Signal(str)

    VOLUME_TIMEOUT_S = 600

    def __init__(self, step_path: str, msh_path: Path, detail: str,
                 n_layers: int = 0, bl_thickness: float | None = None,
                 bl_expansion: float = 1.2, refinement_zones: list | None = None,
                 max_cell_size: float = 0, min_cell_size: float = 0,
                 max_cells_target: int = 0,
                 size_field_file: str | Path | None = None,
                 parent=None):
        super().__init__(parent)
        # Solution-derived target-size lattice (core.solution_adaptive). Passed
        # to the subprocess by env var like refinement_zones — the CLI already
        # takes ten positional args and an eleventh would be unreadable.
        self._size_field_file = str(size_field_file) if size_field_file else ""
        self._step_path = step_path
        self._msh_path = msh_path
        self._detail = detail
        self._n_layers = n_layers
        self._bl_thickness = bl_thickness
        self._bl_expansion = bl_expansion
        self._refinement_zones = refinement_zones or []
        self._max_cell_size = max_cell_size
        self._min_cell_size = min_cell_size
        self._max_cells_target = max_cells_target
        self._proc_holder: dict = {}
        self._cancelled = False

    @Slot()
    def cancel(self):
        """Kill the live GMSH subprocess, if any is running right now.

        See GmshSurfaceWorker.cancel — same gap, same fix: Cancel in the
        GUI previously reset the UI without ever touching this worker's
        actual subprocess, which kept running orphaned in the background.
        """
        self._cancelled = True
        proc = self._proc_holder.get("proc")
        if proc is not None and proc.poll() is None:
            _kill_pid_tree(proc.pid)

    @Slot()
    def run(self):
        import json
        import os
        # Pass refinement zones as JSON via env var (avoids shell-escaping issues)
        zones_json = json.dumps(self._refinement_zones) if self._refinement_zones else ""
        args = [
            "volume", self._step_path, str(self._msh_path), self._detail,
            str(self._n_layers), str(self._bl_thickness or 0), str(self._bl_expansion),
            str(self._max_cell_size), str(self._min_cell_size), str(self._max_cells_target),
        ]
        cmd, run_cwd = _gmsh_wrapper_script_cmd(args, "--gmsh-volume")
        self.log_line.emit("[gmsh] Running volume mesh generation in subprocess...")
        env = os.environ.copy()
        if zones_json:
            env["GMSH_REFINEMENT_ZONES"] = zones_json
        if self._size_field_file:
            env["GMSH_SOLUTION_SIZE_FIELD"] = self._size_field_file
            self.log_line.emit(
                f"[gmsh] solution-adaptive size field: {self._size_field_file}"
            )
        try:
            returncode, stdout_lines, stderr_lines, timed_out = _stream_subprocess(
                cmd, run_cwd, self.VOLUME_TIMEOUT_S, env=env,
                on_line=lambda ln: self.log_line.emit(f"[gmsh] {ln}"),
                proc_holder=self._proc_holder,
            )
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))
            return

        if self._cancelled:
            self.log_line.emit("[gmsh] Volume generation cancelled.")
            return

        if timed_out:
            self.failed.emit(f"GMSH volume exceeded {self.VOLUME_TIMEOUT_S}s")
            return

        # Parse stdout FIRST, even on a non-zero exit: the CLI's own
        # except-block always reports a caught error as JSON on stdout
        # ({"success": false, "error": "..."}) before calling sys.exit(1)
        # — that's the real, specific failure reason. Checking returncode
        # first (the previous order) discarded that message in favor of
        # raw stderr, which can contain unrelated noise (e.g. Python's
        # own "module found in sys.modules" RuntimeWarning from the -m
        # invocation) that has nothing to do with why meshing failed —
        # confirmed from a real user run where the surfaced error was
        # just that warning while the actual cause never reached the UI.
        # GMSH itself may print "Info:" lines to stdout before the JSON,
        # so take the last non-empty line rather than the whole stream.
        result = None
        non_empty = [ln for ln in stdout_lines if ln.strip()]
        if non_empty:
            try:
                result = json.loads(non_empty[-1])
            except json.JSONDecodeError:
                result = None

        if result is not None:
            if not result.get("success"):
                self.failed.emit(result.get("error", "Unknown GMSH error"))
                return
            self.finished.emit(result)
            return

        if returncode != 0:
            self.failed.emit("\n".join(stderr_lines).strip() or f"GMSH volume failed (exit {returncode})")
            return

        self.failed.emit("GMSH volume: no JSON output produced")


class WatertightWorker(QObject):
    """Runs watertight check + optional pymeshfix repair in a subprocess."""

    finished = Signal(object)
    log_line = Signal(str)
    failed = Signal(str)
    TIMEOUT_S = 180

    def __init__(self, stl_paths: list[Path], parent=None):
        super().__init__(parent)
        self._stl_paths = stl_paths
        self._proc_holder: dict = {}
        self._cancelled = False

    @Slot()
    def cancel(self):
        """Kill the live subprocess, if any is running right now. See
        GmshSurfaceWorker.cancel for why this exists."""
        self._cancelled = True
        proc = self._proc_holder.get("proc")
        if proc is not None and proc.poll() is None:
            _kill_pid_tree(proc.pid)

    @Slot()
    def run(self):
        import json
        args = ["check"] + [str(p) for p in self._stl_paths]
        cmd, run_cwd = _gmsh_wrapper_script_cmd(
            args, "--watertight", script_name="geometry_repair.py",
        )
        self.log_line.emit("[watertight] Checking geometry...")
        try:
            returncode, stdout_lines, stderr_lines, timed_out = _stream_subprocess(
                cmd, run_cwd, self.TIMEOUT_S,
                on_line=lambda ln: self.log_line.emit(f"[watertight] {ln}"),
                proc_holder=self._proc_holder,
            )
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))
            return

        if self._cancelled:
            self.log_line.emit("[watertight] Cancelled.")
            return

        if timed_out:
            self.failed.emit(f"Watertight check exceeded {self.TIMEOUT_S}s")
            return

        result = None
        non_empty = [ln for ln in stdout_lines if ln.strip()]
        if non_empty:
            try:
                result = json.loads(non_empty[-1])
            except json.JSONDecodeError:
                result = None

        if result is not None:
            if not result.get("success"):
                self.failed.emit(result.get("error", "Unknown error"))
                return
            self.finished.emit(result)
            return

        if returncode != 0:
            self.failed.emit("\n".join(stderr_lines).strip() or f"Check failed (exit {returncode})")
            return

        self.failed.emit("Watertight: no JSON output produced")


class ExportWorker(QObject):
    """Export mesh to a format in a background thread.

    Signals:
        finished(str): emitted with the output path on success.
        error_occurred(str): emitted with the error message on failure.
    """
    finished = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, case_dir: Path, fmt: str, output_path: str, of_config=None):
        super().__init__()
        self._case_dir = case_dir
        self._fmt = fmt
        self._output_path = output_path
        self._of_config = of_config

    def run(self):
        try:
            from cfmesh_autogui.core.mesh_export import export_mesh
            out = export_mesh(self._case_dir, self._fmt, self._output_path, of_config=self._of_config)
            self.finished.emit(str(out))
        except Exception as e:
            self.error_occurred.emit(str(e))



class SolutionAdaptiveWorker(QObject):
    """Runs the solution-adaptive refinement loop in a background QThread.

    The loop is solve -> compute a refinement indicator -> remesh from the
    original CAD with a solution-derived size field, repeated until the
    engineering quantity of interest stops moving (see
    ``core.solution_adaptive``). Every stage is emitted on ``log_line`` so the
    visible GUI log shows solve progress, residuals, indicator statistics, the
    target sizing chosen and the cell counts — a multi-minute loop that logged
    nothing would be indistinguishable from a hang.

    The remesh half is supplied by the caller as *remesh_fn* rather than being
    hard-coded here, because the GUI already owns the mesh-generation path
    (geometry, detail level, BL settings, patch naming) and duplicating it
    would let the adaptive mesh silently diverge from the normal one.
    """

    log_line = Signal(str)
    finished = Signal(object)  # AdaptiveResult
    failed = Signal(str)
    cycle_done = Signal(int, int)  # (cycle, n_cells)

    def __init__(self, case_dir: Path | str, bounds: tuple, remesh_fn,
                 params=None, of_config: OFConfig | None = None, parent=None):
        super().__init__(parent)
        self._case_dir = Path(case_dir).resolve()
        self._bounds = bounds
        self._remesh_fn = remesh_fn
        self._params = params
        self._of_config = of_config or OFConfig()
        self._cancelled = False

    @Slot()
    def cancel(self):
        self._cancelled = True
        self.log_line.emit(
            "[adaptive] Cancel requested — will stop after the current stage."
        )

    @Slot()
    def run(self):
        from cfmesh_autogui.core.solution_adaptive import (
            AdaptiveParams, SolutionAdaptiveRefiner,
        )

        params = self._params or AdaptiveParams()

        def _remesh(size_field: Path, cycle: int):
            if self._cancelled:
                raise RuntimeError("cancelled by user")
            case_dir, n_cells = self._remesh_fn(size_field, cycle)
            self.cycle_done.emit(cycle, n_cells)
            return case_dir, n_cells

        try:
            refiner = SolutionAdaptiveRefiner(
                params=params, of_config=self._of_config,
                on_line=self.log_line.emit,
            )
            result = refiner.run(
                initial_case_dir=self._case_dir,
                bounds=self._bounds,
                remesh_fn=_remesh,
            )
        except Exception as exc:  # noqa: BLE001 — surfaced to the GUI
            logger.exception("Solution-adaptive refinement worker failed")
            self.failed.emit(str(exc))
            return

        for line in result.summary().splitlines():
            self.log_line.emit(f"[adaptive] {line}")
        if result.errors and not result.cycles:
            self.failed.emit("; ".join(result.errors))
            return
        self.finished.emit(result)
