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

from polyfoammesh.core.tet_poly_dual import _face_extent, _face_planarity

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
    # see the matching fix/comment in tet_poly_dual._detect_defects: OpenFOAM
    # takes MAX of the two terms, not their SUM.
    fd = np.maximum(0.2 * dn, _face_extent(points, faces, cf, sv, n_int))
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


def _flatten_adjacency(adj: list[list[int]]) -> tuple[np.ndarray, np.ndarray]:
    """Flatten the ragged adjacency list into one (edge_v, edge_n) edge-list
    pair, one row per (vertex, neighbour) occurrence — the representation
    `_laplacian_relax` vectorises over. Topology-only (independent of point
    positions), so this is computed once per smoothing run, not per step.
    """
    degrees = np.fromiter((len(n) for n in adj), dtype=np.int64, count=len(adj))
    edge_v = np.repeat(np.arange(len(adj), dtype=np.int64), degrees)
    edge_n = np.fromiter(
        (n for nbrs in adj for n in nbrs), dtype=np.int64, count=int(degrees.sum()),
    )
    return edge_v, edge_n


def _laplacian_relax(
    points: np.ndarray,
    adj: tuple[np.ndarray, np.ndarray],
    boundary: np.ndarray,
    relax: float,
    weighted: bool = True,
) -> np.ndarray:
    """One Laplacian relaxation step on interior vertices.

    new_pos[v] = pos[v] + relax * (weighted_mean(neighbours) - pos[v]).
    ``weighted`` uses inverse-distance weights (closer neighbours pull
    harder, preserving the local shape); otherwise it is the plain mean.
    Boundary vertices are copied unchanged.

    ``adj`` is the flattened (edge_v, edge_n) pair from `_flatten_adjacency`
    — every (vertex, neighbour) occurrence scattered-summed with
    ``np.add.at`` in one vectorised pass, replacing what used to be an
    explicit per-vertex Python loop (the dominant cost of poly smoothing:
    profiled at ~50% of total conversion time on a 152K-point mesh, and the
    main reason smoothing alone took 27+ minutes on an 11M-point production
    mesh — see docs/residual_risks.md "Poly conversion / smoothing speed").
    """
    edge_v, edge_n = adj
    n = len(points)
    diff = points[edge_n] - points[edge_v]
    if weighted:
        w = 1.0 / np.maximum(np.linalg.norm(diff, axis=1), 1e-300)
    else:
        w = np.ones(len(edge_v), dtype=np.float64)
    wsum = np.zeros(n, dtype=np.float64)
    np.add.at(wsum, edge_v, w)
    wnum = np.zeros((n, 3), dtype=np.float64)
    np.add.at(wnum, edge_v, w[:, None] * diff)

    out = points.copy()
    movable = (~boundary) & (wsum > 0.0)
    out[movable] = points[movable] + relax * (wnum[movable] / wsum[movable, None])
    return out


def _face_area_vertex_gradients(faces: list[list[int]], points: np.ndarray, sf: np.ndarray):
    """For every (face, vertex-in-face) pair, the gradient of that face's
    OWN area with respect to that vertex's position.

    Closed form for a planar polygon face with vertices p_0..p_{m-1} and
    Newell area vector S = 0.5 * sum(p_i x p_{i+1}): treating p_k as the
    only free vertex, p_k appears in exactly two of the cross-product
    terms, and (using u^T[a]_x = (u x a)^T for the cross-product matrix
    [a]_x) the area gradient reduces to

        d(area)/d(p_k) = 0.5 * n_hat x (p_{k-1} - p_{k+1})

    where n_hat = S / |S|. Verified against central finite differences on
    200 random planar polygons (m=3..7), max error 1.6e-9 - see
    notes/getme_compactness_reasoning.md. Vectorised across all faces by
    face-degree bucket, same pattern as `tet_poly_dual._face_extent`.

    Returns ``(vidx, grad)``: ``vidx[i]`` is the global point index and
    ``grad[i]`` the (3,) gradient contribution for the i-th (face, vertex)
    occurrence, in the same flattened order as iterating faces then their
    vertices (i.e. compatible with ``np.add.at`` scatter to a per-vertex
    accumulator keyed by ``vidx``).
    """
    n_faces = len(faces)
    sizes = np.fromiter((len(f) for f in faces), dtype=np.int64, count=n_faces)
    n_occ = int(sizes.sum())
    vidx = np.empty(n_occ, dtype=np.int64)
    grad = np.empty((n_occ, 3), dtype=np.float64)
    nhat_all = sf / np.maximum(np.linalg.norm(sf, axis=1), 1e-300)[:, None]

    pos = 0
    for m in np.unique(sizes):
        fids = np.flatnonzero(sizes == m)
        v = np.array([faces[i] for i in fids], dtype=np.int64)  # (k, m)
        p = points[v]  # (k, m, 3)
        p_prev = np.roll(p, 1, axis=1)
        p_next = np.roll(p, -1, axis=1)
        nhat = nhat_all[fids]  # (k, 3)
        # cross(nhat, p_prev - p_next) broadcast over the m vertices
        diff = p_prev - p_next  # (k, m, 3)
        g = 0.5 * np.cross(nhat[:, None, :], diff)  # (k, m, 3)
        k = len(fids)
        vidx[pos:pos + k * m] = v.reshape(-1)
        grad[pos:pos + k * m] = g.reshape(-1, 3)
        pos += k * m
    return vidx, grad


