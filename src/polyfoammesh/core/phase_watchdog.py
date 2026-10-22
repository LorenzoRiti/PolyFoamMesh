# -*- coding: utf-8 -*-
"""One watchdog/heartbeat/ETA infrastructure for every meshing phase.

W8 exists because the pipeline had grown several independent notions of
"slow" and "stuck":

- ``_stream_subprocess`` had its own stall/timeout/heartbeat loop (GMSH,
  checkMesh, gmshToFoam, fTetWild);
- ``PolyDualWorker`` used ``subprocess.run(timeout=300)`` with no heartbeat
  and no reachable child PID;
- ``TetPolyDualConverter`` and ``PolyBoundaryLayerEngine`` only had a cancel
  flag and percentage log lines;
- the GUI had a separate TaskManager heartbeat timeout.

``PhaseWatchdog`` is the single object behind all of them.  It does not
decide *what* a phase should do when it stalls: the caller declares a
``policy`` (``abort``/``retry``/``fallback``/``skip``/``observe``) and
receives a :class:`WatchdogAction` with the evidence.  The subprocess adapter
uses the same object for the real kill, so there is exactly one definition of
"no progress" and one place where the action is logged.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

POLICIES = ("abort", "retry", "fallback", "skip", "observe")


@dataclass(frozen=True)
class WatchdogAction:
    """One declared response to a stalled/timed-out phase."""

    phase: str
    reason: str                 # stall | timeout | external | cancelled
    policy: str                 # one of POLICIES
    elapsed_s: float
    no_progress_s: float
    progress: float | None = None
    eta_s: float | None = None
    evidence: str = ""

    def as_dict(self) -> dict:
        return {
            "phase": self.phase,
            "reason": self.reason,
            "policy": self.policy,
            "elapsed_s": round(float(self.elapsed_s), 3),
            "no_progress_s": round(float(self.no_progress_s), 3),
            "progress": self.progress,
            "eta_s": None if self.eta_s is None else round(float(self.eta_s), 3),
            "evidence": self.evidence,
        }

    def message(self) -> str:
        detail = self.evidence or f"{self.reason} after {self.elapsed_s:.0f}s"
        return (
            f"{self.phase}: {self.reason} ({self.policy}) - {detail}; "
            f"no progress for {self.no_progress_s:.0f}s"
        )


class PhaseStalled(RuntimeError):
    """Raised by :meth:`PhaseWatchdog.guard` with the declared action."""

    def __init__(self, action: WatchdogAction):
        self.action = action
        super().__init__(action.message())


class PhaseWatchdog:
    """Thread-safe liveness/ETA/timeout object shared by all phases.

    Parameters
    ----------
    phase:
        Human-readable phase name (``tet``, ``dual``, ``bl``, ``convert``).
    stall_s:
        No-progress threshold in seconds.  ``None`` disables stall detection.
    timeout_s:
        Absolute ceiling in seconds.  ``None`` disables it.  A timeout is the
        last resort, not the primary stall strategy.
    heartbeat_s:
        Emit ``on_heartbeat`` after this much real silence.  Heartbeats do
        **not** reset the stall clock.
    policy:
        What the caller is expected to do with the action.
    on_event:
        Called exactly once when an action fires.
    on_heartbeat:
        Called with :meth:`snapshot` while a phase is silent but alive.
    on_progress:
        Called by :meth:`progress` as ``(fraction, stage, eta_s)``.
    abort_check / abort_evidence:
        Optional content-aware trigger (e.g. the GMSH signature detector).
        ``abort_check`` returning True fires immediately with reason
        ``"external"``; ``abort_evidence`` supplies the human evidence.
    """

    def __init__(
        self,
        phase: str,
        *,
        stall_s: float | None = None,
        timeout_s: float | None = None,
        heartbeat_s: float | None = 10.0,
        policy: str = "abort",
        clock: Callable[[], float] = time.monotonic,
        on_event: Callable[[WatchdogAction], None] | None = None,
        on_heartbeat: Callable[[dict], None] | None = None,
        on_progress: Callable[[float, str, float | None], None] | None = None,
        abort_check: Callable[[], bool] | None = None,
        abort_evidence: Callable[[], str] | None = None,
    ) -> None:
        if policy not in POLICIES:
            raise ValueError(f"unknown watchdog policy {policy!r}")
        self.phase = str(phase)
        self.stall_s = _positive_or_none(stall_s)
        self.timeout_s = _positive_or_none(timeout_s)
        self.heartbeat_s = _positive_or_none(heartbeat_s)
        self.policy = policy
        self._clock = clock
        self._on_event = on_event
        self._on_heartbeat = on_heartbeat
        self._on_progress = on_progress
        self._abort_check = abort_check
        self._abort_evidence = abort_evidence
        self._start = float(clock())
        self._last_progress = self._start
        self._last_heartbeat = self._start
        self._progress: float | None = None
        self._stage = ""
        self._action: WatchdogAction | None = None
        self._warnings: list[str] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Liveness
    # ------------------------------------------------------------------
    def beat(self, message: str = "") -> None:
        """Record real work (a log line, a completed loop iteration)."""
        with self._lock:
            self._last_progress = float(self._clock())
            if message:
                self._stage = str(message)

    def progress(
        self, fraction: float, stage: str = "", *,
        emit: bool = True,
    ) -> None:
        """Record deterministic progress in ``[0, 1]`` and compute an ETA.

        Out-of-range fractions are clamped and reported in :attr:`warnings`;
        no phase should silently pretend 1.2 is a valid fraction.
        """
        try:
            value = float(fraction)
        except (TypeError, ValueError):
            self._warn(f"progress {fraction!r} is not a number; ignored")
            return
        if not math.isfinite(value):
            self._warn(f"progress {fraction!r} is not finite; ignored")
            return
        if value < 0.0 or value > 1.0:
            self._warn(f"progress {value!r} clamped to [0, 1]")
            value = min(max(value, 0.0), 1.0)
        with self._lock:
            if self._progress is not None and value < self._progress:
                self._warn(
                    f"progress went backwards ({value:.3f} < "
                    f"{self._progress:.3f}); keeping the maximum"
                )
                value = self._progress
            self._progress = value
            self._stage = str(stage or self._stage)
            self.beat(self._stage)
            eta = self.eta_s()
            callback = self._on_progress
        if emit and callback is not None:
            _safe_call(callback, value, self._stage, eta)

    def check(self) -> WatchdogAction | None:
        """Return the action once, or ``None`` while the phase is alive.

        Polling after an action returns the same object without re-emitting
        the event (idempotent, safe for a GUI timer and a worker loop to
        poll concurrently).
        """
        with self._lock:
            if self._action is not None:
                return self._action
            now = float(self._clock())
            elapsed = max(0.0, now - self._start)
            no_progress = max(0.0, now - self._last_progress)

            if self._abort_check is not None:
                try:
                    fired = bool(self._abort_check())
                except Exception:  # noqa: BLE001 - a detector must not crash
                    logger.debug("%s: abort_check failed", self.phase, exc_info=True)
                    fired = False
                if fired:
                    evidence = ""
                    if self._abort_evidence is not None:
                        try:
                            evidence = str(self._abort_evidence())
                        except Exception:  # noqa: BLE001
                            evidence = ""
                    return self._fire(
                        "external", elapsed, no_progress, evidence=evidence,
                    )

            if self.timeout_s is not None and elapsed >= self.timeout_s:
                return self._fire(
                    "timeout", elapsed, no_progress,
                    evidence=f"phase exceeded {self.timeout_s:g}s",
                )

            if self.stall_s is not None and no_progress >= self.stall_s:
                return self._fire(
                    "stall", elapsed, no_progress,
                    evidence=f"no progress for {no_progress:.0f}s "
                             f"(threshold {self.stall_s:g}s)",
                )

            if (
                self.heartbeat_s is not None
                and no_progress >= self.heartbeat_s
                and now - self._last_heartbeat >= self.heartbeat_s
            ):
                self._last_heartbeat = now
                snapshot = self._snapshot_locked(now)
            else:
                snapshot = None
        if snapshot is not None and self._on_heartbeat is not None:
            _safe_call(self._on_heartbeat, snapshot)
        return None

    def guard(self) -> None:
        """Raise :class:`PhaseStalled` if an action has fired."""
        action = self.check()
        if action is not None:
            raise PhaseStalled(action)

    def abort(self, reason: str = "cancelled", evidence: str = "") -> WatchdogAction:
        """Record a caller-initiated stop (e.g. user Cancel)."""
        with self._lock:
            now = float(self._clock())
            return self._fire(
                reason, now - self._start, now - self._last_progress,
                evidence=evidence,
            )

    # ------------------------------------------------------------------
    # Read-only state
    # ------------------------------------------------------------------
    @property
    def action(self) -> WatchdogAction | None:
        return self._action

    @property
    def aborted(self) -> bool:
        return self._action is not None

    @property
    def progress_fraction(self) -> float | None:
        return self._progress

    @property
    def stage(self) -> str:
        return self._stage

    @property
    def warnings(self) -> list[str]:
        with self._lock:
            return list(self._warnings)

    def elapsed_s(self) -> float:
        return max(0.0, float(self._clock()) - self._start)

    def no_progress_s(self) -> float:
        return max(0.0, float(self._clock()) - self._last_progress)

    def eta_s(self) -> float | None:
        with self._lock:
            if self._progress is None or self._progress <= 0.0:
                return None
            if self._progress >= 1.0:
                return 0.0
            elapsed = max(0.0, float(self._clock()) - self._start)
            return elapsed * (1.0 - self._progress) / self._progress

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot_locked(float(self._clock()))

    def _snapshot_locked(self, now: float) -> dict:
        return {
            "phase": self.phase,
            "policy": self.policy,
            "elapsed_s": round(max(0.0, now - self._start), 3),
            "no_progress_s": round(max(0.0, now - self._last_progress), 3),
            "progress": self._progress,
            "stage": self._stage,
            "eta_s": (
                None if self.eta_s() is None else round(float(self.eta_s()), 3)
            ),
            "stall_s": self.stall_s,
            "timeout_s": self.timeout_s,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _warn(self, message: str) -> None:
        with self._lock:
            self._warnings.append(str(message))
        logger.warning("%s watchdog: %s", self.phase, message)

    def _fire(
        self, reason: str, elapsed: float, no_progress: float,
        evidence: str = "",
    ) -> WatchdogAction:
        action = WatchdogAction(
            phase=self.phase,
            reason=reason,
            policy=self.policy,
            elapsed_s=float(elapsed),
            no_progress_s=float(no_progress),
            progress=self._progress,
            eta_s=self.eta_s(),
            evidence=str(evidence or ""),
        )
        self._action = action
        callback = self._on_event
        # Call outside the lock where possible; _fire is also called from
        # check() while the lock is held, and the callback only logs.
        if callback is not None:
            _safe_call(callback, action)
        return action


def _positive_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0.0 else None


def _safe_call(callback: Callable, *args) -> None:
    try:
        callback(*args)
    except Exception:  # noqa: BLE001 - observers must never break a mesh run
        logger.debug("watchdog callback failed", exc_info=True)


# ---------------------------------------------------------------------------
# fTetWild progress (W3 predicted it; W8 wires it)
# ---------------------------------------------------------------------------
class FtetWildProgressDetector:
    """Parse pytetwild's streamed ``pass``/energy counters.

    fTetWild's optimizer prints one ``pass N`` line per iteration and an
    energy update.  The pass index is the only deterministic progress unit
    (W3); energy is recorded as evidence but does not drive the ETA because
    it can be non-monotonic.
    """

    _PASS_RE = re.compile(r"\bpass\s+(\d+)\b", re.IGNORECASE)
    _ENERGY_RE = re.compile(
        r"max_energy\s*[:=]\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)",
        re.IGNORECASE,
    )
    _NV_RE = re.compile(r"#v\s*[:=]?\s*(\d+)", re.IGNORECASE)
    _NT_RE = re.compile(r"#t\s*[:=]?\s*(\d+)", re.IGNORECASE)

    def __init__(self, opt_iter: int = 0):
        self.opt_iter = max(0, int(opt_iter or 0))
        self.pass_index: int | None = None
        self.max_energy: float | None = None
        self.n_vertices: int | None = None
        self.n_tets: int | None = None

    @property
    def fraction(self) -> float | None:
        if self.pass_index is None or self.opt_iter <= 0:
            return None
        return min(max(self.pass_index / float(self.opt_iter), 0.0), 1.0)

    def feed(self, line: str) -> bool:
        """Feed one line; return True when a tracked counter changed."""
        if not line:
            return False
        changed = False
        m = self._PASS_RE.search(line)
        if m:
            self.pass_index = int(m.group(1))
            changed = True
        m = self._ENERGY_RE.search(line)
        if m:
            try:
                self.max_energy = float(m.group(1))
                changed = True
            except ValueError:
                pass
        m = self._NV_RE.search(line)
        if m:
            self.n_vertices = int(m.group(1))
            changed = True
        m = self._NT_RE.search(line)
        if m:
            self.n_tets = int(m.group(1))
            changed = True
        return changed

    def evidence(self) -> str:
        parts = []
        if self.pass_index is not None:
            parts.append(f"pass {self.pass_index}/{self.opt_iter or '?'}")
        if self.max_energy is not None:
            parts.append(f"max_energy {self.max_energy:g}")
        if self.n_tets is not None:
            parts.append(f"#t {self.n_tets:,}")
        return ", ".join(parts)
