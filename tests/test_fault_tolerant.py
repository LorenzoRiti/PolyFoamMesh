"""Tests for the fault-tolerant meshing module."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("fault_tolerant")
FaultTolerantParams = _mod.FaultTolerantParams
FaultTolerantResult = _mod.FaultTolerantResult
FaultTolerantWorkflow = _mod.FaultTolerantWorkflow
GapInfo = _mod.GapInfo


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
    print("ALL PASS")