def _compactness_step(
    points: np.ndarray,
    faces: list[list[int]],
    owner: np.ndarray,
    neigh: np.ndarray,
    n_int: int,
    n_cells: int,
    boundary: np.ndarray,
    sf: np.ndarray,
    step: float,
    adj: tuple[np.ndarray, np.ndarray],
) -> np.ndarray:
    """One GETMe-inspired compactness step: move each interior vertex to
    (approximately) reduce the total surface area of the cells around it,
    weighted by each cell's own area (bigger/rougher cells pull harder) -
    a discretisation of gradient ASCENT of q3(cell) = vol(cell) -
    C^-1 * area(cell)^1.5 (Vartziotis & Wipper's GETMe quality measure,
    arxiv 1406.4333 sec 3.2), dropping the volume term (the existing
    volume-positivity/volume-ratio hard guards in `smooth_dual_mesh`
    already police that separately - see
    notes/getme_compactness_reasoning.md for why this is a safe,
    justified simplification rather than the full q3).

    This targets a DIFFERENT quality axis than `_laplacian_relax`
    (surface-area compactness / aspect ratio, not neighbour-position
    averaging) - it is tried as an ALTERNATIVE candidate step inside the
    same keep-best loop, not a replacement.
    """
    face_area = np.linalg.norm(sf, axis=1)
    cell_area = np.zeros(n_cells, dtype=np.float64)
    np.add.at(cell_area, owner, face_area)
    np.add.at(cell_area, neigh, face_area[:n_int])
    cell_weight = np.sqrt(np.maximum(cell_area, 0.0))

    vidx, area_grad = _face_area_vertex_gradients(faces, points, sf)
    # which face each (face, vertex) occurrence belongs to, to look up
    # both the owner and (if internal) neighbour cell weights
    sizes = np.fromiter((len(f) for f in faces), dtype=np.int64, count=len(faces))
    fidx = np.repeat(np.arange(len(faces), dtype=np.int64), sizes)

    n = len(points)
    accum = np.zeros((n, 3), dtype=np.float64)
    wsum = np.zeros(n, dtype=np.float64)
    is_internal = fidx < n_int
    w_owner = cell_weight[owner[fidx]]
    np.add.at(accum, vidx, -w_owner[:, None] * area_grad)
    np.add.at(wsum, vidx, w_owner)
    if n_int:
        idx_int = np.flatnonzero(is_internal)
        w_neigh = cell_weight[neigh[fidx[idx_int]]]
        np.add.at(accum, vidx[idx_int], -w_neigh[:, None] * area_grad[idx_int])
        np.add.at(wsum, vidx[idx_int], w_neigh)

    # local length scale per vertex: mean distance to its neighbours (the
    # same natural scale `_laplacian_relax` implicitly moves at) - without
    # this, the raw area-gradient direction has no length reference tied
    # to the mesh's own scale, and a fixed `step` would be meaningless
    # (too large on a millimetre-scale mesh, too small on a metre-scale
    # one).
    edge_v, edge_n = adj
    edge_len = np.linalg.norm(points[edge_n] - points[edge_v], axis=1)
    len_sum = np.zeros(n, dtype=np.float64)
    len_cnt = np.zeros(n, dtype=np.float64)
    np.add.at(len_sum, edge_v, edge_len)
    np.add.at(len_cnt, edge_v, 1.0)
    local_scale = np.divide(len_sum, np.maximum(len_cnt, 1.0),
                            out=np.zeros(n), where=len_cnt > 0)

    out = points.copy()
    direction = np.zeros((n, 3), dtype=np.float64)
    movable = (~boundary) & (wsum > 0.0)
    direction[movable] = accum[movable] / wsum[movable, None]
    dnorm = np.linalg.norm(direction, axis=1, keepdims=True)
    unit = np.divide(direction, np.maximum(dnorm, 1e-300), where=dnorm > 1e-300)
    out[movable] = (
        points[movable] + step * local_scale[movable, None] * unit[movable]
    )
    return out


