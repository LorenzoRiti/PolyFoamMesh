"""W10.3: defect attribution and the explicit poly release gate."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core.poly_release import (  # noqa: E402
    FORBIDDEN_RELEASE_LEVERS,
    PolyDefectMeasure,
    attribute_poly_defects,
    evaluate_release,
)


def test_r1_quality_measure_is_accepted_with_documented_residual():
    dual = PolyDefectMeasure(n_cells=79416, pyramids=1, non_ortho=0, skew=0)
    bl = PolyDefectMeasure(
        n_cells=255456, pyramids=0, non_ortho=65, skew=0,
        negative_volumes=0, min_volume=1e-11,
    )
    decision = evaluate_release(
        bl, 99.47, dual_only=dual, strict_checkmesh=False,
    )
    assert decision.accepted is True
    assert decision.residual
    assert decision.attribution["pyramids_added_by_bl"] == -1


def test_release_rejects_a_pyramid_even_at_full_coverage():
    bl = PolyDefectMeasure(n_cells=253681, pyramids=39)
    decision = evaluate_release(bl, 100.0)
    assert decision.accepted is False
    assert any("pyramid" in r for r in decision.reasons)


def test_release_rejects_the_measured_combo_coverage():
    # W10.3 measured the edge-filter 0.9 + budget combo: 0 pyramids but
    # coverage 87.1%; it must not be promoted.
    bl = PolyDefectMeasure(n_cells=154145, pyramids=0)
    decision = evaluate_release(bl, 87.1)
    assert decision.accepted is False
    assert any("coverage" in r for r in decision.reasons)


def test_release_strict_rejects_a_real_checkmesh_failure():
    decision = evaluate_release(
        PolyDefectMeasure(n_cells=10), 100.0,
        strict_checkmesh=False, allow_documented_residual=False,
    )
    assert decision.accepted is False
    assert any("checkMesh" in r for r in decision.reasons)


def test_missing_mesh_measure_is_fail_soft(tmp_path):
    from polyfoammesh.core.poly_release import measure_poly_defects

    m = measure_poly_defects(tmp_path / "does_not_exist")
    assert m.n_cells == 0
    assert m.pyramids == 0


def test_release_rejects_a_single_pyramid_with_low_coverage_twice():
    # Both failures are retained in the reasons; no "best" hides the other.
    d = evaluate_release(PolyDefectMeasure(pyramids=1), 87.0)
    assert d.accepted is False
    assert len(d.reasons) >= 2


def test_forbidden_levers_are_unique_and_include_the_w8_nudge():
    assert len(set(FORBIDDEN_RELEASE_LEVERS)) == len(
        FORBIDDEN_RELEASE_LEVERS
    )
    assert any("nudge" in x or "edge-cap" in x
               for x in FORBIDDEN_RELEASE_LEVERS)


def test_release_rejects_negative_volume():
    bl = PolyDefectMeasure(n_cells=100, negative_volumes=1)
    assert evaluate_release(bl, 99.0).accepted is False


def test_attribution_is_a_difference_not_a_double_count():
    dual = PolyDefectMeasure(pyramids=1, non_ortho=0, skew=0)
    bl = PolyDefectMeasure(pyramids=39, non_ortho=26, skew=5)
    attr = attribute_poly_defects(dual, bl)
    assert attr["pyramids_added_by_bl"] == 38
    assert attr["non_ortho_added_by_bl"] == 26
    assert attr["skew_added_by_bl"] == 5


def test_release_mapping_and_forbidden_levers_are_visible():
    decision = evaluate_release(PolyDefectMeasure(), 100.0)
    data = decision.to_mapping()
    assert data["accepted"] is True
    assert data["bl"]["pyramids"] == 0
    assert "smoothing" in FORBIDDEN_RELEASE_LEVERS
    assert "merge+relax" in FORBIDDEN_RELEASE_LEVERS
