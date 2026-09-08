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
volume becomes non-positive (and, when ``max_volume_ratio > 0``, no cell
pair violates the volume-ratio guard). The best mesh seen across all passes
is the one that is written, so enabling smoothing can never regress the
output — it can only leave it unchanged or improve it.

Only interior vertices move: a vertex that belongs to at least one boundary
face is pinned. Topology (faces/owner/neighbour) never changes.

Phase-2 upgrades over the plain Laplacian (all safe under the keep-best
contract above):

- ``weighted``: inverse-distance-weighted neighbour average instead of the
  unweighted mean — preserves the local shape better on graded meshes.
- ``quality_objective``: a continuous penalty (excess non-orthogonality² +
  λ·skewness²) breaks ties between configurations with equal defect counts,
  steering the smoothing toward lower *max* non-orthogonality instead of
  stopping at the first equal-count pass.
- backtracking: a pass that would regress the defect count (or the
  objective at equal counts) retries with a halved relaxation step instead
  of aborting the whole smoothing run — smaller moves are more likely to
  improve, so more iterations earn their keep.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np

from polyfoammesh.core.tet_poly_dual import _face_extent

logger = logging.getLogger(__name__)


def quality_objective(
    non_ortho_deg,
    skewness,
    limit_deg: float = 65.0,
    lam: float = 1.0,
) -> float:
    """Continuous quality penalty over internal faces.

    ``quality_objective(non_ortho_deg, skewness, limit_deg=65.0, lam=1.0)``
    = Σ max(0, non_ortho − limit_deg)² + λ·Σ skewness².

    The first term penalises faces whose non-orthogonality exceeds the
    target bar (65° default — stricter than the detector's 70°, so the
    smoother keeps pushing well inside the checkMesh limit); the second
    penalises skewness.  Both are continuous, so the objective can rank two
    configurations that the count-based detector considers equal.
    """
    n = np.asarray(non_ortho_deg, dtype=np.float64)
    s = np.asarray(skewness, dtype=np.float64)
    excess = np.clip(n - limit_deg, 0.0, None)
    return float((excess ** 2).sum()) + float(lam * (s ** 2).sum())


def _continuous_metrics(sf, cf, ctr, owner, neigh, n_int, points, faces):
    """Per-internal-face (non_ortho_deg, skewness) — the detector's math,
    without the thresholding, so the objective sees continuous values."""
    if n_int == 0:
        return np.zeros(0), np.zeros(0)
    d = ctr[neigh] - ctr[owner[:n_int]]
    dn = np.linalg.norm(d, axis=1)
    sn = np.linalg.norm(sf[:n_int], axis=1)
    cos = (d * sf[:n_int]).sum(axis=1) / np.maximum(dn * sn, 1e-300)
    non_ortho_deg = np.rad2deg(np.arccos(np.clip(cos, -1.0, 1.0)))

    cpf = cf[:n_int] - ctr[owner[:n_int]]
    denom = (sf[:n_int] * d).sum(axis=1)
    scale = (sf[:n_int] * cpf).sum(axis=1) / np.where(np.abs(denom) > 0, denom, 1e-300)
    sv = cpf - scale[:, None] * d
    svm = np.linalg.norm(sv, axis=1)
    fd = 0.2 * dn + _face_extent(points, faces, cf, sv, n_int)
    skew = svm / np.maximum(fd, 1e-300)
    return non_ortho_deg, skew


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
    weighted: bool = True,
) -> np.ndarray:
    """One Laplacian relaxation step on interior vertices.

    new_pos[v] = pos[v] + relax * (weighted_mean(neighbours) - pos[v]).
    ``weighted`` uses inverse-distance weights (closer neighbours pull
    harder, preserving the local shape); otherwise it is the plain mean.
    Boundary vertices are copied unchanged.
    """
    out = points.copy()
    for v in range(len(points)):
        if boundary[v]:
            continue
        nbrs = adj[v]
        if not nbrs:
            continue
        if weighted:
            diff = points[nbrs] - points[v]
            w = 1.0 / np.maximum(np.linalg.norm(diff, axis=1), 1e-300)
            mean = points[v] + (w[:, None] * diff).sum(axis=0) / w.sum()
        else:
            mean = points[nbrs].mean(axis=0)
        out[v] = points[v] + relax * (mean - points[v])
    return out


