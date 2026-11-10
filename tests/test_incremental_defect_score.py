"""The incremental defect score must count exactly what the full detector
counts after any vertex move (it replaces 945 full re-detections on DN1000)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core import tet_poly_dual as tpd  # noqa: E402


def _hex_grid(n=4):
    """Structured n^3 hex mesh as a polyMesh (owner < neighbour)."""
    idx = lambda i, j, k: i + (n + 1) * (j + (n + 1) * k)  # noqa: E731
    pts = np.array([(i, j, k) for k in range(n + 1) for j in range(n + 1)
                    for i in range(n + 1)], dtype=float)
    cell = lambda i, j, k: i + n * (j + n * k)  # noqa: E731
    internal, bnd = [], []
    for k in range(n):
        for j in range(n):
            for i in range(n):
                c = cell(i, j, k)
                quads = {
                    (1, 0, 0): [idx(i + 1, j, k), idx(i + 1, j + 1, k),
                                idx(i + 1, j + 1, k + 1), idx(i + 1, j, k + 1)],
                    (0, 1, 0): [idx(i, j + 1, k), idx(i, j + 1, k + 1),
                                idx(i + 1, j + 1, k + 1), idx(i + 1, j + 1, k)],
                    (0, 0, 1): [idx(i, j, k + 1), idx(i + 1, j, k + 1),
                                idx(i + 1, j + 1, k + 1), idx(i, j + 1, k + 1)],
                }
                for (di, dj, dk), q in quads.items():
                    ii, jj, kk = i + di, j + dj, k + dk
                    if ii < n and jj < n and kk < n:
                        internal.append((c, cell(ii, jj, kk), q))
                    else:
                        bnd.append((c, q))
                if i == 0:
                    bnd.append((c, [idx(0, j, k), idx(0, j, k + 1),
                                    idx(0, j + 1, k + 1), idx(0, j + 1, k)]))
                if j == 0:
                    bnd.append((c, [idx(i, 0, k), idx(i + 1, 0, k),
                                    idx(i + 1, 0, k + 1), idx(i, 0, k + 1)]))
                if k == 0:
                    bnd.append((c, [idx(i, j, 0), idx(i, j + 1, 0),
                                    idx(i + 1, j + 1, 0), idx(i + 1, j, 0)]))
    internal.sort(key=lambda t: (t[0], t[1]))
    faces = [q for _, _, q in internal] + [q for _, q in bnd]
    owner = np.array([o for o, _, _ in internal] + [o for o, _ in bnd])
    neigh = np.array([nb for _, nb, _ in internal])
    return pts, faces, owner, neigh, len(internal), n ** 3


def _full(pts, faces, owner, neigh, n_int, n_cells):
    sf, cf = tpd._face_geometry(pts, faces)
    ctr, vol = tpd._cell_centres(sf, cf, owner, neigh, n_int, n_cells)
    _, c = tpd._detect_defects(pts, faces, sf, cf, ctr, owner, neigh, n_int,
                               n_cells, boundary_skew=True)
    return c["pyramid"] + c["non_ortho"] + c["skew"], float(vol.min())


def test_incremental_matches_full_detector():
    pts, faces, owner, neigh, n_int, n_cells = _hex_grid()
    inc = tpd.IncrementalDefectScore(pts, faces, owner, neigh, n_int, n_cells,
                                     boundary_skew=True)
    assert inc.total == _full(pts, faces, owner, neigh, n_int, n_cells)[0] == 0
    rng = np.random.default_rng(3)
    cur = pts.copy()
    seen_defects = False
    for step in range(40):
        moved = rng.choice(len(pts), 3, replace=False)
        cand = cur.copy()
        cand[moved] += rng.normal(size=(3, 3)) * 0.6
        tot, vmin = inc.trial(cand, moved)
        ft, fv = _full(cand, faces, owner, neigh, n_int, n_cells)
        assert tot == ft
        assert np.isclose(vmin, fv, rtol=1e-9, atol=1e-12)
        seen_defects |= tot > 0
        if step % 2 == 0:
            inc.accept()
            cur = cand
    assert seen_defects        # the moves really exercised the counters
