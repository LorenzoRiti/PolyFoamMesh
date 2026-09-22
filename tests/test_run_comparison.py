"""Record schema, thresholds wiring, child CLI — no WSL."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks import run_comparison as rc  # noqa: E402


def _metrics(**over) -> dict:
    m = {
        "cell_count": 1000, "max_non_ortho": 40.0, "avg_non_ortho": 10.0,
        "max_skewness": 1.2, "max_aspect_ratio": 25.0, "neg_cells": 0,
        "min_volume": 1e-5,
    }
    m.update(over)
    return m


def test_make_record_passing() -> None:
    rec = rc.make_record("cylinder", "hex", success=True, wall_time_s=1.5,
                         metrics=_metrics(), errors=[], warnings=[])
    assert rec["geometry"] == "cylinder"
    assert rec["runner"] == "hex"
    assert rec["thresholds_pass"] is True
    assert rec["failed_thresholds"] == []
    assert rec["success"] is True
    assert rec["timed_out"] is False


def test_make_record_fails_on_bad_metrics() -> None:
    rec = rc.make_record("slot1", "poly", success=True, wall_time_s=9.0,
                         metrics=_metrics(neg_cells=2, max_skewness=5.0),
                         errors=[], warnings=[])
    assert rec["thresholds_pass"] is False
    assert set(rec["failed_thresholds"]) == {"neg_cells_zero", "skewness_max"}
    assert rec["success"] is False


def test_make_record_timeout_fails_threshold() -> None:
    rec = rc.make_record("venturi", "gmsh_direct", success=False, wall_time_s=1500.0,
                         metrics=_metrics(cell_count=0), errors=["gmsh timed out"],
                         warnings=[], timed_out=True)
    assert rec["thresholds_pass"] is False
    assert "timed_out" in rec["failed_thresholds"]


def test_make_record_fatal_error_detected() -> None:
    rec = rc.make_record("x", "hex", success=False, wall_time_s=0.0,
                         metrics=_metrics(), errors=["checkMesh FATAL IO ERROR"], warnings=[])
    assert rec["thresholds_pass"] is False
    assert "fatal" in rec["failed_thresholds"]


def test_single_cli_rejects_unknown_runner() -> None:
    with pytest.raises(SystemExit):
        rc._build_parser().parse_args(
            ["--single", "--runner", "starccm", "--geometry", "cylinder", "--out", "x.json"]
        )


def test_record_is_json_serialisable() -> None:
    rec = rc.make_record("g", "hex", success=True, wall_time_s=0.1,
                         metrics=_metrics(), errors=[], warnings=[])
    json.dumps(rec)
