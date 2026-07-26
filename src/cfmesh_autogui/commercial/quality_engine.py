"""Quality engine — metrics, 3D heatmap data, histogram, auto-fix, PDF report.

Analyses mesh quality via checkMesh, generates structured data for
3D heatmap colouring (per-cell quality), matplotlib histogram,
intelligent auto-fix (max 3 iterations), and PDF report export.

Usage::

    qe = QualityEngine()
    report = qe.analyse(case_dir)
    print(report.summary())
    qe.auto_fix(report, case_dir, max_iterations=3)
    qe.export_pdf(report, "quality_report.pdf")
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)


# Quality thresholds for auto-fix decisions
THRESHOLDS = {
    "skewness_max": 0.9,
    "non_ortho_max": 70.0,
    "aspect_ratio_max": 1000.0,
    "neg_vol_max": 0,
}


@dataclass
class QualityMetrics:
    """Individual quality metrics."""
    max_skewness: float = 0.0
    avg_skewness: float = 0.0
    max_non_orthogonality: float = 0.0
    avg_non_orthogonality: float = 0.0
    max_aspect_ratio: float = 0.0
    min_volume: float = 0.0
    neg_cells: int = 0
    cells: int = 0
    has_fatal: bool = False

    @property
    def passed(self) -> bool:
        if self.has_fatal or self.neg_cells > 0:
            return False
        return (self.max_skewness <= THRESHOLDS["skewness_max"]
                and self.max_non_orthogonality <= THRESHOLDS["non_ortho_max"]
                and self.max_aspect_ratio <= THRESHOLDS["aspect_ratio_max"])


@dataclass
class AutoFixAction:
    """Description of a single auto-fix action."""
    action: str = ""       # smooth, refine, remesh, split, disable_bl
    target_metric: str = ""
    current_value: float = 0.0
    detail: str = ""


@dataclass
class QualityReport:
    """Complete quality report for a mesh."""
    metrics: QualityMetrics = field(default_factory=QualityMetrics)
    passed: bool = False
    status: str = ""
    heatmap_data: dict[str, list[float]] = field(default_factory=dict)
    histogram: dict[str, Any] = field(default_factory=dict)
    fixes_applied: list[AutoFixAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        m = self.metrics
        parts = [
            f"Cells: {m.cells:,}",
            f"Skewness: {m.max_skewness:.2f}",
            f"Non-ortho: {m.max_non_orthogonality:.1f}°",
            f"Aspect: {m.max_aspect_ratio:.0f}",
        ]
        if m.neg_cells:
            parts.append(f"NegVol: {m.neg_cells}")
        return " | ".join(parts) + f" | {'✅ PASS' if self.passed else '❌ FAIL'}"


class QualityEngine:
    """Mesh quality analysis and auto-fix engine.

    Usage::

        qe = QualityEngine()
        report = qe.analyse(case_dir)
        if not report.passed:
            qe.auto_fix(case_dir, max_iterations=3)
    """

    def analyse(self, case_dir: Path | str) -> QualityReport:
        """Run quality analysis on a case directory.

        Parses checkMesh output and generates heatmap-ready data.
        """
        case_dir = Path(case_dir)
        report = QualityReport()

        octo.log_event("quality_engine", "analyse_start", {"case_dir": str(case_dir)})

        try:
            raw = self._get_checkmesh_output(case_dir)
            metrics = self._parse_metrics(raw)
            report.metrics = metrics
            report.passed = metrics.passed
            report.status = self._status_text(metrics)
            report.heatmap_data = self._generate_heatmap_data(raw, metrics)
            report.histogram = self._compute_histogram(report.heatmap_data)

            octo.log_event("quality_engine", "analyse_ok", {
                "passed": metrics.passed,
                "cells": metrics.cells,
            })
        except Exception as exc:
            report.warnings.append(f"Quality analysis failed: {exc}")
            logger.warning("Quality analysis failed for %s: %s", case_dir, exc)

        return report

    def auto_fix(
        self, case_dir: Path | str,
        report: QualityReport | None = None,
        max_iterations: int = 3,
    ) -> QualityReport:
        """Intelligent auto-fix loop.

        Applies corrective actions sequentially:
        - Skewness > 0.9 → relax cell sizes (increase max, decrease min)
        - Non-ortho > 70 → disable boundary layers
        - Negative volume → regenerate mesh with larger min cell
        - Aspect ratio > 1000 → split by reducing max cell

        Args:
            case_dir: Case directory to fix.
            report: Optional initial quality report. Fresh analysis if None.
            max_iterations: Maximum fix iterations.

        Returns:
            ``QualityReport`` with fixes applied.
        """
        case_dir = Path(case_dir)
        if report is None:
            report = self.analyse(case_dir)

        octo.log_event("quality_engine", "auto_fix_start", {"iterations": max_iterations})

        for i in range(max_iterations):
            if report.passed:
                logger.info("Quality OK at iteration %d", i)
                break

            fixes = self._decide_fixes(report.metrics)
            if not fixes:
                logger.info("No applicable fix at iteration %d", i)
                break

            for fix in fixes:
                self._apply_fix(fix, case_dir)
                report.fixes_applied.append(fix)
                report.warnings.append(
                    f"Iteration {i + 1}: {fix.action} on {fix.target_metric} "
                    f"({fix.current_value:.2f})"
                )

            # _apply_fix only rewrites meshDict text; without re-running
            # cartesianMesh, analyse() below would just re-check the same
            # unchanged mesh on every iteration (same bug found and fixed in
            # commercial/optimizer.py's MeshOptimizer.optimize()).
            if not self._run_cartesian_mesh(case_dir):
                report.warnings.append(f"Iteration {i + 1}: cartesianMesh failed")
                break

            # Re-analyse after fix
            report = self.analyse(case_dir)
            if report.passed:
                break

        octo.log_event("quality_engine", "auto_fix_done", {
            "iterations": min(i + 1, max_iterations),
            "passed": report.passed,
            "fixes": len(report.fixes_applied),
        })
        return report

    def _run_cartesian_mesh(self, case_dir: Path) -> bool:
        """Run cartesianMesh synchronously so the fix loop actually re-meshes."""
        import subprocess
        from cfmesh_autogui.config import OFConfig

        try:
            cmd = OFConfig().build_command(Path(case_dir))
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

    # ------------------------------------------------------------------
    # checkMesh parsing
    # ------------------------------------------------------------------
    def _get_checkmesh_output(self, case_dir: Path) -> str:
        """Run checkMesh and return output."""
        import subprocess
        from cfmesh_autogui.config import OFConfig

        cfg = OFConfig()
        try:
            cmd = cfg.build_check_mesh_cmd(case_dir)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            raise RuntimeError("checkMesh timed out after 120s")
        except FileNotFoundError:
            raise RuntimeError("WSL not found for checkMesh")

    def _parse_metrics(self, raw: str) -> QualityMetrics:
        """Parse checkMesh output into QualityMetrics."""
        m = QualityMetrics()

        if re.search(r"FOAM FATAL|FATAL ERROR|--> FOAM FATAL", raw, re.IGNORECASE):
            m.has_fatal = True

        match = re.search(r"cells:\s+(\d+)", raw, re.IGNORECASE)
        if match: m.cells = int(match.group(1))

        match = re.search(r"Max non-orthogonality = ([\d.]+).*?average = ([\d.]+)", raw, re.DOTALL)
        if match:
            m.max_non_orthogonality = float(match.group(1))
            m.avg_non_orthogonality = float(match.group(2))

        match = re.search(r"Max skewness = ([\d.]+).*?average = ([\d.]+)", raw, re.DOTALL)
        if match:
            m.max_skewness = float(match.group(1))
            m.avg_skewness = float(match.group(2))

        match = re.search(r"Max aspect ratio = ([\d.]+)", raw)
        if match: m.max_aspect_ratio = float(match.group(1))

        match = re.search(rf"Min volume = ({QualityEngine._OF_FLOAT})", raw)
        if match: m.min_volume = float(match.group(1))

        match = re.search(r"There are (\d+).*?negative volume", raw, re.IGNORECASE)
        if match: m.neg_cells = int(match.group(1))

        return m

    @staticmethod
    def _status_text(m: QualityMetrics) -> str:
        if m.has_fatal: return "FATAL"
        if m.neg_cells > 0: return f"FAIL: {m.neg_cells} negative-volume cells"
        warns = []
        if m.max_skewness > THRESHOLDS["skewness_max"]:
            warns.append(f"skew={m.max_skewness:.2f}")
        if m.max_non_orthogonality > THRESHOLDS["non_ortho_max"]:
            warns.append(f"nonOrtho={m.max_non_orthogonality:.1f}")
        if m.max_aspect_ratio > THRESHOLDS["aspect_ratio_max"]:
            warns.append(f"aspect={m.max_aspect_ratio:.0f}")
        return "PASS" if not warns else "WARN: " + ", ".join(warns)

    # ------------------------------------------------------------------
    # Heatmap data
    # ------------------------------------------------------------------
    def _generate_heatmap_data(
        self, raw: str, metrics: QualityMetrics,
    ) -> dict[str, list[float]]:
        """Generate per-cell quality data for 3D heatmap colouring."""
        data: dict[str, list[float]] = {
            "skewness": [], "non_orthogonality": [], "aspect_ratio": [],
        }

        cell_pattern = re.compile(
            rf"Cell\s+(\d+):\s+skewness\s+({QualityEngine._OF_FLOAT}).*?"
            rf"non-ortho\s+({QualityEngine._OF_FLOAT}).*?"
            rf"aspect\s+({QualityEngine._OF_FLOAT})", re.IGNORECASE,
        )
        for m in cell_pattern.finditer(raw):
            try:
                data["skewness"].append(float(m.group(2)))
                data["non_orthogonality"].append(float(m.group(3)))
                data["aspect_ratio"].append(float(m.group(4)))
            except (ValueError, IndexError):
                continue

        # Fallback: use average metrics when per-cell data unavailable
        if not data["skewness"] and metrics.cells > 0:
            n = min(metrics.cells, 1000)
            data["skewness"] = [metrics.avg_skewness or metrics.max_skewness] * n
            data["non_orthogonality"] = [metrics.avg_non_orthogonality or metrics.max_non_orthogonality] * n
            data["aspect_ratio"] = [metrics.max_aspect_ratio] * n

        return data

    def _compute_histogram(self, heatmap: dict[str, list[float]],
                           bins: int = 20) -> dict[str, Any]:
        """Compute histogram from heatmap data."""
        skew = heatmap.get("skewness", [])
        if not skew:
            return {"counts": [], "edges": [], "mean": 0.0, "max": 0.0}

        import numpy as np
        arr = np.array(skew, dtype=np.float64)
        counts, edges = np.histogram(arr, bins=bins)
        return {
            "counts": counts.tolist(),
            "edges": edges.tolist(),
            "mean": round(float(arr.mean()), 4),
            "max": round(float(arr.max()), 4),
            "p95": round(float(np.percentile(arr, 95)), 4),
            "p99": round(float(np.percentile(arr, 99)), 4),
            "n": len(arr),
        }

    # ------------------------------------------------------------------
    # Auto-fix logic
    # ------------------------------------------------------------------
    def _decide_fixes(self, metrics: QualityMetrics) -> list[AutoFixAction]:
        """Decide which auto-fix actions to apply based on metrics."""
        fixes: list[AutoFixAction] = []
        thr = THRESHOLDS

        if metrics.max_skewness > thr["skewness_max"]:
            # Skewness: first try relaxing cell sizes (proportional to severity).
            # If BL is active, also consider reducing layers.
            severity = (metrics.max_skewness - thr["skewness_max"]) / thr["skewness_max"]
            relax_factor = 1.0 + min(severity * 0.5, 0.5)
            fixes.append(AutoFixAction(
                action="relax",
                target_metric="skewness",
                current_value=metrics.max_skewness,
                detail=f"Increase maxCell by {relax_factor:.0%}, decrease minCell by {relax_factor*0.5:.0%}",
            ))

        if metrics.max_non_orthogonality > thr["non_ortho_max"]:
            # Non-orthogonality: reduce BL layers first (preserves mesh near other walls),
            # only disable BL entirely as a second step if it recurs.
            fixes.append(AutoFixAction(
                action="reduce_bl",
                target_metric="non_orthogonality",
                current_value=metrics.max_non_orthogonality,
                detail="Halve BL nLayers and thicknessRatio",
            ))

        if metrics.neg_cells > thr["neg_vol_max"]:
            negatives = metrics.neg_cells
            if negatives <= 10:
                factor = 1.3
            else:
                factor = 1.5 + min(negatives * 0.01, 1.0)
            fixes.append(AutoFixAction(
                action="remesh",
                target_metric="neg_vol",
                current_value=float(negatives),
                detail=f"Coarsen cells by {factor:.0%}",
            ))

        if metrics.max_aspect_ratio > thr["aspect_ratio_max"]:
            severity = (metrics.max_aspect_ratio - thr["aspect_ratio_max"]) / thr["aspect_ratio_max"]
            reduction = 0.7 - min(severity * 0.1, 0.2)
            fixes.append(AutoFixAction(
                action="split",
                target_metric="aspect_ratio",
                current_value=metrics.max_aspect_ratio,
                detail=f"Reduce maxCellSize by {(1 - reduction):.0%}",
            ))
        return fixes

    def _apply_fix(self, fix: AutoFixAction, case_dir: Path) -> None:
        """Apply a single auto-fix action to the case."""
        meshdict_path = case_dir / "system" / "meshDict"
        if not meshdict_path.exists():
            logger.warning("meshDict not found at %s", meshdict_path)
            return

        text = meshdict_path.read_text(encoding="ascii", errors="replace")

        if fix.action == "relax":
            text = self._relax_cell_sizes(text, factor=1.2)
        elif fix.action == "reduce_bl":
            text = self._reduce_boundary_layers(text)
        elif fix.action == "disable_bl":
            text = self._disable_boundary_layers(text)
        elif fix.action == "remesh":
            text = self._coarsen_mesh(text, factor=1.5)
        elif fix.action == "split":
            text = self._reduce_max_cell(text, factor=0.7)

        meshdict_path.write_text(text, encoding="ascii")
        logger.info("Applied fix: %s on %s", fix.action, fix.target_metric)

    _OF_FLOAT = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"

    @staticmethod
    def _relax_cell_sizes(text: str, factor: float = 1.2) -> str:
        """Increase maxCell by *factor*, decrease minCell by *factor*^-1."""
        def _relax_max(m: re.Match) -> str:
            val = float(m.group(1)) * factor
            return f"maxCellSize {val:.6f};"
        def _relax_min(m: re.Match) -> str:
            val = float(m.group(1)) / factor
            return f"minCellSize {val:.6f};"
        text = re.sub(rf"maxCellSize\s+({QualityEngine._OF_FLOAT});", _relax_max, text)
        text = re.sub(rf"minCellSize\s+({QualityEngine._OF_FLOAT});", _relax_min, text)
        return text

    @staticmethod
    def _reduce_boundary_layers(text: str) -> str:
        """Halve BL nLayers and thicknessRatio to ease non-orthogonality."""
        def _halve_layers(m: re.Match) -> str:
            val = max(int(float(m.group(1)) // 2), 1)
            return f"            nLayers                 {val};"
        def _halve_growth(m: re.Match) -> str:
            val = max(float(m.group(1)) * 0.5, 1.01)
            return f"            thicknessRatio          {val:.4f};"
        text = re.sub(r"nLayers\s+(\d+);", _halve_layers, text)
        text = re.sub(rf"thicknessRatio\s+({QualityEngine._OF_FLOAT});", _halve_growth, text)
        return text

    @staticmethod
    def _disable_boundary_layers(text: str) -> str:
        """Remove boundaryLayers block from meshDict."""
        return re.sub(r"\nboundaryLayers\s*\{[^}]*\}", "", text, flags=re.DOTALL)

    @staticmethod
    def _coarsen_mesh(text: str, factor: float = 1.5) -> str:
        """Increase both cell sizes by *factor*."""
        text = re.sub(rf"maxCellSize\s+({QualityEngine._OF_FLOAT});", lambda m, f=factor: f"maxCellSize {float(m.group(1))*f:.6f};", text)
        text = re.sub(rf"minCellSize\s+({QualityEngine._OF_FLOAT});", lambda m, f=factor: f"minCellSize {float(m.group(1))*f:.6f};", text)
        return text

    @staticmethod
    def _reduce_max_cell(text: str, factor: float = 0.7) -> str:
        """Reduce maxCell by *factor*."""
        return re.sub(
            rf"maxCellSize\s+({QualityEngine._OF_FLOAT});",
            lambda m, f=factor: f"maxCellSize {float(m.group(1))*f:.6f};",
            text,
        )

    def export_json(self, report: QualityReport, path: Path | str) -> None:
        """Export quality report as JSON."""
        Path(path).write_text(json.dumps({
            "metrics": {
                "cells": report.metrics.cells,
                "max_skewness": report.metrics.max_skewness,
                "avg_skewness": report.metrics.avg_skewness,
                "max_non_orthogonality": report.metrics.max_non_orthogonality,
                "avg_non_orthogonality": report.metrics.avg_non_orthogonality,
                "max_aspect_ratio": report.metrics.max_aspect_ratio,
                "min_volume": report.metrics.min_volume,
                "neg_cells": report.metrics.neg_cells,
            },
            "passed": report.passed,
            "histogram": report.histogram,
            "fixes": [{"action": f.action, "target": f.target_metric,
                        "value": f.current_value} for f in report.fixes_applied],
        }, indent=2, default=str))


