"""Keep-best second dual pass at a lower boundary feature angle: faceted
walls (20-40 deg creases) need the crease vertices kept, smooth CAD does not.
The pass must restore the primal before rebuilding and keep the better mesh."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core import tet_poly_dual as tpd  # noqa: E402


def _case(tmp_path):
    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "points").write_text("primal")
    return poly


def _fake(monkeypatch, defects_by_angle, seen):
    def run_once(self):
        poly = self._case_dir / "constant" / "polyMesh"
        seen.append(((poly / "points").read_text(), self._boundary_feature_angle))
        angle = self._boundary_feature_angle
        (poly / "points").write_text(f"dual@{angle:.0f}")
        r = tpd.DualPolyResult()
        r.success = True
        r.residual_defects = defects_by_angle[angle]
        return r

    monkeypatch.setattr(tpd.TetPolyDualConverter, "_run_once", run_once)


def _conv(tmp_path, fb=20.0):
    return tpd.TetPolyDualConverter(
        tmp_path, log=lambda m: None, boundary_feature_angle=40.0,
        fallback_feature_angle=fb,
    )


def test_lower_angle_kept_when_better(tmp_path, monkeypatch):
    poly = _case(tmp_path)
    seen: list = []
    _fake(monkeypatch, {40.0: 3, 20.0: 0}, seen)
    r = _conv(tmp_path).run()
    assert r.residual_defects == 0
    assert seen == [("primal", 40.0), ("primal", 20.0)]   # rebuilt from the primal
    assert (poly / "points").read_text() == "dual@20"


def test_first_result_restored_when_not_better(tmp_path, monkeypatch):
    poly = _case(tmp_path)
    seen: list = []
    _fake(monkeypatch, {40.0: 3, 20.0: 11}, seen)
    conv = _conv(tmp_path)
    r = conv.run()
    assert r.residual_defects == 3
    assert (poly / "points").read_text() == "dual@40"
    assert conv._boundary_feature_angle == 40.0


def test_clean_dual_or_disabled_runs_once(tmp_path, monkeypatch):
    _case(tmp_path)
    seen: list = []
    _fake(monkeypatch, {40.0: 0, 20.0: 0}, seen)
    _conv(tmp_path).run()
    assert len(seen) == 1
    seen.clear()
    _fake(monkeypatch, {40.0: 5, 20.0: 0}, seen)
    _conv(tmp_path, fb=0.0).run()
    assert len(seen) == 1


def test_many_defects_skip_the_second_build(tmp_path, monkeypatch):
    _case(tmp_path)
    seen: list = []
    _fake(monkeypatch, {40.0: 25, 20.0: 0}, seen)
    _conv(tmp_path).run()
    assert len(seen) == 1
