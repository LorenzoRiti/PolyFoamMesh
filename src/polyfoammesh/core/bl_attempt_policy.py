# -*- coding: utf-8 -*-
"""One deterministic BL attempt policy/executor (W10.1).

The engine still owns the single-attempt build primitive.  This module owns
the *sequence*: named attempts, the real result of each, keep-best and a
declared gap when no attempt reaches the acceptance target.

Why it exists (measured R1, 2026-10-22): the standard attempt is a valid
mesh with 100% coverage but 39 checkMesh face pyramids, 5 skew faces and 26
non-ortho errors.  A quality-first attempt (decoupled_vertex, zero pyramid
tolerance) excludes 187 concave-rim faces, keeps 99.47% coverage and leaves
0 pyramids / 1 skew after the boundary-pyramid clearance.  The old worker
never tried it because the standard attempt was already "successful".  The
policy makes that choice explicit and reversible.

No preset or default is changed here: ``BLAttemptPolicy.compatibility()``
contains exactly one standard attempt.  Callers opt into
``for_ftetwild()`` only when they want the measured quality attempt.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BLAttempt:
    """One declarative attempt passed to ``PolyBoundaryLayerEngine.run``."""

    name: str
    overrides: Mapping[str, Any] = field(default_factory=dict)
    clear_boundary_pyramids: bool = False


@dataclass(frozen=True)
class BLAttemptPolicy:
    """Ordered attempts + explicit acceptance; deterministic by design."""

    name: str
    attempts: tuple[BLAttempt, ...]
    min_coverage_pct: float = 98.0
    max_pyramids: int = 0
    min_h1_ratio: float = 0.9
    stop_on_accepted: bool = True

    @classmethod
    def compatibility(cls) -> "BLAttemptPolicy":
        """One standard attempt: byte-identical to the pre-W10.1 chain."""
        return cls(
            name="compatibility",
            attempts=(BLAttempt("standard"),),
        )

    @classmethod
    def for_ftetwild(cls) -> "BLAttemptPolicy":
        """Standard first, then the measured quality-first closure attempt.

        The quality attempt is only run when the standard result adds
        pyramids (the executor short-circuits otherwise).  It deliberately
        keeps the same h1 request; it does not fake the structural cap.
        """
        return cls(
            name="ftetwild-quality",
            attempts=(
                BLAttempt("standard"),
                BLAttempt(
                    "quality-decoupled-vertex",
                    overrides={
                        "pyr_tolerance": 0,
                        "local_termination": "decoupled_vertex",
                        "local_height_retry": False,
                        "local_max_rounds": 3,
                        "cover_thin_first": False,
                    },
                    clear_boundary_pyramids=True,
                ),
            ),
        )


@dataclass
class BLAttemptResult:
    attempt: BLAttempt
    result: Any = None
    coverage_pct: float = 0.0
    h1_ratio: float = 0.0
    h1_median: float | None = None
    pyramids: int = 0
    wall_seconds: float = 0.0
    error: str = ""
    h1_ok: bool = False
    accepted: bool = False

    def score(self) -> tuple:
        """Quality/coverage first; never hide a lower h1 behind coverage."""
        return (
            int(self.accepted),
            -int(self.pyramids),
            float(self.coverage_pct),
            float(self.h1_ratio),
            -float(self.wall_seconds),
        )


@dataclass
class BLChainResult:
    policy: BLAttemptPolicy
    attempts: list[BLAttemptResult]
    best: BLAttemptResult | None = None
    gap_reason: str = ""
    accepted: bool = False
    h1_ok: bool = False

    def to_mapping(self) -> dict:
        return {
            "policy": self.policy.name,
            "accepted": self.accepted,
            "h1_ok": self.h1_ok,
            "selected_attempt": self.best.attempt.name if self.best else "",
            "coverage_pct": self.best.coverage_pct if self.best else 0.0,
            "h1_ratio": self.best.h1_ratio if self.best else 0.0,
            "h1_median_m": self.best.h1_median if self.best else None,
            "pyramids": self.best.pyramids if self.best else None,
            "gap_reason": self.gap_reason,
            "attempts": [
                {
                    "name": a.attempt.name,
                    "success": bool(a.result and a.result.success),
                    "coverage_pct": a.coverage_pct,
                    "h1_ratio": a.h1_ratio,
                    "pyramids": a.pyramids,
                    "accepted": a.accepted,
                    "wall_s": a.wall_seconds,
                    "error": a.error,
                }
                for a in self.attempts
            ],
        }


class BLChainExecutor:
    """Run a policy, snapshot the best mesh, restore it if a later try loses."""

    def __init__(self, log: Callable[[str], None] | None = None) -> None:
        self._log = log or (lambda msg: logger.info(msg))

    def run(
        self,
        engine,
        base_kwargs: Mapping[str, Any],
        policy: BLAttemptPolicy | None = None,
    ) -> BLChainResult:
        policy = policy or BLAttemptPolicy.compatibility()
        attempts: list[BLAttemptResult] = []
        best: BLAttemptResult | None = None
        best_snapshot: Path | None = None
        snapshot_root = Path(tempfile.mkdtemp(prefix="pbm_bl_best_"))
        _case_dir = getattr(engine, "case_dir", None)
        case_dir = Path(_case_dir) if _case_dir is not None else None
        poly = (
            case_dir / "constant" / "polyMesh"
            if case_dir is not None else None
        )
        baseline: Path | None = None
        if poly is not None and poly.exists():
            baseline = snapshot_root / "baseline_polyMesh"
            shutil.copytree(poly, baseline)
        try:
            for attempt_index, attempt in enumerate(policy.attempts):
                # Every attempt must start from the SAME dual-only input.
                # Without this, attempt 2 reads the mesh attempt 1 just wrote
                # and tries to add a second BL (measured W10.1: 588 s and a
                # multiply-connected failure instead of the real quality
                # attempt).
                if attempt_index > 0 and baseline is not None and poly is not None:
                    shutil.rmtree(poly, ignore_errors=True)
                    shutil.copytree(baseline, poly)
                kwargs = dict(base_kwargs)
                kwargs.update(dict(attempt.overrides))
                self._log(
                    f"[bl] policy {policy.name}: attempt '{attempt.name}'"
                )
                t0 = time.perf_counter()
                try:
                    res = engine.run(**kwargs)
                    err = ""
                except Exception as exc:  # noqa: BLE001 - one attempt must not
                    res = None
                    err = f"{type(exc).__name__}: {exc}"
                    self._log(f"[bl] attempt '{attempt.name}' failed: {err}")
                elapsed = time.perf_counter() - t0
                if (res is not None and res.success
                        and attempt.clear_boundary_pyramids
                        and case_dir is not None):
                    from polyfoammesh.core.bl_poly import (
                        clear_boundary_pyramids_case,
                    )

                    try:
                        cleared = clear_boundary_pyramids_case(
                            case_dir, log=self._log,
                        )
                        if cleared:
                            old = int(getattr(res, "n_pyramids_after", 0))
                            res.n_pyramids_after = max(0, old - cleared)
                            res.stats["n_pyramids_after"] = res.n_pyramids_after
                            self._log(
                                f"[bl] attempt '{attempt.name}': cleared "
                                f"{cleared} boundary pyramid face(s)"
                            )
                    except Exception as exc:  # noqa: BLE001
                        self._log(
                            f"[bl] attempt '{attempt.name}': pyramid "
                            f"clearance failed: {exc}"
                        )
                stats = getattr(res, "stats", {}) or {}
                # The local-termination attempt does not recompute the
                # per-vertex precompute cap.  Carry the standard attempt's
                # measured cap onto it: the geometry did not change.
                if (res is not None and res.success
                        and "local-termination" in str(
                            getattr(res, "h1_cap_reason", "")
                        )):
                    for previous in attempts:
                        prev_cap = (
                            getattr(previous.result, "stats", {}) or {}
                        ).get("height_cap")
                        if prev_cap:
                            stats["height_cap"] = prev_cap
                            res.h1_cap_reason = getattr(
                                previous.result, "h1_cap_reason", "",
                            )
                            stats["h1_cap_reason"] = res.h1_cap_reason
                            break
                outcome = BLAttemptResult(
                    attempt=attempt,
                    result=res,
                    coverage_pct=float(
                        stats.get("wall_face_coverage_pct", 0.0) or 0.0
                    ),
                    h1_ratio=float(getattr(res, "h1_ratio", 0.0) or 0.0),
                    h1_median=getattr(res, "h1_median", None),
                    pyramids=int(getattr(res, "n_pyramids_after", 0) or 0),
                    wall_seconds=elapsed,
                    error=err,
                    h1_ok=float(getattr(res, "h1_ratio", 0.0) or 0.0)
                    >= policy.min_h1_ratio,
                )
                outcome.accepted = bool(
                    res is not None and res.success
                    and outcome.coverage_pct >= policy.min_coverage_pct
                    and outcome.pyramids <= policy.max_pyramids
                )
                attempts.append(outcome)
                self._log(
                    f"[bl] attempt '{attempt.name}': "
                    f"coverage {outcome.coverage_pct:.2f}% | "
                    f"h1 {outcome.h1_ratio:.3f}x | "
                    f"pyramids {outcome.pyramids} | "
                    f"{'accepted' if outcome.accepted else 'not accepted'} "
                    f"({elapsed:.1f}s)"
                )
                if (res is not None and res.success
                        and (best is None or outcome.score() > best.score())):
                    if poly is not None and poly.exists():
                        if best_snapshot is not None:
                            shutil.rmtree(best_snapshot, ignore_errors=True)
                        best_snapshot = snapshot_root / "best_polyMesh"
                        shutil.copytree(poly, best_snapshot)
                    best = outcome
                if (policy.stop_on_accepted and outcome.accepted
                        and outcome.h1_ok):
                    break
                if (policy.stop_on_accepted and outcome.accepted
                        and not outcome.h1_ok
                        and best is outcome):
                    # A quality result is still the best available; do not
                    # spend another full BL build only to reproduce the same
                    # structural h1 cap.
                    break
            if best is None:
                if baseline is not None and poly is not None:
                    shutil.rmtree(poly, ignore_errors=True)
                    shutil.copytree(baseline, poly)
                gap = "every attempt failed or produced no mesh"
                return BLChainResult(policy=policy, attempts=attempts,
                                     gap_reason=gap)
            if (best_snapshot is not None and poly is not None
                    and poly.exists()):
                # If the last attempt is not the selected best, restore the
                # snapshot so the caller always gets the selected mesh.
                last = attempts[-1]
                if last is not best:
                    shutil.rmtree(poly, ignore_errors=True)
                    shutil.copytree(best_snapshot, poly)
                    self._log(
                        f"[bl] restored best attempt '{best.attempt.name}'"
                    )
            gaps = []
            if not best.accepted:
                gaps.append(
                    f"quality target not met (coverage {best.coverage_pct:.2f}%"
                    f" >= {policy.min_coverage_pct:.0f}%, pyramids "
                    f"{best.pyramids} <= {policy.max_pyramids})"
                )
            if best.h1_median is not None and not best.h1_ok:
                gaps.append(
                    f"h1 {best.h1_median * 1e3:.4g} mm = "
                    f"{best.h1_ratio:.3f}x requested (< "
                    f"{policy.min_h1_ratio:.2f}x); "
                    f"{getattr(best.result, 'h1_cap_reason', '')}"
                )
            gap = "; ".join(gaps)
            if best.result is not None:
                stats = getattr(best.result, "stats", None)
                if not isinstance(stats, dict):
                    stats = {}
                    try:
                        best.result.stats = stats
                    except Exception:  # noqa: BLE001 - reporting only
                        stats = None
                if stats is not None:
                    stats["bl_policy"] = policy.name
                    stats["bl_selected_attempt"] = best.attempt.name
                    stats["bl_attempts"] = [
                        a.attempt.name for a in attempts
                    ]
                    stats["bl_gap_reason"] = gap
            if gap:
                self._log(f"[bl] policy gap: {gap}")
            return BLChainResult(
                policy=policy,
                attempts=attempts,
                best=best,
                gap_reason=gap,
                accepted=bool(best.accepted),
                h1_ok=bool(best.h1_ok),
            )
        finally:
            shutil.rmtree(snapshot_root, ignore_errors=True)


def run_bl_policy(
    engine,
    base_kwargs: Mapping[str, Any],
    policy: BLAttemptPolicy | None = None,
    log: Callable[[str], None] | None = None,
) -> BLChainResult:
    """Convenience entry point used by the worker/CLI."""
    return BLChainExecutor(log=log).run(engine, base_kwargs, policy=policy)
