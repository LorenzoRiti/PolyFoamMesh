"""The adaptive sizing field may not blow the tet count up: DN1000 valve,
71 mm cell: 6.7M tets / 51 min with the field, 73k tets / 19 s without."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core import ftetwild_wrapper as ftw  # noqa: E402

MAXC, H, VF = 0.07, 0.07, 0.35          # max cell, grid spacing, fluid volume


def _growth(v, refined):
    inv0 = 1.0 / MAXC ** 3
    return 1.0 + ((1.0 / v[refined] ** 3) - inv0).sum() * H ** 3 * 0.5 / (VF * inv0)


def _field(n_refined=400, target=0.004, n_total=2000):
    v = np.full(n_total, MAXC)
    v[:n_refined] = target
    return v


def test_field_over_budget_is_scaled_down_and_meets_the_budget():
    v = _field()
    out, m, g0, g1 = ftw._cap_field_growth(v, MAXC, H, VF, 5.0)
    assert g0 > 50 and m > 1.0
    assert g1 <= 5.0 * 1.001
    refined = v < MAXC * 0.999
    assert _growth(out, refined) == g1 or abs(_growth(out, refined) - g1) < 1e-9
    assert out.max() <= MAXC + 1e-15            # never coarser than the max cell
    assert (out[~refined] == MAXC).all()        # unrefined points untouched


def test_grading_is_kept_when_scaling():
    v = _field()
    v[:100] = 0.002                              # finest region
    out, m, _g0, _g1 = ftw._cap_field_growth(v, MAXC, H, VF, 4.0)
    assert out[0] < out[200] < MAXC              # finer stays finer
    assert np.isclose(out[0] / out[200], 0.002 / 0.004, rtol=1e-9)


def test_field_within_budget_is_untouched():
    v = _field(n_refined=3, target=0.06)
    out, m, g0, g1 = ftw._cap_field_growth(v, MAXC, H, VF, 5.0)
    assert m == 1.0 and g0 == g1 and (out == v).all()


def test_no_budget_or_nothing_refined_is_a_noop():
    v = _field()
    assert ftw._cap_field_growth(v, MAXC, H, VF, 0.0)[1] == 1.0
    flat = np.full(100, MAXC)
    assert ftw._cap_field_growth(flat, MAXC, H, VF, 5.0)[1] == 1.0
