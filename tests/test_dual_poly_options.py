"""DualPolyWorker must always run the converter with the measured-best
quality options enabled (wedge cells + median faces), or the poly path
silently regresses to a worse dual. Regression guard for Phase 3."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from unittest import mock  # noqa: E402

from polyfoammesh.core.openfoam_runner import DualPolyWorker  # noqa: E402


class _Result:
    success = True
    errors = []
    n_tets_before = 100
    n_cells_after = 40
    n_residual_tets = 0
    stage_times = {"total": 1.0}


def test_dual_poly_worker_passes_best_options(tmp_path):
    """The measured-best options must be passed on every poly run."""
    case = tmp_path / "case"
    poly = case / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "points").write_text("dummy", encoding="utf-8")

    captured: dict = {}

    class _RecordingConverter:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def run(self):
            return _Result()

    worker = DualPolyWorker(case)
    worker.log_line.connect(lambda m: None)

    with mock.patch(
        "polyfoammesh.core.tet_poly_dual.TetPolyDualConverter",
        _RecordingConverter,
    ):
        worker.run()

    # The poly path now asks for the COLLAPSED boundary (one polygonal face
    # per boundary vertex, the polyDualMesh/STAR-CCM+ topology) instead of
    # the exact per-corner subdivision. Measured A/B on two real tet meshes:
    #
    #   user's own mesh (102,080 cells):
    #     exact    462,846 boundary faces (all quads),   0 defects, drift 0.00%
    #     collapse  80,111 boundary faces (5.8x fewer),  0 defects, drift 0.00%
    #   valve1 (152,086 cells, the hard concave-feature case):
    #     exact    226,542 boundary faces, 1056 defects (766 pyramid)
    #     collapse  42,130 boundary faces (5.4x fewer),
    #                                       483 defects (321 pyramid), -1.08%
    #
    # i.e. the collapse is not only what makes the mesh READ as polyhedral
    # (pentagons/hexagons instead of three quads tiling each primal
    # triangle, whose edges otherwise survive and look like a triangulated
    # surface) — on the hard case it also HALVES the residual defects and
    # cuts the inverted face pyramids by 58%, which is the concave-feature
    # defect that agglomeration, vertex-star split and wedge cells all
    # failed to improve.
    #
    # wedge_cells/median_faces are deliberately NOT passed: median_faces
    # measured slightly worse (1056 vs 1027 defects on valve1), wedge_cells
    # made no difference (1056 either way), and both are incompatible with
    # the collapse, which disables them explicitly anyway.
    assert captured.get("collapse_smooth_edges") is True
    assert captured.get("boundary_feature_angle") == 40.0
    assert captured.get("cancel") is not None


class _BLResult:
    success = True
    errors: list[str] = []
    n_prism_cells = 10
    total_thickness = 0.01


def _run_dual_poly_worker_with_bl(tmp_path, bl_params, captured_bl_kwargs):
    case = tmp_path / "case"
    poly = case / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "points").write_text("dummy", encoding="utf-8")

    class _RecordingConverter:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            return _Result()

    class _RecordingBLEngine:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, **kwargs):
            captured_bl_kwargs.update(kwargs)
            return _BLResult()

    worker = DualPolyWorker(case, bl_params=bl_params)
    worker.log_line.connect(lambda m: None)

    with mock.patch(
        "polyfoammesh.core.tet_poly_dual.TetPolyDualConverter",
        _RecordingConverter,
    ), mock.patch(
        "polyfoammesh.core.bl_poly.PolyBoundaryLayerEngine",
        _RecordingBLEngine,
    ):
        worker.run()


def test_concave_closure_flag_sets_decoupled_vertex_local_termination(tmp_path):
    """The opt-in GUI checkbox (params_panel.get_bl_params()['concaveClosure'])
    must reach the production BL engine as local_termination='decoupled_vertex'
    — the H4 mode measured to close the boundary layer on the
    production-collapsed valve and on tight geometries where the default
    path leaves gaps, see docs/residual_risks.md 'FASE 9'."""
    captured: dict = {}
    _run_dual_poly_worker_with_bl(
        tmp_path,
        {"nLayers": 3, "firstLayerThickness": 0.001, "thicknessRatio": 1.2,
         "applyToAll": True, "concaveClosure": True},
        captured,
    )
    assert captured.get("local_termination") == "decoupled_vertex"


def test_concave_closure_triggers_post_bl_merge_repair(tmp_path):
    """2026-09-14: concaveClosure=True must also run the post-BL cell-merge
    repair (measured best topology - merging AFTER the BL is built avoids
    the aspect-ratio regression merging BEFORE it causes, see
    notes/getme_compactness_reasoning.md). Verifies the wiring calls
    repair_concave_cells_if_safe with the mesh read from the case's
    polyMesh, and writes the result back only when the guard applies it."""
    import numpy as np

    import polyfoammesh.core.foam_mesh_io as fio

    fake_mesh = (
        "points", ["face0", "face1"], np.array([0, 0]), np.array([0]), "patches",
    )
    captured_bl: dict = {}
    calls: dict = {}

    def fake_repair(points, faces, owner, neigh, n_int, n_cells, patches):
        calls["called_with"] = (points, faces, owner, neigh, n_int, n_cells, patches)
        return (faces, owner, neigh, n_int, n_cells, patches,
                {"applied": True, "reason": "accepted: test"})

    with mock.patch(
        "polyfoammesh.core.foam_mesh_io.read_polymesh", return_value=fake_mesh,
    ), mock.patch(
        "polyfoammesh.core.foam_mesh_io.write_polymesh",
    ) as mock_write, mock.patch(
        "polyfoammesh.core.poly_cell_merge.repair_concave_cells_if_safe",
        side_effect=fake_repair,
    ):
        _run_dual_poly_worker_with_bl(
            tmp_path,
            {"nLayers": 3, "firstLayerThickness": 0.001, "thicknessRatio": 1.2,
             "applyToAll": True, "concaveClosure": True},
            captured_bl,
        )

    assert "called_with" in calls, "repair_concave_cells_if_safe was not invoked"
    assert mock_write.called, "applied repair must be written back to the polyMesh"


def test_concave_closure_off_skips_post_bl_merge_repair(tmp_path):
    """Default (unchecked) behaviour: no read/write/repair of the polyMesh
    beyond what the BL engine itself already did."""
    captured_bl: dict = {}
    with mock.patch(
        "polyfoammesh.core.poly_cell_merge.repair_concave_cells_if_safe",
    ) as mock_repair:
        _run_dual_poly_worker_with_bl(
            tmp_path,
            {"nLayers": 3, "firstLayerThickness": 0.001, "thicknessRatio": 1.2,
             "applyToAll": True, "concaveClosure": False},
            captured_bl,
        )
    mock_repair.assert_not_called()


def test_concave_closure_off_by_default_leaves_local_termination_unset(tmp_path):
    """Default (unchecked) behaviour must be byte-identical to before this
    option existed: local_termination is never passed."""
    captured: dict = {}
    _run_dual_poly_worker_with_bl(
        tmp_path,
        {"nLayers": 3, "firstLayerThickness": 0.001, "thicknessRatio": 1.2,
         "applyToAll": True, "concaveClosure": False},
        captured,
    )
    assert "local_termination" not in captured
