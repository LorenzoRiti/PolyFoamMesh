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
