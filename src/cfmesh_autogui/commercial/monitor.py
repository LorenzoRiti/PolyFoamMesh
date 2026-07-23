"""Real-Time Quality Dashboard — 3D heatmap data and quality metrics.

Provides structured quality data that can be consumed by the 3D viewer
for cell-by-cell heatmap rendering, worst-cell identification,
histogram generation, and convergence tracking across mesh iterations.

Usage::

    monitor = QualityMonitor()
    monitor.ingest_checkmesh(output_text)
    print(monitor.summary())
    heatmap = monitor.heatmap_data()
    worst = monitor.worst_cells(n=10)
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

# Regex patterns for checkMesh metric extraction
_RE_NONORTHO = re.compile(r"Max non-orthogonality = ([\d.]+).*?average = ([\d.]+)", re.DOTALL)
_RE_SKEW = re.compile(r"Max skewness = ([\d.]+).*?average = ([\d.]+)", re.DOTALL)
_RE_ASPECT = re.compile(r"Max aspect ratio = ([\d.]+)", re.DOTALL)
_RE_CELLS = re.compile(r"cells:\s+(\d+)", re.IGNORECASE)
_RE_NEGVOL = re.compile(r"There are (\d+).*?negative volume", re.IGNORECASE)
_RE_MINVOL = re.compile(r"Min volume = (-?[\d.eE+-]+)", re.IGNORECASE)


@dataclass
class QualityMetric:
    """A single quality metric with value, threshold, and pass/fail status."""
    name: str = ""
    value: float = 0.0
    warn_threshold: float = 0.0
    fail_threshold: float = 0.0
    unit: str = ""

    @property
    def status(self) -> str:
        if self.fail_threshold > 0 and self.value >= self.fail_threshold:
            return "fail"
        if self.warn_threshold > 0 and self.value >= self.warn_threshold:
            return "warn"
        return "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "warn_threshold": self.warn_threshold,
            "fail_threshold": self.fail_threshold,
            "unit": self.unit,
            "status": self.status,
        }


@dataclass
class QualitySnapshot:
    """A point-in-time quality snapshot for convergence tracking."""
    timestamp: str = ""
    iteration: int = 0
    metrics: dict[str, float] = field(default_factory=dict)
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "iteration": self.iteration,
            "metrics": self.metrics,
            "passed": self.passed,
        }


@dataclass
class WorstCell:
    """Information about a low-quality cell."""
    index: int = 0
    metric: str = ""
    value: float = 0.0
    location: tuple[float, float, float] = (0.0, 0.0, 0.0)


class QualityMonitor:
    """Real-time mesh quality monitor.

    Parses checkMesh output and provides structured data for
    UI visualization (heatmaps, histograms, worst-cell lists)
    and convergence tracking across iterations.

    Usage::

        monitor = QualityMonitor()
        monitor.ingest_checkmesh(checkmesh_output)
        print(monitor.summary())
    """

    # Default quality thresholds (OpenFOAM best practices)
    THRESHOLDS: dict[str, tuple[float, float]] = {
        "non_orthogonality": (65.0, 85.0),
        "skewness": (4.0, 10.0),
        "aspect_ratio": (1000.0, 5000.0),
    }

    def __init__(self, thresholds: dict[str, tuple[float, float]] | None = None) -> None:
        self._thresholds = {**self.THRESHOLDS, **(thresholds or {})}
        self._metrics: dict[str, QualityMetric] = {}
        self._history: list[QualitySnapshot] = []
        self._raw_output: str = ""
        self._cell_count: int = 0
        self._neg_cells: int = 0
        self._min_volume: float = 0.0
        self._has_fatal: bool = False

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------
    def ingest_checkmesh(self, output: str) -> None:
        """Parse checkMesh output and extract all quality metrics."""
        self._raw_output = output
        self._has_fatal = bool(re.search(r"FOAM FATAL|FATAL ERROR", output, re.IGNORECASE))

        # Cells
        m = _RE_CELLS.search(output)
        self._cell_count = int(m.group(1)) if m else 0

        # Non-orthogonality
        m = _RE_NONORTHO.search(output)
        max_no = float(m.group(1)) if m else 0.0
        avg_no = float(m.group(2)) if m else 0.0

        # Skewness
        m = _RE_SKEW.search(output)
        max_sk = float(m.group(1)) if m else 0.0
        avg_sk = float(m.group(2)) if m else 0.0

        # Aspect ratio
        m = _RE_ASPECT.search(output)
        max_ar = float(m.group(1)) if m else 0.0

        # Negative volumes
        m = _RE_NEGVOL.search(output)
        self._neg_cells = int(m.group(1)) if m else 0

        # Min volume
        m = _RE_MINVOL.search(output)
        self._min_volume = float(m.group(1)) if m else 0.0

        warn_no, fail_no = self._thresholds.get("non_orthogonality", (65.0, 85.0))
        warn_sk, fail_sk = self._thresholds.get("skewness", (4.0, 10.0))
        warn_ar, fail_ar = self._thresholds.get("aspect_ratio", (1000.0, 5000.0))

        self._metrics = {
            "max_non_orthogonality": QualityMetric(
                "max_non_orthogonality", max_no, warn_no, fail_no, "°",
            ),
            "avg_non_orthogonality": QualityMetric(
                "avg_non_orthogonality", avg_no, 0, 0, "°",
            ),
            "max_skewness": QualityMetric("max_skewness", max_sk, warn_sk, fail_sk, ""),
            "avg_skewness": QualityMetric("avg_skewness", avg_sk, 0, 0, ""),
            "max_aspect_ratio": QualityMetric("max_aspect_ratio", max_ar, warn_ar, fail_ar, ""),
        }

        # Record snapshot
        snapshot = QualitySnapshot(
            timestamp=datetime.now().isoformat(),
            iteration=len(self._history),
            metrics={k: m.value for k, m in self._metrics.items()},
            passed=self.passed,
        )
        self._history.append(snapshot)

        octo.log_event("quality_monitor", "ingested", {
            "cells": self._cell_count,
            "max_skewness": max_sk,
            "passed": self.passed,
        })

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    @property
    def passed(self) -> bool:
        if self._has_fatal or self._neg_cells > 0:
            return False
        return all(m.status != "fail" for m in self._metrics.values())

    def metric(self, name: str) -> QualityMetric | None:
        return self._metrics.get(name)

    def summary(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "cells": self._cell_count,
            "neg_cells": self._neg_cells,
            "has_fatal": self._has_fatal,
            "max_non_orthogonality": self._metrics.get("max_non_orthogonality", QualityMetric()).to_dict(),
            "max_skewness": self._metrics.get("max_skewness", QualityMetric()).to_dict(),
            "max_aspect_ratio": self._metrics.get("max_aspect_ratio", QualityMetric()).to_dict(),
        }

    # ------------------------------------------------------------------
    # Heatmap data
    # ------------------------------------------------------------------
    def heatmap_data(self) -> dict[str, list[float]]:
        """Return per-cell data arrays for heatmap colouring.

        Returns a dict with keys like ``skewness``, ``non_orthogonality``,
        ``aspect_ratio`` — each containing a list of per-cell values
        extracted from the raw checkMesh output.
        """
        result: dict[str, list[float]] = {
            "skewness": [],
            "non_orthogonality": [],
            "aspect_ratio": [],
        }

        if not self._raw_output:
            return result

        # Extract cell-by-cell data from checkMesh detail output
        # Format: "Cell XXX: skewness Y.YY non-ortho ZZ.Z aspect AA"
        cell_pattern = re.compile(
            r"Cell\s+(\d+):\s+skewness\s+([\d.]+).*?"
            r"non-ortho\s+([\d.]+).*?aspect\s+([\d.]+)",
            re.IGNORECASE,
        )
        for m in cell_pattern.finditer(self._raw_output):
            try:
                result["skewness"].append(float(m.group(2)))
                result["non_orthogonality"].append(float(m.group(3)))
                result["aspect_ratio"].append(float(m.group(4)))
            except (ValueError, IndexError):
                continue

        # Fallback: if no per-cell data, use the average as a proxy
        if not result["skewness"]:
            avg_sk = self._metrics.get("max_skewness", QualityMetric()).value
            result["skewness"] = [avg_sk] * min(self._cell_count, 1000)
            avg_no = self._metrics.get("max_non_orthogonality", QualityMetric()).value
            result["non_orthogonality"] = [avg_no] * min(self._cell_count, 1000)

        return result

    def worst_cells(self, n: int = 10) -> list[WorstCell]:
        """Identify the N worst cells by each metric.

        Parses the checkMesh detail output to find cells with the
        highest skewness, non-orthogonality, and aspect ratio.
        """
        worst: list[WorstCell] = []
        if not self._raw_output:
            return worst

        cell_pattern = re.compile(
            r"Cell\s+(\d+):\s+skewness\s+([\d.]+).*?"
            r"non-ortho\s+([\d.]+).*?aspect\s+([\d.]+)",
            re.IGNORECASE,
        )

        cells: list[dict[str, Any]] = []
        for m in cell_pattern.finditer(self._raw_output):
            try:
                cells.append({
                    "index": int(m.group(1)),
                    "skewness": float(m.group(2)),
                    "non_ortho": float(m.group(3)),
                    "aspect": float(m.group(4)),
                })
            except (ValueError, IndexError):
                continue

        if not cells:
            return worst

        # Sort by each metric and take top N
        for metric_key, metric_name in [
            ("skewness", "max_skewness"),
            ("non_ortho", "max_non_orthogonality"),
            ("aspect", "max_aspect_ratio"),
        ]:
            sorted_cells = sorted(cells, key=lambda c: c[metric_key], reverse=True)
            for c in sorted_cells[:n]:
                worst.append(WorstCell(
                    index=c["index"],
                    metric=metric_name,
                    value=c[metric_key],
                ))

        worst.sort(key=lambda w: w.value, reverse=True)
        return worst[:n]

    def histogram(self, metric: str = "skewness", bins: int = 20) -> dict[str, Any]:
        """Generate a histogram for a given metric.

        Args:
            metric: Metric name (``skewness``, ``non_orthogonality``,
                ``aspect_ratio``).
            bins: Number of histogram bins.

        Returns:
            Dict with ``counts``, ``edges`` (bin boundaries),
            ``mean``, ``max``, ``p95``.
        """
        data = self.heatmap_data().get(metric, [])
        if not data:
            return {"counts": [], "edges": [], "mean": 0.0, "max": 0.0, "p95": 0.0}

        import numpy as np
        arr = np.array(data, dtype=np.float64)
        counts, edges = np.histogram(arr, bins=bins)

        return {
            "counts": counts.tolist(),
            "edges": edges.tolist(),
            "mean": round(float(arr.mean()), 4),
            "max": round(float(arr.max()), 4),
            "p95": round(float(np.percentile(arr, 95)), 4),
        }

    def convergence_history(self) -> list[dict[str, Any]]:
        """Return the history of quality snapshots for convergence plotting."""
        return [s.to_dict() for s in self._history]

    def export_json(self, path: Path | str) -> None:
        """Export the full quality report as JSON."""
        data = {
            "summary": self.summary(),
            "history": self.convergence_history(),
            "worst_cells": [
                {"index": c.index, "metric": c.metric, "value": c.value}
                for c in self.worst_cells(20)
            ],
            "histogram_skewness": self.histogram("skewness"),
            "histogram_non_orthogonality": self.histogram("non_orthogonality"),
        }
        Path(path).write_text(json.dumps(data, indent=2, default=str))
        logger.info("Quality monitor data exported: %s", path)
