# -*- coding: utf-8 -*-
"""Boundary layers for the barycentric-dual polyhedral mesh.

Adds an anisotropic prism layer stack to the wall of an EXISTING polyhedral
mesh produced by the tet->poly dual converter (`core/tet_poly_dual.py`)  - 
no external tool, pure Python + numpy, headless.

Why this is the right construction (measured, not assumed)
----------------------------------------------------------

The median-dual wall faces are planar quads ``[a, mid(ab), cent(abc), mid(ca)]``
owned by the dual cell of the primal boundary vertex ``a``.  Extruding each
wall quad into a prism stack is conforming BY CONSTRUCTION:

- consecutive wall quads share their edge-mid / triangle-centroid vertices,
  so the extruded prism side faces pair up exactly (one face, two prisms);
- each wall vertex moves to ONE last-layer position shared by every prism
  that touches it, so the top surface tiles without gaps or overlaps;
- the interior cell that owned a wall face simply swaps that face for the
  top face of the deepest prism (same vertex set, extruded), and its other
  wall-adjacent faces swap the moved wall vertices for the extruded ones  - 
  the face pairs (owner/neighbour) never change, only vertex positions.

The topology pattern that OpenFOAM accepts (wall-adjacent edges shared by
the two wall quads plus one internal face) is preserved exactly: checkMesh
reports ``Mesh OK`` on the dual input and on the BL output.

Algorithm (per run)
-------------------

1. select the extrusion faces (all boundary faces by default; with
   ``patch_names`` a named subset is used as-is  -  at the edge between the
   BL region and an unselected patch, each prism side face lies in the
   plane of the adjacent (unselected) wall face and becomes a BOUNDARY
   face of that patch, owned by the prism; the unselected wall faces move
   their shared wall vertices to the last layer so every cell stays
   closed  -  partial-patch BL is conforming and physically correct);
2. per wall vertex: extrusion normal (default: normalised sum of incident
   face area-vectors; opt-in ``normal_method="most_visible"``: the unit
   vector minimising the MAXIMUM angle to any incident face normal, per
   Alauzet's closed-advancing-layer paper -- area-weighted averaging is
   measured there to terminate BL growth earliest at concave ridges,
   because it is pulled toward whichever incident face is largest instead
   of staying equidistant from all of them; see
   notes/most_visible_normal_reasoning.md); max height = ``clamp_factor``
   times the distance to the nearest non-wall vertex of the incident cells
   (inversion guard);
3. layer heights ``h_k = h1 * r^(k-1)``, cumulative, per-vertex clamped;
4. build the prism cells, move the wall vertices of the wall-adjacent
   internal faces to their last-layer positions, swap wall faces for the
   top faces, then solve the face windings EXACTLY by a global parity/BFS
   pass over the face graph (``_solve_global_windings``)  -  every cell
   becomes a closed oriented surface by construction, deterministic
   O(F+E).  This replaced a per-cell greedy repair that sat in a local
   minimum on twisted concave cells (measured on the reference valve: 24
   cells left unclosed at scale 1.0, and a catastrophic global flip at
   scale 0.6 with 378,605 non-positive volumes);
5. validate (owner < neighbour, cell closure, all positive volumes,
   total volume conserved, no unused points) and write atomically via
   `foam_mesh_io`; on validation failure, retry with progressively smaller
   heights (keep-best, mesh never left half-written).

Opt-in local termination (``run(local_termination=True)``) additionally
uses binary layer counts (a wall face keeps the full stack or is not
extruded  -  no intermediate counts, so no transition bands) and iteratively
excludes wall faces whose prisms are geometrically invalid on concave
inputs, carrying the exclusions across the height-fallback scales.  It is
the only measured path that produces and validates a boundary layer on
the reference valve; the wall shell under the excluded faces is given up
(a local, documented domain change).  Defaults are unchanged.
"""

from __future__ import annotations

from collections import deque
import itertools
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from polyfoammesh.core import foam_mesh_io as fio
from polyfoammesh.core.tet_poly_dual import _newell

logger = logging.getLogger(__name__)


@dataclass
class PolyBoundaryLayerResult:
    """Outcome of a BL insertion on the poly mesh."""
    success: bool = False
    n_prism_cells: int = 0
    n_layers: int = 0
    first_height: float = 0.0          # absolute, metres (as requested)
    growth_rate: float = 1.2
    total_thickness: float = 0.0       # absolute, metres (final, after clamp)
    n_wall_faces: int = 0
    wall_patches: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _gil_yield(step: int, every: int = 256) -> None:
    """Release the GIL periodically so the Qt main thread keeps repainting
    while this worker's long pure-Python loops run.  Without this the GUI
    freezes for the minutes the BL step takes (Windows even marks the
    window 'non risponde'), even though the work itself is on a QThread."""
    if step % every == 0:
        time.sleep(0)


def _most_visible_normal(f_norms: list, iters: int = 200) -> np.ndarray:
    """Unit vector minimising the MAXIMUM angle to any vector in
    ``f_norms`` (already unit-normalised incident face normals).

    Iterative min-max (1-centre-on-sphere) fixed point: start from the
    area-weighted average, repeatedly slerp a decaying step toward the
    currently-worst (largest-angle) face normal. Verified against a
    brute-force sampled search (30 random concave-ridge-like fans,
    spread 20-160deg, k=2..6 incident faces): always at least as good as
    the plain average, within ~1.4deg of the true optimum -- see
    notes/most_visible_normal_reasoning.md.
    """
    f = np.asarray(f_norms, dtype=float)
    if len(f) <= 1:
        return f[0] if len(f) else np.zeros(3)
    n = f.sum(axis=0)
    nrm = np.linalg.norm(n)
    if nrm <= 1e-300:
        return f[0]
    n = n / nrm
    for k in range(iters):
        dots = f @ n
        f_worst = f[int(np.argmin(dots))]
        cosang = float(np.clip(n @ f_worst, -1.0, 1.0))
        ang = float(np.arccos(cosang))
        if ang < 1e-9:
            break
        t = 0.5 / (1.0 + k * 0.15)
        sin_ang = np.sin(ang)
        n = (np.sin((1 - t) * ang) / sin_ang) * n + (np.sin(t * ang) / sin_ang) * f_worst
        n = n / (np.linalg.norm(n) + 1e-300)
    return n


def _laplacian_smooth_normal_field(
    normals: np.ndarray, wall_verts: list, adjacency: dict, iters: int = 20,
) -> np.ndarray:
    """Standard Laplacian smoothing of a per-vertex unit-normal FIELD over
    the wall topology (self + adjacent wall vertices, re-normalised every
    iteration since directions are smoothed, not free vectors), per
    Alauzet's closed-advancing-layer BL paper's "normal smoothing".

    Verified on a hand-computable 5-vertex star
    (``scratchpad/verify_normal_smoothing.py``): converges to a stable
    fixed point, preserves unit length exactly, and pulls a divergent
    "ridge" normal toward its neighbours' consensus direction.
    """
    smoothed = normals.copy()
    for _ in range(iters):
        nxt = smoothed.copy()
        for w in wall_verts:
            nbrs = adjacency.get(w, ())
            if not nbrs:
                continue
            acc = smoothed[w].copy()
            for w2 in nbrs:
                acc += smoothed[w2]
            acc /= (1 + len(nbrs))
            nrm = np.linalg.norm(acc)
            nxt[w] = acc / nrm if nrm > 1e-300 else smoothed[w]
        smoothed = nxt
    return smoothed


def _guided_filter_wall_normals(fn, fc, fa, fe, neighbours,
                                sigma_r: float = 0.35,
                                sigma_s_scale: float = 1.5):
    """Guided bilateral filter on wall-FACE unit normals (Zhang et al.,
    "Guided mesh normal filtering", Pacific Graphics 2015, adapted).

    ``fn``/``fc``/``fa``/``fe`` are per-face unit normals, centroids, areas
    and mean edge lengths over the selected wall faces; ``neighbours[bi]``
    lists the 1-ring neighbour face positions.  Returns filtered unit
    normals, same order.  Unlike Laplacian smoothing (which washes across
    ridges) and unlike ``most_visible`` (per-vertex independent, neighbours
    may disagree and twist prism sides), the range kernel keys off
    GUIDANCE normals ``g`` — each face's locally most-consistent patch
    average — so smoothing crosses flat regions freely but stops at real
    ridges, keeping neighbouring extrusion directions mutually consistent.
    Single pass (v1); iterate the caller side if ever needed.
    """
    n = len(fn)
    out = np.empty_like(fn)
    for bi in range(n):
        nb = neighbours[bi]
        # --- guidance: most-consistent local patch average ---
        # candidates: best pair {i,j} (j = most-aligned neighbour) and the
        # full 1-ring; min spread wins.  The singleton {i} is deliberately
        # NOT a candidate (its spread is trivially 0 and would always win,
        # collapsing guidance to self = plain bilateral).  On a ridge the
        # best pair sits on one side, so guidance stays on-side and the
        # range kernel stops smoothing across the feature.
        ring_idx = [bi] + [j for j in nb if j != bi]
        if len(ring_idx) == 1:
            best_g = fn[bi]
        else:
            ring_n = fn[ring_idx]
            dots = ring_n @ fn[bi]
            j_best = int(np.argmax(dots[1:]) + 1)
            best_g = fn[bi]
            best_spread = 2.0
            for cand in ([bi, ring_idx[j_best]], ring_idx):
                cn = fn[cand]
                m = cn.mean(axis=0)
                nm = np.linalg.norm(m)
                if nm <= 1e-300:
                    continue
                spread = float(1.0 - np.min(cn @ (m / nm)))
                if spread < best_spread:
                    best_spread = spread
                    best_g = m / nm
        guides = np.empty((len(ring_idx), 3))
        # guidance of self is best_g; neighbours use their own normals as
        # cheap guidance proxy (full per-neighbour patch search is O(k^2);
        # the range kernel only needs them to diverge at real ridges).
        for k, j in enumerate(ring_idx):
            guides[k] = best_g if j == bi else fn[j]
        # --- joint bilateral ---
        d2 = np.sum((fc[ring_idx] - fc[bi]) ** 2, axis=1)
        sig_s = max(sigma_s_scale * fe[bi], 1e-300)
        gd = np.sum((guides - best_g) ** 2, axis=1)
        w = (fa[ring_idx]
             * np.exp(-d2 / (2.0 * sig_s * sig_s))
             * np.exp(-gd / (2.0 * sigma_r * sigma_r)))
        acc = (w[:, None] * fn[ring_idx]).sum(axis=0)
        nrm = np.linalg.norm(acc)
        out[bi] = acc / nrm if nrm > 1e-300 else fn[bi]
    return out




_FACE_GEOMETRY_CHUNK = 250_000


def _face_geometry_block(points: np.ndarray, faces: list[list[int]]):
    """One dense-vectorised block of ``_face_geometry`` (see below)."""
    n_faces = len(faces)
    lens = np.fromiter((len(f) for f in faces), dtype=np.int64, count=n_faces)
    max_len = int(lens.max())
    if max_len == 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    flat = np.fromiter(
        itertools.chain.from_iterable(faces), dtype=np.int64,
        count=int(lens.sum()),
    )
    cols = np.arange(max_len)[None, :]
    mask = cols < lens[:, None]
    pad = np.zeros((n_faces, max_len), dtype=np.int64)
    pad[mask] = flat
    coords = points[pad]  # (n_faces, max_len, 3); padded slots are masked off
    # next vertex position per slot: k+1, wrapping to 0 at each face's own
    # length (faces have different lengths)
    nxt_col = np.where(cols + 1 < lens[:, None], cols + 1, 0)
    nxt = coords[np.arange(n_faces)[:, None], nxt_col]
    # Newell per edge: 0.5 * (p_k x p_k+1); (a-b)x(a+b) = 2(a x b), so
    # summing 0.5*(a x b) gives the same area vector as the reference
    # implementation in tet_poly_dual._newell.
    sf = 0.5 * np.where(mask[..., None], np.cross(coords, nxt), 0.0).sum(axis=1)
    cf = np.where(mask[..., None], coords, 0.0).sum(axis=1) / lens[:, None]
    return sf, cf


def _face_geometry(points: np.ndarray, faces: list[list[int]],
                   _chunk: int = _FACE_GEOMETRY_CHUNK):
    """(area vectors, centroids) for every face  -  OpenFOAM convention:
    centroid = arithmetic mean of the vertices.  Vectorised (padded face
    matrix) so meshes with >1M faces stay fast, CHUNKED so meshes with
    >10M faces stay in RAM: the dense ``(n_faces, max_len, 3)`` temporaries
    are ~72 bytes/face/slot-set (coords + next + cross output), i.e. ~1.2GB
    per 15M quad faces per live temporary, times 3-4 live at once  -  one
    unchunked call peaked past 5GB on a 5M-cell mesh and this function is
    called dozens of times per run (every metric/validation pass).  Chunking
    bounds the transient to ~80MB per 250k-face block with bitwise-identical
    output (same per-face op order; verified by
    ``test_face_geometry_chunked_matches_unchunked``)."""
    n_faces = len(faces)
    if n_faces == 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    # Fully vectorised within each block: no Python loop over faces (building
    # the padded index matrix one row at a time was the dominant cost) and
    # none over the padded columns either. Measured on 60,000 mixed 4..7-gons:
    # 107 ms -> 58 ms (1.84x). The outer loop is over BLOCKS only (one slice
    # per 250k faces), so the per-face speed is unchanged while the dense
    # temporaries stay bounded.
    if n_faces <= _chunk:
        return _face_geometry_block(points, faces)
    sf = np.empty((n_faces, 3))
    cf = np.empty((n_faces, 3))
    for lo in range(0, n_faces, _chunk):
        s, c = _face_geometry_block(points, faces[lo:lo + _chunk])
        sf[lo:lo + len(s)] = s
        cf[lo:lo + len(c)] = c
    return sf, cf


