"""Mesh quality optimisation engine — inspired by MeshLab, Star-CCM+ Optimizer.

Provides programmatic mesh quality assessment and improvement:

  - Multi-metric evaluation (skewness, non-orthogonality, aspect ratio, volume)
  - Auto-fix strategies with iteration loop
  - Before/after comparison report
  - Quality heatmap data generation
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polyfoammesh.core.openfoam_runner import MeshQualityReport

from polyfoammesh.core.quality_thresholds import OPTIMIZER_THRESHOLDS
from polyfoammesh.octopoda_local import octo

# Lazy imports (avoid cadquery DLL chain via core/__init__)
_openfoam_runner = None
_meshdict_gen = None


def _lazy_runner():
    global _openfoam_runner
    if _openfoam_runner is None:
        from polyfoammesh.core import openfoam_runner as _openfoam_runner
    return _openfoam_runner


def _lazy_meshdict():
    global _meshdict_gen
    if _meshdict_gen is None:
        from polyfoammesh.core import meshdict_gen as _meshdict_gen
    return _meshdict_gen

logger = logging.getLogger(__name__)


@dataclass
class QualitySnapshot:
    """A point-in-time quality measurement."""
    timestamp: str = ""
    metrics: dict[str, float] = field(default_factory=dict)
    passed: bool = False
    status: str = ""
    fix_attempt: int = 0

    @classmethod
    def from_report(cls, report: MeshQualityReport, attempt: int = 0) -> QualitySnapshot:
        return cls(
            timestamp=datetime.now().isoformat(),
            metrics=report.to_dict().get("metrics", {}),
            passed=report.passed,
            status=report.status,
            fix_attempt=attempt,
        )


@dataclass
class QualityReport:
    """Structured before/after quality report."""
    initial: QualitySnapshot | None = None
    final: QualitySnapshot | None = None
    history: list[QualitySnapshot] = field(default_factory=list)
    iterations: int = 0
    converged: bool = False
    summary: str = ""

    def improved(self) -> bool:
        """True if final quality is better than initial."""
        if not self.initial or not self.final:
            return False
        return self.final.passed or (
            self.final.metrics.get("max_skewness", 999) <
            self.initial.metrics.get("max_skewness", 999)
        )

    def metrics_improvement(self) -> dict[str, float]:
        """Return per-metric absolute improvement (negative = worse)."""
        if not self.initial or not self.final:
            return {}
        result = {}
        for key in self.final.metrics:
            old_val = self.initial.metrics.get(key, 0)
            new_val = self.final.metrics.get(key, 0)
            # For quality metrics, lower is better
            result[key] = old_val - new_val
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "iterations": self.iterations,
            "converged": self.converged,
            "summary": self.summary,
            "initial": {
                "passed": self.initial.passed if self.initial else False,
                "status": self.initial.status if self.initial else "",
                "metrics": self.initial.metrics if self.initial else {},
            } if self.initial else None,
            "final": {
                "passed": self.final.passed if self.final else False,
                "status": self.final.status if self.final else "",
                "metrics": self.final.metrics if self.final else {},
            } if self.final else None,
            "history": [
                {"fix_attempt": s.fix_attempt, "passed": s.passed, "status": s.status}
                for s in self.history
            ],
        }

    def to_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str))


class MeshOptimizer:
    """Mesh quality optimiser with auto-fix loop.

    Uses checkMesh for evaluation and applies meshDict parameter
    adjustments (cell size relaxation, BL disable) to improve quality.

    Usage::

        opt = MeshOptimizer(of_config)
        report = opt.optimize(case_dir, max_iterations=5)
        print(report.summary)
    """

    # Default threshold overrides for the auto-fix loop
    # (single source of truth: polyfoammesh.core.quality_thresholds)
    THRESHOLDS = OPTIMIZER_THRESHOLDS

    def __init__(self, of_config=None) -> None:
        from polyfoammesh.config import OFConfig
        self._of_config = of_config or OFConfig()
        self._report = QualityReport()

    @staticmethod
    def _passes_quality(report, thr: dict) -> bool:
        """Whether *report* meets THIS optimiser's quality thresholds.

        `MeshQualityReport.passed` only rules out fatal errors and negative-
        volume cells — it says nothing about skewness/non-orthogonality/aspect
        ratio. Both the "already passes, no fix needed" entry check and the
        per-iteration convergence check used `.passed` directly, so this
        auto-fix engine reported success immediately for ANY mesh without a
        fatal error, no matter how bad its skewness/non-ortho/aspect ratio
        actually were — it could never fix the exact defects it exists to fix.
        """
        if not report.passed:
            return False
        return (
            report.max_skewness <= thr["skewness_max"]
            and report.max_non_ortho <= thr["non_ortho_max"]
            and report.max_aspect_ratio <= thr["aspect_ratio_max"]
        )

    def optimize(
        self,
        case_dir: Path | str,
        max_iterations: int = 5,
        thresholds: dict[str, float] | None = None,
        on_step: Callable | None = None,
    ) -> QualityReport:
        """Run the optimiser loop.

        Args:
            case_dir: OpenFOAM case directory with an existing polyMesh.
            max_iterations: Maximum number of fix→eval cycles.
            thresholds: Quality thresholds dict (skewness_max, etc.).
            on_step: Optional callback invoked after each iteration
                with ``(iteration, snapshot)``.

        Returns:
            A ``QualityReport`` with before/after comparison.
        """
        case_dir = Path(case_dir)
        thr = {**self.THRESHOLDS, **(thresholds or {})}
        self._report = QualityReport(history=[])

        octo.log_event("optimizer", "optimize_start", {
            "case_dir": str(case_dir),
            "max_iterations": max_iterations,
        })

        # 1. Initial quality snapshot
        initial_report = self._run_checkmesh(case_dir)
        if initial_report is None:
            self._report.summary = "checkMesh failed — cannot evaluate initial quality."
            return self._report

        self._report.initial = QualitySnapshot.from_report(initial_report, 0)
        self._report.history.append(self._report.initial)

        if self._passes_quality(initial_report, thr):
            self._report.final = self._report.initial
            self._report.converged = True
            self._report.iterations = 0
            self._report.summary = "Mesh passes all quality checks — no fix needed."
            octo.log_event("optimizer", "already_passed", {"skewness": initial_report.max_skewness})
            return self._report

        logger.info(
            "Optimiser: initial quality FAIL — skewness=%.2f nonOrtho=%.1f aspect=%.0f",
            initial_report.max_skewness, initial_report.max_non_ortho,
            initial_report.max_aspect_ratio,
        )

        # 2. Fix loop
        current_max = 0.05
        current_min = 0.01
        # meshDict's BL contract: thicknessRatio = growth ratio (>1),
        # firstLayerThickness = absolute metres (same bug found and fixed in
        # watertight.py/fault_tolerant.py/mesh_engine.py — a first-layer
        # fraction passed as "thicknessRatio" gets clamped to a default growth
        # ratio and silently drops the first-layer size entirely).
        bl_params: dict | None = {
            "nLayers": 3, "thicknessRatio": 1.2, "firstLayerThickness": 0.005 * current_max,
        }
        bl_disabled = False

        # Patches typed via renameBoundary or every inlet/outlet reverts to
        # cfMesh's default `wall` on the meshDict rewrites below. The mesh
        # already exists in case_dir (checkMesh just ran on it), so read its
        # real patch names rather than needing fresh geometry.
        patch_names: list[str] | None = None
        try:
            from polyfoammesh.core.boundary_reader import parse_boundary
            boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
            if boundary_path.exists():
                patch_names = [p.name for p in parse_boundary(boundary_path)]
        except Exception as exc:
            logger.debug("Optimiser: could not read existing patch names: %s", exc)

        for iteration in range(1, max_iterations + 1):
            if on_step:
                on_step(iteration, self._report.history[-1])

            # Determine fix strategy
            snapshot = self._report.history[-1]
            metrics = snapshot.metrics
            max_skew = metrics.get("max_skewness", 0)
            max_non_ortho = metrics.get("max_non_ortho", 0)

            fixes: list[str] = []
            if max_skew > thr["skewness_max"]:
                # Relax cell sizes
                current_min *= 0.7
                current_max *= 1.2
                fixes.append(f"relax cells (max={current_max:.4f} min={current_min:.4f})")

            if max_non_ortho > thr["non_ortho_max"] and not bl_disabled:
                bl_params = None
                bl_disabled = True
                fixes.append("disable boundary layers")

            if not fixes:
                logger.info("Optimiser: no applicable fix at iteration %d", iteration)
                break

            logger.info("Optimiser iteration %d: %s", iteration, "; ".join(fixes))

            # Apply fixes via meshDict rewrite
            try:
                _lazy_meshdict().write_meshdict(
                    case_dir,
                    max_cell_size=current_max,
                    min_cell_size=current_min,
                    bl_params=bl_params,
                    patch_names=patch_names,
                )
            except Exception as exc:
                logger.warning("Optimiser: meshDict rewrite failed: %s", exc)
                continue

            # Re-run meshing. This used to create a RetryRunner and never call
            # .run() on it — the loop rewrote meshDict with relaxed parameters
            # but then re-checked the SAME unchanged mesh every iteration,
            # so it could never actually converge or improve anything.
            if not self._run_cartesian_mesh(case_dir):
                logger.warning("Optimiser: cartesianMesh failed at iteration %d", iteration)
                continue

            # Re-evaluate
            new_report = self._run_checkmesh(case_dir)
            if new_report is None:
                continue

            snapshot = QualitySnapshot.from_report(new_report, iteration)
            self._report.history.append(snapshot)

            if self._passes_quality(new_report, thr):
                self._report.final = snapshot
                self._report.converged = True
                self._report.iterations = iteration
                self._report.summary = (
                    f"Quality passed after {iteration} fix iteration(s). "
                    f"Skewness: {initial_report.max_skewness:.2f} → {new_report.max_skewness:.2f}"
                )
                octo.log_event("optimizer", "converged", {"iterations": iteration})
                return self._report

        # 3. Final state (did not converge)
        final_snapshot = self._report.history[-1] if self._report.history else None
        self._report.final = final_snapshot
        self._report.iterations = len(self._report.history) - 1
        self._report.converged = False
        self._report.summary = (
            f"Optimiser did not converge after {self._report.iterations} iterations. "
            f"Best skewness: {initial_report.max_skewness:.2f} → "
            f"{final_snapshot.metrics.get('max_skewness', 0):.2f}" if final_snapshot else "No improvement."
        )

        octo.log_event("optimizer", "not_converged", {"iterations": self._report.iterations})
        return self._report

    def _run_cartesian_mesh(self, case_dir: Path) -> bool:
        """Run cartesianMesh synchronously so the fix loop actually re-meshes.

        Mirrors _run_checkmesh's synchronous style rather than RetryRunner
        (async, QThread-based — a poor fit for this iterate-and-recheck loop).
        Refuses (returns False) when the existing mesh is GMSH tet / tet→poly
        output — re-meshing would silently replace it (Fase 3 P3.2 guard).
        """
        import subprocess

        from polyfoammesh.core.validation import mesh_remeshable

        ok, reason = mesh_remeshable(case_dir)
        if not ok:
            logger.warning("optimizer: refusing cartesianMesh — %s", reason)
            return False

        try:
            cmd = self._of_config.build_command(case_dir)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("cartesianMesh timed out for %s", case_dir)
            return False
        except FileNotFoundError:
            logger.warning("WSL not found for cartesianMesh")
            return False
        except Exception as exc:
            logger.warning("cartesianMesh failed: %s", exc)
            return False

    def _run_checkmesh(self, case_dir: Path) -> Any | None:
        """Run checkMesh synchronously and parse result."""
        import subprocess

        try:
            cmd = self._of_config.build_check_mesh_cmd(case_dir)
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )
            full = result.stdout + "\n" + result.stderr
            return _lazy_runner().parse_checkmesh_output(full)
        except subprocess.TimeoutExpired:
            logger.warning("checkMesh timed out for %s", case_dir)
            return None
        except FileNotFoundError:
            logger.warning("WSL not found for checkMesh")
            return None
        except Exception as exc:
            logger.warning("checkMesh failed: %s", exc)
            return None

    def generate_heatmap_data(self, report: MeshQualityReport) -> dict[str, list[float]]:
        """Generate per-cell quality metrics for heatmap visualisation.

        Returns metrics arrays that can be used for VTK colouring or
        matplotlib histograms.
        """
        return report.to_dict().get("metrics", {})