def _max_pair_volume_ratio(vol, owner, neigh, n_int) -> float:
    """Max over internal faces of max(v_o, v_n)/min(v_o, v_n)."""
    if n_int == 0:
        return 0.0
    vo = vol[owner[:n_int]]
    vn = vol[neigh]
    mn = np.minimum(vo, vn)
    mx = np.maximum(vo, vn)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = np.where(mn > 0, mx / np.maximum(mn, 1e-300), np.inf)
    return float(np.max(ratios)) if len(ratios) else 0.0


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
    weighted: bool = True,
    objective: Callable = quality_objective,
    objective_limit_deg: float = 65.0,
    backtrack: bool = True,
    max_volume_ratio: float = 0.0,
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
    best_obj: float | None = None
    best_counts: dict = {}
    sf, cf = face_geometry(points, faces)
    ctr, vol = cell_centres(sf, cf, owner, neigh, n_int, n_cells)
    _, counts = detect_defects(points, faces, sf, cf, ctr, owner, neigh, n_int, n_cells)
    best_total = counts["pyramid"] + counts["non_ortho"] + counts["skew"]
    best_obj = _current_objective(
        objective, objective_limit_deg, sf, cf, ctr, owner, neigh, n_int,
        points, faces,
    )
    best_counts = dict(counts)

    cur_points = points
    for it in range(1, iterations + 1):
        step = relaxation
        accepted = False
        for _bt in range(6):  # full step + up to 5 backtracking halvings
            cand = _laplacian_relax(cur_points, adj, boundary, step, weighted=weighted)
            sf_c, cf_c = face_geometry(cand, faces)
            ctr_c, vol_c = cell_centres(sf_c, cf_c, owner, neigh, n_int, n_cells)
            if np.any(vol_c <= 0.0):
                step *= 0.5
                continue
            if max_volume_ratio > 0.0 and (
                _max_pair_volume_ratio(vol_c, owner, neigh, n_int) > max_volume_ratio
            ):
                step *= 0.5
                continue
            _, cand_counts = detect_defects(
                cand, faces, sf_c, cf_c, ctr_c, owner, neigh, n_int, n_cells,
            )
            cand_total = (
                cand_counts["pyramid"] + cand_counts["non_ortho"] + cand_counts["skew"]
            )
            if log:
                log(
                    f"[poly 7/9] smoothing iter {it} (step {step:.3f}): defects "
                    f"{cand_counts['pyramid']}/{cand_counts['non_ortho']}/"
                    f"{cand_counts['skew']} (total {cand_total})"
                )
            if cand_total < best_total:
                cand_obj = _current_objective(
                    objective, objective_limit_deg, sf_c, cf_c, ctr_c, owner, neigh,
                    n_int, cand, faces,
                )
                best_total = cand_total
                best_obj = cand_obj
                best_points = cand
                best_counts = dict(cand_counts)
                cur_points = cand
                accepted = True
                break
            if cand_total == best_total:
                # Continuous objective breaks the tie: accept only when it
                # does not regress (moves toward a smoother configuration).
                # Keep the best-objective configuration among equal counts so
                # the returned mesh is the smoothest at the minimum count.
                cand_obj = _current_objective(
                    objective, objective_limit_deg, sf_c, cf_c, ctr_c, owner, neigh,
                    n_int, cand, faces,
                )
                if best_obj is None or cand_obj <= best_obj:
                    best_obj = cand_obj
                    best_points = cand
                    best_counts = dict(cand_counts)
                    cur_points = cand
                    accepted = True
                    break
            # Quality regressed (counts or objective) — backtrack to a
            # smaller step; give up on this iteration when the step is
            # exhausted, and stop smoothing entirely (the configuration is
            # stable against this direction of movement).
            if not backtrack or step * 0.5 < 1e-4:
                break
            step *= 0.5
        if not accepted:
            if log:
                log(
                    f"[poly 7/9] smoothing iter {it}: no improving step found — "
                    "configuration stable, stop"
                )
            break
        if best_total == 0:
            break

    if log:
        log(f"[poly 7/9] smoothing done: best total defects {best_total}")
    return best_points, best_counts


def _current_objective(objective, limit_deg, sf, cf, ctr, owner, neigh, n_int,
                       points, faces) -> float:
    non_ortho, skew = _continuous_metrics(sf, cf, ctr, owner, neigh, n_int, points, faces)
    return objective(non_ortho, skew, limit_deg=limit_deg)
