"""Concave-edge cell split: an L-shaped cell whose centroid falls in front
of its smaller wall face must be cut in two along the concave edge, with the
wall untouched and a valid polyMesh topology."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core.poly_cell_split import (  # noqa: E402
    relax_boundary_pyramids,
    split_concave_boundary_cells,
)
from polyfoammesh.core.tet_poly_dual import (  # noqa: E402
    _cell_centres,
    _detect_defects,
    _face_geometry,
)

# Lopsided L (long arm along y): the centroid sits above the y=1 wall face.
_L = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 6), (0, 6)]


def _three_slab_mesh():
    """Three stacked L prisms; the middle one (cell 0) is the test cell."""
    levels = [-1.0, 0.0, 1.0, 2.0]
    points = np.array(
        [(x, y, z) for z in levels for (x, y) in _L], dtype=float,
    )
    n = len(_L)

    def ring(level):
        return [level * n + i for i in range(n)]

    faces = [ring(2), list(reversed(ring(1)))]          # 0|1 at z=1, 0|2 at z=0
    owner = [0, 0]
    neigh = [1, 2]
    for cell, (lo, hi) in ((0, (1, 2)), (1, (2, 3)), (2, (0, 1))):
        for i in range(n):
            j = (i + 1) % n
            faces.append([lo * n + i, lo * n + j, hi * n + j, hi * n + i])
            owner.append(cell)
    faces.append(ring(3))                               # top of cell 1
    owner.append(1)
    faces.append(list(reversed(ring(0))))               # bottom of cell 2
    owner.append(2)
    patches = [{"name": "wall", "startFace": 2, "nFaces": len(faces) - 2}]
    return points, faces, np.array(owner), np.array(neigh), 2, 3, patches


def _score(pts, faces, owner, neigh, n_int, n_cells):
    sf, cf = _face_geometry(pts, faces)
    ctr, vol = _cell_centres(sf, cf, owner, neigh, n_int, n_cells)
    _, cnt = _detect_defects(pts, faces, sf, cf, ctr, owner, neigh, n_int, n_cells)
    pv = (sf[n_int:] * (cf[n_int:] - ctr[owner[n_int:]])).sum(axis=1)
    return (cnt["pyramid"] + cnt["non_ortho"] + cnt["skew"], float(vol.min()),
            np.where(pv <= 0.0)[0] + n_int, sf, cf, ctr, vol)


def _assert_closed_cells(faces, owner, neigh, n_cells):
    for c in range(n_cells):
        edges: Counter = Counter()
        mine = [f for f, o in zip(faces, owner) if o == c]
        mine += [faces[i] for i, nb in enumerate(neigh) if nb == c]
        for f in mine:
            for a, b in zip(f, f[1:] + f[:1]):
                edges[(min(a, b), max(a, b))] += 1
        assert set(edges.values()) == {2}, f"cell {c} is not closed"


def test_l_cell_is_split_along_the_concave_edge():
    points, faces, owner, neigh, n_int, n_cells, patches = _three_slab_mesh()
    before = _score(points, faces, owner, neigh, n_int, n_cells)
    assert before[0] == 3 and len(before[2]) == 3   # one bad wall face per slab
    vol_before = float(before[6].sum())
    wall_before = sorted(sorted(f) for f in faces[n_int:])

    f2, o2, nb2, ni2, nc2, pat2, rep = split_concave_boundary_cells(
        points, faces, owner, neigh, n_int, n_cells, patches, _score,
    )

    after = _score(points, f2, o2, nb2, ni2, nc2)
    assert after[0] < before[0]
    assert rep["splits"] >= 1 and nc2 == n_cells + rep["splits"]
    assert after[1] > 0.0
    assert abs(float(after[6].sum()) - vol_before) < 1e-9
    # the wall is untouched: same boundary faces, patch table still tiles them
    assert sorted(sorted(f) for f in f2[ni2:]) == wall_before
    assert pat2[0]["startFace"] == ni2 and pat2[0]["nFaces"] == len(f2) - ni2
    # OpenFOAM ordering: owner < neighbour, internal faces upper-triangular
    assert np.all(o2[:ni2] < nb2)
    pairs = list(zip(o2[:ni2].tolist(), nb2.tolist()))
    assert pairs == sorted(pairs)
    _assert_closed_cells(f2, o2.tolist(), nb2.tolist(), nc2)
    # inputs are not modified
    assert len(faces) == 22 and patches[0]["startFace"] == 2


def test_clean_mesh_is_returned_unchanged():
    points, faces, owner, neigh, n_int, n_cells, patches = _three_slab_mesh()
    points = points.copy()
    points[:, 1] = np.minimum(points[:, 1], 2.0)    # symmetric L: no defect
    assert _score(points, faces, owner, neigh, n_int, n_cells)[0] == 0

    f2, o2, nb2, ni2, nc2, pat2, rep = split_concave_boundary_cells(
        points, faces, owner, neigh, n_int, n_cells, patches, _score,
    )
    assert rep["splits"] == 0 and nc2 == n_cells and ni2 == n_int
    assert [list(f) for f in f2] == [list(f) for f in faces]

    pts2, rrep = relax_boundary_pyramids(
        points, faces, owner, neigh, n_int, n_cells, _score,
    )
    assert pts2 is points and rrep["cleared"] == 0


def test_relaxation_never_moves_wall_vertices():
    """Every vertex of this mesh is on the wall: nothing may move."""
    points, faces, owner, neigh, n_int, n_cells, _ = _three_slab_mesh()
    pts2, rep = relax_boundary_pyramids(
        points, faces, owner, neigh, n_int, n_cells, _score,
    )
    assert pts2 is points
    assert rep["cleared"] == 0 and rep["stuck"] >= 1
