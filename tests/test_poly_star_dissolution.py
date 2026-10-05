"""Boundary-star dissolution helpers for the poly dual (opt-in, default off)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import polyfoammesh.core.tet_poly_dual as tpd
from polyfoammesh.core.tet_poly_dual import _newell_area, _splice_boundary_loops
from test_tet_poly_dual import build_tet_case  # noqa: E402


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


def test_splice_handles_wrap_around_chain():
    # same two squares, but `a` starts mid-shared-chain: the (1,2) chain
    # straddles a[0]/a[-1] and the linear run-scan must not miss it
    xyz = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [2.0, 0.0, 0.0], [2.0, 1.0, 0.0],
    ], dtype=float)
    a = [2, 3, 0, 1]        # left square rotated: shared chain wraps the seam
    b = [1, 4, 5, 2]        # right square (area 1), shared chain (2,1)
    merged = _splice_boundary_loops(a, b, xyz)
    assert merged is not None
    assert len(set(merged)) == 6
    assert abs(_newell_area(xyz, merged) - 2.0) < 1e-9


def test_splice_rejects_overlapping_union():
    # vertex 6 pokes into the right square: the union is not a simple polygon,
    # so its Newell area is not area(a) + area(b)
    xyz = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [2.0, 0.0, 0.0], [2.0, 1.0, 0.0], [2.0, 0.5, 0.0],
    ], dtype=float)
    a = [0, 1, 2, 6]
    b = [1, 4, 5, 2]
    assert _splice_boundary_loops(a, b, xyz) is None


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


# ---------------------------------------------------------------------------
# converter wiring: the flag exists, is >= 0, and OFF is byte-identical
# ---------------------------------------------------------------------------

def test_converter_rejects_negative_threshold():
    with pytest.raises(ValueError):
        tpd.TetPolyDualConverter(Path("."), dissolve_tiny_boundary_stars=-0.1)


def _cube_case_points_tets() -> tuple[np.ndarray, list[list[int]]]:
    """The cube fixture of tests/test_tet_poly_dual.py::test_cube_invariants."""
    corners = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0], [1.0, 0.0, 1.0], [1.0, 1.0, 1.0], [0.0, 1.0, 1.0],
    ])
    pts = np.vstack([corners, corners.mean(axis=0)])
    faces2d = [
        [0, 1, 2, 3], [0, 4, 5, 1], [1, 5, 6, 2],
        [2, 6, 7, 3], [3, 7, 4, 0], [4, 7, 6, 5],
    ]
    tets = []
    for f in faces2d:
        for tri in ((f[0], f[1], f[2]), (f[0], f[2], f[3])):
            tets.append([tri[0], tri[1], tri[2], 8])
    return pts, tets


@pytest.mark.parametrize("collapse", [False, True])
def test_default_off_is_byte_identical(tmp_path, collapse):
    """Global constraint: `dissolve_tiny_boundary_stars=0.0` (and the absent
    default) must leave the written polyMesh byte-identical — in collapse
    mode too, which is the only mode where the pass can ever run."""
    pts, tets = _cube_case_points_tets()
    conv_kwargs: dict = {"log": lambda _m: None}
    if collapse:
        conv_kwargs["collapse_smooth_edges"] = True
    case_off = build_tet_case(tmp_path / "off", pts, tets)
    case_explicit = build_tet_case(tmp_path / "explicit", pts, tets)

    res_off = tpd.TetPolyDualConverter(case_off, **conv_kwargs).run()
    res_explicit = tpd.TetPolyDualConverter(
        case_explicit, dissolve_tiny_boundary_stars=0.0, **conv_kwargs,
    ).run()
    assert res_off.success, res_off.errors
    assert res_explicit.success, res_explicit.errors
    assert res_off.n_dissolved_stars == 0
    assert res_explicit.n_dissolved_stars == 0
    assert res_explicit.warnings == []

    poly_off = case_off / "constant" / "polyMesh"
    poly_explicit = case_explicit / "constant" / "polyMesh"
    names = sorted(p.name for p in poly_off.iterdir())
    assert names == sorted(p.name for p in poly_explicit.iterdir())
    assert names, "the converter wrote no polyMesh files"
    for name in names:
        assert (poly_off / name).read_bytes() == (poly_explicit / name).read_bytes(), name


def _boundary_face_count(case_dir: Path) -> int:
    from polyfoammesh.core.foam_mesh_io import read_polymesh

    _pts, faces, _own, neigh, _patches = read_polymesh(case_dir / "constant" / "polyMesh")
    return len(faces) - len(neigh)


def test_dissolution_splices_when_plan_forces_a_merge(tmp_path, monkeypatch):
    """Forced plan {0: 1} on the cube: the two corner polygons must merge
    into one (boundary face count drops by 1) and the mesh stays valid."""
    pts, tets = _cube_case_points_tets()
    case = build_tet_case(tmp_path / "splice", pts, tets)
    monkeypatch.setattr(tpd, "_plan_star_dissolution", lambda P, f: {0: 1})
    conv = tpd.TetPolyDualConverter(
        case, log=lambda _m: None, collapse_smooth_edges=True,
        dissolve_tiny_boundary_stars=1.0,
    )
    res = conv.run()
    assert res.success, res.errors
    assert res.n_dissolved_stars == 1
    assert conv._last_dissolve_fallbacks == 0
    assert getattr(res, "dissolve_warning", None) is None

    case_ref = build_tet_case(tmp_path / "ref", pts, tets)
    tpd.TetPolyDualConverter(
        case_ref, log=lambda _m: None, collapse_smooth_edges=True,
    ).run()
    # both stars split into 3 sub-faces at the cube's feature edges; the union
    # keeps 4 feature points (the shared edge's midpoint becomes interior), so
    # the merged boundary has 2 fewer faces than the two separate stars
    assert _boundary_face_count(case) == _boundary_face_count(case_ref) - 2


def test_dissolution_falls_back_when_splice_refuses(tmp_path, monkeypatch):
    """A refused splice must keep the absorbed polygon as a separate face
    (count unchanged) and record the fallback - never a corrupt union."""
    pts, tets = _cube_case_points_tets()
    case = build_tet_case(tmp_path / "fallback", pts, tets)
    monkeypatch.setattr(tpd, "_plan_star_dissolution", lambda P, f: {0: 1})
    monkeypatch.setattr(tpd, "_splice_boundary_loops", lambda a, b, xyz: None)
    conv = tpd.TetPolyDualConverter(
        case, log=lambda _m: None, collapse_smooth_edges=True,
        dissolve_tiny_boundary_stars=1.0,
    )
    res = conv.run()
    assert res.success, res.errors
    assert res.n_dissolved_stars == 1
    assert conv._last_dissolve_fallbacks == 1

    case_ref = build_tet_case(tmp_path / "ref2", pts, tets)
    tpd.TetPolyDualConverter(
        case_ref, log=lambda _m: None, collapse_smooth_edges=True,
    ).run()
    assert _boundary_face_count(case) == _boundary_face_count(case_ref)
