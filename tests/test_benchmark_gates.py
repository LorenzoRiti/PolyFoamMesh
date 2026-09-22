from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.gates import passes_absolute_thresholds  # noqa: E402


def _good() -> dict:
    return {
        "cell_count": 1000, "max_non_ortho": 40.0, "max_skewness": 1.2,
        "max_aspect_ratio": 25.0, "neg_cells": 0,
        "timed_out": False, "fatal": False,
    }


def test_good_metrics_pass() -> None:
    ok, failed = passes_absolute_thresholds(_good())
    assert ok is True
    assert failed == []


def test_each_threshold_failure_is_reported_by_name() -> None:
    m = _good()
    m.update(max_non_ortho=70.5, max_skewness=4.1, max_aspect_ratio=1001.0)
    ok, failed = passes_absolute_thresholds(m)
    assert ok is False
    assert set(failed) == {"non_ortho_max", "skewness_max", "aspect_ratio_max"}


def test_thresholds_come_from_core_quality_thresholds() -> None:
    from polyfoammesh.core.quality_thresholds import THRESHOLDS
    m = _good()
    m["max_non_ortho"] = THRESHOLDS["non_ortho_max"] + 0.5
    ok, failed = passes_absolute_thresholds(m)
    assert not ok and "non_ortho_max" in failed
    m["max_non_ortho"] = THRESHOLDS["non_ortho_max"]  # boundary: <= passes
    ok, _ = passes_absolute_thresholds(m)
    assert ok is True


def test_neg_cells_and_timeout_and_fatal_fail() -> None:
    m = _good(); m["neg_cells"] = 3
    ok, failed = passes_absolute_thresholds(m)
    assert not ok and failed == ["neg_cells_zero"]
    m = _good(); m["timed_out"] = True
    ok, failed = passes_absolute_thresholds(m)
    assert not ok and failed == ["timed_out"]
    m = _good(); m["fatal"] = True
    ok, failed = passes_absolute_thresholds(m)
    assert not ok and failed == ["fatal"]


def test_zero_cells_fails() -> None:
    m = _good(); m["cell_count"] = 0
    ok, failed = passes_absolute_thresholds(m)
    assert not ok and failed == ["cells_produced"]
