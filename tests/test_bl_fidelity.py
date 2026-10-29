"""W10.1: measured BL fidelity, explicit caps and keep-best policy."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from polyfoammesh.core.bl_attempt_policy import (  # noqa: E402
    BLAttempt,
    BLAttemptPolicy,
    BLChainExecutor,
)
from polyfoammesh.core.bl_fidelity import (  # noqa: E402
    cap_reason,
    effective_h1_stats,
    estimate_u_tau,
    height_cap_stats,
    y_plus_for_h1,
)
from polyfoammesh.core.openfoam_runner import (  # noqa: E402
    bl_quality_policy_enabled,
)


class _Result:
    def __init__(self, *, coverage=100.0, h1_ratio=0.113, pyramids=0,
                 h1_median=0.000142, success=True):
        self.success = success
        self.stats = {
            "wall_face_coverage_pct": coverage,
            "n_pyramids_after": pyramids,
            "h1_cap_reason": "h1 cap: support=92%, edge=8%",
        }
        self.h1_ratio = h1_ratio
        self.h1_median = h1_median
        self.n_pyramids_after = pyramids
        self.h1_cap_reason = "h1 cap: support=92%, edge=8%"
        self.warnings = []


class _FakeEngine:
    """Writes a marker polyMesh and returns the next declared result."""

    def __init__(self, tmp_path, results):
        self.case_dir = Path(tmp_path)
        (self.case_dir / "constant" / "polyMesh").mkdir(parents=True)
        self._results = list(results)
        self.calls = []
        self.seen: list[str] = []
        self.marker = self.case_dir / "constant" / "polyMesh" / "marker"

    def run(self, **kwargs):
        self.calls.append(dict(kwargs))
        self.seen.append(
            self.marker.read_text(encoding="utf-8")
            if self.marker.exists() else ""
        )
        name, result = self._results.pop(0)
        self.marker.write_text(name, encoding="utf-8")
        return result


def test_height_cap_counts_support_edge_and_fade():
    pre = {
        "wall_verts": [0, 1, 2],
        "support_d": {0: 0.0001, 1: 1.0, 2: 1.0},
        "edge_cap": {0: 1.0, 1: 0.0001, 2: 1.0},
        "angle_fade": {0: 1.0, 1: 1.0, 2: 0.001},
    }
    stats = height_cap_stats(pre, 0.001, 2, growth=1.2)
    assert stats["n_wall_vertices"] == 3
    assert stats["support_bound"] == 1
    assert stats["edge_bound"] == 2
    assert stats["fade_bound"] == 1
    assert stats["below_requested_h1"] == 3
    assert "support=" in cap_reason(stats)


def test_stack_closure_blends_at_half_surface_cell():
    # AeroBench rule (cited): the outermost layer should be ~0.5x the
    # surface cell so the stack blends into the core mesh.  h1=1mm,
    # r=1.2, n=5 -> final = 1mm * 1.2^4 = 2.07mm; surface 8mm -> blends.
    from polyfoammesh.core.bl_fidelity import stack_closure

    got = stack_closure(0.001, 5, 1.2, 0.008)
    assert got["final_layer"] == pytest.approx(0.001 * 1.2 ** 4)
    assert got["ratio"] == pytest.approx(0.001 * 1.2 ** 4 / 0.008)
    assert got["blends"] is True
    assert got["target_ratio"] == pytest.approx(0.5)


def test_stack_closure_flags_overshoot():
    from polyfoammesh.core.bl_fidelity import stack_closure

    # Same stack against a 2mm surface cell: final 2.07mm > cell.
    got = stack_closure(0.001, 5, 1.2, 0.002)
    assert got["blends"] is False
    assert got["ratio"] == pytest.approx(0.001 * 1.2 ** 4 / 0.002)
    # Uniform growth (r=1): final == first.
    flat = stack_closure(0.001, 5, 1.0, 0.008)
    assert flat["final_layer"] == pytest.approx(0.001)
    assert flat["blends"] is True
    # No surface cell to compare against: unavailable, never "blends".
    assert stack_closure(0.001, 5, 1.2, 0.0)["available"] is False


def test_effective_h1_uses_wall_face_centres_and_first_prism(monkeypatch):
    import polyfoammesh.core.bl_poly as bp

    monkeypatch.setattr(
        bp, "_face_geometry",
        lambda *a, **k: (np.zeros((1, 3)), np.zeros((1, 3))),
    )
    # The first prism centroid is 0.5 mm from the wall; h1 = 2 * distance.
    monkeypatch.setattr(
        bp, "_cell_centroids",
        lambda *a, **k: (np.array([[0.0005, 0.0, 0.0]]), None),
    )
    built = {
        "points": np.zeros((1, 3)),
        "faces": [[0]],
        "owner": np.array([0]),
        "neigh": np.array([], dtype=np.int64),
        "n_int": 0,
        "wall_faces": [0],
        "wall_face_centres": np.zeros((1, 3)),
        "prism_start": 0,
        "prism_off": {0: 0},
        "prism_nf": {0: 1},
    }
    stats = effective_h1_stats(built, 0.001)
    assert stats["h1_median"] == pytest.approx(0.001)
    assert stats["h1_ratio"] == pytest.approx(1.0)


def test_y_plus_estimate_is_labelled_and_unavailable_without_utau():
    u_tau, nu, method = estimate_u_tau(10.0, 1.0, "air", "flat_plate")
    assert u_tau > 0.0
    assert nu > 0.0
    assert "estimate" in method
    yp = y_plus_for_h1(0.000142, u_tau, nu)
    assert yp is not None and yp > 0.0
    assert y_plus_for_h1(0.000142, 0.0, nu) is None
    missing, _nu, why = estimate_u_tau(0.0, 1.0, "air")
    assert missing == 0.0
    assert why == "unavailable"


def test_report_matches_built_h1_and_has_per_patch_yplus():
    from test_bl_poly import WORK_ROOT, _build_dual_case

    from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine
    from polyfoammesh.core.openfoam_runner import boundary_layer_report

    case = _build_dual_case(WORK_ROOT / "fidelity_report")
    res = PolyBoundaryLayerEngine(case, log=lambda _m: None).run(
        n_layers=2, first_height=0.01, growth_rate=1.2, apply_to_all=True,
    )
    assert res.success, res.errors
    rep = boundary_layer_report(
        case, requested_h1=0.01, velocity=10.0, fluid="air",
        h1_cap_reason=res.h1_cap_reason,
    )
    assert rep["h1_median"] == pytest.approx(res.h1_median)
    assert rep["h1_ratio"] == pytest.approx(res.h1_ratio)
    assert rep["y_plus_median"] is not None
    assert rep["y_plus_method"].startswith("flat-plate")
    assert rep["per_patch"]
    patch = next(iter(rep["per_patch"].values()))
    assert patch["y_plus_median"] is not None
    assert "h1_ratio" in patch


def test_quality_policy_rollback_switch(monkeypatch):
    monkeypatch.delenv("POLYFOAMMESH_BL_QUALITY_POLICY", raising=False)
    assert bl_quality_policy_enabled() is True
    monkeypatch.setenv("POLYFOAMMESH_BL_QUALITY_POLICY", "0")
    assert bl_quality_policy_enabled() is False
    monkeypatch.setenv("POLYFOAMMESH_BL_QUALITY_POLICY", "yes")
    assert bl_quality_policy_enabled() is True


def test_compatibility_policy_is_one_standard_attempt():
    policy = BLAttemptPolicy.compatibility()
    assert [a.name for a in policy.attempts] == ["standard"]
    assert policy.attempts[0].overrides == {}


def test_ftetwild_policy_has_the_measured_quality_attempt():
    policy = BLAttemptPolicy.for_ftetwild()
    names = [a.name for a in policy.attempts]
    assert names == ["standard", "quality-decoupled-vertex"]
    q = policy.attempts[1]
    assert q.overrides["pyr_tolerance"] == 0
    assert q.overrides["local_termination"] == "decoupled_vertex"
    assert q.clear_boundary_pyramids is True


def test_policy_selects_quality_over_higher_coverage_with_pyramids(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        "polyfoammesh.core.bl_poly.clear_boundary_pyramids_case",
        lambda *a, **k: 0,
    )
    engine = _FakeEngine(tmp_path, [
        ("standard", _Result(coverage=100.0, pyramids=39)),
        ("quality", _Result(coverage=99.47, pyramids=0)),
    ])
    chain = BLChainExecutor().run(
        engine, {}, BLAttemptPolicy.for_ftetwild(),
    )
    assert chain.accepted is True
    assert chain.best.attempt.name == "quality-decoupled-vertex"
    assert chain.best.coverage_pct == pytest.approx(99.47)
    assert engine.marker.read_text(encoding="utf-8") == "quality"
    # Attempt 2 started from the original polyMesh, not from the standard
    # BL mesh attempt 1 wrote.
    assert engine.seen[1] == ""
    assert chain.to_mapping()["selected_attempt"] == "quality-decoupled-vertex"


def test_policy_keep_best_restores_the_earlier_snapshot(tmp_path):
    engine = _FakeEngine(tmp_path, [
        ("good", _Result(coverage=100.0, pyramids=0)),
        ("worse", _Result(coverage=99.0, pyramids=2)),
    ])
    policy = BLAttemptPolicy(
        name="keep-best",
        attempts=(BLAttempt("good"), BLAttempt("worse")),
        stop_on_accepted=False,
    )
    chain = BLChainExecutor().run(engine, {}, policy)
    assert chain.best.attempt.name == "good"
    assert engine.marker.read_text(encoding="utf-8") == "good"


def test_policy_reports_h1_gap_instead_of_hiding_it(tmp_path):
    engine = _FakeEngine(tmp_path, [
        ("standard", _Result(coverage=100.0, pyramids=39, h1_ratio=0.113)),
    ])
    chain = BLChainExecutor().run(
        engine, {}, BLAttemptPolicy.for_ftetwild(),
    )
    # The quality attempt is not in the fake queue; the standard result is
    # still returned and the h1 cap is declared.
    assert chain.best is not None
    assert chain.best.h1_ratio == pytest.approx(0.113)
    assert "h1" in chain.gap_reason.lower()
    assert chain.accepted is False


def test_policy_records_attempts_even_when_one_raises(tmp_path):
    class _BadEngine(_FakeEngine):
        def run(self, **kwargs):
            self.calls.append(dict(kwargs))
            raise RuntimeError("boom")

    engine = _BadEngine(tmp_path, [])
    chain = BLChainExecutor().run(
        engine, {}, BLAttemptPolicy.compatibility(),
    )
    assert chain.best is None
    assert chain.attempts
    assert "every attempt failed" in chain.gap_reason
    assert "boom" in chain.attempts[0].error
