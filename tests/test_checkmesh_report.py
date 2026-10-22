"""W8: Failed checkMesh must be explainable, never a bare banner."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from polyfoammesh.core.checkmesh_report import CheckMeshReport  # noqa: E402
from polyfoammesh.core.openfoam_runner import (  # noqa: E402
    MeshQualityReport,
    parse_checkmesh_output,
)

R1 = """\
    cells:            253681
    Mesh non-orthogonality Max: 128.044 average: 11.7423
 ***Error in face pyramids: 39 faces are incorrectly oriented.
 ***Max skewness = 170.893, 5 highly skew faces detected which may impair the quality of the results
Failed 3 mesh checks.
"""

HEALTHY = """\
    points:           8903
    faces:            24287
    cells:            7694
    Max aspect ratio = 39.5564 OK.
    Max cell openness = 3.2e-16 OK.
    Boundary openness (1.6e-16 1.1e-16 1.2e-16) OK.
    Min volume = 6.30328e-06. Max volume = 0.0162677.
    Mesh non-orthogonality Max: 61.5453 average: 15.0436
    Max skewness = 0.657556 OK.

Mesh OK.
"""


def test_r1_failed_report_has_counts_types_and_cause():
    r = CheckMeshReport.from_output(R1)
    assert r.failed_checks == 3
    assert r.mesh_ok is False
    assert r.passed is False
    assert set(r.defects) == {"face_pyramids", "skewness", "non_orthogonality"}
    assert r.defects["face_pyramids"].count == 39
    assert r.defects["skewness"].count == 5
    assert r.defects["skewness"].value == pytest.approx(170.893)
    assert r.defects["non_orthogonality"].value == pytest.approx(128.044)
    assert "face_pyramids" in r.cause()
    assert "Failed 3" in r.acceptance_reason


def test_r1_full_output_ignores_headers_and_keeps_written_sets():
    full = R1 + (
        "\nChecking patch topology for multiply connected surfaces...\n"
        " ***Number of non-orthogonality errors: 26.\n"
        ' <<Writing 124 non-orthogonal faces to set nonOrthoFaces\n'
        ' <<Writing 32 faces with incorrect orientation to set wrongOrientedFaces\n'
    )
    r = CheckMeshReport.from_output(full)
    assert "multiply_connected" not in r.defects
    assert r.defects["non_orthogonality"].count == 26
    assert "set:nonOrthoFaces" in r.defects["non_orthogonality"].locations
    assert "set:wrongOrientedFaces" in r.defects["wrong_orientation"].locations


def test_healthy_report_is_pass_with_no_defects():
    r = CheckMeshReport.from_output(HEALTHY)
    assert r.passed is True
    assert r.acceptance == "pass"
    assert r.failed_checks == 0
    assert r.defects == {}
    assert r.cells == 7694
    assert r.max_non_ortho == pytest.approx(61.5453)


def test_failure_cannot_be_accepted_without_a_reason():
    r = CheckMeshReport.from_output(R1)
    with pytest.raises(ValueError):
        r.accept("  ")
    assert r.passed is False
    r.accept("R1 accepted: 39 pyramids + 5 skew are the documented fTetWild residual")
    assert r.passed is True
    assert r.accepted is True
    assert "documented" in r.acceptance_reason


def test_fatal_output_cannot_be_accepted():
    r = CheckMeshReport.from_output(
        "FOAM FATAL ERROR: cannot read polyMesh\nFailed 1 mesh checks."
    )
    assert r.fatal is True
    with pytest.raises(ValueError):
        r.accept("not allowed")


def test_legacy_negative_volume_is_structured():
    r = CheckMeshReport.from_output(
        "There are 3 cells with negative volume\nMin volume = -5.3e-10\n"
        "Max skewness = 8.5 average = 1.5\n"
    )
    assert r.neg_cells == 3
    assert r.defects["negative_volume"].count == 3
    assert r.defects["skewness"].count == 1
    assert r.passed is False


def test_mesh_quality_report_carries_the_structured_model():
    r = parse_checkmesh_output(R1)
    assert isinstance(r, MeshQualityReport)
    assert r.passed is False
    assert r.structured is not None
    payload = r.to_dict()
    assert payload["structured"]["failed_checks"] == 3
    assert payload["structured"]["defects"]["face_pyramids"]["count"] == 39
    assert payload["structured"]["acceptance"] == "fail"


def test_mesh_quality_report_is_a_view_over_the_single_parser():
    """M2: no second checkMesh parser — every scalar comes from CheckMeshReport."""
    for text in (R1, HEALTHY):
        r = parse_checkmesh_output(text)
        s = r.structured
        assert s is not None
        assert r.cells == s.cells
        assert r.faces == s.faces
        assert r.points == s.points
        assert r.max_non_ortho == s.max_non_ortho
        assert r.avg_non_ortho == s.avg_non_ortho
        assert r.max_skewness == s.max_skewness
        assert r.max_aspect_ratio == s.max_aspect_ratio
        assert r.neg_cells == s.neg_cells
        assert r.min_volume == s.min_volume
        assert r.has_fatal == s.fatal


def test_explicit_acceptance_reaches_mesh_quality_report():
    r = parse_checkmesh_output(R1)
    assert r.passed is False
    r.structured.accept("accepted for the documented R1 residual")
    assert r.passed is True
    assert r.to_dict()["structured"]["acceptance"] == "accepted"
