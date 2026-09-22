"""Absolute quality thresholds for the comparison harness.

Numbers come from polyfoammesh.core.quality_thresholds.THRESHOLDS (single
source of truth) plus neg_cells == 0 and cell_count > 0 (spec: victory =
absolute thresholds AND equal-or-better vs snappy).
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from polyfoammesh.core.quality_thresholds import THRESHOLDS  # noqa: E402


def passes_absolute_thresholds(metrics: dict) -> tuple[bool, list[str]]:
    failed: list[str] = []
    if metrics.get("timed_out"):
        failed.append("timed_out")
    if metrics.get("fatal"):
        failed.append("fatal")
    if int(metrics.get("cell_count", 0)) <= 0:
        failed.append("cells_produced")
    if int(metrics.get("neg_cells", 0)) != 0:
        failed.append("neg_cells_zero")
    if float(metrics.get("max_non_ortho", 0.0)) > THRESHOLDS["non_ortho_max"]:
        failed.append("non_ortho_max")
    if float(metrics.get("max_skewness", 0.0)) > THRESHOLDS["skewness_max"]:
        failed.append("skewness_max")
    if float(metrics.get("max_aspect_ratio", 0.0)) > THRESHOLDS["aspect_ratio_max"]:
        failed.append("aspect_ratio_max")
    return (not failed, failed)
