"""Tests for the fault-tolerant meshing module."""
from __future__ import annotations

import shutil
import sys
import tempfile
import types
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("fault_tolerant")
FaultTolerantParams = _mod.FaultTolerantParams
FaultTolerantResult = _mod.FaultTolerantResult
FaultTolerantWorkflow = _mod.FaultTolerantWorkflow
GapInfo = _mod.GapInfo

# Realistic checkMesh output for a healthy mesh — parse_checkmesh_output must
# extract cells > 0 and no fatal error so the quality gate passes.
_CHECKMESH_OK = """\
Checking topology...
    Mesh is consistent.
    Boundary definition OK.
    Cell to face addressing OK.
    Point usage OK.
    Upper triangular ordering OK.
    Face vertices OK.
    Number of regions: 1 (OK).
    cells:           1234
    faces:           5678
    points:          1234
Checking geometry...
    Overall domain bounding box (-0.5 -0.5 -0.5) (0.5 0.5 0.5)
    Mesh has 3 geometric (non-empty/wedge) directions (1 1 1)
    Mesh has 3 solution (non-empty/wedge) directions (1 1 1)
    Boundary openness (1.6e-16 1.1e-16 1.2e-16) OK.
    Max cell openness = 3.2e-16 OK.
    Max aspect ratio = 1.73 OK.
    Minimum face area = 0.25. Maximum face area = 0.5.  Face area magnitudes OK.
    Min volume = 0.125. Max volume = 0.125.  Total volume = 1. Non-closed volume = 0.
    Mesh non-orthogonality Max: 0 average: 0
    Max skewness = 0 OK.
    Coupled point location match (average 0) OK.

Mesh OK.

End
"""


def _write_watertight_stl(tmp: Path) -> Path:
    """Write a small watertight box STL — real enough for _step_import."""
    import trimesh

    box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    box.metadata["name"] = "wall"
    stl_path = tmp / "box.stl"
    box.export(str(stl_path))
    return stl_path


def _make_workflow():
    """Workflow with a real watertight STL geometry in a fresh temp dir."""
    tmp = Path(tempfile.mkdtemp(prefix="ft_test_"))
    stl = _write_watertight_stl(tmp)
    wf = FaultTolerantWorkflow()
    wf._case_dir = tmp / "case"
    wf.set_geometry(stl)
    return wf, tmp


def _fake_run_factory(poly_points, fail_first_mesh=False, checkmesh_stdout=_CHECKMESH_OK):
    """Build a subprocess.run stand-in that:
    - returns checkMesh output for checkMesh commands
    - simulates cartesianMesh (optionally failing the first attempt) and
      writes the polyMesh/points file on success
    - returns harmless output for hardware-budget probes (wsl free -b,
      wmic, powershell) — their callers handle empty output gracefully
    """
    calls = {"mesh": 0}

    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        if "checkMesh" in joined:
            return types.SimpleNamespace(returncode=0, stdout=checkmesh_stdout, stderr="")
        if "cartesianMesh" in joined:
            calls["mesh"] += 1
            if fail_first_mesh and calls["mesh"] == 1:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="")
            poly_points.parent.mkdir(parents=True, exist_ok=True)
            poly_points.write_text("// points\n")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    return fake_run, calls


def test_fault_tolerant_params_defaults():
    p = FaultTolerantParams()
    assert p.gap_h == 0.001
    assert p.occlude_internal
    assert p.merge_tolerance == 0.0001


def test_fault_tolerant_params_to_dict():
    p = FaultTolerantParams(gap_h=0.002)
    d = p.to_dict()
    assert d["gap_h"] == 0.002
    assert "occlude_internal" in d


def test_fault_tolerant_result_defaults():
    r = FaultTolerantResult()
    assert not r.success
    assert r.gaps_found == 0
    assert r.cells == 0
    assert r.errors == []


def test_fault_tolerant_result_with_data():
    r = FaultTolerantResult(success=True, gaps_found=5, cells=12000)
    assert r.success
    assert r.gaps_found == 5
    assert r.cells == 12000


def test_gap_info():
    g = GapInfo(location=(0, 0, 0), gap_size=0.002, patch_name="wall")
    assert g.gap_size == 0.002
    assert g.patch_name == "wall"


def test_workflow_init():
    wf = FaultTolerantWorkflow()
    assert wf._max_cell == 0.05
    assert wf._min_cell == 0.01
    assert isinstance(wf._params, FaultTolerantParams)


def test_workflow_set_params():
    wf = FaultTolerantWorkflow()
    params = FaultTolerantParams(gap_h=0.005)
    wf.set_params(params)
    assert wf._params.gap_h == 0.005


def test_workflow_set_cell_sizes_valid():
    wf = FaultTolerantWorkflow()
    wf.set_cell_sizes(0.05, 0.01)
    assert wf._max_cell == 0.05


