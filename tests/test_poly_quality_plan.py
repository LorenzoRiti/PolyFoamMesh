"""Acceptance tests for the poly-mesh-quality plan (tet → dual, senza lasciare il poly).

Covers the four phases, each behind its flag with the historical default:

Phase 1  tet_poly_dual.dual_point_mode — voronoi_weighted/optimized placement
         must never regress the barycentric dual (defects, negative volumes,
         closure) and must not raise max non-orthogonality on the classical
         cube case.
Phase 2  poly_smoother — the continuous objective + backtracking upgrades are
         safe under the keep-best contract (existing tests) and the objective
         ranks lower-max-non-ortho configurations better.
Phase 3  poly_aggregator quasi-coplanar merging — more aggressive merging
         (fewer faces per cell on a flat cluster) without producing warped
         merged faces (skewness gate).
Phase 4  bl_poly max_core_volume_ratio — a BL whose prism/core volume ratio
         exceeds the bound is rejected by _validate; a conforming one passes.
Phase 5  quality_thresholds — one canonical set, skewness_max == 4.0 on every
         consumer (accept-on-max).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pytest

from cfmesh_autogui.core import foam_mesh_io as fio
from cfmesh_autogui.core import quality_thresholds as qt
from cfmesh_autogui.core import tet_poly_dual as tpd
from cfmesh_autogui.core.poly_smoother import (
    _max_pair_volume_ratio,
    quality_objective,
)
from test_tet_poly_dual import build_tet_case  # noqa: E402

# ---------------------------------------------------------------------------
# Phase 1 — dual-point placement (RESOLVED by measurement)
# ---------------------------------------------------------------------------

def _cube_tets_pts():
    """Unit cube split into 6 right tets around the centre apex."""
    pts = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0], [1.0, 0.0, 1.0], [1.0, 1.0, 1.0], [0.0, 1.0, 1.0],
    ], dtype=np.float64)
    c = pts.mean(axis=0)
    faces = [
        [0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4],
        [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7],
    ]
    tets = []
    for f in faces:
        tets.append([f[0], f[1], f[2], 8])
        tets.append([f[0], f[2], f[3], 8])
    return tets, np.vstack([pts, c])


# MEASURED on the real valve (824,661 tets) and on synthetic cases: moving the
# per-tet dual points to circumcenters / incenters ("voronoi_weighted" /
# "optimized") DEGRADES the dual — defects 895/302 -> 1740/9321 on the valve,
# worse max non-orthogonality on the cube and Kuhn cube, and the volume/
# closure safety net had to revert ~30% of the tets.  Product decision
# (2026-08-12): the placement modes were removed; the dual keeps the
# barycentric construction (byte-identical output) and mesh quality is
# improved by the smoothing pass instead (Phase 2 below).  This test pins the
# removed-kwarg behaviour so nobody re-introduces a silently-bad placement.
def test_dual_point_mode_removed():
    with pytest.raises(TypeError):
        tpd.TetPolyDualConverter("x", dual_point_mode="voronoi_weighted")


# ---------------------------------------------------------------------------
# Phase 2 — quality_objective + smoothing upgrades
# ---------------------------------------------------------------------------

def test_smoothing_reduces_max_non_ortho_on_injected_defects(tmp_path):
    """The smoothing pass is the quality lever: on a clean cube dual with
    injected interior-vertex noise, smoothing must lower max non-ortho and
    never raise the defect count or create negative volumes."""
    from cfmesh_autogui.core.poly_smoother import smooth_dual_mesh

    tets, pts = _cube_tets_pts()
    case = build_tet_case(tmp_path / "base", pts, tets)
    conv = tpd.TetPolyDualConverter(case, smooth=False)
    res = conv.run()
    assert res.success, res.errors
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    n_int = len(neigh)
    n_cells = int(res.n_cells_after)

    boundary = np.zeros(len(points), dtype=bool)
    for f in faces[n_int:]:
        boundary[f] = True
    rng = np.random.default_rng(11)
    injected = points.copy()
    for v in np.flatnonzero(~boundary):
        injected[v] += rng.normal(0, 0.25, 3)

    sf, cf = tpd._face_geometry(injected, faces)
    ctr, vol = tpd._cell_centres(sf, cf, owner, neigh, n_int, n_cells)
    _, counts = tpd._detect_defects(injected, faces, sf, cf, ctr, owner, neigh, n_int, n_cells)
    total0 = counts["pyramid"] + counts["non_ortho"] + counts["skew"]
    assert total0 > 0, "injection must create defects"

    smoothed, _ = smooth_dual_mesh(
        injected, faces, owner, neigh, n_int, n_cells,
        tpd._detect_defects, tpd._face_geometry, tpd._cell_centres,
        iterations=6, relaxation=0.5,
    )
    sf2, cf2 = tpd._face_geometry(smoothed, faces)
    ctr2, vol2 = tpd._cell_centres(sf2, cf2, owner, neigh, n_int, n_cells)
    _, counts2 = tpd._detect_defects(smoothed, faces, sf2, cf2, ctr2, owner, neigh, n_int, n_cells)
    total2 = counts2["pyramid"] + counts2["non_ortho"] + counts2["skew"]
    assert total2 <= total0
    assert (vol2 > 0.0).all()
    d = ctr2[neigh] - ctr2[owner[:n_int]]
    dn = np.linalg.norm(d, axis=1)
    sn = np.linalg.norm(sf2[:n_int], axis=1)
    cos = (d * sf2[:n_int]).sum(axis=1) / np.maximum(dn * sn, 1e-300)
    non_ortho_after = np.rad2deg(np.arccos(np.clip(cos, -1.0, 1.0)))
    # the smoothed mesh must be strictly better than the noisy one
    sf1, cf1 = tpd._face_geometry(injected, faces)
    d1 = ctr[neigh] - ctr[owner[:n_int]]
    dn1 = np.linalg.norm(d1, axis=1)
    sn1 = np.linalg.norm(sf1[:n_int], axis=1)
    cos1 = (d1 * sf1[:n_int]).sum(axis=1) / np.maximum(dn1 * sn1, 1e-300)
    non_ortho_before = np.rad2deg(np.arccos(np.clip(cos1, -1.0, 1.0)))
    assert non_ortho_after.max() <= non_ortho_before.max() + 1e-9

def test_quality_objective_ranks_lower_max_non_ortho_better():
    # same skewness, lower max non-ortho → strictly lower objective
    lo = quality_objective([60.0, 55.0], [0.1, 0.1], limit_deg=65.0)
    hi = quality_objective([66.0, 55.0], [0.1, 0.1], limit_deg=65.0)
    assert lo < hi
    # below the limit → zero non-ortho contribution; only skewness matters
    assert quality_objective([40.0, 50.0], [0.0, 0.0], limit_deg=65.0) == 0.0
    # skewness enters scaled by lam
    s1 = quality_objective([40.0], [1.0], limit_deg=65.0, lam=1.0)
    s2 = quality_objective([40.0], [1.0], limit_deg=65.0, lam=2.0)
    assert s2 == 2.0 * s1


def test_max_pair_volume_ratio():
    vol = np.array([1.0, 5.0, 2.0])
    owner = np.array([0, 1, 2], dtype=np.int64)
    neigh = np.array([1, 2, 0], dtype=np.int64)
    assert _max_pair_volume_ratio(vol, owner, neigh, 3) == 5.0
    assert _max_pair_volume_ratio(vol, owner, neigh, 0) == 0.0


# ---------------------------------------------------------------------------
# Phase 3 — skewness-aware quasi-coplanar merging
# ---------------------------------------------------------------------------

def _q():
    from _test_helpers import load_commercial_module
    return load_commercial_module("poly_aggregator")


def test_aggregation_params_quasi_coplanar_default_off():
    p = _q().AggregationParams()
    assert p.quasi_coplanar_tolerance == 0.0      # historical default: off
    assert p.max_merge_skewness == 0.9


def test_merge_acceptable_strict_matches_are_coplanar():
    mod = _q()
    va = [np.array([0.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]),
          np.array([1.0, 1.0, 0.0]), np.array([0.0, 1.0, 0.0])]
    vb = [np.array([0.5, 0.0, 0.0]), np.array([1.0, 0.5, 0.0]),
          np.array([0.5, 1.0, 0.0]), np.array([0.0, 0.5, 0.0])]
    assert mod.are_coplanar(va, vb)
    assert mod.merge_acceptable(va, vb, tol=1e-6, quasi_tol=0.0, max_skew=0.9)


def test_merge_acceptable_quasi_coplanar_gated_by_skewness():
    mod = _q()
    # two planar squares sharing the edge ((1,0,0),(1,1,0)) exactly, tilted
    # ~2° about that edge: strictly not coplanar at 1e-6, but within a 0.01
    # cosine slack.  The merged polygon is mildly warped — its skewness
    # decides the merge.
    va = [np.array([0.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]),
          np.array([1.0, 1.0, 0.0]), np.array([0.0, 1.0, 0.0])]
    th = np.deg2rad(2.0)
    c, s = np.cos(th), np.sin(th)
    # rotate the far edge (x from 1 to 2) upward about the shared edge:
    # p = (x, y, 0) -> (1 + c*(x-1), y, s*(x-1))
    def _tilt(v):
        return np.array([1.0 + c * (v[0] - 1.0), v[1], s * (v[0] - 1.0)])
    vb = [_tilt(v) for v in
          [np.array([1.0, 0.0, 0.0]), np.array([1.0, 1.0, 0.0]),
           np.array([2.0, 1.0, 0.0]), np.array([2.0, 0.0, 0.0])]]
    assert not mod.are_coplanar(va, vb, 1e-6)
    merged = mod.merge_two_faces(va, vb)
    assert len(merged) == 6
    skew = mod.face_skewness(merged)
    # merged polygon is mildly warped (skew << 0.9) → accepted with slack
    assert 0.0 < skew < 0.9
    assert mod.merge_acceptable(va, vb, tol=1e-6, quasi_tol=0.01, max_skew=0.9)
    # a too-strict skewness gate refuses the mildly warped merge
    assert not mod.merge_acceptable(va, vb, tol=1e-6, quasi_tol=0.01, max_skew=1e-9)
    # an extreme tilt is refused even with a large slack
    def _drop(v):
        return np.array([v[0], v[1], -2.0])
    vb90 = [_drop(v) for v in
            [np.array([1.0, 0.0, 0.0]), np.array([1.0, 1.0, 0.0]),
             np.array([2.0, 1.0, 0.0]), np.array([2.0, 0.0, 0.0])]]
    assert not mod.merge_acceptable(va, vb90, tol=1e-6, quasi_tol=0.5, max_skew=0.9)


def test_quasi_coplanar_merging_reduces_face_count():
    """Enabling the quasi-coplanar merge merges faces that the strict rule
    leaves alone, so a cluster ends up with fewer faces per cell."""
    mod = _q()
    agg = mod.PolyAggregator()
    # two planar squares sharing the edge ((1,0,0),(1,1,0)) exactly, tilted
    # ~2° about that edge — a realistic pair of adjacent cluster faces.
    va = [np.array([0.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]),
          np.array([1.0, 1.0, 0.0]), np.array([0.0, 1.0, 0.0])]
    th = np.deg2rad(2.0)
    c, s = np.cos(th), np.sin(th)

    def _tilt(v):
        return np.array([1.0 + c * (v[0] - 1.0), v[1], s * (v[0] - 1.0)])

    vb = [_tilt(v) for v in
          [np.array([1.0, 0.0, 0.0]), np.array([1.0, 1.0, 0.0]),
           np.array([2.0, 1.0, 0.0]), np.array([2.0, 0.0, 0.0])]]
    pts = np.vstack([va, vb])
    f1 = mod.Face(vertices=[0, 1, 2, 3], normal=mod.face_normal(va),
                  owner=0, neighbour=-1, is_boundary=True,
                  patch_name="walls", patch_type="patch")
    f2 = mod.Face(vertices=[4, 5, 6, 7], normal=mod.face_normal(vb),
                  owner=0, neighbour=-1, is_boundary=True,
                  patch_name="walls", patch_type="patch")
    agg.params.quasi_coplanar_tolerance = 0.0
    out_off = agg._merge_cluster_faces([f1, f2], pts)
    assert len(out_off) == 2, "strict merging must leave the tilted pair"
    agg.params.quasi_coplanar_tolerance = 0.01   # ~8° cosine slack
    agg.params.max_merge_skewness = 0.9
    out_on = agg._merge_cluster_faces([f1, f2], pts)
    assert len(out_on) == 1, (
        f"quasi-coplanar merging must merge the mildly tilted pair "
        f"({len(out_off)} -> {len(out_on)})"
    )


def test_quasi_coplanar_merging_keeps_skewness_bound():
    """A merge whose result would be very warped is refused BY THE SKEWNESS
    GATE (not by the normal-dot gate): the shared edge is exact, the tilt is
    inside the quasi-coplanar slack, only the warped merged polygon trips it."""
    mod = _q()
    agg = mod.PolyAggregator()
    # z=0 square sharing the edge ((1,0,0),(1,1,0)) with a square whose far
    # edge is dropped to z=-2 — the normals agree within a large slack, but
    # the merged 6-gon is very warped.
    pts = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [2.0, 1.0, -2.0], [2.0, 0.0, -2.0],
    ], dtype=np.float64)
    f1 = mod.Face(vertices=[0, 1, 2, 3],
                  normal=mod.face_normal([pts[v] for v in [0, 1, 2, 3]]),
                  owner=0, neighbour=-1, is_boundary=True,
                  patch_name="walls", patch_type="patch")
    f2 = mod.Face(vertices=[4, 5, 6, 7],
                  normal=mod.face_normal([pts[v] for v in [4, 5, 6, 7]]),
                  owner=0, neighbour=-1, is_boundary=True,
                  patch_name="walls", patch_type="patch")
    # sanity: the pair is NOT strictly coplanar and the normals agree within
    # the 0.6 cosine slack — so only the skewness gate can refuse it
    va = [pts[v] for v in [0, 1, 2, 3]]
    vb = [pts[v] for v in [4, 5, 6, 7]]
    assert not mod.are_coplanar(va, vb, 1e-6)
    merged = mod.merge_two_faces(va, vb)
    assert len(merged) == 6
    warped_skew = mod.face_skewness(merged)
    assert warped_skew > 0.2, f"expected a warped merge, got skew {warped_skew}"
    # strict gate (0.2) → refused by the skewness gate, not the dot gate
    agg.params.quasi_coplanar_tolerance = 0.6
    agg.params.max_merge_skewness = 0.2
    out = agg._merge_cluster_faces([f1, f2], pts)
    assert len(out) == 2, "warped merge must be refused by the skewness gate"
    # lenient gate → the same pair merges (the dot gate is not the blocker)
    agg.params.max_merge_skewness = 2.0
    out2 = agg._merge_cluster_faces([f1, f2], pts)
    assert len(out2) == 1


def test_merge_two_faces_opposite_winding():
    """The merge must produce a simple polygon even when the two rings
    traverse the shared edge in opposite directions (the consistent-winding
    case for closed surfaces) — no duplicated endpoint, no bowtie."""
    mod = _q()
    # two unit squares sharing the edge ((1,0),(1,1)); B walks that edge the
    # opposite way.  The union is the 2x1 rectangle, traced as a simple
    # 6-gon with the shared-edge endpoints as collinear boundary vertices.
    pts = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [2.0, 0.0, 0.0], [2.0, 1.0, 0.0],
    ])
    va = [pts[0], pts[1], pts[2], pts[3]]     # (0,0),(1,0),(1,1),(0,1)
    vb = [pts[2], pts[1], pts[4], pts[5]]     # (1,1),(1,0),(2,0),(2,1)
    merged = mod.merge_two_faces(va, vb)
    assert len(merged) == 6
    assert len({tuple(v) for v in merged}) == 6, "no duplicated vertex"
    # simple + planar: the 2x1 rectangle has area 2 and zero warpage
    assert mod.face_skewness(merged) < 1e-12
    # and the union area is area(A)+area(B) = 2 (a bowtie would cancel)
    x = [v[0] for v in merged]
    y = [v[1] for v in merged]
    area = 0.5 * abs(
        sum(x[i] * y[(i + 1) % 6] - x[(i + 1) % 6] * y[i] for i in range(6))
    )
    assert abs(area - 2.0) < 1e-9


def test_merge_two_faces_equal_area_symmetric_split():
    """A regular hexagon split into two equal coplanar quads: both candidate
    walks tie on |area|, so the tie-break must prefer the ring without a
    duplicated vertex (the degenerate candidate used to win on `>=`)."""
    mod = _q()
    s = np.sqrt(3.0) / 2.0
    H = np.array([
        [1.0, 0.0], [0.5, s], [-0.5, s], [-1.0, 0.0],
        [-0.5, -s], [0.5, -s],
    ])
    # split along the diagonal H2-H5 (through the centre → equal halves)
    pts = np.hstack([H, np.zeros((len(H), 1))])
    va = [pts[0], pts[1], pts[2], pts[5]]
    vb = [pts[2], pts[3], pts[4], pts[5]]
    merged = mod.merge_two_faces(va, vb)
    assert len({tuple(v) for v in merged}) == len(merged), "no duplicated vertex"
    x = [v[0] for v in merged]
    y = [v[1] for v in merged]
    n = len(merged)
    area = 0.5 * abs(
        sum(x[i] * y[(i + 1) % n] - x[(i + 1) % n] * y[i] for i in range(n))
    )
    assert abs(area - 2.598076211) < 1e-6, f"union area {area} != hexagon area"


# ---------------------------------------------------------------------------
# Phase 4 — BL core volume-ratio constraint
# ---------------------------------------------------------------------------

def test_bl_volume_ratio_rejects_oversized_prism(tmp_path):
    """_validate with max_core_volume_ratio rejects a BL whose prism cell is
    far bigger than the adjacent core cell, and accepts a conforming one."""
    from cfmesh_autogui.core.bl_poly import PolyBoundaryLayerEngine

    pts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],       # core bottom
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],       # core top (shared)
        [0, 0, 101], [1, 0, 101], [1, 1, 101], [0, 1, 101],  # prism top
    ], dtype=np.float64)
    faces = [
        # internal faces first (OpenFOAM convention): the shared z=1 square
        [4, 5, 6, 7],
        # core hex (cell 0): bottom + 4 sides
        [0, 3, 2, 1], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7],
        # prism hex (cell 1): top + 4 sides (bottom = the shared face above)
        [8, 9, 10, 11], [4, 5, 9, 8], [5, 6, 10, 9], [6, 7, 11, 10], [7, 4, 8, 11],
    ]
    owner = np.array([0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1], dtype=np.int64)
    # one internal face: face 0 (the core top / prism bottom)
    neigh = np.array([1], dtype=np.int64)
    n_int = 1
    eng = PolyBoundaryLayerEngine(tmp_path)
    built = {
        "points": pts, "faces": faces, "owner": owner, "neigh": neigh,
        "n_int": n_int,
        "patches": [{"name": "walls", "type": "wall", "nFaces": 10, "startFace": 1}],
        "n_prism_cells": 1,
        "pyr_before": 0, "heights": np.array([0.0, 100.0]),
    }
    total_vol0 = 1.0 + 100.0
    # prism is 100x the core → ratio 100 > 20 → rejected under the constraint
    ok, msg = eng._validate(built, total_vol0, max_core_volume_ratio=20.0)
    assert not ok and "volume ratio" in msg
    # historical default (0 = disabled) → same mesh passes
    ok2, msg2 = eng._validate(built, total_vol0)
    assert ok2, msg2


# ---------------------------------------------------------------------------
# Phase 5 — unified thresholds
# ---------------------------------------------------------------------------

def test_thresholds_canonical_skewness_4_all_paths():
    assert qt.THRESHOLDS["skewness_max"] == 4.0
    assert qt.ADAPTIVE_THRESHOLDS["skewness_max"] == 4.0
    assert qt.QUALITY_THRESHOLDS["skewness_max"] == 4.0
    assert qt.OPTIMIZER_THRESHOLDS["skewness_max"] == 4.0


def test_thresholds_consumers_share_values():
    from _test_helpers import load_commercial_module

    opt = load_commercial_module("optimizer")
    qe = load_commercial_module("quality_engine")
    assert opt.MeshOptimizer.THRESHOLDS is qt.OPTIMIZER_THRESHOLDS
    assert qe.THRESHOLDS is qt.QUALITY_THRESHOLDS
