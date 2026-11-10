"""Two bare wall zones touching at one vertex must be merged: their
terminator strips would otherwise share the extruded column above it and
leave boundary edges with 4 faces (BL rejected on slot1/concave_bracket)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core.bl_poly import _close_bare_pinches  # noqa: E402


def _grid(n: int = 3):
    """n x n quads on an (n+1) x (n+1) vertex grid, all boundary faces."""
    def vid(i, j):
        return i * (n + 1) + j

    faces, index = [], {}
    for i in range(n):
        for j in range(n):
            index[(i, j)] = len(faces)
            faces.append([vid(i, j), vid(i + 1, j), vid(i + 1, j + 1), vid(i, j + 1)])
    return faces, index


def test_diagonal_bare_faces_are_merged():
    faces, idx = _grid()
    bnd = list(range(len(faces)))
    nf = {bi: 5 for bi in bnd}
    nf[idx[(0, 0)]] = 0
    nf[idx[(1, 1)]] = 0

    bared = _close_bare_pinches(faces, 0, bnd, nf)

    assert bared == 2
    assert nf[idx[(0, 1)]] == 0 and nf[idx[(1, 0)]] == 0
    assert nf[idx[(2, 2)]] == 5
    # fixpoint: a second pass has nothing left to do
    assert _close_bare_pinches(faces, 0, bnd, nf) == 0


def test_edge_adjacent_bare_faces_are_left_alone():
    faces, idx = _grid()
    bnd = list(range(len(faces)))
    nf = {bi: 5 for bi in bnd}
    nf[idx[(0, 0)]] = 0
    nf[idx[(0, 1)]] = 0
    before = dict(nf)

    assert _close_bare_pinches(faces, 0, bnd, nf) == 0
    assert nf == before


def test_unselected_face_counts_as_bare():
    """A face outside the selection (another patch) pinches like a 0-layer one."""
    faces, idx = _grid()
    bnd = [bi for bi in range(len(faces)) if bi != idx[(0, 0)]]
    nf = {i: 5 for i in range(len(bnd))}
    nf[bnd.index(idx[(1, 1)])] = 0

    assert _close_bare_pinches(faces, 0, bnd, nf) == 2
    assert nf[bnd.index(idx[(0, 1)])] == 0 and nf[bnd.index(idx[(1, 0)])] == 0


def test_fully_layered_wall_is_untouched():
    faces, _ = _grid()
    bnd = list(range(len(faces)))
    nf = {bi: 5 for bi in bnd}
    assert _close_bare_pinches(faces, 0, bnd, nf) == 0
    assert set(nf.values()) == {5}
