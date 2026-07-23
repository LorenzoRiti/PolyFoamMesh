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

        match = re.search(r"Min volume = (-?[\d.eE+-]+)", raw)
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
            r"Cell\s+(\d+):\s+skewness\s+([\d.]+).*?"
            r"non-ortho\s+([\d.]+).*?aspect\s+([\d.]+)", re.IGNORECASE,
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
            fixes.append(AutoFixAction(
                action="relax",
                target_metric="skewness",
                current_value=metrics.max_skewness,
                detail="Increase maxCell, decrease minCell by 20%",
            ))
        if metrics.max_non_orthogonality > thr["non_ortho_max"]:
            fixes.append(AutoFixAction(
                action="disable_bl",
                target_metric="non_orthogonality",
                current_value=metrics.max_non_orthogonality,
                detail="Disable boundary layers and regenerate",
            ))
        if metrics.neg_cells > thr["neg_vol_max"]:
            fixes.append(AutoFixAction(
                action="remesh",
                target_metric="neg_vol",
                current_value=float(metrics.neg_cells),
                detail="Regenerate with coarser cells",
            ))
        if metrics.max_aspect_ratio > thr["aspect_ratio_max"]:
            fixes.append(AutoFixAction(
                action="split",
                target_metric="aspect_ratio",
                current_value=metrics.max_aspect_ratio,
                detail="Reduce maxCellSize by 30%",
            ))
        return fixes

    def _apply_fix(self, fix: AutoFixAction, case_dir: Path) -> None:
        """Apply a single auto-fix action to the case."""
        import json
        meshdict_path = case_dir / "system" / "meshDict"
        if not meshdict_path.exists():
            logger.warning("meshDict not found at %s", meshdict_path)
            return

        text = meshdict_path.read_text(encoding="ascii", errors="replace")

        if fix.action == "relax":
            text = self._relax_cell_sizes(text)
        elif fix.action == "disable_bl":
            text = self._disable_boundary_layers(text)
        elif fix.action == "remesh":
            text = self._coarsen_mesh(text)
        elif fix.action == "split":
            text = self._reduce_max_cell(text)

        meshdict_path.write_text(text, encoding="ascii")
        logger.info("Applied fix: %s on %s", fix.action, fix.target_metric)

    @staticmethod
    def _relax_cell_sizes(text: str) -> str:
        """Increase maxCell by 20%, decrease minCell by 20%."""
        def _relax_max(m: re.Match) -> str:
            val = float(m.group(1)) * 1.2
            return f"maxCellSize {val:.6f};"
        def _relax_min(m: re.Match) -> str:
            val = float(m.group(1)) * 0.8
            return f"minCellSize {val:.6f};"
        text = re.sub(r"maxCellSize\s+([\d.]+);", _relax_max, text)
        text = re.sub(r"minCellSize\s+([\d.]+);", _relax_min, text)
        return text

    @staticmethod
    def _disable_boundary_layers(text: str) -> str:
        """Remove boundaryLayers block from meshDict."""
        return re.sub(r"\nboundaryLayers\s*\{[^}]*\}", "", text, flags=re.DOTALL)

    @staticmethod
    def _coarsen_mesh(text: str) -> str:
        """Increase both cell sizes by 50%."""
        text = re.sub(r"maxCellSize\s+([\d.]+);", lambda m: f"maxCellSize {float(m.group(1))*1.5:.6f};", text)
        text = re.sub(r"minCellSize\s+([\d.]+);", lambda m: f"minCellSize {float(m.group(1))*1.5:.6f};", text)
        return text

    @staticmethod
    def _reduce_max_cell(text: str) -> str:
        """Reduce maxCell by 30%."""
        return re.sub(
            r"maxCellSize\s+([\d.]+);",
            lambda m: f"maxCellSize {float(m.group(1))*0.7:.6f};",
            text,
        )

    # ------------------------------------------------------------------
    # PDF export
    # ------------------------------------------------------------------
    def export_pdf(self, report: QualityReport, output_path: Path | str) -> None:
        """Export quality report as PDF."""
        output_path = Path(output_path)
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib import colors
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import mm

            c = canvas.Canvas(str(output_path), pagesize=A4)
            width, height = A4
            margin = 20 * mm
            y = height - margin
            m = report.metrics

            # Title
            c.setFont("Helvetica-Bold", 18)
            c.drawString(margin, y, "Mesh Quality Report")
            y -= 10 * mm
            c.setFont("Helvetica", 10)
            c.drawString(margin, y, f"Generated: {datetime.now():%Y-%m-%d %H:%M}")
            y -= 8 * mm

            # Status
            c.setFont("Helvetica-Bold", 14)
            status_color = colors.green if m.passed else colors.red
            c.setFillColor(status_color)
            c.drawString(margin, y, f"Status: {'PASS' if m.passed else 'FAIL'}")
            c.setFillColor(colors.black)
            y -= 10 * mm

            # Metrics table
            c.setFont("Helvetica-Bold", 12)
            c.drawString(margin, y, "Quality Metrics")
            y -= 6 * mm
            c.setFont("Helvetica", 10)

            metrics_rows = [
                ("Cells", f"{m.cells:,}", ""),
                ("Max Skewness", f"{m.max_skewness:.2f}",
                 "PASS" if m.max_skewness <= THRESHOLDS["skewness_max"] else "FAIL"),
                ("Max Non-Orthogonality", f"{m.max_non_orthogonality:.1f}°",
                 "PASS" if m.max_non_orthogonality <= THRESHOLDS["non_ortho_max"] else "FAIL"),
                ("Max Aspect Ratio", f"{m.max_aspect_ratio:.0f}",
                 "PASS" if m.max_aspect_ratio <= THRESHOLDS["aspect_ratio_max"] else "FAIL"),
                ("Min Volume", f"{m.min_volume:.6e}", ""),
                ("Negative Cells", f"{m.neg_cells}",
                 "OK" if m.neg_cells == 0 else "FAIL"),
            ]
            for label, value, status in metrics_rows:
                c.drawString(margin + 5 * mm, y, label)
                c.drawString(margin + 60 * mm, y, value)
                if status:
                    c.setFillColor(colors.green if status in ("PASS", "OK") else colors.red)
                    c.drawString(margin + 110 * mm, y, status)
                    c.setFillColor(colors.black)
                y -= 5 * mm

            y -= 5 * mm

            # Auto-fix summary
            if report.fixes_applied:
                c.setFont("Helvetica-Bold", 12)
                c.drawString(margin, y, "Auto-Fix Actions")
                y -= 6 * mm
                c.setFont("Helvetica", 9)
                for fix in report.fixes_applied:
                    c.drawString(margin + 5 * mm, y,
                                 f"• {fix.action}: {fix.target_metric} ({fix.current_value:.2f})")
                    y -= 4 * mm
                    if y < margin:
                        c.showPage()
                        y = height - margin

            # Histogram data
            if report.histogram and report.histogram.get("counts"):
                y -= 5 * mm
                c.setFont("Helvetica-Bold", 12)
                c.drawString(margin, y, "Skewness Distribution")
                y -= 6 * mm
                c.setFont("Helvetica", 9)
                h = report.histogram
                c.drawString(margin, y, f"Mean: {h['mean']:.4f}  |  "
                             f"P95: {h['p95']:.4f}  |  P99: {h['p99']:.4f}  |  "
                             f"Max: {h['max']:.4f}")

            c.save()
            logger.info("PDF quality report exported: %s", output_path)

        except ImportError:
            # Fallback: export as JSON
            json_path = output_path.with_suffix(".json")
            json_path.write_text(json.dumps({
                "metrics": {
                    "cells": m.cells,
                    "max_skewness": m.max_skewness,
                    "max_non_orthogonality": m.max_non_orthogonality,
                    "max_aspect_ratio": m.max_aspect_ratio,
                    "neg_cells": m.neg_cells,
                },
                "passed": m.passed,
                "histogram": report.histogram,
                "fixes": [f.__dict__ for f in report.fixes_applied],
            }, indent=2))
            logger.info("JSON quality report exported: %s", json_path)

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
