"""Boundary-star dissolution helpers for the poly dual (opt-in, default off)."""
from __future__ import annotations

import numpy as np

import polyfoammesh.core.tet_poly_dual as tpd
from polyfoammesh.core.tet_poly_dual import _newell_area, _splice_boundary_loops


class _StarP:
    """Primal stub for `_boundary_star_areas` (points + bnd_tri only)."""

    def __init__(self):
        self.points = np.array([
            [0.0, 0.0, 0.0],    # 0 tiny star centre
            [0.01, 0.0, 0.0],   # 1
            [1.0, 0.0, 0.0],    # 2 big star centre
            [1.01, 0.0, 0.0],   # 3
            [1.0, 1.0, 0.0],    # 4
            [0.0, 1.0, 0.0],    # 5
        ])
        # t0/t3 tiny (~5e-3), t1/t2 big (~0.5)
        self.bnd_tri = np.array([
            [0, 1, 5],
            [1, 2, 5],
            [2, 4, 5],
            [2, 3, 4],
        ])
        self.n_bnd = 4


class _EdgeP:
    """Primal stub for `_smooth_boundary_neighbours` (edge fields only)."""

    def __init__(self, feature: bool = False):
        self.n_pi = 100
        self.bnd_e_faces = {10: [0, 1], 11: [0, 2]}
        self.uniq_key = {10: 0 * 100 + 1, 11: 0 * 100 + 5}
        self.feat_edge = {10: feature, 11: False}
        self.patch_of_bnd = np.array([0, 0, 0])


def test_star_areas_flag_the_tiny_star():
    areas = tpd._boundary_star_areas(_StarP())
    assert 0.0 < areas[0] < areas[2]
    assert areas[2] > 0.5


def test_plan_dissolves_tiny_into_largest_neighbour(monkeypatch):
    areas = np.zeros(6)
    areas[0], areas[1], areas[2] = 0.005, 0.505, 1.005
    areas[3], areas[4], areas[5] = 0.005, 0.505, 1.005
    monkeypatch.setattr(tpd, "_boundary_star_areas", lambda P: areas)
    monkeypatch.setattr(
        tpd, "_smooth_boundary_neighbours",
        lambda P: {0: [1, 5], 3: [2, 4], 1: [0, 5], 5: [0, 1, 2],
                   2: [1, 5, 3, 4], 4: [3, 2]},
    )
    plan = tpd._plan_star_dissolution(object(), threshold_fraction=0.5)
    assert plan == {0: 5, 3: 2}


def test_plan_is_empty_when_disabled(monkeypatch):
    monkeypatch.setattr(tpd, "_boundary_star_areas", lambda P: np.array([1.0, 1.0]))
    assert tpd._plan_star_dissolution(object(), threshold_fraction=0.0) == {}


def test_smooth_neighbours_exclude_feature_edges():
    assert tpd._smooth_boundary_neighbours(_EdgeP()) == {0: [1, 5], 1: [0], 5: [0]}
    assert tpd._smooth_boundary_neighbours(_EdgeP(feature=True)) == {0: [5], 5: [0]}


def test_splice_two_squares_sharing_an_edge():
    # left square 0-1-2-3, right square sharing edge (1,2) traversed reversed
    xyz = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [2.0, 0.0, 0.0], [2.0, 1.0, 0.0],
    ], dtype=float)
    a = [0, 1, 2, 3]        # left square (area 1)
    b = [1, 4, 5, 2]        # right square (area 1), shared chain (2,1)
    merged = _splice_boundary_loops(a, b, xyz)
    assert merged is not None
    assert len(set(merged)) == 6
    assert abs(_newell_area(xyz, merged) - 2.0) < 1e-9


def test_splice_rejects_disjoint_loops():
    xyz = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [5.0, 5.0, 0.0], [6.0, 5.0, 0.0], [6.0, 6.0, 0.0], [5.0, 6.0, 0.0],
    ], dtype=float)
    assert _splice_boundary_loops([0, 1, 2, 3], [4, 5, 6, 7], xyz) is None


def test_splice_rejects_identical_loops():
    xyz = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
    ], dtype=float)
    assert _splice_boundary_loops([0, 1, 2, 3], [0, 1, 2, 3], xyz) is None