def _face_area_vector_vertex_jacobians(faces: list[list[int]], points: np.ndarray):
    """For every (face, vertex-in-face) pair, the 3x3 Jacobian of that
    face's Newell AREA VECTOR (not magnitude) with respect to that
    vertex's position.

    dS/d(p_k) = 0.5 * (skew(p_{k-1}) - skew(p_{k+1})), where skew(a) is
    the cross-product matrix (skew(a) @ v == a x v). Verified against
    central finite differences on 200 random planar polygons (m=3..7),
    max error 4.2e-10 - see notes/getme_compactness_reasoning.md. This is
    the vector-valued generalisation of `_face_area_vertex_gradients`
    (which only needed d|S|/dp_k); OpenFOAM's aspect-ratio directional
    term needs the per-AXIS component d(S_axis)/dp_k, i.e. individual
    rows of this Jacobian.

    Returns ``(vidx, jac)``: ``vidx[i]`` is the global point index and
    ``jac[i]`` the (3, 3) Jacobian for the i-th (face, vertex) occurrence
    (row = area-vector axis, column = vertex coordinate), in the same
    flattened face-then-vertex order as
    `_face_area_vertex_gradients`.
    """
    n_faces = len(faces)
    sizes = np.fromiter((len(f) for f in faces), dtype=np.int64, count=n_faces)
    n_occ = int(sizes.sum())
    vidx = np.empty(n_occ, dtype=np.int64)
    jac = np.empty((n_occ, 3, 3), dtype=np.float64)

    def skew_batch(a):
        # a: (k, 3) -> (k, 3, 3) cross-product matrices
        z = np.zeros(a.shape[0])
        return np.stack([
            np.stack([z, -a[:, 2], a[:, 1]], axis=1),
            np.stack([a[:, 2], z, -a[:, 0]], axis=1),
            np.stack([-a[:, 1], a[:, 0], z], axis=1),
        ], axis=1)

    pos = 0
    for m in np.unique(sizes):
        fids = np.flatnonzero(sizes == m)
        v = np.array([faces[i] for i in fids], dtype=np.int64)  # (k, m)
        p = points[v]  # (k, m, 3)
        p_prev = np.roll(p, 1, axis=1)
        p_next = np.roll(p, -1, axis=1)
        k = len(fids)
        j = 0.5 * (
            skew_batch(p_prev.reshape(-1, 3)) - skew_batch(p_next.reshape(-1, 3))
        )  # (k*m, 3, 3)
        vidx[pos:pos + k * m] = v.reshape(-1)
        jac[pos:pos + k * m] = j
        pos += k * m
    return vidx, jac


