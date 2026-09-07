"""The guided workflow must never point the user at a step they can't do,
and must always name the single next thing to do (senza impazzimenti)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfmesh_autogui.core.workflow import MeshingWorkflow, Status, Step


def test_fresh_workflow_points_at_loading_geometry():
    wf = MeshingWorkflow()
    assert wf.next_step() is Step.GEOMETRY
    assert "Load" in wf.next_action()
    # everything downstream is locked until there's a geometry
    for s in (Step.SIZING, Step.GENERATE, Step.QUALITY, Step.EXPORT):
        assert wf.status_of(s) is Status.LOCKED


def test_generate_is_locked_until_geometry_and_sizing():
    wf = MeshingWorkflow(geometry_loaded=True, watertight=True)
    assert wf.status_of(Step.GENERATE) is Status.LOCKED  # sizing missing
    assert wf.next_step() is Step.SIZING
    wf.sizing_ready = True
    assert wf.status_of(Step.GENERATE) is Status.READY
    assert wf.next_step() is Step.GENERATE
    assert "Generate Mesh" in wf.next_action()


def test_non_watertight_geometry_is_flagged_not_hidden():
    wf = MeshingWorkflow(geometry_loaded=True, watertight=False, sizing_ready=True)
    # geometry step warns rather than silently reads as done
    assert wf.status_of(Step.GEOMETRY) is Status.WARNING
    # the user is steered to the caveat first
    assert wf.next_step() is Step.GEOMETRY
    assert "watertight" in wf.next_action().lower()


def test_generate_still_possible_when_not_watertight_but_warned():
    wf = MeshingWorkflow(geometry_loaded=True, watertight=False, sizing_ready=True)
    # not locked — the user may proceed at their own risk
    assert wf.status_of(Step.GENERATE) is Status.WARNING


def test_boundary_layers_is_optional_and_never_the_forced_next_step():
    wf = MeshingWorkflow(geometry_loaded=True, watertight=True, sizing_ready=True)
    assert wf.status_of(Step.BOUNDARY_LAYERS) is Status.READY
    # next action skips over the optional BL step straight to generate
    assert wf.next_step() is Step.GENERATE


def test_quality_and_export_unlock_after_meshing():
    wf = MeshingWorkflow(
        geometry_loaded=True, watertight=True, sizing_ready=True, mesh_generated=True
    )
    assert wf.status_of(Step.QUALITY) is Status.READY
    assert wf.status_of(Step.EXPORT) is Status.READY
    assert wf.next_step() is Step.QUALITY


def test_failed_quality_is_a_warning_not_done():
    wf = MeshingWorkflow(
        geometry_loaded=True, watertight=True, sizing_ready=True,
        mesh_generated=True, quality_passed=False,
    )
    assert wf.status_of(Step.QUALITY) is Status.WARNING


def test_completed_workflow_reports_done():
    wf = MeshingWorkflow(
        geometry_loaded=True, watertight=True, sizing_ready=True,
        mesh_generated=True, quality_passed=True, exported=True,
    )
    assert wf.next_step() is None
    assert "complete" in wf.next_action().lower()
    done, total = wf.progress()
    assert done == total


def test_progress_excludes_optional_boundary_layers():
    wf = MeshingWorkflow()
    _, total = wf.progress()
    # geometry, sizing, generate, quality, export = 5 required steps
    assert total == 5


def test_no_step_is_both_locked_and_next():
    """Sanity: next_step never returns a locked step, in any reachable state."""
    from itertools import product

    for geo, wt, siz, mesh, qp in product(
        [False, True], [None, False, True], [False, True],
        [False, True], [None, False, True],
    ):
        wf = MeshingWorkflow(
            geometry_loaded=geo, watertight=wt, sizing_ready=siz,
            mesh_generated=mesh, quality_passed=qp,
        )
        nxt = wf.next_step()
        if nxt is not None:
            assert wf.status_of(nxt) is not Status.LOCKED
