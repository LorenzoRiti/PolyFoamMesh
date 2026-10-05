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


def test_stage_failure_normalises_timeout_sentinel() -> None:
    # _wsl_of_run returns the bare sentinel "TIMEOUT" on a stage-budget
    # overrun; without normalisation the composed string was
    # "snappyHexMesh failed: TIMEOUT", which matches neither "FATAL" nor
    # "timed out" in make_record's predicates (Task 2 review finding).
    assert rc._stage_failure("snappyHexMesh", "TIMEOUT") == "snappyHexMesh timed out"
    assert rc._stage_failure("blockMesh", "  TIMEOUT \n") == "blockMesh timed out"
    # Non-timeout output keeps the original "failed:" wording/tail.
    assert (
        rc._stage_failure("snappyHexMesh", "line1\nline2\nFOAM FATAL IO ERROR")
        == "snappyHexMesh failed: line1\nline2\nFOAM FATAL IO ERROR"
    )
    assert (
        rc._stage_failure("blockMesh", "a\nb\nc", tail=2)
        == "blockMesh failed: b\nc"
    )


def test_snappy_stage_timeout_sentinel_marks_record_timed_out(
    tmp_path, monkeypatch
) -> None:
    """End-to-end producer path: TIMEOUT sentinel -> timed_out record.

    Replays the thin_gap/snappy_expert stage timeout (snappyHexMesh hits its
    stage budget, _wsl_of_run returns (-1, "TIMEOUT")) without WSL, then
    normalises the bench dict exactly as run_single does.
    """
    import benchmarks.run_benchmarks as rb
    import benchmarks.snappy_baseline as sb

    def fake_prepare(stl_path, pipeline="hex", require_watertight=True):
        return None, {
            "name": stl_path.stem, "meshes": [], "bbox_dim": 1.0,
            "max_cell": 0.1, "min_cell": 0.01, "size_warnings": [],
        }

    def fake_wsl(cmd, timeout=180):
        if "snappyHexMesh" in cmd:
            return -1, "TIMEOUT"  # stage budget exhausted (SNAPPY_MESH_TIMEOUT_S)
        return 0, ""

    monkeypatch.setattr(rb, "_prepare_geometry", fake_prepare)
    monkeypatch.setattr(rb, "_wsl_of_run", fake_wsl)
    monkeypatch.setattr(rb, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(sb, "prepare_snappy_case", lambda *a, **k: None)

    bench = rc._run_snappy(tmp_path / "thin_gap.stl", "expert")

    assert bench["success"] is False
    assert bench["errors"] == ["snappyHexMesh timed out"]

    rec = rc._record_from_bench("thin_gap", "snappy_expert", bench)
    assert any("timed out" in e.lower() for e in rec["errors"])
    assert rec["timed_out"] is True
    assert "timed_out" in rec["failed_thresholds"]
    assert "fatal" in rec["failed_thresholds"]
    assert rec["thresholds_pass"] is False


def test_check_mesh_failure_string() -> None:
    # _run_check_mesh -> _wsl_of_run: -1 = stage-budget overrun, -2 = WSL
    # missing. Only the "<stage> timed out" wording trips the predicates.
    assert rc._check_mesh_failure(-1) == "checkMesh timed out"
    assert rc._check_mesh_failure(-2) == "WSL not found"
    assert rc._check_mesh_failure(7) == "checkMesh exit 7"


def test_check_mesh_timeout_marks_record_timed_out(tmp_path, monkeypatch) -> None:
    """checkMesh stage-budget overrun (rc == -1) must flip timed_out/fatal.

    Both in-file runners (_run_gmsh_direct, _run_snappy) appended the bare
    "checkMesh exit -1", which matches neither make_record's "FATAL"/"timed
    out" predicate nor _record_from_bench's timed_out scan — so the record
    kept timed_out=False on a real overrun (review finding F1, the class of
    bug the committed fast-tier baseline froze).
    """
    import benchmarks.run_benchmarks as rb
    import benchmarks.snappy_baseline as sb

    def fake_prepare(stl_path, pipeline="hex", require_watertight=True):
        return None, {
            "name": stl_path.stem, "meshes": [], "bbox_dim": 1.0,
            "max_cell": 0.1, "min_cell": 0.01, "size_warnings": [],
        }

    def fake_wsl(cmd, timeout=180):
        return 0, ""

    def fake_check(case_dir, timeout=120):
        return -1, "", 0.0  # CHECK_TIMEOUT_S exhausted inside _wsl_of_run

    monkeypatch.setattr(rb, "_prepare_geometry", fake_prepare)
    monkeypatch.setattr(rb, "_wsl_of_run", fake_wsl)
    monkeypatch.setattr(rb, "_run_check_mesh", fake_check)
    monkeypatch.setattr(rb, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(sb, "prepare_snappy_case", lambda *a, **k: None)

    snappy = rc._run_snappy(tmp_path / "thin_gap.stl", "base")
    assert snappy["errors"] == ["checkMesh timed out"]
    rec = rc._record_from_bench("thin_gap", "snappy_base", snappy)
    assert rec["timed_out"] is True
    assert "timed_out" in rec["failed_thresholds"]
    assert "fatal" in rec["failed_thresholds"]
    assert rec["thresholds_pass"] is False

    import polyfoammesh.core.gmsh_subprocess as gs

    monkeypatch.setattr(gs, "run_gmsh_volume", lambda *a, **k: None)
    gmsh = rc._run_gmsh_direct(tmp_path / "thin_gap.stl")
    assert gmsh["errors"] == ["checkMesh timed out"]
    rec = rc._record_from_bench("thin_gap", "gmsh_direct", gmsh)
    assert rec["timed_out"] is True
    assert "timed_out" in rec["failed_thresholds"]
    assert rec["thresholds_pass"] is False
