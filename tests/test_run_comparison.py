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


def test_poly_sliver_dissolve_defaults_off() -> None:
    opts = rc._build_parser().parse_args(["--tier", "fast"])
    assert opts.poly_sliver_dissolve == 0.0


def test_poly_sliver_dissolve_forwarded_to_child(tmp_path, monkeypatch) -> None:
    """run_parent(..., poly_sliver_dissolve=0.25) appends the flag to every child cmd."""
    seen: list[list[str]] = []

    def fake_run(cmd, timeout=None, **kw):
        seen.append(list(cmd))

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(rc.subprocess, "run", fake_run)

    rc.run_parent(
        tier="fast", runners=["hex"], geometries=["cylinder"],
        poly_sliver_dissolve=0.25, _child_out_dir=tmp_path, _write_results=False,
    )
    assert seen
    for cmd in seen:
        i = cmd.index("--poly-sliver-dissolve")
        assert cmd[i + 1] == "0.25"

    # Default 0.0 must not change any existing behaviour: flag not appended.
    seen.clear()
    rc.run_parent(
        tier="fast", runners=["hex"], geometries=["cylinder"],
        _child_out_dir=tmp_path, _write_results=False,
    )
    assert seen
    assert all("--poly-sliver-dissolve" not in cmd for cmd in seen)


def test_run_single_passes_dissolve_to_poly(tmp_path, monkeypatch) -> None:
    """The child's poly branch threads poly_sliver_dissolve into the runner."""
    import benchmarks.run_benchmarks as rb

    seen_kwargs: dict = {}

    def fake_poly(stl_path, **kw):
        seen_kwargs.update(kw)
        return {
            "geometry": Path(stl_path).stem, "success": True,
            "wall_time_s": 1.0, "cell_count": 1000, "max_non_ortho": 40.0,
            "avg_non_ortho": 10.0, "max_skewness": 1.2,
            "max_aspect_ratio": 25.0, "neg_cells": 0, "min_volume": 1e-5,
            "errors": [], "warnings": [],
        }

    monkeypatch.setattr(rb, "_benchmark_poly_one", fake_poly)
    rc.run_single(
        "poly", tmp_path / "cylinder.stl", tmp_path / "out.json",
        poly_sliver_dissolve=0.25,
    )
    assert seen_kwargs["sliver_dissolve"] == 0.25


def test_poly_bl_min_face_defaults_off() -> None:
    opts = rc._build_parser().parse_args(["--tier", "fast"])
    assert opts.poly_bl_min_face == 0.0


def test_poly_bl_min_face_forwarded_to_child(tmp_path, monkeypatch) -> None:
    """run_parent(..., poly_bl_min_face=0.2) appends the flag to every child cmd."""
    seen: list[list[str]] = []

    def fake_run(cmd, timeout=None, **kw):
        seen.append(list(cmd))

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(rc.subprocess, "run", fake_run)

    rc.run_parent(
        tier="fast", runners=["hex"], geometries=["cylinder"],
        poly_bl_min_face=0.2, _child_out_dir=tmp_path, _write_results=False,
    )
    assert seen
    for cmd in seen:
        i = cmd.index("--poly-bl-min-face")
        assert cmd[i + 1] == "0.2"

    # Default 0.0 must not change any existing behaviour: flag not appended.
    seen.clear()
    rc.run_parent(
        tier="fast", runners=["hex"], geometries=["cylinder"],
        _child_out_dir=tmp_path, _write_results=False,
    )
    assert seen
    assert all("--poly-bl-min-face" not in cmd for cmd in seen)