def _axis_balance_step(
    points: np.ndarray,
    faces: list[list[int]],
    owner: np.ndarray,
    neigh: np.ndarray,
    n_int: int,
    n_cells: int,
    boundary: np.ndarray,
    sf: np.ndarray,
    step: float,
    adj: tuple[np.ndarray, np.ndarray],
) -> np.ndarray:
    """A candidate step targeting OpenFOAM's DIRECTIONAL aspect-ratio term
    specifically (primitiveMeshTools::cellClosedness): per cell, the ratio
    of the largest to the smallest of the three axis-wise sums of
    |face-area-vector component| (how much face area "faces" x, y, and z).
    A cell stretched along one axis has small area facing that axis and
    large area facing the other two - measured directly on the cylinder:
    `_compactness_step` (which targets the SEPARATE area/volume^1.5 term
    of the same OpenFOAM formula) reduced non-orthogonality and skewness
    but made real checkMesh aspect ratio WORSE (4.46 -> 6.35) - the two
    terms of the formula are related but distinct, and only one was
    being targeted. See notes/getme_compactness_reasoning.md.

    Minimises a smooth surrogate (variance of the three per-cell axis
    sums, not the non-differentiable max/min ratio itself - the standard
    "pull all three toward their mean" relaxation of a minimax problem)
    via gradient descent, using the closed-form, finite-difference
    verified Jacobian from `_face_area_vector_vertex_jacobians`. Same
    hard-guard/keep-best contract as every other candidate in this file -
    this function only proposes a direction, `smooth_dual_mesh` decides
    whether to accept it.
    """
    face_axis_abs = np.abs(sf)  # (n_faces, 3)
    cell_axis_sum = np.zeros((n_cells, 3), dtype=np.float64)
    np.add.at(cell_axis_sum, owner, face_axis_abs)
    np.add.at(cell_axis_sum, neigh, face_axis_abs[:n_int])
    cell_mean = cell_axis_sum.mean(axis=1, keepdims=True)
    cell_dev = cell_axis_sum - cell_mean  # (n_cells, 3), sums to 0 per row

    sign_s = np.sign(sf)  # (n_faces, 3)
    vidx, jac = _face_area_vector_vertex_jacobians(faces, points)
    sizes = np.fromiter((len(f) for f in faces), dtype=np.int64, count=len(faces))
    fidx = np.repeat(np.arange(len(faces), dtype=np.int64), sizes)

    n = len(points)
    accum = np.zeros((n, 3), dtype=np.float64)
    wsum = np.zeros(n, dtype=np.float64)

    def add_side(cell_of_face):
        # d(variance)/dp_k = sum_axis 2*dev[cell,axis] * sign(S_axis) * J[axis,:]
        dev = cell_dev[cell_of_face]  # (occ, 3)
        s = sign_s[fidx]  # (occ, 3)
        weight = 2.0 * dev * s  # (occ, 3)
        # grad = weight @ J  (contract axis dim with Jacobian's row dim)
        grad = np.einsum("oa,oac->oc", weight, jac)
        cell_scale = np.linalg.norm(cell_axis_sum[cell_of_face], axis=1)
        np.add.at(accum, vidx, -cell_scale[:, None] * grad)
        np.add.at(wsum, vidx, cell_scale)

    add_side(owner[fidx])
    if n_int:
        idx_int = np.flatnonzero(fidx < n_int)
        add_side_neigh_cells = neigh[fidx[idx_int]]
        dev = cell_dev[add_side_neigh_cells]
        s = sign_s[fidx[idx_int]]
        weight = 2.0 * dev * s
        grad = np.einsum("oa,oac->oc", weight, jac[idx_int])
        cell_scale = np.linalg.norm(cell_axis_sum[add_side_neigh_cells], axis=1)
        np.add.at(accum, vidx[idx_int], -cell_scale[:, None] * grad)
        np.add.at(wsum, vidx[idx_int], cell_scale)

    edge_v, edge_n = adj
    edge_len = np.linalg.norm(points[edge_n] - points[edge_v], axis=1)
    len_sum = np.zeros(n, dtype=np.float64)
    len_cnt = np.zeros(n, dtype=np.float64)
    np.add.at(len_sum, edge_v, edge_len)
    np.add.at(len_cnt, edge_v, 1.0)
    local_scale = np.divide(len_sum, np.maximum(len_cnt, 1.0),
                            out=np.zeros(n), where=len_cnt > 0)

    out = points.copy()
    direction = np.zeros((n, 3), dtype=np.float64)
    movable = (~boundary) & (wsum > 0.0)
    direction[movable] = accum[movable] / wsum[movable, None]
    dnorm = np.linalg.norm(direction, axis=1, keepdims=True)
    unit = np.divide(direction, np.maximum(dnorm, 1e-300), where=dnorm > 1e-300)
    out[movable] = (
        points[movable] + step * local_scale[movable, None] * unit[movable]
    )
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
    use_compactness: bool = False,
    use_axis_balance: bool = False,
    worst_aspect_thr: float = 0.0,
    zonal_aspect_lam: float = 5000.0,
) -> tuple[np.ndarray, dict]:
    """Smooth interior dual vertices, keep-best w.r.t. the defect detector.

    Returns ``(points, best_counts)`` — the best points seen (never worse
    than the input, by construction) and the defect breakdown at that point.

    ``detect_defects``/``face_geometry``/``cell_centres`` are the in-process
    replicas from ``tet_poly_dual``, so acceptance is measured with exactly
    the same metric checkMesh uses.

    ``use_compactness`` (default False, opt-in, 2026-09-14): also try a
    GETMe-inspired compactness step (`_compactness_step`, reduces total
    cell surface area — the same quality axis behind aspect ratio, which
    plain Laplacian relaxation never targets) as a SECOND candidate at
    every backtracking level, alongside the existing Laplacian move. The
    first candidate (in order: Laplacian, then compactness) that passes
    every existing hard guard (volume positivity, volume ratio,
    planarity) AND improves the defect count/objective is accepted -
    Laplacian's own well-tested behaviour is completely unchanged when
    this flag is off (the default), and unaffected in priority even when
    it's on (compactness only gets a chance where Laplacian's candidate
    at that exact step size did not pass). Measured on a cylinder mesh:
    real checkMesh non-orthogonality 41.7 -> 36.1 deg, skewness 1.28 ->
    1.16 - but aspect ratio got WORSE (4.46 -> 6.35), because
    OpenFOAM's aspect ratio is `max` of this area/volume^1.5 term and a
    SEPARATE directional term (see `use_axis_balance`) that compactness
    alone does not target and can inadvertently worsen.

    ``use_axis_balance`` (default False, opt-in, 2026-09-14): targets
    OpenFOAM's OTHER aspect-ratio term directly (the max/min ratio of
    per-cell axis-wise face-area sums - `_axis_balance_step`), the one
    `use_compactness` alone made worse. Tried as a further candidate in
    the same list, same acceptance contract. See
    notes/getme_compactness_reasoning.md.

    ``worst_aspect_thr`` (default 0.0 = off, opt-in, Lane D2): Freitag-style
    worst-element focus. When > 0, each iteration adds MASKED variants of
    the active candidate moves (displacement zeroed outside the vertices
    of cells currently above the threshold; boundary still pinned) plus a
    zonal objective term ``zonal_aspect_lam * max(0, max_aspect - thr)^2``
    (L_inf on the worst cell — a tail SUM was tried first and measurably
    raised the max while "improving") on top of the existing global aspect
    term. Moves the global steps cannot express (they drag 10k good
    vertices along, vetoing zone-helping moves via offsetting penalties).
    Default ``zonal_aspect_lam`` 5000 ≈ balance vs the base objective at
    cylinder-class scales (measured — re-balance from a scale print when
    applying elsewhere). See notes/zonal_aspect_relaxation_reasoning.md.
    """
    zonal_on = float(worst_aspect_thr) > 0.0
    if iterations <= 0:
        return points, {}

    boundary = _boundary_vertex_mask(points, faces, n_int)
    adj = _flatten_adjacency(_vertex_neighbours(points, faces))

    best_points = points
    best_total: int | None = None
    best_obj: float | None = None
    best_counts: dict = {}
    sf, cf = face_geometry(points, faces)
    ctr, vol = cell_centres(sf, cf, owner, neigh, n_int, n_cells)
    _, counts = detect_defects(points, faces, sf, cf, ctr, owner, neigh, n_int, n_cells)
    best_total = counts["pyramid"] + counts["non_ortho"] + counts["skew"]
    # When either shape-based candidate is enabled, fold aspect ratio into
    # the tie-break objective too - otherwise the acceptance criterion is
    # blind to the very quality axis these candidates target, and a step
    # that improves aspect ratio but not non-ortho/skew would never be
    # preferred over one that doesn't (see the "converges to the same
    # result" finding in notes/getme_compactness_reasoning.md).
    # lam=1 (measured, real checkMesh, cylinder). A higher weight (10) was
    # tried and looked like a free win in the in-process replica (aspect
    # ratio 4.49 -> 4.40, non-ortho/skew unchanged) but real checkMesh
    # showed the opposite: non-ortho max 36.6 -> 49.6 and skew 1.17 -> 1.31
    # got WORSE while aspect ratio only improved slightly - a net
    # regression on 2 of 3 metrics, not a free lunch. Reverted to lam=1.
    # See notes/getme_compactness_reasoning.md.
    aspect_lam = 1.0 if (use_compactness or use_axis_balance) else 0.0
    best_obj = _current_objective(
        objective, objective_limit_deg, sf, cf, ctr, owner, neigh, n_int,
        points, faces, n_cells=n_cells, vol=vol, aspect_lam=aspect_lam,
        zonal_lam=zonal_aspect_lam if zonal_on else 0.0,
        zonal_thr=float(worst_aspect_thr),
    )
    best_counts = dict(counts)

    # Planarity hard guard (2026-09-14): non-ortho/skew are not the only
    # quality axis a Laplacian step can affect. Measured regression: on a
    # single-tet toy mesh (0 non-ortho/skew defects to begin with, so any
    # accepted step is chasing an infinitesimal objective difference), an
    # accepted step turned 3 EXACTLY-planar faces (by construction) into
    # measurably non-planar ones (max deviation 0.0021 vs a 1.7e-6
    # tolerance) — the objective/acceptance below never looked at planarity
    # at all. Track the worst face-planarity deviation the same way volume
    # positivity is already tracked, and reject any candidate that makes it
    # meaningfully worse than the CURRENT best (small relative slack for
    # float noise, not a magic constant tuned to any one case).
    _, _, dev0 = _face_planarity(points, faces)
    best_planarity = float(dev0.max()) if len(dev0) else 0.0
    planarity_slack = max(best_planarity * 1e-6, 1e-12)

    cur_points = points
    for it in range(1, iterations + 1):
        # sf/cf for the shape-based candidates (compactness, axis-balance)
        # must be recomputed from the CURRENT points every iteration - they
        # are gradients evaluated AT cur_points, not at the original input;
        # using the original sf here would silently point every iteration
        # after the first in a stale direction.
        candidate_fns = [("laplacian", lambda pts, st: _laplacian_relax(
            pts, adj, boundary, st, weighted=weighted))]
        if use_compactness or use_axis_balance:
            sf_cur, _cf_cur = face_geometry(cur_points, faces)
            if use_compactness:
                candidate_fns.append(("compactness", lambda pts, st, s=sf_cur: _compactness_step(
                    pts, faces, owner, neigh, n_int, n_cells, boundary, s, st, adj)))
            if use_axis_balance:
                candidate_fns.append(("axis_balance", lambda pts, st, s=sf_cur: _axis_balance_step(
                    pts, faces, owner, neigh, n_int, n_cells, boundary, s, st, adj)))
        if zonal_on:
            # Zone = vertices of cells currently above the threshold,
            # recomputed every iteration (focus follows the worst as the
            # mesh improves). Boundary stays pinned: mask forced to 0
            # there even if a bad cell touches the wall.
            sf_z, _cf_z = face_geometry(cur_points, faces)
            _ctr_z, vol_z = cell_centres(
                sf_z, _cf_z, owner, neigh, n_int, n_cells)
            ar_z = _cell_aspect_ratio(
                sf_z, owner, neigh, n_int, n_cells, vol_z)
            vmask = np.zeros(len(cur_points))
            _own_z = np.asarray(owner)
            _nb_z = np.asarray(neigh)
            for _fi, _f in enumerate(faces):
                _c = int(_own_z[_fi])
                if ar_z[_c] > worst_aspect_thr or (
                        _fi < n_int and ar_z[int(_nb_z[_fi])] > worst_aspect_thr):
                    for _v in _f:
                        vmask[_v] = 1.0
            vmask[boundary] = 0.0
            _base_fns = list(candidate_fns)
            _masked = [
                ("zonal_" + _nm,
                 lambda pts, st, f=_fn, m=vmask:
                 pts + m[:, None] * (f(pts, st) - pts))
                for _nm, _fn in _base_fns
            ]
            # Focus first: masked moves express what the global steps
            # cannot (zone help without dragging good vertices along); the
            # unmasked originals stay available as fallback later in the
            # same backtracking level.
            candidate_fns = _masked + candidate_fns

        step = relaxation
        accepted = False
        for _bt in range(6):  # full step + up to 5 backtracking halvings
            for cand_name, cand_fn in candidate_fns:
                cand = cand_fn(cur_points, step)
                sf_c, cf_c = face_geometry(cand, faces)
                ctr_c, vol_c = cell_centres(sf_c, cf_c, owner, neigh, n_int, n_cells)
                if np.any(vol_c <= 0.0):
                    continue
                if max_volume_ratio > 0.0 and (
                    _max_pair_volume_ratio(vol_c, owner, neigh, n_int) > max_volume_ratio
                ):
                    continue
                _, _, dev_c = _face_planarity(cand, faces)
                cand_planarity = float(dev_c.max()) if len(dev_c) else 0.0
                if cand_planarity > best_planarity + planarity_slack:
                    continue
                _, cand_counts = detect_defects(
                    cand, faces, sf_c, cf_c, ctr_c, owner, neigh, n_int, n_cells,
                )
                cand_total = (
                    cand_counts["pyramid"] + cand_counts["non_ortho"] + cand_counts["skew"]
                )
                if log:
                    log(
                        f"[poly 7/9] smoothing iter {it} [{cand_name}] "
                        f"(step {step:.3f}): defects "
                        f"{cand_counts['pyramid']}/{cand_counts['non_ortho']}/"
                        f"{cand_counts['skew']} (total {cand_total})"
                    )
                if cand_total < best_total:
                    cand_obj = _current_objective(
                        objective, objective_limit_deg, sf_c, cf_c, ctr_c, owner, neigh,
                        n_int, cand, faces, n_cells=n_cells, vol=vol_c,
                        aspect_lam=aspect_lam,
                        zonal_lam=zonal_aspect_lam if zonal_on else 0.0,
                        zonal_thr=float(worst_aspect_thr),
                    )
                    best_total = cand_total
                    best_obj = cand_obj
                    best_points = cand
                    best_counts = dict(cand_counts)
                    best_planarity = cand_planarity
                    cur_points = cand
                    accepted = True
                    break
                if cand_total == best_total:
                    # Continuous objective breaks the tie: accept only on a
                    # REAL improvement (strict <), not merely "no worse". Using
                    # <= here (as this used to) accepted numerically-neutral or
                    # even marginally-worse steps whenever they happened to
                    # round to "not greater than" - harmless on meshes with
                    # real headroom to improve, but on an already-optimal tiny
                    # mesh (e.g. a single tet, no non-ortho/skew defects to
                    # begin with) it let one aimless step through per call,
                    # measurably distorting exactly-planar faces for zero
                    # quality benefit. See
                    # notes/smoothing_gate_fix_reasoning.md.
                    cand_obj = _current_objective(
                        objective, objective_limit_deg, sf_c, cf_c, ctr_c, owner, neigh,
                        n_int, cand, faces, n_cells=n_cells, vol=vol_c,
                        aspect_lam=aspect_lam,
                        zonal_lam=zonal_aspect_lam if zonal_on else 0.0,
                        zonal_thr=float(worst_aspect_thr),
                    )
                    if best_obj is None or cand_obj < best_obj:
                        best_obj = cand_obj
                        best_points = cand
                        best_counts = dict(cand_counts)
                        best_planarity = cand_planarity
                        cur_points = cand
                        accepted = True
                        break
            if accepted:
                break
            # Quality regressed (counts or objective) for every candidate —
            # backtrack to a smaller step; give up on this iteration when the
            # step is exhausted, and stop smoothing entirely (the
            # configuration is stable against every candidate direction).
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
        # Historical behaviour (default path, use_compactness=False):
        # stop as soon as threshold-crossing defects hit zero. Kept
        # UNCHANGED for the default path (do not disturb what tonight's
        # other fixes already validated against). When use_compactness is
        # on, keep iterating up to `iterations` even at zero defects -
        # "zero threshold violations" does not mean "no more continuous
        # quality to gain" (non-orthogonality degrees, skewness value,
        # and now compactness/aspect-ratio can all still improve), and
        # the compactness candidate specifically only gets evaluated on
        # iterations past the first, since `accepted` breaks out of the
        # backtracking loop as soon as Laplacian's own tie-break succeeds.
        if best_total == 0 and not (use_compactness or use_axis_balance
                                   or zonal_on):
            break

    if log:
        log(f"[poly 7/9] smoothing done: best total defects {best_total}")
    return best_points, best_counts