def _cell_centroids(points, faces, owner, neigh, n_int, n_cells):
    """EXACT OpenFOAM-2512 cell centres + volumes  -  byte-for-byte the
    algorithm in `primitiveMeshTools::makeCellCentresAndVols` (pyramid-volume
    weighted pyramid centres with the face-centre-average apex), so the
    in-process 'face pyramids' gate matches checkMesh's verdict exactly.

    Returns (centres (n,3), volumes (n,)).  Vectorised with add.at."""
    n_faces = len(faces)
    if n_faces == 0:
        return np.zeros((n_cells, 3)), np.zeros(n_cells)
    sf, cf = _face_geometry(points, faces)

    # step 1: cEst = average of the cell's face centres
    c_est = np.zeros((n_cells, 3))
    n_cf = np.zeros(n_cells)
    np.add.at(c_est, owner, cf)
    np.add.at(n_cf, owner, 1.0)
    if n_int:
        np.add.at(c_est, neigh[:n_int], cf[:n_int])
        np.add.at(n_cf, neigh[:n_int], 1.0)
    c_est /= np.maximum(n_cf, 1e-300)[:, None]

    # step 2: accumulate 3*face-pyramid volume and pyramid centres
    C = np.zeros((n_cells, 3))
    V3 = np.zeros(n_cells)
    w_o = (sf * (cf - c_est[owner])).sum(axis=1)
    np.add.at(V3, owner, w_o)
    np.add.at(C, owner, w_o[:, None] * (0.75 * cf + 0.25 * c_est[owner]))
    if n_int:
        w_n = (sf[:n_int] * (c_est[neigh[:n_int]] - cf[:n_int])).sum(axis=1)
        np.add.at(V3, neigh[:n_int], w_n)
        np.add.at(C, neigh[:n_int], w_n[:, None] * (
            0.75 * cf[:n_int] + 0.25 * c_est[neigh[:n_int]]))
    ok = np.abs(V3) > 1e-300
    C[ok] /= V3[ok, None]
    C[~ok] = c_est[~ok]
    return C, V3 / 3.0


def _dual_convexity_wall_fade(points, faces, owner, neigh, n_int, n_cells,
                              wall_verts, wall_face_of):
    """Per-wall-vertex concavity signal measured on the DUAL CELLS themselves.

    The valve defect class is a dual cell that wraps a concave CAD feature
    edge: the cell is genuinely non-convex and its OpenFOAM cell centroid
    falls outside one of its own faces (checkMesh's 'face pyramids'
    per-side test) while the wall-face normals stay smooth  -  the measured
    root cause why ``angle_fade`` never fires on the valve (>= 0.8 on all
    226,538 wall vertices).  This criterion replaces the normal fade with
    the dual-cell convexity signal: per wall vertex, the fraction of its
    incident wall faces whose OWNER cell is non-convex in that exact sense,
    mapped into the same 0.05..1.0 convention as ``angle_fade``:

        fade[w] = max(0.05, 1 - bad_owner_cells / owner_cells)

    1.0 = locally convex (keep the full layer count), low = a concave
    dual cell is underneath (the layer count fits the height budget, i.e.
    the stack terminates there).  ``_build`` consumes it unchanged, so the
    only difference vs ``angle_fade`` is WHERE the fade fires.

    Returns (fade dict, stats dict with the raw defect counts).
    """
    C, _ = _cell_centroids(points, faces, owner, neigh, n_int, n_cells)
    sf, cf = _face_geometry(points, faces)
    bad = np.zeros(len(faces), dtype=bool)
    bad |= (sf * (cf - C[owner])).sum(axis=1) <= 0.0
    if n_int:
        bad[:n_int] |= (
            (sf[:n_int] * (cf[:n_int] - C[neigh[:n_int]])).sum(axis=1) >= 0.0
        )
    cell_bad = np.zeros(n_cells, dtype=bool)
    cell_bad[owner[bad]] = True
    if n_int:
        cell_bad[neigh[:n_int][bad[:n_int]]] = True

    fade: dict[int, float] = {}
    n_lt_half = 0
    for wi, w in enumerate(wall_verts):
        _gil_yield(wi, 64)
        wf = wall_face_of.get(w, ())
        if not wf:
            fade[w] = 1.0
            continue
        own_cells = []
        for fi in wf:
            c = int(owner[fi])
            if c not in own_cells:
                own_cells.append(c)
        n_bad = sum(1 for c in own_cells if cell_bad[c])
        f = max(0.05, 1.0 - n_bad / len(own_cells))
        fade[w] = f
        if f < 0.5:
            n_lt_half += 1
    return fade, {
        "dual_bad_cells": int(cell_bad.sum()),
        "dual_bad_faces": int(bad.sum()),
        "dual_wall_verts_fade_lt_half": n_lt_half,
    }


def _pyramid_violations(points, faces, owner, neigh, n_int, n_cells,
                        return_idx=False):
    """Number of faces whose normal points INTO one of its cells (checkMesh's
    'face pyramids' criterion, exact cell centroids).  checkMesh tests BOTH
    the owner side (normal must point out of the owner's centroid) and the
    neighbour side (normal must point toward the neighbour's centroid).

    With return_idx=True, returns (count, ndarray of violated face indices)."""
    C, _ = _cell_centroids(points, faces, owner, neigh, n_int, n_cells)
    sf, cf = _face_geometry(points, faces)
    bad_owner = (sf * (cf - C[owner])).sum(axis=1) <= 0.0
    bad_mask = bad_owner.copy()
    if n_int:
        bad_neigh = (sf[:n_int] * (cf[:n_int] - C[neigh[:n_int]])).sum(axis=1) >= 0.0
        bad_mask[:n_int] |= bad_neigh
    idx = np.flatnonzero(bad_mask)
    bad = int(len(idx))
    if return_idx:
        return bad, idx
    return bad


class _FaceParityCSR:
    """Compressed-row adjacency for the face-parity graph.

    Lane A RAM fix: the old ``dict[int, list[tuple]]`` adjacency held one
    dict entry + one list + one tuple per face-edge-side (~150 bytes/side,
    ~10GB on a 5M-cell mesh).  CSR holds the same pairs in three dense
    arrays (offsets int64, neighbours int32, parities int8, ~10 bytes/side)
    with per-face neighbour order identical to the dict insertion order, so
    the BFS solve assigns identical values.  ``items()`` yields
    ``(face, [(nbr, parity), ...])`` for the equivalence test normalizer.
    """

    __slots__ = ("n_faces", "off", "nbr", "par")

    def __init__(self, n_faces: int, off: np.ndarray, nbr: np.ndarray,
                 par: np.ndarray):
        self.n_faces = n_faces
        self.off = off
        self.nbr = nbr
        self.par = par

    def row(self, f: int):
        s, e = int(self.off[f]), int(self.off[f + 1])
        return zip(self.nbr[s:e], self.par[s:e])

    def items(self):
        for f in range(self.n_faces):
            s, e = int(self.off[f]), int(self.off[f + 1])
            yield f, list(zip(self.nbr[s:e].tolist(), self.par[s:e].tolist()))


_PARITY_CELL_BATCH = 200_000


