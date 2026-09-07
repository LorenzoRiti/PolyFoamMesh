"""WatertightWorkflow's meshDict-writing steps must actually produce a
correct meshDict: patches typed via renameBoundary, and boundary-layer keys
matching cfMesh's real contract — not the ones from before this file's
undefined-name bugs were first fixed, which still used the stale BL contract
and never passed patch_names at all.

Uses the real core modules (geometry/meshdict_gen/case_setup), not the
lightweight-stub helper other watertight tests use, since that stub replaces
core.geometry/core.gui with empty modules and can't exercise real sizing/
meshDict writing.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:  # cadquery/OCP native libs need their dir on PATH on Windows
    import OCP as _ocp

    _d = os.path.dirname(_ocp.__file__)
    if _d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

import pytest

cq = pytest.importorskip("cadquery")

from polyfoammesh.commercial.watertight import WatertightWorkflow  # noqa: E402
from polyfoammesh.core.geometry import create_test_cylinder  # noqa: E402
from _test_helpers import space_free_tmp_root  # noqa: E402

# OpenFOAM/cfMesh reject paths with spaces (the user's home dir has one);
# route through a space-free root like the rest of the suite/benchmarks do.
# On a space-free temp dir (Linux CI) that resolves to /tmp instead of the
# drive root, so tests leave no 'C:' residue in the checkout.
WORK_ROOT = space_free_tmp_root() / "cfmesh_bench" / "watertight_meshdict_test"


@pytest.fixture
def workflow():
    shutil.rmtree(WORK_ROOT, ignore_errors=True)
    tmp = WORK_ROOT / f"run_{int(time.time() * 1000)}"
    tmp.mkdir(parents=True)

    cyl = create_test_cylinder(1.0, 2.0)
    step_path = tmp / "cyl.step"
    cq.exporters.export(cyl, str(step_path))

    wf = WatertightWorkflow()
    assert wf.set_geometry(str(step_path)).valid
    assert wf.set_case_dir(str(tmp / "case")).valid
    wf.set_cell_sizes(0.3, 0.05)
    wf.set_detail("medium")

    wf._step_import()
    wf._step_heal()
    wf._step_features()
    return wf


def _meshdict_text(wf) -> str:
    return (wf._case_dir / "system" / "meshDict").read_text()


def test_sizing_step_types_patches_via_rename_boundary(workflow):
    workflow._step_sizing()
    content = _meshdict_text(workflow)
    assert "renameBoundary" in content
    assert re.search(r'"inlet"\s*\{\s*newName\s+inlet;\s*type\s+patch;', content)
    assert re.search(r'"outlet"\s*\{\s*newName\s+outlet;\s*type\s+patch;', content)


def test_boundary_layer_step_uses_real_cfmesh_bl_contract(workflow):
    workflow.set_boundary_layers(n_layers=4, thickness_ratio=0.02, expansion_ratio=1.3)
    workflow._step_sizing()
    workflow._step_boundary_layer()
    content = _meshdict_text(workflow)

    assert re.search(r"nLayers\s+4;", content)
    # thicknessRatio must be the GROWTH ratio (1.3), not the first-layer
    # fraction (0.02) — the bug this pins down.
    assert re.search(r"thicknessRatio\s+1\.3;", content)
    assert "expansionRatio" not in content
    # the first-layer fraction must reach cfMesh as an ABSOLUTE thickness,
    # not be silently dropped (there was no key for it at all before).
    assert "maxFirstLayerThickness" in content


def test_boundary_layer_step_also_types_patches(workflow):
    """The BL step re-writes meshDict — it must not regress the patch typing
    the sizing step already established."""
    workflow.set_boundary_layers(n_layers=3, thickness_ratio=0.01, expansion_ratio=1.2)
    workflow._step_sizing()
    workflow._step_boundary_layer()
    content = _meshdict_text(workflow)
    assert "renameBoundary" in content


def test_volume_mesh_and_quality_steps_do_not_need_a_qt_event_loop(workflow, monkeypatch):
    """_step_volume_mesh() and _step_quality() used to run cartesianMesh/
    checkMesh via QThread + a Qt signal (QueuedConnection for the runner,
    a local QEventLoop for checkMesh) that only ever gets pumped by a
    running QApplication event loop. Called the way batch_mesh.py's
    _process_single() actually calls WatertightWorkflow.run() — a plain
    synchronous call, no QApplication.exec() running anywhere — both hung
    indefinitely (verified live: >120s with no progress on a trivial 1m
    box that meshes in ~4s standalone). Both must now run cartesianMesh/
    checkMesh as direct synchronous subprocess calls instead.
    """
    calls = {"cartesian_mesh": 0, "checkmesh": 0}

    # Mock physical-core detection to avoid extra subprocess.run call
    # from OpenMPAccel._detect_physical_cores inside build_command.
    #
    # Patch the CLASS OBJECT directly (obtained via a normal import)
    # instead of monkeypatch.setattr's string-path form. This codebase's
    # test suite has two competing ways of loading commercial/* modules:
    # a normal package import (what this file uses) and
    # _test_helpers.load_commercial_module (used by other test files to
    # bypass cadquery's native DLL chain — see that helper's docstring),
    # which injects its own module instances into sys.modules by hand.
    # When a test using the latter runs earlier in the same pytest
    # session, monkeypatch's dotted-string resolver — which walks
    # "polyfoammesh.commercial.openmp_accel.OpenMPAccel...` as package
    # ATTRIBUTES, not just sys.modules lookups — gets confused by the
    # resulting package/attribute mismatch and raises AttributeError
    # (order-dependent: passes in isolation, fails as part of the full
    # suite). Importing the class directly and patching it sidesteps that
    # resolver entirely.
    from polyfoammesh.commercial.openmp_accel import OpenMPAccel
    monkeypatch.setattr(OpenMPAccel, "_detect_physical_cores", lambda self: 4)

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "checkMesh" in cmd_str:
            calls["checkmesh"] += 1
        elif "cartesianMesh" in cmd_str:
            calls["cartesian_mesh"] += 1
        # Minimal polyMesh so the "poly_points exists" gate in
        # _step_quality() passes and it actually reaches the subprocess call.
        poly = workflow._case_dir / "constant" / "polyMesh"
        poly.mkdir(parents=True, exist_ok=True)
        (poly / "points").write_text("0\n(\n)\n")
        return type("R", (), {"returncode": 0, "stdout": "cells: 10\n", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    workflow._step_sizing()
    workflow._step_boundary_layer()
    workflow._step_volume_mesh()
    assert calls["cartesian_mesh"] == 1

    workflow._step_quality()
    assert calls["checkmesh"] == 1
    assert workflow._result.quality is not None
