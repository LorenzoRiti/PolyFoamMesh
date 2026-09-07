"""Tests for the watertight workflow module."""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("watertight")
WorkflowStep = _mod.WorkflowStep
WorkflowResult = _mod.WorkflowResult
RefinementSource = _mod.RefinementSource
WatertightWorkflow = _mod.WatertightWorkflow


def test_workflow_step_enum():
    assert len(WorkflowStep) == 9
    assert WorkflowStep.IMPORT.name == "IMPORT"
    assert WorkflowStep.EXPORT.name == "EXPORT"


def test_workflow_result_defaults():
    r = WorkflowResult()
    assert not r.success
    assert r.cell_count == 0
    assert r.errors == []


def test_workflow_result_with_data():
    r = WorkflowResult(success=True, cell_count=5000, case_dir="/tmp/test")
    assert r.success
    assert r.cell_count == 5000


def test_refinement_source():
    s = RefinementSource(
        name="test", shape="box",
        center=(0, 0, 0), size=(1, 1, 1), cell_size=0.01,
    )
    assert s.name == "test"
    assert s.shape == "box"
    assert s.enabled


def test_watertight_workflow_init():
    wf = WatertightWorkflow()
    assert wf._max_cell == 0.05
    assert wf._min_cell == 0.01
    assert wf._detail == "medium"


def test_watertight_set_detail_valid():
    wf = WatertightWorkflow()
    r = wf.set_detail("fine")
    assert r.valid
    assert wf._detail == "fine"


def test_watertight_set_detail_invalid():
    wf = WatertightWorkflow()
    r = wf.set_detail("ultra")
    assert not r.valid


def test_watertight_set_cell_sizes_valid():
    wf = WatertightWorkflow()
    r = wf.set_cell_sizes(0.05, 0.01)
    assert r.valid
    assert wf._max_cell == 0.05


def test_watertight_set_cell_sizes_invalid():
    wf = WatertightWorkflow()
    r = wf.set_cell_sizes(-0.05, 0.01)
    assert not r.valid


def test_watertight_set_boundary_layers_valid():
    wf = WatertightWorkflow()
    r = wf.set_boundary_layers(3, 0.005, 1.2)
    assert r.valid
    assert wf._bl_params is not None


def test_watertight_set_boundary_layers_invalid():
    wf = WatertightWorkflow()
    r = wf.set_boundary_layers(0, 0.005, 1.2)
    assert not r.valid


def test_watertight_validate_geometry_nonexistent():
    wf = WatertightWorkflow()
    r = wf.set_geometry("Z:\\nonexistent\\file.step")
    assert not r.valid


def test_add_refinement():
    wf = WatertightWorkflow()
    source = RefinementSource("box1", "box", (0, 0, 0), (1, 1, 1), 0.01)
    wf.add_refinement(source)
    assert len(wf._refinement_sources) == 1


def test_register_callback():
    wf = WatertightWorkflow()
    calls = []
    wf.on_step(WorkflowStep.IMPORT, lambda s: calls.append(s))
    wf._emit_callbacks(WorkflowStep.IMPORT)
    assert len(calls) == 1
    assert calls[0] == WorkflowStep.IMPORT


def test_workflow_requires_geometry():
    wf = WatertightWorkflow()
    result = wf.run()
    assert not result.success
    assert len(result.errors) > 0


def test_step_boundary_layer_no_walls_skips_bl(monkeypatch, tmp_path):
    """No wall patches must skip BL entirely, not apply it to every patch."""
    wf = WatertightWorkflow()
    wf.set_boundary_layers(3, 0.005, 1.2)
    wf._meshes = [
        types.SimpleNamespace(metadata={"name": "inlet"}),
        types.SimpleNamespace(metadata={"name": "outlet"}),
        types.SimpleNamespace(metadata={"name": "symmetry"}),
    ]
    wf._case_dir = tmp_path
    wf._result = WorkflowResult()
    wf._max_cell = 0.05
    wf._min_cell = 0.01

    class _FakeGeom:
        def compute_bbox_dim(self, meshes):
            return (1.0, 1.0, 1.0)

        def validate_cell_sizes(self, bbox_dim, max_cell, min_cell):
            return (0.05, 0.01, None)

    calls = {}

    class _FakeMeshdict:
        def write_meshdict(self, *args, **kwargs):
            calls["bl_params"] = kwargs.get("bl_params")

    monkeypatch.setattr(_mod, "_lazy_geom", lambda: _FakeGeom())
    monkeypatch.setattr(_mod, "_lazy_meshdict", lambda: _FakeMeshdict())

    wf._step_boundary_layer()

    assert wf._bl_params["wallPatches"] == []
    assert calls["bl_params"] is None, "meshDict must be written without a BL block"
    assert any(
        "inlet" in w and "outlet" in w for w in wf._result.warnings
    ), "warning should name the excluded patches"


if __name__ == "__main__":
    test_workflow_step_enum()
    test_workflow_result_defaults()
    test_workflow_result_with_data()
    test_refinement_source()
    test_watertight_workflow_init()
    test_watertight_set_detail_valid()
    test_watertight_set_detail_invalid()
    test_watertight_set_cell_sizes_valid()
    test_watertight_set_cell_sizes_invalid()
    test_watertight_set_boundary_layers_valid()
    test_watertight_set_boundary_layers_invalid()
    test_watertight_validate_geometry_nonexistent()
    test_add_refinement()
    test_register_callback()
    test_workflow_requires_geometry()
    print("ALL PASS")
