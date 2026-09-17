"""Quality-driven smoothing of the dual polyhedral mesh.

Tests the keep-best safety contract of ``smooth_dual_mesh``:
- boundary vertices never move (the dual boundary is bit-perfect);
- a mesh with artificially injected interior defects is improved or at worst
  left unchanged (never regressed);
- no cell volume ever becomes non-positive.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pytest

from polyfoammesh.core import foam_mesh_io as fio
from polyfoammesh.core.poly_smoother import (
    _boundary_vertex_mask,
    _flatten_adjacency,
    _laplacian_relax,
    _vertex_neighbours,
    smooth_dual_mesh,
)
from polyfoammesh.core.tet_poly_dual import (
    TetPolyDualConverter,
    _cell_centres,
    _detect_defects,
    _face_geometry,
)
from test_tet_poly_dual import build_tet_case  # noqa: E402


def _cube_tets_pts() -> tuple[list, np.ndarray]:
    """Unit cube [0,1]^3 split into 6 tets, apex at the centre."""
    pts = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0], [1.0, 0.0, 1.0], [1.0, 1.0, 1.0], [0.0, 1.0, 1.0],
    ], dtype=np.float64)
    c = pts.mean(axis=0)
    faces_of_cube = [
        [0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4],
        [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7],
    ]
    tets = []
    for f in faces_of_cube:
        tets.append([f[0], f[1], f[2], 8])
        tets.append([f[0], f[2], f[3], 8])
    pts = np.vstack([pts, c])
    return tets, pts


def _run_dual(tmp_path: Path) -> tuple:
    """Build the cube tet case, run the plain dual, read back the polyMesh."""
    tets, pts = _cube_tets_pts()
    case = build_tet_case(tmp_path, pts, tets)
    conv = TetPolyDualConverter(case, smooth=False)
    res = conv.run()
    assert res.success, res.errors
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neighbour, patches = fio.read_polymesh(poly)
    n_int = len(neighbour)
    # n_cells must come from the converter result, not owner.max()+1: the
    # written owner file can top out below the last cell index.
    n_cells = int(res.n_cells_after)
    return points, faces, owner, neighbour, patches, n_int, n_cells


def _defects(points, faces, owner, neigh, n_int, n_cells):
    sf, cf = _face_geometry(points, faces)
    ctr, vol = _cell_centres(sf, cf, owner, neigh, n_int, n_cells)
    _, counts = _detect_defects(points, faces, sf, cf, ctr, owner, neigh, n_int, n_cells)
    return counts, vol


def test_boundary_vertices_are_identified():
    pts = np.zeros((6, 3))
    faces = [[0, 1, 2], [3, 4, 5]]  # all boundary
    mask = _boundary_vertex_mask(pts, faces, n_int=0)
    assert mask.all()

    faces2 = [[0, 1, 2]]  # internal face only
    mask2 = _boundary_vertex_mask(pts, faces2, n_int=1)
    assert not mask2.any()


def test_laplacian_relax_matches_independent_reference_loop():
    """_laplacian_relax was rewritten from an explicit per-vertex Python loop
    to a vectorised scatter-add (the dominant cost of poly smoothing — see
    docs/residual_risks.md). Cross-check the vectorised result against a
    reference implementation written independently here (a plain Python
    loop, deliberately not reusing any smoother code), for both the
    weighted and unweighted modes, and for a boundary vertex that must not
    move regardless of neighbours."""
    rng = np.random.default_rng(0)
    points = rng.uniform(-1.0, 1.0, size=(9, 3))
    # a small ring 0-1-2-3-0 plus a pendant vertex 4 hanging off 0, an
    # isolated vertex 5 (no neighbours), and a boundary vertex 6 with
    # neighbours it must still not move for.
    faces = [[0, 1, 2, 3], [0, 4, 1], [6, 7, 8]]
    boundary = np.zeros(len(points), dtype=bool)
    boundary[6] = True
    adj_list = _vertex_neighbours(points, faces)
    adj_flat = _flatten_adjacency(adj_list)

    for weighted in (True, False):
        for relax in (1.0, 0.4):
            got = _laplacian_relax(points, adj_flat, boundary, relax, weighted=weighted)

            want = points.copy()
            for v in range(len(points)):
                if boundary[v] or not adj_list[v]:
                    continue
                nbrs = adj_list[v]
                if weighted:
                    diff = points[nbrs] - points[v]
                    w = 1.0 / np.maximum(np.linalg.norm(diff, axis=1), 1e-300)
                    mean = points[v] + (w[:, None] * diff).sum(axis=0) / w.sum()
                else:
                    mean = points[nbrs].mean(axis=0)
                want[v] = points[v] + relax * (mean - points[v])

            np.testing.assert_allclose(got, want, rtol=1e-12, atol=1e-12)

    # vertex 5 has no neighbours and vertex 6 is boundary: both stay put.
    got = _laplacian_relax(points, adj_flat, boundary, 1.0, weighted=True)
    np.testing.assert_array_equal(got[5], points[5])
    np.testing.assert_array_equal(got[6], points[6])


def test_smoothing_keeps_boundary_fixed_and_never_regresses(tmp_path):
    points, faces, owner, neigh, _patches, n_int, n_cells = _run_dual(tmp_path)
    boundary = _boundary_vertex_mask(points, faces, n_int)
    assert boundary.any() and (~boundary).any(), "dual must have both kinds"

    base_counts, _ = _defects(points, faces, owner, neigh, n_int, n_cells)
    base_total = base_counts["pyramid"] + base_counts["non_ortho"] + base_counts["skew"]

    # Inject defects: displace interior vertices strongly.
    rng = np.random.default_rng(3)
    injected = points.copy()
    interior_idx = np.flatnonzero(~boundary)
    for v in interior_idx:
        injected[v] += rng.normal(0, 0.15, 3)
    inj_counts, inj_vol = _defects(injected, faces, owner, neigh, n_int, n_cells)
    inj_total = inj_counts["pyramid"] + inj_counts["non_ortho"] + inj_counts["skew"]
    assert inj_total > 0, "injection must create defects"
    assert (inj_vol > 0.0).all()

    smoothed, _best = smooth_dual_mesh(
        injected, faces, owner, neigh, n_int, n_cells,
        _detect_defects, _face_geometry, _cell_centres,
        iterations=6, relaxation=0.5,
    )
    sm_counts, sm_vol = _defects(smoothed, faces, owner, neigh, n_int, n_cells)
    sm_total = sm_counts["pyramid"] + sm_counts["non_ortho"] + sm_counts["skew"]

    # Safety: never worse than the mesh it started from; meaningful gain.
    assert sm_total <= inj_total
    assert sm_total < inj_total, f"smoothing did not reduce defects {inj_total}->{sm_total}"
    # Boundary pinned bit-for-bit; no negative volumes; topology untouched.
    assert np.array_equal(smoothed[boundary], injected[boundary])
    assert (sm_vol > 0.0).all()
    assert len(smoothed) == len(injected)


def test_smoothing_on_clean_mesh_is_noop(tmp_path):
    points, faces, owner, neigh, _patches, n_int, n_cells = _run_dual(tmp_path)
    base_counts, _ = _defects(points, faces, owner, neigh, n_int, n_cells)
    base_total = base_counts["pyramid"] + base_counts["non_ortho"] + base_counts["skew"]

    smoothed, _best = smooth_dual_mesh(
        points, faces, owner, neigh, n_int, n_cells,
        _detect_defects, _face_geometry, _cell_centres,
        iterations=4, relaxation=0.5,
    )
    sm_counts, sm_vol = _defects(smoothed, faces, owner, neigh, n_int, n_cells)
    sm_total = sm_counts["pyramid"] + sm_counts["non_ortho"] + sm_counts["skew"]
    assert sm_total <= base_total
    assert (sm_vol > 0.0).all()


def test_zonal_mode_empty_zone_is_bit_identical(tmp_path):
    """Lane D2 plumbing pin: with a threshold above every cell's aspect
    (empty zone), one iteration must follow the default path bit-for-bit
    (masked candidates are immobile, zonal objective term is exactly 0).
    iterations=1 isolates the stop-condition, which legitimately differs
    (zonal keeps polishing at zero defects)."""
    points, faces, owner, neigh, _patches, n_int, n_cells = _run_dual(tmp_path)
    rng = np.random.default_rng(3)
    boundary = _boundary_vertex_mask(points, faces, n_int)
    injected = points.copy()
    for v in np.flatnonzero(~boundary):
        injected[v] += rng.normal(0, 0.15, 3)
    kw = dict(
        detect_defects=_detect_defects, face_geometry=_face_geometry,
        cell_centres=_cell_centres,
    )
    base, _ = smooth_dual_mesh(
        injected, faces, owner, neigh, n_int, n_cells,
        iterations=1, relaxation=0.5, **kw)
    zoned, _ = smooth_dual_mesh(
        injected, faces, owner, neigh, n_int, n_cells,
        iterations=1, relaxation=0.5, worst_aspect_thr=1e18, **kw)
    np.testing.assert_array_equal(zoned, base)


def test_zonal_mode_holds_safety_contract(tmp_path):
    """Lane D2 contract: a live zone never breaks the keep-best guarantees
    (defects non-increasing, boundary pinned, volumes positive)."""
    points, faces, owner, neigh, _patches, n_int, n_cells = _run_dual(tmp_path)
    boundary = _boundary_vertex_mask(points, faces, n_int)
    rng = np.random.default_rng(3)
    injected = points.copy()
    for v in np.flatnonzero(~boundary):
        injected[v] += rng.normal(0, 0.15, 3)
    inj_counts, _ = _defects(injected, faces, owner, neigh, n_int, n_cells)
    inj_total = inj_counts["pyramid"] + inj_counts["non_ortho"] + inj_counts["skew"]
    assert inj_total > 0
    smoothed, _best = smooth_dual_mesh(
        injected, faces, owner, neigh, n_int, n_cells,
        _detect_defects, _face_geometry, _cell_centres,
        iterations=4, relaxation=0.5, worst_aspect_thr=1.5,
    )
    sm_counts, sm_vol = _defects(smoothed, faces, owner, neigh, n_int, n_cells)
    sm_total = sm_counts["pyramid"] + sm_counts["non_ortho"] + sm_counts["skew"]
    assert sm_total <= inj_total
    assert np.array_equal(smoothed[boundary], injected[boundary])
    assert (sm_vol > 0.0).all()
