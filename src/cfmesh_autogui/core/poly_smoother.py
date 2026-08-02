"""Quality-driven smoothing of the barycentric dual polyhedral mesh.

Why this exists
---------------
The dual rebuild produces geometrically valid cells, but checkMesh's
non-orthogonality and skewness metrics are not minimized by the construction
itself. Commercial meshers post-process the dual with a smoothing pass:
interior vertices are relaxed (Laplacian) while the boundary stays fixed
(the dual boundary is an exact subdivision of the input surface — moving
boundary vertices would break the bit-perfect surface contract).

Safety contract
---------------
Every smoothing pass is *accepted only if* the in-process defect detector
(the same one that reproduces checkMesh exactly, 895/895 on the reference
valve) reports no more total defects than the current best, AND no cell
volume becomes non-positive. The best mesh seen across all passes is the one
that is written, so enabling smoothing can never regress the output — it can
only leave it unchanged or improve it.

Only interior vertices move: a vertex that belongs to at least one boundary
face is pinned. Topology (faces/owner/neighbour) never changes.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def _boundary_vertex_mask(points: np.ndarray, faces: list, n_int: int) -> np.ndarray:
    """True for every vertex used by at least one boundary face (>= n_int).

    The dual's boundary vertices lie exactly on the input surface and are
    never allowed to move.
    """
    mask = np.zeros(len(points), dtype=bool)
    for f in faces[n_int:]:
        mask[f] = True
    return mask


def _vertex_neighbours(points: np.ndarray, faces: list) -> list[list[int]]:
    """Adjacency: for each vertex, the list of vertices sharing an edge.

    Two vertices are neighbours if they appear consecutively (cyclically) in
    any face. Only faces' vertices are used — the dual is a closed 2-manifold,
    so this fully captures the edge graph.
    """
    n = len(points)
    adj: list[set[int]] = [set() for _ in range(n)]
    for f in faces:
        m = len(f)
        for i in range(m):
            a, b = f[i], f[(i + 1) % m]
            adj[a].add(b)
            adj[b].add(a)
    return [sorted(s) for s in adj]


def _laplacian_relax(
    points: np.ndarray,
    adj: list[list[int]],
    boundary: np.ndarray,
    relax: float,
) -> np.ndarray:
    """One Laplacian relaxation step on interior vertices.

    new_pos[v] = pos[v] + relax * (mean(neighbours) - pos[v]).
    Boundary vertices are copied unchanged.
    """
    out = points.copy()
    for v in range(len(points)):
        if boundary[v]:
            continue
        nbrs = adj[v]
        if not nbrs:
            continue
        mean = points[nbrs].mean(axis=0)
        out[v] = points[v] + relax * (mean - points[v])
    return out


def smooth_dual_mesh(
    points: np.ndarray,
    faces: list,
    owner: np.ndarray,
    neigh: np.ndarray,
    n_int: int,
    n_cells: int,
    detect_defects,
    face_geometry,
    cell_centres,
    iterations: int = 3,
    relaxation: float = 0.5,
    log=None,
) -> tuple[np.ndarray, dict]:
    """Smooth interior dual vertices, keep-best w.r.t. the defect detector.

    Returns ``(points, best_counts)`` — the best points seen (never worse
    than the input, by construction) and the defect breakdown at that point.

    ``detect_defects``/``face_geometry``/``cell_centres`` are the in-process
    replicas from ``tet_poly_dual``, so acceptance is measured with exactly
    the same metric checkMesh uses.
    """
    if iterations <= 0:
        return points, {}

    boundary = _boundary_vertex_mask(points, faces, n_int)
    adj = _vertex_neighbours(points, faces)

    best_points = points
    best_total: int | None = None
    best_counts: dict = {}
    sf, cf = face_geometry(points, faces)
    ctr, vol = cell_centres(sf, cf, owner, neigh, n_int, n_cells)
    _, counts = detect_defects(points, faces, sf, cf, ctr, owner, neigh, n_int, n_cells)
    best_total = counts["pyramid"] + counts["non_ortho"] + counts["skew"]
    best_counts = dict(counts)

    cur_points = points
    for it in range(1, iterations + 1):
        cand = _laplacian_relax(cur_points, adj, boundary, relaxation)
        sf, cf = face_geometry(cand, faces)
        ctr, vol = cell_centres(sf, cf, owner, neigh, n_int, n_cells)
        if np.any(vol <= 0.0):
            if log:
                log(f"[poly 7/9] smoothing iter {it}: rejected (negative cell volume)")
            break  # divergence — stop smoothing entirely
        _, counts = detect_defects(cand, faces, sf, cf, ctr, owner, neigh, n_int, n_cells)
        total = counts["pyramid"] + counts["non_ortho"] + counts["skew"]
        if log:
            log(
                f"[poly 7/9] smoothing iter {it}: defects "
                f"{counts['pyramid']}/{counts['non_ortho']}/{counts['skew']} "
                f"(total {total})"
            )
        if total < best_total:
            best_total = total
            best_points = cand
            best_counts = dict(counts)
            cur_points = cand
            if total == 0:
                break
        elif total == best_total:
            # Accept a pass that keeps the same quality (moves toward a
            # smoother configuration) but never regresses.
            cur_points = cand
        else:
            # Quality regressed — keep the previous configuration and stop.
            break

    if log:
        log(f"[poly 7/9] smoothing done: best total defects {best_total}")
    return best_points, best_counts
