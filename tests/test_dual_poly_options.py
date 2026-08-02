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

from cfmesh_autogui.core.openfoam_runner import DualPolyWorker  # noqa: E402


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
        "cfmesh_autogui.core.tet_poly_dual.TetPolyDualConverter",
        _RecordingConverter,
    ):
        worker.run()

    assert captured.get("wedge_cells") is True
    assert captured.get("median_faces") is True
    assert captured.get("cancel") is not None