def _cell_aspect_ratio(sf, owner, neigh, n_int, n_cells, vol) -> np.ndarray:
    """Per-cell aspect ratio, matching OpenFOAM's own formula
    (primitiveMeshTools::cellClosedness, fetched from source): the MAX of
    (a) the ratio of the largest to smallest of the three per-cell
    axis-wise sums of |face-area-vector component| (directional
    elongation), and (b) (1/6) * total face area / volume^(2/3)
    (compactness). See notes/getme_compactness_reasoning.md.
    """
    face_axis_abs = np.abs(sf)
    cell_axis_sum = np.zeros((n_cells, 3), dtype=np.float64)
    np.add.at(cell_axis_sum, owner, face_axis_abs)
    np.add.at(cell_axis_sum, neigh, face_axis_abs[:n_int])
    dir_ratio = cell_axis_sum.max(axis=1) / np.maximum(cell_axis_sum.min(axis=1), 1e-300)

    face_area = np.linalg.norm(sf, axis=1)
    cell_area = np.zeros(n_cells, dtype=np.float64)
    np.add.at(cell_area, owner, face_area)
    np.add.at(cell_area, neigh, face_area[:n_int])
    v = np.maximum(vol, 1e-300)
    compact_ratio = (1.0 / 6.0) * cell_area / np.power(v, 2.0 / 3.0)

    return np.maximum(dir_ratio, compact_ratio)