def test_workflow_set_cell_sizes_invalid():
    wf = FaultTolerantWorkflow()
    try:
        wf.set_cell_sizes(-0.05, 0.01)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_workflow_set_boundary_layers():
    wf = FaultTolerantWorkflow()
    wf.set_boundary_layers(3, 0.005, 1.2)
    assert wf._bl_params is not None
    assert wf._bl_params["nLayers"] == 3


def test_workflow_requires_geometry():
    wf = FaultTolerantWorkflow()
    result = wf.run()
    assert not result.success
    assert len(result.errors) > 0


def test_workflow_set_geometry_nonexistent():
    wf = FaultTolerantWorkflow()
    try:
        wf.set_geometry("Z:\\nonexistent.stl")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_run_failing_volume_fill_sets_stage_and_errors():
    """A failing cartesianMesh must NOT report success — the old stub set
    success=True after only writing the meshDict."""
    wf, tmp = _make_workflow()
    poly_points = wf._case_dir / "constant" / "polyMesh" / "points"
    fake_run, calls = _fake_run_factory(poly_points, fail_first_mesh=True)
    try:
        with unittest.mock.patch("subprocess.run", side_effect=fake_run):
            result = wf.run()
        assert not result.success
        assert len(result.errors) > 0
        assert result.stage == "volume_fill"
        assert calls["mesh"] == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_success_path():
    """cartesianMesh + checkMesh succeed and a polyMesh exists on disk →
    success=True, cells populated, stage == 'done'."""
    wf, tmp = _make_workflow()
    poly_points = wf._case_dir / "constant" / "polyMesh" / "points"
    fake_run, calls = _fake_run_factory(poly_points)
    try:
        with unittest.mock.patch("subprocess.run", side_effect=fake_run):
            result = wf.run()
        assert result.success
        assert result.cells > 0
        assert result.stage == "done"
        assert result.quality is not None
        assert calls["mesh"] == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_bl_fallback_rewrites_meshdict_without_bl():
    """First cartesianMesh call (with BL) fails, the retry without BL
    succeeds — the meshDict must have been rewritten without boundaryLayers."""
    wf, tmp = _make_workflow()
    wf.set_boundary_layers(3, 0.005, 1.2)
    case_dir = wf._case_dir
    poly_points = case_dir / "constant" / "polyMesh" / "points"
    fake_run, calls = _fake_run_factory(poly_points, fail_first_mesh=True)
    try:
        with unittest.mock.patch("subprocess.run", side_effect=fake_run):
            result = wf.run()
        assert result.success
        assert calls["mesh"] == 2
        content = (case_dir / "system" / "meshDict").read_text()
        assert "boundaryLayers" not in content
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_fatal_checkmesh_report_fails():
    """A fatal checkMesh report must fail the run even though cartesianMesh
    exited 0 and a polyMesh exists — the final quality gate."""
    wf, tmp = _make_workflow()
    poly_points = wf._case_dir / "constant" / "polyMesh" / "points"
    fatal = "cells: 0\n--> FOAM FATAL ERROR\n"
    fake_run, _calls = _fake_run_factory(poly_points, checkmesh_stdout=fatal)
    try:
        with unittest.mock.patch("subprocess.run", side_effect=fake_run):
            result = wf.run()
        assert not result.success
        assert len(result.errors) > 0
        assert result.stage == "quality"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_stage_set_progressively():
    """stage advances through the pipeline in order as each step completes."""
    wf, tmp = _make_workflow()
    poly_points = wf._case_dir / "constant" / "polyMesh" / "points"
    fake_run, _calls = _fake_run_factory(poly_points)
    seen = []

    step_names = ["_step_import", "_step_detect_gaps", "_step_close_gaps",
                  "_step_prepare_meshdict", "_step_volume_fill", "_step_quality"]
    for name in step_names:
        original = getattr(wf, name)

        def make_wrapper(orig):
            def wrapper(*a, **kw):
                orig(*a, **kw)
                seen.append(wf._result.stage)
            return wrapper

        setattr(wf, name, make_wrapper(original))

    try:
        with unittest.mock.patch("subprocess.run", side_effect=fake_run):
            result = wf.run()
        assert result.stage == "done"
        assert seen == ["import", "gaps", "wrap", "meshdict", "volume_fill", "quality"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_fault_tolerant_params_defaults()
    test_fault_tolerant_params_to_dict()
    test_fault_tolerant_result_defaults()
    test_fault_tolerant_result_with_data()
    test_gap_info()
    test_workflow_init()
    test_workflow_set_params()
    test_workflow_set_cell_sizes_valid()
    test_workflow_set_cell_sizes_invalid()
    test_workflow_set_boundary_layers()
    test_workflow_requires_geometry()
    test_workflow_set_geometry_nonexistent()
    test_run_failing_volume_fill_sets_stage_and_errors()
    test_run_success_path()
    test_run_bl_fallback_rewrites_meshdict_without_bl()
    test_run_fatal_checkmesh_report_fails()
    test_stage_set_progressively()
    print("ALL PASS")