def _build_face_parity_graph(faces, owner, neigh, n_int):
    """Build the face-adjacency parity graph consumed by the BFS winding
    solve: for every (edge, cell) with exactly two face-sides, an edge
    ``adj[f1] += [(f2, parity)]`` (symmetric) where ``parity = -d1*d2``.

    Vectorized (numpy) data restructuring only - no decision-making, just
    re-arranges the known face/owner/neighbour input into a different
    lookup shape. This used to be two nested Python loops over ragged face
    data (one dict-append per face-edge-side); see
    notes/global_windings_speed_reasoning.md for the equivalence argument
    and tests/test_bl_poly.py for the byte-for-byte check against an
    independently-written reference loop.

    Returns ``(csr, n_incompat)`` where ``csr`` is a ``_FaceParityCSR``
    with the same pairs (and the same per-face neighbour order) the old
    dict version produced, and ``n_incompat`` counts (edge, cell) groups
    that did NOT have exactly two occurrences (non-manifold).

    Lane A RAM fix: the old version materialized the occurrence rows for
    ALL face-edge-sides at once (5 int64 arrays over 2x sides, ~5GB on a
    5M-cell mesh) plus a stacked group-key matrix, a full lexsort scratch
    set, and a dict-of-tuples adjacency (~150 bytes/side).  Grouping is now
    done in batches over CELL ranges (every (edge, cell) group belongs to
    exactly one cell, so batches never split a group and the pairs are
    identical), and the adjacency is CSR (~10 bytes/side).  Peak on a 5M
    hex mesh drops from ~12GB to ~2GB for this function.
    """
    n_faces = len(faces)
    _empty = _FaceParityCSR(
        n_faces, np.zeros(n_faces + 1, dtype=np.int64),
        np.zeros(0, dtype=np.int32), np.zeros(0, dtype=np.int8),
    )
    if n_faces == 0:
        return _empty, 0
    owner_arr = np.asarray(owner)
    neigh_arr = np.asarray(neigh)
    _mx = -1
    if owner_arr.size:
        _mx = max(_mx, int(owner_arr.max()))
    if neigh_arr.size:
        _mx = max(_mx, int(neigh_arr.max()))
    n_cells_seen = _mx + 1
    degrees = np.fromiter((len(f) for f in faces), dtype=np.int64, count=n_faces)
    n_sides = int(degrees.sum())
    if n_sides == 0:
        return _empty, 0
    flat_v = np.fromiter(
        (v for f in faces for v in f), dtype=np.int64, count=n_sides,
    )
    if int(flat_v.max()) >= 2**31 or n_faces >= 2**31 or n_cells_seen >= 2**31:
        raise ValueError(
            "mesh too large for the 31-bit packed parity graph "
            "(verts < 2**31 required, mesh is past it)"
        )
    face_start = np.concatenate(([0], np.cumsum(degrees)[:-1]))
    fi = np.repeat(np.arange(n_faces, dtype=np.int32), degrees)
    pos = np.arange(n_sides, dtype=np.int64) - np.repeat(face_start, degrees)
    deg_flat = np.repeat(degrees, degrees)
    b_side = flat_v[np.repeat(face_start, degrees) + (pos + 1) % deg_flat]
    del deg_flat, pos, face_start
    key = ((np.minimum(flat_v, b_side).astype(np.uint64) << np.uint64(32))
           | np.maximum(flat_v, b_side).astype(np.uint64))
    sd = np.where(flat_v < b_side, 1, -1).astype(np.int8)
    del flat_v, b_side
    fav = owner_arr[fi].astype(np.int32)
    internal = fi < n_int
    nav = np.full(n_sides, -1, dtype=np.int32)
    if n_int and neigh_arr.size:
        nav[internal] = neigh_arr[fi[internal]]
    del owner_arr, neigh_arr

    n_batches = max(1, -(-max(n_cells_seen, 1) // _PARITY_CELL_BATCH))
    bounds = np.linspace(0, max(n_cells_seen, 1), n_batches + 1).astype(np.int64)

    def _batch_pairs(lo: int, hi: int):
        """(f1, f2, parity) for groups whose cell is in [lo, hi)."""
        om = (fav >= lo) & (fav < hi)
        nm = internal & (nav >= lo) & (nav < hi)
        rk = np.concatenate([key[om], key[nm]])
        if len(rk) == 0:
            z32 = np.zeros(0, dtype=np.int32)
            return z32, z32, np.zeros(0, dtype=np.int8), 0
        rc = np.concatenate([fav[om], nav[nm]])
        rf = np.concatenate([fi[om], fi[nm]])
        rd = np.concatenate([sd[om], -sd[nm]])
        order = np.lexsort((rf, rc, rk))
        rk, rc, rf, rd = rk[order], rc[order], rf[order], rd[order]
        del order
        is_new = np.empty(len(rk), dtype=bool)
        is_new[0] = True
        is_new[1:] = (rk[1:] != rk[:-1]) | (rc[1:] != rc[:-1])
        group_id = np.cumsum(is_new) - 1
        group_size = np.bincount(group_id)
        n_inc = int((group_size != 2).sum())
        pair_mask = group_size[group_id] == 2
        pf = rf[pair_mask]
        pd = rd[pair_mask]
        # within each size-2 group, the two rows are adjacent after the
        # sort above (sorted by group key, ties by face).
        f1 = pf[0::2]
        d1 = pd[0::2]
        f2 = pf[1::2]
        d2 = pd[1::2]
        keep = f1 != f2
        return (f1[keep], f2[keep],
                (-d1[keep] * d2[keep]).astype(np.int8), n_inc)

    # pass 1: per-face pair counts -> CSR offsets.
    counts = np.zeros(n_faces, dtype=np.int64)
    n_incompat = 0
    for lo, hi in zip(bounds[:-1].tolist(), bounds[1:].tolist()):
        f1, f2, _par, n_inc = _batch_pairs(int(lo), int(hi))
        n_incompat += n_inc
        if len(f1):
            np.add.at(counts, f1, 1)
            np.add.at(counts, f2, 1)
    off = np.zeros(n_faces + 1, dtype=np.int64)
    np.cumsum(counts, out=off[1:])
    del counts
    nbr = np.empty(int(off[-1]), dtype=np.int32)
    par = np.empty(int(off[-1]), dtype=np.int8)
    # pass 2: fill the CSR rows (stable face order within each batch keeps
    # the per-face neighbour order identical to the old dict version; the
    # BFS assignment is order-independent on consistent graphs anyway).
    for lo, hi in zip(bounds[:-1].tolist(), bounds[1:].tolist()):
        f1, f2, par_b, _n_inc = _batch_pairs(int(lo), int(hi))
        if not len(f1):
            continue
        fa = np.concatenate([f1, f2])
        oa = np.concatenate([f2, f1])
        pa = np.concatenate([par_b, par_b])
        srt = np.argsort(fa, kind="stable")
        sfa, soa, spa = fa[srt], oa[srt], pa[srt]
        del fa, oa, pa, srt
        new = np.empty(len(sfa), dtype=bool)
        new[0] = True
        new[1:] = sfa[1:] != sfa[:-1]
        starts = np.flatnonzero(new)
        rank = (np.arange(len(sfa))
                - np.repeat(starts, np.diff(np.append(starts, len(sfa)))))
        at = off[sfa] + rank
        nbr[at] = soa
        par[at] = spa
    return _FaceParityCSR(n_faces, off, nbr, par), n_incompat


def _solve_global_windings(points, faces, owner, neigh, n_int, n_cells):
    """Exact winding solve: make every cell's boundary a closed oriented
    surface, in one deterministic pass.

    The previous repair was a per-cell greedy flip, which converges to a
    LOCAL minimum: measured on the reference valve (concave dual cells,
    genuinely twisted prisms) it left 24 cells unclosed at scale 1.0 and
    cascaded into a catastrophic global flip at scale 0.6 (378,605
    non-positive volumes).  This solver is exact and needs no iteration.

    Constraint (one per cell and per edge of its boundary): the two faces of
    the cell that share an edge must traverse that edge in OPPOSITE
    directions, so the cell's signed area-vector sum is 0 by the telescoping
    identity.  Each face has one boolean variable (flip or keep); the
    constraints are parity relations ``x_f * x_g = -d_f * d_g`` where ``d``
    is the edge traversal direction *as the cell sees the face* (the stored
    winding for the owner side, reversed for the neighbour side).  The face
    graph is solved by BFS; a conflict means the mesh is not orientable (a
    topological defect, reported, not silently "repaired").  The component
    sign is chosen so every connected component has positive total volume.

    Deterministic, O(F + E).  Returns (faces, stats).
    """
    import time as _time

    t0 = _time.monotonic()
    n_faces = len(faces)

    csr, n_incompat = _build_face_parity_graph(faces, owner, neigh, n_int)

    x = np.ones(n_faces, dtype=np.int8)
    comp = -np.ones(n_faces, dtype=np.int64)
    n_comp = 0
    n_conflict = 0
    for f0 in range(n_faces):
        if comp[f0] >= 0:
            continue
        comp[f0] = n_comp
        q = deque([f0])
        while q:
            f = q.popleft()
            for g, p in csr.row(f):
                want = x[f] * p
                if comp[g] < 0:
                    x[g] = want
                    comp[g] = n_comp
                    q.append(g)
                elif x[g] != want:
                    n_conflict += 1
        n_comp += 1
        if n_comp % 64 == 0:
            _gil_yield(n_comp, 64)

    # Lane A RAM fix: flip in place, copying ONLY the flipped faces.  The
    # input list is exclusively owned by the caller (``_build`` assembled it
    # fresh: every element is already a private copy, and nothing downstream
    # mutates face lists in place), so re-copying every face here just to
    # reverse a few of them wasted a full mesh copy (~2.4GB on a 5M mesh).
    faces = [list(reversed(f)) if x[fi] < 0 else f
             for fi, f in enumerate(faces)]

    # per-component sign: every connected component must have positive total
    # volume (a single global flip cannot fix a mesh with several bodies).
    vols, _, _ = _cell_metrics(points, faces, owner, neigh, n_cells, n_int)
    owner_arr = np.asarray(owner, dtype=np.int64)
    cell_comp = np.full(n_cells, -1, dtype=np.int64)
    cell_comp[owner_arr] = comp
    valid = cell_comp >= 0
    comp_vol = np.bincount(cell_comp[valid], weights=vols[valid],
                           minlength=n_comp)
    neg_comp = comp_vol < 0.0
    n_flipped_comp = int(neg_comp.sum())
    if n_flipped_comp:
        faces = [list(reversed(f)) if neg_comp[comp[fi]] else f
                 for fi, f in enumerate(faces)]

    return faces, {
        "n_components": n_comp,
        "n_flipped_faces": int((x < 0).sum()),
        "n_flipped_components": n_flipped_comp,
        "n_nonmanifold_edges": n_incompat,
        "n_conflicts": n_conflict,
        "solve_s": round(_time.monotonic() - t0, 1),
    }


def _cell_metrics(points, faces, owner, neighbour, n_cells, n_int):
    """Signed cell volumes (outward area convention) + closure residuals.

    Returns (volumes (n_cells,), closure_residual (n_cells,), scale (n_cells,)).
    A cell is closed when closure_residual / scale < ~1e-8; its volume is
    positive when volumes > 0 (outward-pointing faces).
    """
    sf, cf = _face_geometry(points, faces)
    v = np.zeros(n_cells)
    closure = np.zeros((n_cells, 3))
    scale = np.zeros(n_cells)
    np.add.at(v, owner, (cf * sf).sum(axis=1) / 3.0)
    np.add.at(v, neighbour[:n_int], -(cf[:n_int] * sf[:n_int]).sum(axis=1) / 3.0)
    np.add.at(closure, owner, sf)
    np.add.at(closure, neighbour[:n_int], -sf[:n_int])
    np.add.at(scale, owner, np.linalg.norm(sf, axis=1))
    np.add.at(scale, neighbour[:n_int], np.linalg.norm(sf[:n_int], axis=1))
    return v, closure, scale


class PolyBoundaryLayerEngine:
    """Adds a prism boundary layer to an existing polyhedral polyMesh.

    Reads ``constant/polyMesh`` (ASCII or binary, ``faceList`` or
    ``faceCompactList``  -  the readers in ``foam_mesh_io`` handle all of
    them), inserts the layers, validates, and writes back atomically.
    """

    def __init__(self, case_dir: Path | str, log=None, cancel=None):
        self._case_dir = Path(case_dir).resolve()
        self._log = log or (lambda msg: logger.info(msg))
        self._cancel = cancel or (lambda: False)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def run(
        self,
        n_layers: int = 5,
        first_height: float = 0.005,
        growth_rate: float = 1.2,
        patch_names: list[str] | None = None,
        apply_to_all: bool = True,
        clamp_factor: float = 0.5,
        max_core_volume_ratio: float = 0.0,
        concavity_criterion: str = "angle_fade",
        local_termination: bool | str = False,
        early_exit_intermediates: bool = False,
        local_max_rounds: int = 3,
        local_exclude_widen: int = 0,
        normal_method: str = "area_weighted",
        local_height_retry: bool = False,
    ) -> PolyBoundaryLayerResult:
        """Insert the boundary layer. `first_height` is ABSOLUTE (metres).

        - `patch_names`: restrict BL to these boundary patches. At the edge
          between the BL region and an unselected patch, each prism side
          face lies in the plane of the adjacent (unselected) wall face and
          becomes a boundary face of that patch owned by the prism; the
          unselected wall faces move their shared wall vertices to the last
          layer so every cell stays closed. `result.wall_patches` reports
          exactly the selected patches; the patches that gained terminator
          faces grow in `nFaces`.
        - `apply_to_all=True` (default): BL on every boundary patch.
        - `clamp_factor`: max layer-stack height per wall vertex as a
          fraction of the distance to the nearest non-wall vertex of the
          incident cells (inversion guard).
        - `max_core_volume_ratio`: when > 0, a BL attempt is accepted only
          if every BL prism cell's volume is within this ratio of the
          adjacent core cell's volume (STAR-CCM+ style smooth-transition
          constraint; the fallback loop then thins the layers until it
          holds).  0 (default) = constraint disabled, historical behaviour.
        - `concavity_criterion`: where the local layer termination fires.
          "angle_fade" (default) = the wall-face normal angular span fade
          (historical behaviour, byte-identical).  "dual_convexity" =
          the dual-cell convexity signal (per wall vertex, the fraction of
          its incident wall faces whose owner cell is non-convex in
          checkMesh's 'face pyramids' sense)  -  a genuinely different
          detector that fires on the concave-feature dual cells of the
          valve where the normal fade is flat (measured >= 0.8 on all
          valve wall vertices).
        - `normal_method`: how the per-vertex extrusion normal is computed.
          "area_weighted" (default, historical, byte-identical) = normalised
          sum of incident face area-vectors. "most_visible" = the unit
          vector minimising the MAXIMUM angle to any incident face normal
          (Aubry et al., via Alauzet's closed-advancing-layer BL paper),
          opt-in experiment targeting the same concave-ridge early-
          termination failure mode as `local_termination`; see
          notes/most_visible_normal_reasoning.md. "smoothed" = Laplacian
          smoothing of the normal field over the wall-vertex topology
          (~20 iterations), blended with the true normal by (1 -
          angle_fade) so concave ridges are nearly fully smoothed and flat
          regions stay exact (Alauzet's "normal smoothing", adapted to our
          single-direction-per-vertex extrusion model since we have no
          per-layer "distance from the wall" to blend by); see
          notes/normal_smoothing_reasoning.md. "guided" = guided bilateral
          filter on the wall-face normals (Zhang et al., "Guided mesh
          normal filtering", Pacific Graphics 2015, adapted): the range
          kernel keys off per-face guidance normals, so smoothing crosses
          flat regions but stops at real ridges, keeping neighbouring
          extrusion directions mutually consistent (targets the prism-side
          twist failures `most_visible` leaves behind); see
          notes/guided_normal_filter_reasoning.md. "most_visible" is only
          implemented for `concavity_criterion="angle_fade"` (the default);
          raise if combined with "dual_convexity". "smoothed" works under
          both criteria: the blend weight is `(1 - angle_fade)`, and under
          "dual_convexity" that array already carries the dual-cell signal,
          so concave dual cells get the smoothed normal (Lane C).
        - `local_height_retry`: opt-in (default off), only meaningful
          together with `local_termination="decoupled_vertex"`. Before
          excluding a wall vertex whose freshly-extruded prism failed at
          full height, try ONE extra rebuild per round with just that
          batch of failing vertices at a much shorter local height
          (`vertex_height_scale` on `_build`); a vertex that validates at
          the shorter height is kept (rescued) instead of dropped. One
          extra rebuild per exclusion round, not per vertex (a literal
          per-vertex serial retry was measured infeasible: thousands of
          candidate vertices x ~1 rebuild each). See
          notes/local_height_retry_reasoning.md.
        - `local_termination`: opt-in iterative local termination for
          geometries with concave defects (the reference valve).  Each
          attempt uses the binary layer-count mode (a face is either fully
          layered or not extruded at all) and, when validation fails, the
          faces whose prisms are geometrically invalid (non-positive
          volume or failing the face-pyramid criterion) are excluded from
          the BL and the attempt is repeated.  Two modes:
          * `True` (legacy "carry"): exclusions accumulate ACROSS the
            fallback scales  -  a face excluded while trying a coarse scale
            stays excluded on every thinner scale.  Measured on the valve:
            produced and validated a BL that the standard path cannot
            build at any scale; cost: 19.625 exclusions (8,7%) because the
            coarse-scale rounds over-exclude (FASE 3 measured that starting
            at scale 0.1 needs only 2.058).
          * `"decoupled"`: try the REQUESTED scale first (full thickness);
            exclusions are RESET when descending to a thinner scale, so a
            coarse-scale failure does not poison the finer attempt.  The
            healthy region keeps the full layer count, the wall shell
            under the excluded faces is given up (local domain change),
            and only a validated mesh is written (on failure the mesh on
            disk is left byte-identical).
          * `"decoupled_vertex"` (H4 structural variant): same as
            `"decoupled"` except the iterative exclusion unit is the WALL
            VERTEX: every vertex of a bad face is excluded, and a face is
            extruded only if none of its vertices is excluded.  On
            coarsely-faceted (collapsed) production duals a boundary face
            is one vertex star, so this propagates a defective vertex to
            all incident faces in one round.  Measured on the
            production-collapsed valve: see docs/residual_risks.md
            §H4/FASE 9 (result: success/failure and the exclusion count).
          * `False` (default): disabled  -  historical behaviour unchanged.
        - `early_exit_intermediates`: opt-in (default off).  Only meaningful
          together with `local_termination`.  Once the requested scale (1.0)
          has exhausted its rounds without validating, skip the
          intermediate scales (0.6, 0.35, 0.2) and go directly to 0.1.
          Measured on the valve (decoupled mode): drops the build count
          from 15 to 4 and the time from 2.215 s to ~450 s on the fixture
          snapshot; on a healthy geometry the flag is a no-op (success at
          scale 1.0 round 0).  Risk: a geometry whose defects heal at an
          intermediate scale but not at 0.1 will fail this shortcut (mesh
          left byte-identical, same as the standard path).  Measured
          counter-example: the production-collapsed valve under
          `local_termination="decoupled_vertex"` validates at scale 0.6,
          which this flag would skip — do not combine the two there.
        - `local_max_rounds`: opt-in (default 3).  Maximum exclusion rounds
          attempted per height scale in the local-termination loop.  The
          iterative cascade on coarsely-faceted (collapsed) duals can need
          more rounds than 3 to converge; raising this trades time for
          convergence without changing any geometry parameter.  Only
          meaningful together with `local_termination`; ignored otherwise.
        """
        res = PolyBoundaryLayerResult(
            n_layers=int(max(1, min(int(n_layers), 40))),
            first_height=float(first_height),
            growth_rate=float(growth_rate),
        )
        if int(n_layers) < 1:
            res.errors.append("n_layers must be >= 1")
            return res
        if not 1 <= int(local_max_rounds) <= 10:
            res.errors.append("local_max_rounds must be between 1 and 10")
            return res
        if not 0 <= int(local_exclude_widen) <= 3:
            res.errors.append("local_exclude_widen must be between 0 and 3")
            return res
        if res.first_height <= 0:
            res.errors.append("first_height must be > 0")
            return res
        if res.growth_rate < 1.0:
            res.errors.append("growth_rate must be >= 1.0")
            return res
        growth = min(max(res.growth_rate, 1.0), 2.0)
        if res.growth_rate > 2.0:
            res.warnings.append(
                f"growth_rate {res.growth_rate} clamped to 2.0"
            )
        res.growth_rate = growth
        if concavity_criterion not in ("angle_fade", "dual_convexity"):
            res.errors.append(
                f"unknown concavity_criterion {concavity_criterion!r} "
                "(expected 'angle_fade' or 'dual_convexity')"
            )
            return res
        if normal_method not in ("area_weighted", "most_visible", "smoothed",
                                   "guided"):
            res.errors.append(
                f"unknown normal_method {normal_method!r} "
                "(expected 'area_weighted', 'most_visible', 'smoothed' or "
                "'guided')"
            )
            return res
        if normal_method == "most_visible" and concavity_criterion != "angle_fade":
            res.errors.append(
                f"normal_method={normal_method!r} is only implemented for "
                "concavity_criterion='angle_fade'"
            )
            return res
        if local_height_retry and local_termination != "decoupled_vertex":
            res.errors.append(
                "local_height_retry is only implemented for "
                "local_termination='decoupled_vertex'"
            )
            return res
        res.stats["concavity_criterion"] = concavity_criterion

        poly = self._case_dir / "constant" / "polyMesh"
        try:
            points, faces, owner, neighbour, patches = fio.read_polymesh(poly)
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller
            res.errors.append(f"cannot read polyMesh: {exc}")
            return res
        n_int = len(neighbour)
        n_cells = int(max(owner.max(), neighbour.max())) + 1
        total_vol0 = float(
            _cell_metrics(points, faces, owner, neighbour, n_cells, n_int)[0].sum()
        )
        # FASE 2: the BL is valid if it does not ADD pyramid violations.
        # A concave-feature input (valve) may already violate the
        # convexity-based check; the layer count per wall face is reduced
        # locally (to 1) so the BL survives where it fits instead of
        # being cancelled globally by pre-existing defects.
        _, pyr_idx = _pyramid_violations(
            points, faces, owner, neighbour, n_int, n_cells, return_idx=True,
        )
        pyr_before = int(len(pyr_idx))
        # the wall faces whose input cells already violate the pyramid
        # criterion (concave feature spots) are flagged  -  the consistency
        # fixpoint then flattens their connected region to ONE layer
        drop_bnd = {
            int(fi) - n_int for fi in pyr_idx
            if n_int <= int(fi) < len(faces)
        }

        sel, used_patches = self._select_faces(
            faces, owner, patches, n_int, patch_names, apply_to_all,
        )
        if not sel:
            res.errors.append("no selected faces")
            return res
        res.n_wall_faces = len(sel)
        res.wall_patches = used_patches

        if local_termination:
            if local_termination not in (True, "decoupled", "decoupled_vertex"):
                res.errors.append(
                    f"unknown local_termination mode {local_termination!r} "
                    "(expected True, 'decoupled' or 'decoupled_vertex')"
                )
                return res
            return self._run_local_termination(
                res, points, faces, owner, neighbour, patches, n_int,
                sel, drop_bnd, pyr_before, total_vol0,
                max_core_volume_ratio, concavity_criterion, growth, poly,
                decoupled=(local_termination in ("decoupled",
                                                 "decoupled_vertex")),
                early_exit_intermediates=early_exit_intermediates,
                max_per_scale=int(local_max_rounds),
                vertex_exclusions=(local_termination == "decoupled_vertex"),
                normal_method=normal_method,
                height_retry=local_height_retry,
            )

        # fallback: progressively thinner layers AND tighter per-vertex
        # clamping (the pyramid criterion fails on thin twisted rim cells
        # when the extrusion exceeds a small fraction of the local sliver)
        # The scale-independent part of _build (wall verts, normals, fade,
        # support distances, edge/patch maps) is computed ONCE  -  on the valve
        # it measured ~58 s per attempt, so reusing it across the 5 attempts
        # is most of the speedup.
        pre = self._build_precompute(
            points, faces, owner, neighbour, patches, n_int, sel, drop_bnd,
            concavity_criterion=concavity_criterion, normal_method=normal_method,
        )
        for k, v in pre.get("dual_stats", {}).items():
            res.stats[k] = v
        for scale, clamp in (
            (1.0, 0.5), (0.6, 0.35), (0.35, 0.2),
            (0.2, 0.1), (0.1, 0.05),
        ):
            if self._cancel():
                res.errors.append("cancelled")
                return res
            try:
                built = self._build(
                    points, faces, owner, neighbour, patches, n_int,
                    sel, res.n_layers, res.first_height * scale, growth, clamp,
                    pyr_before=pyr_before, drop_bnd=drop_bnd, pre=pre,
                )
            except Exception as exc:  # noqa: BLE001 - keep-best fallback
                self._log(f"[bl] attempt scale={scale} clamp={clamp}: {exc}")
                res.warnings.append(f"attempt at scale={scale} failed: {exc}")
                continue
            self._log(f"[bl] validating attempt at scale={scale} "
                      f"clamp={clamp} (checkMesh criteria)...")
            ok, msg = self._validate(built, total_vol0, max_core_volume_ratio)
            if ok:
                return self._accept_built(res, built, scale, clamp, poly)
            res.warnings.append(
                f"validation failed at scale={scale} clamp={clamp}: {msg}"
            )
            self._log(f"[bl] validation failed at scale={scale} clamp={clamp}: {msg}")

            # Early stop: a fallback attempt only helps if it eventually
            # reaches ZERO not-closed cells.  The winding solve is now exact
            # (no local-minimum behaviour), so a closure failure means a
            # genuinely broken topology (non-manifold edge, reported in
            # ``orient_stats``) that thinner layers cannot repair.  Keep the
            # stagnation guard for that case: once THREE attempts have left a
            # substantial count that is NOT halving, cut the run short
            # instead of burning minutes on the remaining scales.
            # A genuinely converging case (count dropping steeply, e.g.
            # 300 -> 150 -> 80 -> 30 -> 0) never trips this: at attempt 3 the
            # count is already below half of the first attempt.
            m = re.search(r"(\d+) cells not closed", msg)
            if m:
                n_open = int(m.group(1))
                attempts_done = len([w for w in res.warnings
                                     if w.startswith("validation failed at")])
                if attempts_done == 1:
                    self._first_open = n_open
                if (attempts_done >= 3 and n_open > 40
                        and n_open >= 0.5 * getattr(self, "_first_open", n_open)):
                    res.warnings.append(
                        "early stop: still "
                        f"{n_open} cells not closed after {attempts_done} "
                        "attempts (topology defect, count not halving); "
                        "mesh left unchanged"
                    )
                    self._log(
                        "[bl] early stop: still "
                        f"{n_open} cells not closed after {attempts_done} "
                        "attempts (topology defect, count not halving); "
                        "mesh left unchanged"
                    )
                    break

        res.errors.append(
            "could not insert a valid boundary layer (all height scales "
            "failed validation); mesh left unchanged"
        )
        return res

    def _accept_built(self, res, built, scale, clamp, poly):
        """Write the built mesh and fill the result (shared success path)."""
        fio.write_polymesh(
            poly, built["points"], built["faces"],
            built["owner"], built["neigh"], built["patches"],
        )
        res.success = True
        res.n_prism_cells = int(built["n_prism_cells"])
        res.total_thickness = built["heights"][-1]
        res.stats.update(
            scale=scale,
            clamp_factor=clamp,
            n_cells_after=int(built["owner"].max()) + 1,
            n_faces=len(built["faces"]),
            n_internal_faces=len(built["neigh"]),
            n_terminator_faces=int(built["n_terminator_faces"]),
            min_cell_volume=float(built["min_volume"]),
            total_volume=float(built["total_volume"]),
            layers_per_face_min=int(built.get("layers_per_face_min", res.n_layers)),
            layers_per_face_max=int(built.get("layers_per_face_max", res.n_layers)),
        )
        self._log(
            f"[bl] {built['n_prism_cells']:,} prism cells "
            f"({res.n_layers} layers, local "
            f"{built.get('layers_per_face_min', res.n_layers)}.."
            f"{built.get('layers_per_face_max', res.n_layers)}), "
            f"total thickness {res.total_thickness:.6g} m, "
            f"min cell volume {built['min_volume']:.3g}"
        )
        return res

    def _collect_exclusions(self, built, sel, owner, n_int):
        """Boundary faces (values in ``sel``) to remove from the BL after a
        failed attempt: the faces whose prisms have non-positive volume or
        violate the face-pyramid criterion, plus the wall faces of the
        original cells whose new geometry violates it."""
        pts2 = built["points"]
        faces2 = built["faces"]
        own2 = np.asarray(built["owner"])
        nb2 = np.asarray(built["neigh"])
        n_int2 = int(built["n_int"])
        n_cells2 = int(max(own2.max(), nb2.max())) + 1
        vols, _closure, _smag = _cell_metrics(pts2, faces2, own2, nb2,
                                              n_cells2, n_int2)
        C, _ = _cell_centroids(pts2, faces2, own2, nb2, n_int2, n_cells2)
        sf, cf = _face_geometry(pts2, faces2)
        bad_owner = (sf * (cf - C[own2])).sum(axis=1) <= 0.0
        bad_neigh = np.zeros(len(faces2), dtype=bool)
        if n_int2:
            bad_neigh[:n_int2] = (
                sf[:n_int2] * (cf[:n_int2] - C[nb2[:n_int2]])
            ).sum(axis=1) >= 0.0
        bad = bad_owner | bad_neigh

        prism_start = int(built["prism_start"])
        off = built["prism_off"]
        nf = built["prism_nf"]
        cell_to_bi: dict[int, int] = {}
        for bi in range(len(sel)):
            for k in range(nf[bi]):
                cell_to_bi[prism_start + off[bi] + k] = bi

        owner_shared = {bi: int(owner[n_int + b])
                        for bi, b in enumerate(sel)}
        # position in sel -> owner cell of that boundary face
        excluded_positions: set[int] = set()

        def _exclude(bi):
            if bi is not None:
                excluded_positions.add(bi)

        for c in np.flatnonzero(vols <= 0):
            c = int(c)
            if c >= prism_start:
                _exclude(cell_to_bi.get(c))
            else:
                for b, oc in owner_shared.items():
                    if oc == c:
                        excluded_positions.add(b)
        for fi in np.flatnonzero(bad):
            fi = int(fi)
            o = int(own2[fi])
            if o >= prism_start:
                _exclude(cell_to_bi.get(o))
            if fi < n_int2:
                nb = int(nb2[fi])
                if nb >= prism_start:
                    _exclude(cell_to_bi.get(nb))
        # original cells whose NEW faces violate the pyramid criterion
        n_pts_old = int(built.get("n_pts_old", 0))
        if n_pts_old:
            face_has_new = np.array([max(f) >= n_pts_old for f in faces2])
        else:
            face_has_new = np.ones(len(faces2), dtype=bool)
        core_bad = np.flatnonzero(bad_owner & (own2 < prism_start)
                                  & face_has_new)
        core_cells = {int(c) for c in own2[core_bad]}
        for b, oc in owner_shared.items():
            if oc in core_cells:
                excluded_positions.add(b)
        return {sel[bi] for bi in excluded_positions}

    def _widen_exclusions(self, added: set[int], sel_cur, pre: dict,
                           rings: int) -> set[int]:
        """Expand ``added`` by N boundary-edge rings of neighbours.

        Boundary faces share edges (``pre["bnd_edge_faces"]``: edge -> list
        of boundary face indices); the "neighbour of f" is the other face
        sharing the same boundary edge.  A face value is also an index
        into the selected boundary list (the BL selection is auto-closed
        across shared edges, so all boundary faces of the closed selection
        appear).  No-op when ``added`` is empty.
        """
        if not added or rings <= 0:
            return set(added)
        bnd_edge_faces = pre["bnd_edge_faces"]
        neighbours: dict[int, list[int]] = {}
        for faces in bnd_edge_faces.values():
            if len(faces) < 2:
                continue
            a, b = faces
            neighbours.setdefault(a, []).append(b)
            neighbours.setdefault(b, []).append(a)
        frontier = set(added)
        widened = set(added)
        for _ in range(rings):
            nxt: set[int] = set()
            for f in frontier:
                for nf in neighbours.get(f, ()):
                    if nf not in widened:
                        nxt.add(nf)
            if not nxt:
                break
            widened |= nxt
            frontier = nxt
        return widened

    def _retry_local_height(self, points, faces, owner, neighbour, patches,
                            n_int, sel_cur, n_layers, height, growth, clamp,
                            pyr_before, drop_bnd, pre, gained, height_scale):
        """Opt-in (``run(local_height_retry=True)``): one extra rebuild PER
        CANDIDATE SCALE (bounded to 2 here, not one per vertex  -  a literal
        per-vertex serial retry was measured infeasible on the real valve,
        see notes/local_height_retry_reasoning.md) trying a much shorter
        local extrusion height for the wall vertices in ``gained`` (just
        found to fail at full height) instead of excluding them outright.

        Mutates ``height_scale`` in place for every vertex it rescues (so
        the shorter height persists for the rest of this ``run()`` call,
        across scales and rounds).  Returns the set of rescued vertices;
        the caller excludes whatever remains in ``gained - rescued``.
        """
        rescued: set[int] = set()
        remaining = set(gained)
        for retry_scale in (0.2, 0.05):
            if not remaining:
                break
            trial_scale = dict(height_scale)
            for v in remaining:
                trial_scale[v] = retry_scale
            try:
                built = self._build(
                    points, faces, owner, neighbour, patches, n_int,
                    sel_cur, n_layers, height, growth, clamp,
                    pyr_before=pyr_before, drop_bnd=drop_bnd, pre=pre,
                    zero_concave=True, vertex_height_scale=trial_scale,
                )
            except Exception:  # noqa: BLE001 - this scale just doesn't work
                continue
            added2 = self._collect_exclusions(built, sel_cur, owner, n_int)
            still_bad: set[int] = set()
            for b in added2:
                still_bad.update(faces[n_int + b])
            still_bad &= remaining
            newly_rescued = remaining - still_bad
            for v in newly_rescued:
                height_scale[v] = retry_scale
            rescued |= newly_rescued
            remaining = still_bad
        return rescued

    def _run_local_termination(self, res, points, faces, owner, neighbour,
                               patches, n_int, sel, drop_bnd, pyr_before,
                               total_vol0, max_core_volume_ratio,
                               concavity_criterion, growth, poly,
                               decoupled: bool = False,
                               early_exit_intermediates: bool = False,
                               max_per_scale: int = 3,
                               local_exclude_widen: int = 0,
                               vertex_exclusions: bool = False,
                               normal_method: str = "area_weighted",
                               height_retry: bool = False):
        """Opt-in iterative local termination (see ``run`` docstring).

        Binary layer counts (``zero_concave``): a face keeps the full stack
        or is not extruded; no intermediate counts, so the consistency
        fixpoint never floods the healthy region to one layer.  Every failed
        attempt excludes the faces whose prisms are geometrically invalid and
        retries.  Two modes: legacy ``decoupled=False`` carries the
        exclusions ACROSS the fallback scales (a coarse-scale failure leaves
        faces excluded on every thinner scale); ``decoupled=True`` resets the
        exclusion set when descending to a thinner scale, so each height
        budget pays only for its own defects.  Only a mesh that passes
        validation is written  -  on failure the mesh on disk is left
        byte-identical, same contract as the standard path.

        Opt-in ``early_exit_intermediates=True`` (default off): once the
        requested scale (1.0) has exhausted all its rounds without
        validating, skip the intermediate scales (0.6, 0.35, 0.2) and
        proceed directly to 0.1 — on geometrically defective inputs the
        intermediate scales are typically as unproductive as the requested
        one, so the builds spent there are saved.  The requested scale's own
        retry rounds always run first (measured: scale 1.0 can validate at
        round 1 on a current converter output, and skipping that would throw
        away a full-thickness success).  Risk: a geometry whose defect
        heals at an intermediate scale but not at 0.1 will fail this
        shortcut; on failure the mesh is left byte-identical.

        ``max_per_scale`` (default 3, opt-in via ``run(local_max_rounds=N)``):
        maximum exclusion rounds per scale; coarsely-faceted (collapsed)
        duals can need more rounds for the cascade to converge.

        ``local_exclude_widen`` (default 0, opt-in via
        ``run(local_exclude_widen=N)``): after the per-round exclusion
        collection, also exclude the N boundary-edge rings adjacent to
        each newly-excluded face (1 = the immediate neighbours sharing a
        boundary edge, 2 = neighbours-of-neighbours, etc.).  The
        binary per-face granularity on coarsely-faceted (collapsed) duals
        leaves a "rim" of new boundary-layer defects around each excluded
        face; widening to the next ring closes the rim at the cost of
        removing a larger area of wall.  No-op when ``added`` is empty
        (healthy geometry).  Only meaningful together with
        ``local_termination``.

        ``vertex_exclusions`` (default False, opt-in via
        ``run(local_termination="decoupled_vertex")``): H4 structural
        variant — the iterative exclusion unit is the WALL VERTEX, not the
        face.  A face is extruded only if NONE of its vertices is
        excluded; when a bad prism/core cell is collected, every vertex of
        its base face is marked excluded.  On collapsed duals a boundary
        face is one vertex star, so a bad vertex propagates to all its
        incident faces in ONE round (instead of one ring per round), and
        the dropped set converges to the vertex-closure of the defects.
        Mutually exclusive with ``local_exclude_widen`` (widening a
        vertex closure is a no-op by construction); the initial
        ``zero_concave`` drop stays face-level, unchanged.
        """
        carried: set[int] = set()
        res.stats["local_termination_mode"] = (
            "decoupled_vertex" if vertex_exclusions
            else ("decoupled" if decoupled else "carry"))
        res.stats["local_termination_early_exit_intermediates"] = (
            early_exit_intermediates)
        res.stats["local_termination_max_rounds"] = int(max_per_scale)
        skip_remaining: bool = False
        last_dropped = 0
        # persists across every scale/round of THIS run  -  a vertex rescued
        # at one scale keeps its shorter local height for the rest of the
        # run instead of being retried from scratch every attempt.
        height_scale: dict[int, float] = {}
        for scale, clamp in (
            (1.0, 0.5), (0.6, 0.35), (0.35, 0.2),
            (0.2, 0.1), (0.1, 0.05),
        ):
            if skip_remaining and scale > 0.1:
                continue
            excluded = set() if decoupled else carried
            excluded_verts: set[int] = set()
            for _round in range(max_per_scale):
                if self._cancel():
                    res.errors.append("cancelled")
                    return res
                if vertex_exclusions:
                    sel_cur = [
                        b for b in sel
                        if not (set(faces[n_int + b]) & excluded_verts)
                    ]
                    n_dropped = len(sel) - len(sel_cur)
                else:
                    sel_cur = [b for b in sel if b not in excluded]
                    n_dropped = len(excluded)
                last_dropped = n_dropped
                if len(sel_cur) < 16:
                    res.errors.append(
                        "local termination excluded too many faces "
                        f"({n_dropped} of {len(sel)})"
                    )
                    return res
                pre = self._build_precompute(
                    points, faces, owner, neighbour, patches, n_int,
                    sel_cur, drop_bnd,
                    concavity_criterion=concavity_criterion,
                    normal_method=normal_method,
                )
                for k, v in pre.get("dual_stats", {}).items():
                    res.stats[k] = v
                try:
                    built = self._build(
                        points, faces, owner, neighbour, patches, n_int,
                        sel_cur, res.n_layers, res.first_height * scale,
                        growth, clamp, pyr_before=pyr_before,
                        drop_bnd=drop_bnd, pre=pre, zero_concave=True,
                        vertex_height_scale=(height_scale or None),
                    )
                except Exception as exc:  # noqa: BLE001 - keep-best fallback
                    self._log(f"[bl] local attempt scale={scale}: {exc}")
                    res.warnings.append(
                        f"local attempt at scale={scale} failed: {exc}"
                    )
                    break
                self._log(
                    f"[bl] local termination attempt scale={scale} "
                    f"round={_round} excluded={n_dropped} "
                    f"(checkMesh criteria)..."
                )
                ok, msg = self._validate(built, total_vol0,
                                         max_core_volume_ratio)
                if ok:
                    res.stats["local_excluded_faces"] = n_dropped
                    if vertex_exclusions:
                        res.stats["local_excluded_verts"] = len(excluded_verts)
                    res.warnings.append(
                        f"local termination: excluded {n_dropped} of "
                        f"{len(sel)} wall faces (concave/invalid prisms)"
                    )
                    return self._accept_built(res, built, scale, clamp, poly)
                res.warnings.append(
                    f"local validation failed at scale={scale} "
                    f"round={_round}: {msg}"
                )
                self._log(
                    f"[bl] local validation failed at scale={scale} "
                    f"round={_round}: {msg}"
                )
                added = self._collect_exclusions(built, sel_cur, owner, n_int)
                if not added:
                    break
                if vertex_exclusions:
                    # H4: the exclusion unit is the wall vertex.  Mark every
                    # vertex of every collected face; the face-level set is
                    # derived (a face is extruded only if none of its
                    # vertices is excluded), so an excluded vertex drops all
                    # its incident faces in this same round.
                    new_verts: set[int] = set()
                    for b in added:
                        new_verts.update(faces[n_int + b])
                    gained = new_verts - excluded_verts
                    if not gained:
                        break
                    if height_retry:
                        rescued = self._retry_local_height(
                            points, faces, owner, neighbour, patches, n_int,
                            sel_cur, res.n_layers, res.first_height * scale,
                            growth, clamp, pyr_before, drop_bnd, pre,
                            gained, height_scale,
                        )
                        if rescued:
                            res.warnings.append(
                                f"local height retry: rescued {len(rescued)} "
                                f"of {len(gained)} newly-failing wall "
                                f"vertices at scale={scale} round={_round}"
                            )
                        gained = gained - rescued
                        if not gained:
                            continue
                    excluded_verts |= gained
                else:
                    if local_exclude_widen > 0:
                        added = self._widen_exclusions(added, sel_cur, pre,
                                                       local_exclude_widen)
                    excluded |= added
            # early-exit-intermediates: only once the REQUESTED scale has
            # exhausted all its rounds without validating do the intermediate
            # scales become skippable — the requested scale's own retries
            # (rounds 1..n) must always be attempted first: measured on the
            # current converter output, scale 1.0 validates at round 1 (the
            # earlier round-0 trigger was masked by a stale fixture and threw
            # that full-thickness success away).
            if early_exit_intermediates and scale == 1.0:
                skip_remaining = True
                res.warnings.append(
                    "early-exit-intermediates: scale 1.0 exhausted without "
                    "validating; skipping scales 0.6/0.35/0.2"
                )
            # legacy carry mode: the same set flows into the next scale;
            # decoupled mode: each scale starts from an empty set and this
            # only records the latest count for the failure message below.
            carried = excluded
        res.errors.append(
            "could not insert a valid boundary layer with local "
            f"termination ({last_dropped} faces excluded); mesh left "
            "unchanged"
        )
        return res

    # ------------------------------------------------------------------
    # face selection
    # ------------------------------------------------------------------

    def _select_faces(
        self, faces, owner, patches, n_int, patch_names, apply_to_all,
    ) -> tuple[list[int], list[str]]:
        """Boundary faces to extrude + the patches they belong to.

        The selected set is auto-closed across shared edges so that every
        edge of every selected face has exactly two selected faces (required
        for a conforming prism side-face pairing).  Auto-added faces are
        reported through the returned patch-name list.
        """
        n_bnd = len(faces) - n_int
        patch_of = np.empty(n_bnd, dtype=np.int64)
        for pi, p in enumerate(patches):
            s = p["startFace"] - n_int
            nf = p["nFaces"]
            if s < 0 or s + nf > n_bnd:
                continue
            patch_of[s:s + nf] = pi

        if apply_to_all:
            selected = set(range(n_bnd))
        else:
            names = {p["name"] for p in patches}
            wanted = {n for n in (patch_names or []) if n in names}
            if not wanted:
                # nothing matched: fall back to every patch (safe default)
                selected = set(range(n_bnd))
            else:
                selected = {
                    bi for bi in range(n_bnd) if patch_of[bi] in {
                        pi for pi, p in enumerate(patches) if p["name"] in wanted
                    }
                }

        # NO auto-close: the selection is used as-is.  At an edge shared by
        # a selected and an unselected boundary face (the BL/non-BL
        # boundary), ``_build`` emits the prism side faces as BOUNDARY faces
        # on the patch of the adjacent (unselected) wall face, and moves
        # that face's shared wall vertices to the last layer  -  the layer
        # terminates as a wall band, every cell stays closed.
        used_patches = sorted({patches[pi]["name"] for bi in selected
                               for pi in [int(patch_of[bi])]})
        return sorted(selected), used_patches

    # ------------------------------------------------------------------
    # core construction
    # ------------------------------------------------------------------

    def _build_precompute(self, points, faces, owner, neighbour, patches,
                          n_int, sel, drop_bnd,
                          concavity_criterion: str = "angle_fade",
                          normal_method: str = "area_weighted") -> dict:
        """Part of ``_build`` that does NOT depend on the layer heights or the
        inversion clamp (i.e. on ``first_height``/``clamp_factor``): wall
        vertices, per-vertex normals, the feature-aware angular fade (or the
        ``dual_convexity`` dual-cell signal), the smallest incident wall-face
        edge, the interior-support distance, and the edge/patch maps.

        ``concavity_criterion`` selects the concavity detector that drives
        the local layer termination: "angle_fade" (default, historical,
        byte-identical behaviour) or "dual_convexity" (the dual-cell
        convexity signal  -  fires on the concave-feature dual cells of the
        valve where the normal fade is flat).

        The fallback loop calls ``_build`` up to 5 times (one per height
        scale); on the valve this part alone measured ~58 s per attempt, so
        computing it once and reusing it across attempts is the difference
        between minutes and tens of minutes.  Returns a dict consumed by
        ``_build(pre=...)``.
        """
        pts = np.asarray(points, dtype=np.float64)
        n_pts = len(pts)
        n_cells = int(max(owner.max(), neighbour.max())) + 1
        bnd = [n_int + bi for bi in sel]
        drop_bnd = drop_bnd or frozenset()

        # --- wall vertices -------------------------------------------------
        wv: dict[int, None] = {}
        for fi in bnd:
            for v in faces[fi]:
                wv[v] = None
        wall_verts = sorted(wv)

        # vertex -> incident faces index.  Lane A RAM fix: only WALL vertices
        # are ever looked up (the interior-support probe below queries
        # vertices of wall faces, which are wall vertices by construction),
        # so faces are still scanned once but entries are kept only for wall
        # vertices  -  the old map held every vertex (~2GB on a 5M mesh).
        vert_faces: dict[int, list[int]] = {}
        for fi, f in enumerate(faces):
            for v in f:
                if v in wv:
                    vert_faces.setdefault(v, []).append(fi)
        wall_face_of: dict[int, list[int]] = {}
        for fi in bnd:
            for v in faces[fi]:
                wall_face_of.setdefault(v, []).append(fi)

        # --- per-vertex normals (area-weighted face normals) ----------------
        self._log(
            f"[bl] precompute: {len(wall_verts):,} wall vertices, "
            f"{len(bnd):,} boundary faces"
        )
        normals = np.zeros((n_pts, 3))
        prog_stride = max(1, len(bnd) // 20)
        if normal_method == "guided":
            # Guided bilateral filter on wall-face normals (Lane C2): filter
            # first, then area-weight the FILTERED normals to vertices.
            bnd_fn, bnd_fc, bnd_fa, bnd_fe = [], [], [], []
            for pi, fi in enumerate(bnd):
                _gil_yield(pi, 256)
                if pi % prog_stride == 0:
                    self._log(
                        f"[bl] precompute: guided face normals "
                        f"{int(100 * pi / max(1, len(bnd)))}%..."
                    )
                f = faces[fi]
                nv = _newell(pts, f)
                nm = float(np.linalg.norm(nv))
                bnd_fn.append(nv / nm if nm > 1e-300 else np.zeros(3))
                bnd_fc.append(pts[np.asarray(f)].mean(axis=0))
                bnd_fa.append(nm)  # _newell already includes the 0.5
                e = [float(np.linalg.norm(pts[f[k]] - pts[f[(k + 1) % len(f)]]))
                     for k in range(len(f))]
                bnd_fe.append(sum(e) / len(e) if e else 0.0)
            bnd_fn = np.array(bnd_fn)
            bnd_fc = np.array(bnd_fc)
            bnd_fa = np.array(bnd_fa)
            bnd_fe = np.array(bnd_fe)
            edge_faces: dict[tuple[int, int], list[int]] = {}
            for bi, fi in enumerate(bnd):
                f = faces[fi]
                for k in range(len(f)):
                    a, b = f[k], f[(k + 1) % len(f)]
                    key = (a, b) if a < b else (b, a)
                    edge_faces.setdefault(key, []).append(bi)
            neighbours: list[list[int]] = [[] for _ in bnd]
            for lst in edge_faces.values():
                for bi in lst:
                    for bj in lst:
                        if bj != bi and bj not in neighbours[bi]:
                            neighbours[bi].append(bj)
            del edge_faces
            filt = _guided_filter_wall_normals(
                bnd_fn, bnd_fc, bnd_fa, bnd_fe, neighbours)
            for bi, fi in enumerate(bnd):
                for v in faces[fi]:
                    normals[v] += bnd_fa[bi] * filt[bi]
            del bnd_fn, bnd_fc, bnd_fa, bnd_fe, filt, neighbours
        else:
            for pi, fi in enumerate(bnd):
                _gil_yield(pi, 256)
                if pi % prog_stride == 0:
                    self._log(
                        f"[bl] precompute: normals {int(100 * pi / max(1, len(bnd)))}%..."
                    )
                n = _newell(pts, faces[fi])
                for v in faces[fi]:
                    normals[v] += n
        mag = np.linalg.norm(normals, axis=1)
        # only WALL vertices matter: interior dual points (tet/face centroids,
        # interior edge mids) legitimately carry no wall-face normal.
        bad_wall = [w for w in wall_verts if mag[w] <= 1e-300]
        for w in wall_verts:
            normals[w] = normals[w] / mag[w] if mag[w] > 1e-300 else 0.0
        if bad_wall:
            raise ValueError(
                f"{len(bad_wall)} wall vertices have zero area"
            )

        # --- feature-aware height fade ---------------------------------------
        # At sharp features the incident wall-face normals point in nearly
        # OPPOSITE directions (concave re-entrant slivers, e.g. the valve), so
        # their average is directionally unstable and the extruded quads can
        # self-intersect at any height.  Fade the layer height out with the
        # maximum angular span of the incident face normals  -  the classic
        # "terminate the layer at the feature" treatment of production BL
        # codes  -  and cap by a fraction of the smallest incident wall-face
        # edge (a hard bound against twisted slivers).
        #
        # concavity_criterion="dual_convexity" replaces the normal fade with
        # the dual-cell convexity signal (measured flat on the valve: the
        # boundary quads lie on smooth surfaces, so angle_fade >= 0.8 on all
        # wall vertices while 0.25-0.29% of the dual cells are genuinely
        # non-convex around the concave CAD feature edges).
        angle_fade: dict[int, float] = {}
        min_edge: dict[int, float] = {}
        dual_fade, dual_stats = None, {}
        if concavity_criterion == "dual_convexity":
            dual_fade, dual_stats = _dual_convexity_wall_fade(
                pts, faces, owner, neighbour, n_int, n_cells,
                wall_verts, wall_face_of,
            )
        prog_stride = max(1, len(wall_verts) // 20)
        for wi, w in enumerate(wall_verts):
            _gil_yield(wi, 64)
            if wi % prog_stride == 0:
                self._log(
                    f"[bl] precompute: fade/min-edge "
                    f"{int(100 * wi / max(1, len(wall_verts)))}%..."
                )
            f_edges = []
            for fi in wall_face_of.get(w, ()):
                f = faces[fi]
                for k in range(len(f)):
                    a, b = f[k], f[(k + 1) % len(f)]
                    f_edges.append(float(np.linalg.norm(pts[a] - pts[b])))
            min_edge[w] = min(f_edges) if f_edges else float("inf")
            if dual_fade is not None:
                angle_fade[w] = dual_fade[w]
                continue
            f_norms = []
            for fi in wall_face_of.get(w, ()):
                f = faces[fi]
                fn = _newell(pts, f)
                fn = fn / (np.linalg.norm(fn) + 1e-300)
                f_norms.append(fn)
            if normal_method == "most_visible" and f_norms:
                normals[w] = _most_visible_normal(f_norms)
            cos_max = -2.0
            for i in range(len(f_norms)):
                for j in range(i + 1, len(f_norms)):
                    cos_max = max(cos_max, float(f_norms[i] @ f_norms[j]))
            if len(f_norms) < 2:
                cos_max = 1.0  # single incident face = flat corner, no fade
            theta = np.degrees(np.arccos(min(max(cos_max, -1.0), 1.0)))
            # theta = 0..180; fade linearly from full at <=60° to 5% at 150°+
            angle_fade[w] = max(0.05, min(1.0, (150.0 - theta) / 90.0))

        # --- opt-in normal_method="smoothed" (Alauzet's "normal smoothing")--
        # Laplacian-smooth the per-vertex normal field over the wall-vertex
        # topology (~20 iterations), then blend per vertex with the true
        # normal by (1 - angle_fade): flat regions (angle_fade ~= 1) keep the
        # true normal almost exactly, concave ridges (angle_fade as low as
        # 0.05) are nearly fully smoothed  -  reusing the concavity signal
        # already computed above instead of the paper's "distance from the
        # wall" (which does not exist in our single-direction-per-vertex
        # extrusion model; see notes/normal_smoothing_reasoning.md for why).
        if normal_method == "smoothed":
            wall_adj: dict[int, set[int]] = {w: set() for w in wall_verts}
            for fi in bnd:
                f = faces[fi]
                for k in range(len(f)):
                    a, b = f[k], f[(k + 1) % len(f)]
                    if a in wall_adj and b in wall_adj:
                        wall_adj[a].add(b)
                        wall_adj[b].add(a)
            smoothed = _laplacian_smooth_normal_field(
                normals, wall_verts, wall_adj, iters=20,
            )
            for w in wall_verts:
                blend_w = 1.0 - angle_fade[w]
                v = (1.0 - blend_w) * normals[w] + blend_w * smoothed[w]
                nrm = np.linalg.norm(v)
                normals[w] = v / nrm if nrm > 1e-300 else normals[w]

        # --- interior support distance (inversion clamp, scale-independent) --
        # distance from the wall vertex to the nearest NON-wall vertex of the
        # incident cells bounds how far the extruded surface may travel before
        # the wall-adjacent cells invert.  (The actual clamp multiplies this
        # by clamp_factor, which is what the fallback loop varies.)
        support_d: dict[int, float] = {}
        for wi, w in enumerate(wall_verts):
            _gil_yield(wi, 64)
            if wi % prog_stride == 0:
                self._log(
                    f"[bl] precompute: interior support "
                    f"{int(100 * wi / max(1, len(wall_verts)))}%..."
                )
            # interior anchor vertices reachable from w: primal boundary
            # vertices appear ONLY in their wall quads, so probe two hops  - 
            # wall faces of w -> shared wall vertices -> their internal faces
            probe: set[int] = set()
            for fi in wall_face_of.get(w, ()):
                for w2 in faces[fi]:
                    if w2 == w:
                        continue
                    for fi2 in vert_faces.get(w2, ()):
                        for v in faces[fi2]:
                            if v != w and v not in wv:
                                probe.add(v)
            if not probe:
                raise ValueError(f"wall vertex {w} has no interior support")
            d = min(float(np.linalg.norm(pts[w] - pts[v])) for v in probe)
            if not np.isfinite(d) or d <= 1e-12:
                raise ValueError(f"wall vertex {w} has no interior support")
            support_d[w] = d

        # --- selected-face edges (needed by the nv/nf border flattening) ----
        # 2-manifold boundary: every edge has 1 (border) or 2 (interior)
        # selected faces.
        edge_sides: dict[tuple[int, int], list[int]] = {}
        for bi, fi in enumerate(bnd):
            f = faces[fi]
            for k in range(len(f)):
                a, b = f[k], f[(k + 1) % len(f)]
                key = (a, b) if a < b else (b, a)
                edge_sides.setdefault(key, []).append(bi)
        bad = [k for k, lst in edge_sides.items() if len(lst) not in (1, 2)]
        if bad:
            raise ValueError(
                f"{len(bad)} edge(s) with {len(edge_sides[bad[0]])} selected "
                "faces  -  the boundary is not a valid 2-manifold"
            )

        # ALL boundary edges -> incident boundary faces (terminator lookup).
        # Closed 2-manifold boundary: exactly 2 incident faces per edge.
        bnd_edge_faces: dict[tuple[int, int], list[int]] = {}
        n_bnd_faces = len(faces) - n_int
        prog_stride_bf = max(1, n_bnd_faces // 20)
        for bi in range(n_bnd_faces):
            _gil_yield(bi, 4096)
            if bi % prog_stride_bf == 0:
                self._log(
                    f"[bl] precompute: boundary edge map "
                    f"{int(100 * bi / max(1, n_bnd_faces))}%..."
                )
            f = faces[n_int + bi]
            for k in range(len(f)):
                a, b = f[k], f[(k + 1) % len(f)]
                key = (a, b) if a < b else (b, a)
                bnd_edge_faces.setdefault(key, []).append(bi)

        # boundary face index -> patch index (terminator strips are assigned
        # to the patch of the adjacent unselected face)
        n_bnd = len(faces) - n_int
        patch_of = np.empty(n_bnd, dtype=np.int64)
        for pi, p in enumerate(patches):
            s = p["startFace"] - n_int
            if s < 0 or s + p["nFaces"] > n_bnd:
                raise ValueError(
                    f"patch '{p['name']}' startFace/nFaces out of range "
                    f"({s}..{s + p['nFaces']} vs {n_bnd} boundary faces)"
                )
            patch_of[s:s + p["nFaces"]] = pi

        return {
            "pts": pts, "n_pts": n_pts, "bnd": bnd, "wv": wv,
            "wall_verts": wall_verts, "vert_faces": vert_faces,
            "wall_face_of": wall_face_of, "normals": normals,
            "angle_fade": angle_fade, "min_edge": min_edge,
            "support_d": support_d, "edge_sides": edge_sides,
            "bnd_edge_faces": bnd_edge_faces, "patch_of": patch_of,
            "n_wf": len(bnd), "n_bnd": n_bnd,
            "n_cells_old": int(max(owner.max(), neighbour.max())) + 1,
            "dual_stats": dual_stats,
        }

    def _build(self, points, faces, owner, neighbour, patches, n_int,
               sel, n_layers, first_height, growth, clamp_factor,
               pyr_before: int = 0, drop_bnd=None, pre=None,
               concavity_criterion: str = "angle_fade",
               zero_concave: bool = False,
               vertex_height_scale: dict[int, float] | None = None) -> dict:
        """Build the new mesh arrays. Raises on degenerate input.

        ``pre`` is the scale-independent precompute from ``_build_precompute``
        (computed once per run and reused by the fallback loop); when None it
        is computed here, so direct callers keep working.

        ``zero_concave`` (opt-in, experiment): faces flagged as concave
        (input pyramid violations) get ZERO layers instead of being floored
        at one  -  the local wall shell is given up there (moved boundary
        face) so the whole healthy region keeps its full layer count instead
        of being flooded to one layer by the consistency fixpoint.

        ``vertex_height_scale`` (opt-in, default None = no-op by
        construction): per-vertex multiplier on the inversion-clamp height
        budget (``max_h[w] *= vertex_height_scale.get(w, 1.0)``), used by
        ``run(local_height_retry=True)`` to try a SHORTER stack at wall
        vertices that fail at full height instead of excluding them
        outright; see notes/local_height_retry_reasoning.md.
        """
        if pre is None:
            pre = self._build_precompute(
                points, faces, owner, neighbour, patches, n_int, sel,
                drop_bnd, concavity_criterion=concavity_criterion,
            )
        pts = pre["pts"]
        n_pts = pre["n_pts"]
        bnd = pre["bnd"]
        wv = pre["wv"]
        wall_verts = pre["wall_verts"]
        wall_face_of = pre["wall_face_of"]
        normals = pre["normals"]
        angle_fade = pre["angle_fade"]
        min_edge = pre["min_edge"]
        support_d = pre["support_d"]
        edge_sides = pre["edge_sides"]
        bnd_edge_faces = pre["bnd_edge_faces"]
        patch_of = pre["patch_of"]
        n_wf = pre["n_wf"]
        n_bnd = pre["n_bnd"]
        n_cells_old = pre["n_cells_old"]
        drop_bnd = drop_bnd or frozenset()

        # --- per-vertex inversion clamp ------------------------------------
        # distance from the wall vertex to the nearest NON-wall vertex of the
        # incident cells bounds how far the extruded surface may travel before
        # the wall-adjacent cells invert.
        max_h = {}
        for wi, w in enumerate(wall_verts):
            _gil_yield(wi, 64)
            d = support_d[w]
            # inversion guard (distance to interior) + feature fade (angular
            # divergence) + hard cap on the smallest incident wall-face edge
            max_h[w] = angle_fade[w] * min(
                clamp_factor * d, 0.5 * min_edge[w],
            )
            if vertex_height_scale:
                max_h[w] *= vertex_height_scale.get(w, 1.0)

        # --- layer heights --------------------------------------------------
        # cumulative: H_k = h1 * (r^k - 1)/(r - 1); H_total = H_n.
        # growth == 1.0 -> uniform layers.
        h1 = first_height
        cum = np.empty(n_layers)
        if growth > 1.0:
            h = h1
            acc = 0.0
            for k in range(n_layers):
                acc += h
                cum[k] = acc
                h *= growth
        else:
            for k in range(n_layers):
                cum[k] = h1 * (k + 1)
        h_total = float(cum[-1])
        # FASE 2  -  LOCAL TERMINATION: the layer COUNT varies per wall
        # vertex.  Where the local geometry is healthy (angle_fade >= 50%),
        # all n_layers are kept and scaled to the inversion budget (FASE 1
        # behaviour  -  thin but valid).  At CONCAVE feature corners the
        # layers would be crushed slivers that fail the face-pyramid
        # criterion  -  there the count drops locally (n -> n-1 -> ... -> 1)
        # at FULL layer size, instead of thinning everything everywhere.
        nv0: dict[int, int] = {}
        hw0: dict[int, float] = {}
        for w in wall_verts:
            mh = max_h[w]
            if mh <= 0.0:
                nv0[w], hw0[w] = 0, 0.0
                continue
            if angle_fade[w] >= 0.5:
                nv0[w] = n_layers
                hw0[w] = min(cum[n_layers - 1], mh)
            else:
                k = 0
                while k < n_layers and cum[k] <= mh:
                    k += 1
                if k == 0:
                    k = 1  # one clamped layer where even h1 does not fit
                nv0[w] = k
                hw0[w] = cum[k - 1] if cum[k - 1] <= mh else mh
        # per selected face: min over its vertices  -  the stack top needs
        # every vertex at layer n_f; n_f == 0 -> the face is NOT extruded
        # (handled as an unselected face below)
        n_wf = len(bnd)
        nf0: dict[int, int] = {}
        for bi, fi in enumerate(bnd):
            m = min((nv0[v] for v in faces[fi]), default=0)
            # flagged faces: ONE layer (never 0  -  the shell must stay
            # covered so the volume is conserved); degenerate vertices with
            # no extrusion budget force 0 (handled as unselected below)
            nf0[bi] = min(1, m) if bi in drop_bnd else m
        if zero_concave:
            # BINARY local termination  -  the building block of the opt-in
            # ``run(local_termination=True)`` path: a face is either fully
            # layered (all vertices can take the full stack) or not extruded
            # at all.  No intermediate counts -> no transition faces needed
            # (equal counts pair directly, dropped faces use the FASE 1
            # terminator machinery).  This keeps the healthy region at
            # n_layers instead of flooding it to 1 via the consistency
            # fixpoint, at the price of giving up the thin wall shell under
            # the dropped faces (measured negligible on the valve).
            nf = {}
            nv = {w: 0 for w in wall_verts}
            for bi, fi in enumerate(bnd):
                fdrop = bi in drop_bnd
                if not fdrop:
                    for v in faces[fi]:
                        if angle_fade[v] < 0.5 or max_h[v] <= 0.0:
                            fdrop = True
                            break
                if fdrop:
                    nf[bi] = 0
                    continue
                nf[bi] = n_layers
                for v in faces[fi]:
                    nv[v] = n_layers
            if not any(nf[bi] > 0 for bi in range(n_wf)):
                raise ValueError("zero_concave dropped every selected face")
            hw = {
                w: (min(cum[nv[w] - 1], max_h[w]) if nv[w] > 0 else 0.0)
                for w in wall_verts
            }
        else:
            # Consistency fixpoint: every wall vertex's layer count is clamped
            # to the SHALLOWEST incident selected face's count.  This is what
            # makes the variable-depth construction conforming: the stack top
            # faces, the moved internal faces and the terminator strips all use
            # the same layer points per vertex, so no transition band is left
            # open at a vertex whose own budget exceeds the faces around it.
            # The local termination still varies the count along the surface
            # (healthy regions keep n_layers, concave regions drop layers), at
            # the price of flattening each connected region to its minimum  -  a
            # documented trade-off: the n->n-1 graduation would need per-step
            # transition faces, which fail cell closure on the real valve.
            nv = dict(nv0)
            nf = nf0
            changed = True
            while changed:
                changed = False
                for bi, fi in enumerate(bnd):
                    nfb = nf[bi]
                    if nfb == 0:
                        continue
                    for v in faces[fi]:
                        if nv[v] > nfb:
                            nv[v] = nfb
                            changed = True
                nf = {}
                for bi, fi in enumerate(bnd):
                    m = min((nv[v] for v in faces[fi]), default=0)
                    nf[bi] = min(1, m) if bi in drop_bnd else m
            hw = {
                w: (hw0[w] if nv[w] == nv0[w] else
                    (min(cum[nv[w] - 1], max_h[w]) if nv[w] > 0 else 0.0))
                for w in wall_verts
            }
        total = max((hw[w] for w in wall_verts), default=h_total)
        frac = cum / h_total  # 0..1 layer progression (reporting only)

        # --- new point allocation ------------------------------------------
        # index (w, k): k=0 -> original point; k=1..nv[w] -> extruded
        new_pt = {}
        nxt = n_pts
        pts_list = [pts]
        for w in wall_verts:
            n_w = normals[w]
            nvw = nv[w]
            if nvw == 0:
                continue
            frac_w = cum[:nvw] / cum[nvw - 1]  # 0..1 within the local stack
            for k in range(1, nvw + 1):
                new_pt[(w, k)] = nxt
                nxt += 1
                pts_list.append(pts[w] - n_w * (hw[w] * frac_w[k - 1]))
        new_points = np.vstack(pts_list)

        def pt(w: int, k: int) -> int:
            if k == 0:
                return w
            return new_pt[(w, k)]

        # --- cell numbering --------------------------------------------------
        # prism cells get ids n_cells_old .. ; modified cells keep their ids.
        # Each face f gets nf[bi] prisms at ids
        # prism_start + off[bi] .. prism_start + off[bi] + nf[bi] - 1.
        prism_start = n_cells_old
        off = {}
        acc_off = 0
        for bi in range(n_wf):
            off[bi] = acc_off
            acc_off += nf[bi]
        n_prism_total = acc_off
        if n_prism_total == 0:
            raise ValueError("no prism cells after local layer termination")

        # --- edge -> (selected boundary faces) map for side-face pairing ----
        # Every edge of the selected set has either TWO selected faces (the
        # usual internal side-face pairing) or exactly ONE (the BL/non-BL
        # boundary: the prism side face becomes a boundary face assigned to
        # the adjacent unselected patch).  Zero/3+ means a non-manifold
        # boundary  -  invalid input.  (edge_sides/bnd_edge_faces/patch_of are
        # computed once in _build_precompute, before the nv/nf flattening.)

        # --- face assembly ----------------------------------------------------
        # internal faces first (modified originals + new prism faces),
        # then boundary faces in the ORIGINAL order (patch offsets hold).
        new_faces: list[list[int]] = []
        new_own: list[int] = []
        new_nb: list[int] = []

        wall_v = set(wall_verts)

        def _move_face(f: list[int], k: int) -> list[int]:
            """Replace wall vertices with their layer-k points."""
            return [pt(v, k) if v in wall_v else v for v in f]

        def _move_face_var(f: list[int]) -> list[int]:
            """FASE 2: move each wall vertex to its OWN last layer (nv[v])."""
            return [pt(v, nv[v]) if v in wall_v else v for v in f]

        # 1. original internal faces: move wall vertices to their last
        #    (per-vertex) layer.  Every output face is a COPY  -  the repair
        #    passes mutate the output lists in place, and mutating an
        #    aliased input face would corrupt the next fallback attempt.
        for fi in range(n_int):
            f = faces[fi]
            if any(v in wall_v for v in f):
                new_faces.append(_move_face_var(f))
            else:
                new_faces.append(list(f))
            new_own.append(int(owner[fi]))
            new_nb.append(int(neighbour[fi]))

        # 2. new prism faces  -  geometric orientation using the prism
        #    centroid (vertex average; reliable for the near-convex stacks,
        #    and any residual mis-orientation is repaired below)
        prism_cent = np.zeros((n_wf, n_layers, 3))
        for bi, fi in enumerate(bnd):
            _gil_yield(bi, 256)
            f = faces[fi]
            for k in range(nf[bi]):
                base = [pt(v, k) for v in f]
                top = [pt(v, k + 1) for v in f]
                verts = base + top
                prism_cent[bi, k] = new_points[verts].mean(axis=0)

        # top faces: ORIGINAL owner cell <-> prism(f, last). The original
        # cell has the lower index, so it must be the OWNER (upper-triangular
        # convention owner < neighbour); the normal then points from the
        # interior cell toward the prism  -  the same direction as the wall
        # face normal, so the top face keeps the wall face's winding.
        for bi, fi in enumerate(bnd):
            if nf[bi] == 0:
                continue
            _gil_yield(bi, 256)
            f = faces[fi]
            top = [pt(v, nf[bi]) for v in f]
            own_c = int(owner[fi])
            nb_c = prism_start + off[bi] + nf[bi] - 1
            poly = list(top)
            nrm = _newell(new_points, poly)
            wall_nrm = _newell(new_points, [pt(v, 0) for v in f])
            if float(nrm @ wall_nrm) < 0.0:
                poly.reverse()
            new_faces.append(poly)
            new_own.append(own_c)
            new_nb.append(nb_c)

        # inter-layer faces: prism(f, k-1) <-> prism(f, k) across the
        # horizontal face at layer k (every stack layer needs its top face)
        for bi, fi in enumerate(bnd):
            _gil_yield(bi, 256)
            f = faces[fi]
            for k in range(1, nf[bi]):
                poly = [pt(v, k) for v in f]
                own_c = prism_start + off[bi] + (k - 1)
                nb_c = prism_start + off[bi] + k
                nrm = _newell(new_points, poly)
                fc = new_points[poly].mean(axis=0)
                if float(nrm @ (fc - prism_cent[bi, k - 1])) < 0.0:
                    poly.reverse()
                new_faces.append(poly)
                new_own.append(own_c)
                new_nb.append(nb_c)

        # side faces: prism(f, k) <-> prism(f', k) across each shared edge
        # for k < min(nf_f, nf_g).  Where the two stacks differ in depth
        # (FASE 2 local termination), the DEEPER stack's extra layers become
        # internal transition faces between the deeper prism and the ORIGINAL
        # owner cell of the shallower face  -  the step is closed against the
        # neighbouring interior cell, never as a spurious boundary surface.
        # Each wall-face edge is emitted ONCE (from the lower-index face side).
        # terminator (adjacent face NOT extruded, nf == 0): at the BL/non-BL
        # boundary the prism side quad lies IN THE PLANE of the adjacent wall
        # face  -  it is that wall band, so it becomes a BOUNDARY face owned by
        # the prism and assigned to the patch of the adjacent face (FASE 1);
        # the band between the local strip depth and the moved depth of the
        # unselected face is an INTERNAL transition face (FASE 2 band).
        term_by_patch: dict[int, list[tuple[list[int], int]]] = {}
        n_terminators = 0

        def _emit_side(poly: list[int], own_c: int, nb_c: int,
                       cent: np.ndarray) -> None:
            """Append an internal face, orienting the normal geometrically
            (away from the neighbour-side prism centroid)."""
            nrm = _newell(new_points, poly)
            fc = new_points[poly].mean(axis=0)
            if float(nrm @ (fc - cent)) < 0.0:
                poly.reverse()
            new_faces.append(poly)
            new_own.append(own_c)
            new_nb.append(nb_c)

        prog_stride = max(1, n_wf // 20)
        for bi, fi in enumerate(bnd):
            _gil_yield(bi, 128)
            if bi % prog_stride == 0:
                self._log(
                    f"[bl] building prism side faces "
                    f"{int(100 * bi / max(1, n_wf))}%..."
                )
            nf_bi = nf[bi]
            if nf_bi == 0:
                continue
            f = faces[fi]
            m = len(f)
            for e in range(m):
                va, vb = f[e], f[(e + 1) % m]
                key = (va, vb) if va < vb else (vb, va)
                others = [x for x in edge_sides[key] if x != bi]
                if not others or nf[others[0]] == 0:
                    # terminator: the adjacent face is NOT extruded (it is
                    # on a patch without BL, or FASE 2 dropped it locally
                    # with nf==0).  (bnd_edge_faces holds ORIGINAL boundary
                    # indices, so the current face is sel[bi], not bi.)
                    uf = [x for x in bnd_edge_faces.get(key, ()) if x != sel[bi]]
                    if len(uf) != 1:
                        raise ValueError(
                            f"terminator edge {key} has {len(uf)} "
                            "unselected incident faces"
                        )
                    pi_target = int(patch_of[uf[0]])
                    for k in range(nf_bi):
                        poly = [
                            pt(va, k), pt(vb, k),
                            pt(vb, k + 1), pt(va, k + 1),
                        ]
                        nrm = _newell(new_points, poly)
                        fc = new_points[poly].mean(axis=0)
                        # outward for the boundary face: normal away from
                        # the owner (the prism)
                        if float(nrm @ (fc - prism_cent[bi, k])) < 0.0:
                            poly.reverse()
                        term_by_patch.setdefault(pi_target, []).append(
                            (poly, prism_start + off[bi] + k)
                        )
                        n_terminators += 1
                    continue
                obi = others[0]
                if bi > obi:
                    continue  # the other incident face emits this side face
                # both faces are extruded.  The consistency fixpoint makes
                # the layer counts of adjacent faces EQUAL (nf_bi == nf_obi:
                # every vertex of a face has nv == nf[face]), so every layer
                # pairs prism(f,k) <-> prism(g,k)  -  the per-face graduation
                # would need transition faces that fail closure on real
                # concave meshes (documented in CONTRIBUTO).
                for k in range(nf_bi):
                    poly = [
                        pt(va, k), pt(vb, k),
                        pt(vb, k + 1), pt(va, k + 1),
                    ]
                    own_c = prism_start + off[bi] + k
                    nb_c = prism_start + off[obi] + k
                    _emit_side(poly, own_c, nb_c, prism_cent[bi, k])

        # sort internal faces by (owner, neighbour)  -  OpenFOAM convention
        io = np.array(new_own, dtype=np.int64)
        inb = np.array(new_nb, dtype=np.int64)
        if len(io) and np.any(io == inb):
            raise ValueError("internal face with identical owner and neighbour")
        sort_idx = np.lexsort((inb, io))
        new_faces_int = [new_faces[i] for i in sort_idx]
        out_own = io[sort_idx].tolist()
        out_nb = inb[sort_idx].tolist()
        new_n_int = len(new_faces_int)

        # boundary faces: per patch, in ORIGINAL patch order  -  the original
        # faces (selected -> prism bottoms, re-owned by the first-layer
        # prism, ORIGINAL wall vertices; unselected -> wall vertices moved
        # to the LAST layer so their edges still match the moved internal
        # faces and the top faces  -  otherwise the owner cell would be left
        # open at the BL border), then the NEW terminator strips assigned
        # to this patch (prism side faces at the BL/non-BL border, owned by
        # the prisms).  nFaces/startFace are rebuilt to cover the strips.
        sel_set = set(sel)
        sel_pos = {b: i for i, b in enumerate(sel)}
        bnd_faces: list[list[int]] = []
        bnd_owner: list[int] = []
        new_patches = []
        start = new_n_int
        for pi, p in enumerate(patches):
            pf: list[list[int]] = []
            po: list[int] = []
            for bi in range(p["nFaces"]):
                b = p["startFace"] - n_int + bi
                f = faces[n_int + b]
                if b in sel_set:
                    sbi = sel_pos[b]
                    if nf[sbi] == 0:
                        # FASE 2: face dropped locally (0 layers)  -  keep it
                        # as an unselected wall face, vertices to their own
                        # last layer
                        pf.append(_move_face_var(f))
                        po.append(int(owner[n_int + b]))
                    else:
                        # prism bottom (at the wall, layer 0 = original)
                        pf.append(list(f))
                        po.append(prism_start + off[sbi])
                else:
                    pf.append(_move_face_var(f))
                    po.append(int(owner[n_int + b]))
            for poly, own_c in term_by_patch.get(pi, ()):
                pf.append(poly)
                po.append(own_c)
            bnd_faces.extend(pf)
            bnd_owner.extend(po)
            new_patches.append({
                "name": p["name"], "type": p.get("type", "patch"),
                "nFaces": len(pf), "startFace": start,
            })
            start += len(pf)

        out_faces = new_faces_int + bnd_faces
        out_own_all = out_own + bnd_owner
        out_nb_all = out_nb  # len == n_int_new (boundary has no neighbour)

        # EXACT winding solve: the construction orients every new face
        # geometrically, which is reliable on near-convex stacks but can
        # mis-orient faces on twisted concave slivers.  Instead of the old
        # per-cell greedy flip (which converged to a LOCAL minimum: measured
        # on the valve it left 24 cells unclosed at scale 1.0 and cascaded
        # into a catastrophic global flip at scale 0.6 with 378,605
        # non-positive volumes), solve the parity constraints over the whole
        # face graph in one deterministic O(F+E) pass  -  every cell becomes a
        # closed oriented surface and each component ends up with positive
        # total volume.  See _solve_global_windings.
        n_cells_new = int(max(max(out_own_all), max(out_nb_all))) + 1
        self._log("[bl] prism faces assembled  -  solving global winding "
                  "orientation...")
        out_faces, orient_stats = _solve_global_windings(
            new_points, out_faces, out_own_all, out_nb_all,
            new_n_int, n_cells_new,
        )
        self._log(
            f"[bl] winding solve: {orient_stats['n_components']} components, "
            f"{orient_stats['n_flipped_faces']} faces flipped, "
            f"{orient_stats['n_nonmanifold_edges']} non-manifold edges, "
            f"{orient_stats['n_conflicts']} conflicts "
            f"({orient_stats['solve_s']}s)"
        )

        built_terminators = n_terminators

        return {
            "points": new_points,
            "faces": out_faces,
            "owner": np.array(out_own_all, dtype=np.int64),
            "neigh": np.array(out_nb_all, dtype=np.int64),
            "patches": new_patches,
            "n_int": new_n_int,
            "heights": np.concatenate([[0.0], np.asarray(cum) * (
                total / h_total if h_total > 0 else 1.0
            )]),
            "total_volume": None,
            "min_volume": None,
            "n_terminator_faces": built_terminators,
            "n_prism_cells": n_prism_total,
            "layers_per_face_min": min(nf.values()) if nf else 0,
            "layers_per_face_max": max(nf.values()) if nf else 0,
            "n_faces_dropped": sum(1 for v in nf.values() if v == 0),
            "pyr_before": pyr_before,
            "orient_stats": orient_stats,
            # diagnostic / local-termination mapping: for every selected
            # face bi (position in the caller's ``sel``), its prism cell ids
            # are prism_start + off[bi] + k, k = 0..nf[bi]-1 (none when
            # nf[bi] == 0)
            "prism_start": prism_start,
            "prism_off": dict(off),
            "prism_nf": dict(nf),
            "n_pts_old": n_pts,
        }

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------

    def _validate(self, built, total_vol0, max_core_volume_ratio: float = 0.0) -> tuple[bool, str]:
        pts = built["points"]
        faces = built["faces"]
        owner = built["owner"]
        neigh = built["neigh"]
        n_int = built["n_int"]
        n_cells = int(max(owner.max(), neigh.max())) + 1

        if len(owner) != len(faces):
            return False, "owner/faces length mismatch"
        if len(neigh) != n_int:
            return False, "neighbour length != n_int"
        if n_int and (neigh.min() < 0 or neigh.max() >= n_cells):
            return False, "neighbour index out of range"
        if owner.min() < 0 or owner.max() >= n_cells:
            return False, "owner index out of range"
        # patch accounting invariants: the boundary block starts at n_int and
        # the patches tile every boundary face exactly (SA-2 hardening  -  a
        # silent write with a broken table would FATAL at OpenFOAM read).
        patches = built["patches"]
        if patches and patches[0]["startFace"] != n_int:
            return False, "first patch does not start at n_int"
        if sum(p["nFaces"] for p in patches) != len(faces) - n_int:
            return False, "patch nFaces do not cover the boundary faces"

        vols, closure, scale_mag = _cell_metrics(pts, faces, owner, neigh, n_cells, n_int)
        rel_closure = np.linalg.norm(closure, axis=1) / np.maximum(scale_mag, 1e-300)
        if not np.all(rel_closure < 1e-8):
            n_bad = int(np.sum(rel_closure >= 1e-8))
            return False, f"{n_bad} cells not closed (max rel {rel_closure.max():.2e})"
        if np.any(vols <= 0.0):
            n_neg = int(np.sum(vols <= 0.0))
            return False, f"{n_neg} cells with non-positive volume"
        if not np.isfinite(vols).all():
            return False, "non-finite cell volume"

        v_total = float(vols.sum())
        # Volume-conservation tolerance. This is a redundant SAFETY NET, not
        # the primary correctness check: a genuine topological hole (a lost
        # or duplicated face) breaks the cell-closure test above, which runs
        # at 1e-8 and is exact regardless of face shape. What this test adds
        # is a guard against a construction that closes but encloses the
        # wrong region.
        #
        # It cannot be held at 1e-6 any more. The volume here is computed by
        # the divergence theorem from face centroids (vertex average) and
        # Newell area vectors, and that pair is only EXACT for PLANAR faces.
        # Since the dual boundary may now be collapsed into n-gons (one
        # polygon per boundary vertex  -  the topology that makes the mesh
        # actually read as polyhedral), those faces are generally non-planar
        # and the measure itself carries an approximation error larger than
        # 1e-6, with no defect in the mesh.
        #
        # Measured on a cylinder with named inlet/outlet/wall patches and
        # wall-only BL on a collapsed boundary: closure, positive volumes and
        # the face-pyramid criterion ALL pass, while volume "drifts" by
        # 1.6e-4 relative (0.276206 vs 0.276249)  -  so every height-scale
        # attempt was rejected and BL failed outright, purely on this check.
        # 1e-3 still catches a real hole (which is orders of magnitude
        # bigger, and would fail closure first anyway) while tolerating the
        # n-gon quadrature error.
        rel_vol_err = abs(v_total - total_vol0) / max(abs(total_vol0), 1e-30)
        if rel_vol_err > 1e-3:
            return False, (
                f"volume not conserved: {v_total:.6g} vs {total_vol0:.6g} "
                f"(relative {rel_vol_err:.2e})"
            )

        # checkMesh's 'face pyramids' criterion: every face normal must point
        # out of its owner's centroid.  This is what makes the output pass
        # checkMesh on sharp features (twisted rim cells fail here even when
        # closed)  -  the scale fallback then thins the layers until it holds.
        # FASE 2: a concave-feature INPUT (valve) already violates the
        # convexity-based check; the BL is valid if it does not ADD
        # violations (the local layer termination drops the layers where
        # they would make things worse).
        n_pyr = _pyramid_violations(pts, faces, owner, neigh, n_int, n_cells)
        pyr_before = built.get("pyr_before", 0)
        if n_pyr > pyr_before:
            return False, (
                f"{n_pyr} faces violate the face-pyramid criterion "
                f"(input had {pyr_before})"
            )

        # BL → core volume-ratio constraint (STAR-CCM+ smooth-transition):
        # every BL prism cell must be within max_core_volume_ratio of the
        # adjacent core cell's volume.  Prism cells are the ones appended
        # after the original cells (id >= n_cells - n_prisms), so the check
        # only looks at internal faces crossing the prism/core boundary  - 
        # the outermost prism layer vs the core.
        if max_core_volume_ratio > 0.0:
            n_prisms = int(built.get("n_prism_cells", 0))
            orig = n_cells - n_prisms
            if n_int and orig > 0 and n_prisms > 0:
                o = owner[:n_int]
                nb = neigh[:n_int]
                crossing = (o >= orig) ^ (nb >= orig)
                if crossing.any():
                    vo = vols[o[crossing]]
                    vn = vols[nb[crossing]]
                    mn = np.minimum(vo, vn)
                    mx = np.maximum(vo, vn)
                    with np.errstate(divide="ignore", invalid="ignore"):
                        ratios = mx / np.maximum(mn, 1e-300)
                    worst = float(np.max(ratios))
                    if worst > max_core_volume_ratio:
                        return False, (
                            f"BL/core volume ratio {worst:.1f} exceeds "
                            f"{max_core_volume_ratio}"
                        )

        used = set()
        for f in faces:
            used.update(f)
        if len(used) != len(pts):
            unused = len(pts) - len(used)
            return False, f"{unused} unused points"

        built["total_volume"] = v_total
        built["min_volume"] = float(vols.min())
        return True, "ok"