def _current_objective(objective, limit_deg, sf, cf, ctr, owner, neigh, n_int,
                       points, faces, n_cells=None, vol=None,
                       aspect_lam: float = 0.0, zonal_lam: float = 0.0,
                       zonal_thr: float = 3.5) -> float:
    non_ortho, skew = _continuous_metrics(sf, cf, ctr, owner, neigh, n_int, points, faces)
    val = objective(non_ortho, skew, limit_deg=limit_deg)
    if (aspect_lam > 0.0 or zonal_lam > 0.0) and n_cells is not None and vol is not None:
        # Additive aspect-ratio penalty, kept OUT of the public
        # quality_objective() (used directly by tests/other callers with
        # the historical (non_ortho, skew) signature) - opt-in here only,
        # so it cannot change behaviour for any caller that doesn't pass
        # aspect_lam > 0. Penalise excess over 1 (a perfect cell) rather
        # than the raw ratio, to keep the same "squared excess" shape as
        # the non-orthogonality term.
        aspect = _cell_aspect_ratio(sf, owner, neigh, n_int, n_cells, vol)
        if aspect_lam > 0.0:
            val += float(aspect_lam) * float((np.maximum(aspect - 1.0, 0.0) ** 2).sum())
        if zonal_lam > 0.0:
            # Lane D2 (Freitag-style worst-element focus): L_inf term on
            # the worst cell ONLY — max(0, max_aspect - thr)^2. A SUM over
            # the tail was tried first and failed measurably: fixing twenty
            # 3.6-cells outweighs worsening the 4.49 max cell, so the max
            # ROSE 4.489 -> 4.542 while the "zonal" objective improved.
            # Minimax means minimax. Kept ALONGSIDE the global term (not
            # instead): sub-threshold damage elsewhere must still count —
            # the merge-trial lesson (checkMesh severity vs gate counts).
            val += float(zonal_lam) * float(
                max(0.0, float(aspect.max()) - zonal_thr) ** 2)
    return val