def test_run_single_passes_bl_min_face_to_poly(tmp_path, monkeypatch) -> None:
    """The child's poly branch threads poly_bl_min_face into the runner."""
    import benchmarks.run_benchmarks as rb

    seen_kwargs: dict = {}

    def fake_poly(stl_path, **kw):
        seen_kwargs.update(kw)
        return {
            "geometry": Path(stl_path).stem, "success": True,
            "wall_time_s": 1.0, "cell_count": 1000, "max_non_ortho": 40.0,
            "avg_non_ortho": 10.0, "max_skewness": 1.2,
            "max_aspect_ratio": 25.0, "neg_cells": 0, "min_volume": 1e-5,
            "errors": [], "warnings": [],
        }

    monkeypatch.setattr(rb, "_benchmark_poly_one", fake_poly)
    rc.run_single(
        "poly", tmp_path / "cylinder.stl", tmp_path / "out.json",
        poly_bl_min_face=0.2,
    )
    assert seen_kwargs["bl_min_face"] == 0.2


def test_poly_dual_config_defaults() -> None:
    """The five dual-config opts default to the converter's own defaults."""
    opts = rc._build_parser().parse_args(["--tier", "fast"])
    assert opts.poly_dual_smooth_iters == 3
    assert opts.poly_dual_compact is False
    assert opts.poly_dual_axis is False
    assert opts.poly_dual_planar_fix == "none"
    assert opts.poly_dual_worst_aspect == 0.0


def test_poly_dual_config_non_default_combo_forwarded_to_child(
    tmp_path, monkeypatch
) -> None:
    """run_parent(...non-default dual combo...) appends every flag, right value."""
    seen: list[list[str]] = []

    def fake_run(cmd, timeout=None, **kw):
        seen.append(list(cmd))

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(rc.subprocess, "run", fake_run)

    rc.run_parent(
        tier="fast", runners=["hex"], geometries=["cylinder"],
        poly_dual_smooth_iters=10, poly_dual_compact=True, poly_dual_axis=True,
        poly_dual_planar_fix="triangulate", poly_dual_worst_aspect=5000.0,
        _child_out_dir=tmp_path, _write_results=False,
    )
    assert seen
    for cmd in seen:
        i = cmd.index("--poly-dual-smooth-iters")
        assert cmd[i + 1] == "10"
        assert "--poly-dual-compact" in cmd
        assert "--poly-dual-axis" in cmd
        j = cmd.index("--poly-dual-planar-fix")
        assert cmd[j + 1] == "triangulate"
        k = cmd.index("--poly-dual-worst-aspect")
        assert cmd[k + 1] == "5000.0"

    # Defaults must not change any existing behaviour: no flag appended.
    seen.clear()
    rc.run_parent(
        tier="fast", runners=["hex"], geometries=["cylinder"],
        _child_out_dir=tmp_path, _write_results=False,
    )
    assert seen
    for flag in ("--poly-dual-smooth-iters", "--poly-dual-compact",
                 "--poly-dual-axis", "--poly-dual-planar-fix",
                 "--poly-dual-worst-aspect"):
        assert all(flag not in cmd for cmd in seen)


def test_run_single_passes_dual_config_to_poly(tmp_path, monkeypatch) -> None:
    """The child's poly branch threads the five dual opts into the runner."""
    import benchmarks.run_benchmarks as rb

    seen_kwargs: dict = {}

    def fake_poly(stl_path, **kw):
        seen_kwargs.update(kw)
        return {
            "geometry": Path(stl_path).stem, "success": True,
            "wall_time_s": 1.0, "cell_count": 1000, "max_non_ortho": 40.0,
            "avg_non_ortho": 10.0, "max_skewness": 1.2,
            "max_aspect_ratio": 25.0, "neg_cells": 0, "min_volume": 1e-5,
            "errors": [], "warnings": [],
        }

    monkeypatch.setattr(rb, "_benchmark_poly_one", fake_poly)
    rc.run_single(
        "poly", tmp_path / "cylinder.stl", tmp_path / "out.json",
        poly_dual_smooth_iters=10, poly_dual_compact=True, poly_dual_axis=True,
        poly_dual_planar_fix="planarize", poly_dual_worst_aspect=5000.0,
    )
    assert seen_kwargs["dual_smooth_iters"] == 10
    assert seen_kwargs["dual_compact"] is True
    assert seen_kwargs["dual_axis"] is True
    assert seen_kwargs["dual_planar_fix"] == "planarize"
    assert seen_kwargs["dual_worst_aspect"] == 5000.0
