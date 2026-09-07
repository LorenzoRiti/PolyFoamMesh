"""Hermetic tests for the poly-pipeline benchmark extension.

Covers what can be tested without WSL/OpenFOAM: pipeline selection, poly gate
evaluation (including the defect-allowance regression gate), result
serialisation, baseline allowance loading and the shared geometry prep.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks import run_benchmarks as rb  # noqa: E402


# ---------------------------------------------------------------------------
# Pipeline selection
# ---------------------------------------------------------------------------

def test_pipeline_flag_defaults_to_hex() -> None:
    parser = rb._build_parser()
    assert parser.parse_args([]).pipeline == "hex"
    assert parser.parse_args(["--pipeline", "hex"]).pipeline == "hex"
    assert parser.parse_args(["--pipeline", "poly"]).pipeline == "poly"
    assert parser.parse_args(["--pipeline", "all"]).pipeline == "all"


def test_pipeline_flag_rejects_unknown() -> None:
    parser = rb._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--pipeline", "tet"])


# ---------------------------------------------------------------------------
# Defect allowance / gate evaluation
# ---------------------------------------------------------------------------

def test_defect_tolerance() -> None:
    assert rb._defect_tolerance(0) == 1
    assert rb._defect_tolerance(1) == 1
    assert rb._defect_tolerance(10) == 1
    assert rb._defect_tolerance(100) == 5
    assert rb._defect_tolerance(852) == 42


def _poly_result(**overrides) -> dict:
    base = dict(
        geometry="pipe",
        success=True,
        wall_time_s=10.0,
        tet_count=1000,
        poly_count=200,
        bl_prism_cells=50,
        bl_thickness=0.01,
        defect_count=5,
        defect_allowance=5,
        cell_count=200,
        max_non_ortho=40.0,
        avg_non_ortho=10.0,
        max_skewness=1.0,
        max_aspect_ratio=3.0,
        neg_cells=0,
        min_volume=1e-6,
    )
    base.update(overrides)
    return rb._make_poly_result(**base)


def test_poly_result_passes_when_within_allowance() -> None:
    r = _poly_result()
    assert r["success"] is True
    assert r["failed_gates"] == []
    assert r["pipeline"] == "poly"
    assert r["reduction_ratio"] == pytest.approx(5.0)
    assert r["hard_gates"]["defects_within_allowance"] is True
    assert r["hard_gates"]["bl_produced"] is True


def test_poly_result_fails_when_defects_regress_beyond_allowance() -> None:
    # 20 defects vs allowance 5 + tolerance 1 -> regression must fail.
    r = _poly_result(defect_count=20)
    assert r["success"] is False
    assert "defects_within_allowance" in r["failed_gates"]


def test_poly_result_tolerance_absorbs_small_noise() -> None:
    # allowance 100 -> tolerance 5; 104 is within, 106 is not.
    assert _poly_result(defect_count=104, defect_allowance=100)["success"] is True
    assert _poly_result(defect_count=106, defect_allowance=100)["success"] is False


def test_poly_result_no_allowance_does_not_bind() -> None:
    # First run / no baseline yet: the defect gate must not fail the case.
    r = _poly_result(defect_allowance=None, defect_count=999)
    assert r["hard_gates"]["defects_within_allowance"] is True
    assert r["success"] is True


def test_poly_result_missing_bl_fails_bl_produced() -> None:
    r = _poly_result(bl_prism_cells=0, bl_thickness=0.0)
    assert r["success"] is False
    assert "bl_produced" in r["failed_gates"]


def test_poly_result_hex_gates_still_apply() -> None:
    r = _poly_result(max_non_ortho=80.0)
    assert "max_non_ortho_below_70" in r["failed_gates"]
    r = _poly_result(neg_cells=3)
    assert "neg_cells_zero" in r["failed_gates"]
    r = _poly_result(max_skewness=9.0)
    assert "max_skewness_below_4" in r["failed_gates"]
    r = _poly_result(errors=["boom"])
    assert "no_fatal_errors" in r["failed_gates"]


def test_poly_result_serialisation_roundtrip() -> None:
    r = _poly_result()
    text = json.dumps(r, indent=2, default=str)
    loaded = json.loads(text)
    assert loaded["geometry"] == "pipe"
    assert loaded["pipeline"] == "poly"
    assert loaded["stage_times"] == {}
    assert loaded["stages"] == {}
    assert loaded["defect_breakdown"] == {}
    assert loaded["defect_allowance"] == 5
    assert loaded["hard_gates"]["defects_within_allowance"] is True


# ---------------------------------------------------------------------------
# Baseline allowance loading
# ---------------------------------------------------------------------------

def test_load_poly_allowances_missing_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rb, "POLY_BASELINE", tmp_path / "nope.json")
    assert rb._load_poly_allowances() == {}


def test_load_poly_allowances_reads_defect_counts(tmp_path, monkeypatch) -> None:
    baseline = tmp_path / "BASELINE_POLY.json"
    baseline.write_text(json.dumps([
        {"geometry": "pipe", "defect_count": 3},
        {"geometry": "box_obstacle", "defect_count": 0},
        {"geometry": "pipe_constriction", "defect_count": 12},
        {"geometry": "non_watertight", "defect_count": 0},
    ]), encoding="utf-8")
    monkeypatch.setattr(rb, "POLY_BASELINE", baseline)
    assert rb._load_poly_allowances() == {
        "pipe": 3, "box_obstacle": 0, "pipe_constriction": 12, "non_watertight": 0,
    }


def test_load_poly_allowances_ignores_malformed(tmp_path, monkeypatch) -> None:
    baseline = tmp_path / "BASELINE_POLY.json"
    baseline.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(rb, "POLY_BASELINE", baseline)
    assert rb._load_poly_allowances() == {}


# ---------------------------------------------------------------------------
# Hex result unchanged
# ---------------------------------------------------------------------------

def test_hex_result_keeps_existing_gates() -> None:
    r = rb._make_result(
        "pipe", success=True, wall_time_s=2.0, cell_count=1320,
        max_non_ortho=38.78, avg_non_ortho=9.29, max_skewness=0.57,
        max_aspect_ratio=7.75, neg_cells=0, min_volume=3.5e-7,
    )
    assert r["pipeline"] == "hex"
    assert r["success"] is True
    assert r["hard_gates"] == {
        "mesh_ok": True,
        "neg_cells_zero": True,
        "max_non_ortho_below_70": True,
        "max_skewness_below_4": True,
        "no_fatal_errors": True,
        "cells_produced": True,
    }
    assert r["failed_gates"] == []


def test_hex_negative_case_unchanged() -> None:
    r = rb._make_result(
        "non_watertight", success=True, wall_time_s=0.0,
        warnings=["Watertight check: False"], is_negative_case=True,
    )
    assert r["pipeline"] == "hex"
    assert r["negative_case"] is True
    assert r["hard_gates"] == {"correctly_rejected": True}


# ---------------------------------------------------------------------------
# Shared geometry prep (hermetic, no WSL)
# ---------------------------------------------------------------------------

@pytest.fixture
def box_stl(tmp_path) -> Path:
    mesh = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    path = tmp_path / "box.stl"
    mesh.export(path)
    return path


def test_prepare_geometry_watertight(box_stl) -> None:
    early, ctx = rb._prepare_geometry(box_stl, pipeline="poly")
    assert early is None
    assert ctx is not None
    assert ctx["name"] == "box"
    assert ctx["max_cell"] > 0
    assert ctx["min_cell"] > 0
    assert ctx["min_cell"] <= ctx["max_cell"]


def test_prepare_geometry_negative_case(box_stl, tmp_path) -> None:
    # A deliberately non-watertight STL must be rejected as a negative case.
    mesh = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    faces = mesh.faces[:-1]  # drop one triangle -> open surface
    open_mesh = trimesh.Trimesh(vertices=mesh.vertices, faces=faces, process=False)
    path = tmp_path / "non_watertight.stl"
    open_mesh.export(path)
    early, ctx = rb._prepare_geometry(path, pipeline="poly")
    assert ctx is None
    assert early is not None
    assert early["negative_case"] is True
    assert early["pipeline"] == "poly"
    assert early["success"] is True  # correctly rejected


def test_prepare_geometry_missing_file(tmp_path) -> None:
    early, ctx = rb._prepare_geometry(tmp_path / "missing.stl", pipeline="poly")
    assert ctx is None
    assert early is not None
    assert early["success"] is False
    assert early["errors"]


# ---------------------------------------------------------------------------
# main() pipeline wiring (hermetic: WSL and meshing are monkeypatched)
# ---------------------------------------------------------------------------

def test_main_all_writes_hex_and_poly_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rb, "_wsl_alive", lambda: True)
    monkeypatch.setattr(rb, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(rb, "GEOMETRIES_DIR", tmp_path)

    def _fake_hex(stl_path, keep_case=False):
        return rb._make_result(
            stl_path.stem, success=True, wall_time_s=1.0, cell_count=10,
        )

    def _fake_poly(stl_path, keep_case=False, allowances=None):
        return rb._make_poly_result(
            stl_path.stem, success=True, wall_time_s=2.0,
            tet_count=100, poly_count=20, bl_prism_cells=10,
            bl_thickness=0.001, defect_count=0, defect_allowance=0,
            cell_count=20, max_non_ortho=30.0, max_skewness=1.0,
        )

    monkeypatch.setattr(rb, "_benchmark_one", _fake_hex)
    monkeypatch.setattr(rb, "_benchmark_poly_one", _fake_poly)

    # One watertight STL so the geometry scan finds something.
    mesh = trimesh.creation.box(extents=(0.1, 0.1, 0.1))
    (tmp_path / "box.stl").write_bytes(mesh.export(file_type="stl"))

    rc = rb.main(["--pipeline", "all"])
    assert rc == 0
    files = sorted(p.name for p in tmp_path.glob("*.json"))
    assert len(files) == 2
    assert any(f.endswith(".json") and not f.endswith("_poly.json") for f in files)
    assert any(f.endswith("_poly.json") for f in files)
    poly_file = next(p for p in tmp_path.glob("*_poly.json"))
    data = json.loads(poly_file.read_text("utf-8"))
    assert data[0]["pipeline"] == "poly"
    assert data[0]["geometry"] == "box"


def test_main_poly_only_writes_poly_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rb, "_wsl_alive", lambda: True)
    monkeypatch.setattr(rb, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(rb, "GEOMETRIES_DIR", tmp_path)

    def _fake_poly(stl_path, keep_case=False, allowances=None):
        return rb._make_poly_result(
            stl_path.stem, success=True, wall_time_s=2.0,
            tet_count=100, poly_count=20, bl_prism_cells=10,
            bl_thickness=0.001, defect_count=0, defect_allowance=0,
            cell_count=20, max_non_ortho=30.0, max_skewness=1.0,
        )

    monkeypatch.setattr(rb, "_benchmark_poly_one", _fake_poly)

    mesh = trimesh.creation.box(extents=(0.1, 0.1, 0.1))
    (tmp_path / "box.stl").write_bytes(mesh.export(file_type="stl"))

    rc = rb.main(["--pipeline", "poly"])
    assert rc == 0
    files = sorted(p.name for p in tmp_path.glob("*.json"))
    assert len(files) == 1
    assert files[0].endswith("_poly.json")