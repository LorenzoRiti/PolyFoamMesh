"""Unit + regression tests for the barycentric-dual tet->poly converter.

The fast tests build tiny tetrahedral OpenFOAM cases entirely in memory
(no GMSH, no WSL) and assert the geometric/topological invariants the
converter must guarantee before it writes anything:

- every primal vertex becomes exactly one dual cell,
- every dual cell is closed (area-vector sum ~ 0), with a positive volume,
- internal faces are upper-triangular (owner < neighbour),
- the output boundary is the exact subdivision of the input boundary
  (each primal boundary triangle -> exactly 3 quads, same patch),
- non-tet input is rejected with a clear message,
- a failing write never corrupts the original polyMesh (atomic rollback).

The regression test loads a recorded real-valve dual mesh (npz) and asserts the
in-process defect detector reproduces exactly what a real checkMesh reported
(pyramid count 895 predicted / 895 reported), which is the guard that keeps
every number this project measures trustworthy without a WSL round trip.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import polyfoammesh.core.foam_mesh_io as fio
from polyfoammesh.core.tet_poly_dual import (
    DualPolyResult,
    TetPolyDualConverter,
    _cell_centres,
    _detect_defects,
    _face_geometry,
    main as tet_poly_dual_main,
    planarity_report,
)

FIXDIR = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# helpers: build tiny pure-tet OpenFOAM cases in memory
# ---------------------------------------------------------------------------

def _newell(points: np.ndarray, verts) -> np.ndarray:
    p = points[list(verts)]
    n = np.zeros(3)
    for i in range(len(p)):
        j = (i + 1) % len(p)
        n[0] += (p[i, 1] - p[j, 1]) * (p[i, 2] + p[j, 2])
        n[1] += (p[i, 2] - p[j, 2]) * (p[i, 0] + p[j, 0])
        n[2] += (p[i, 0] - p[j, 0]) * (p[i, 1] + p[j, 1])
    return 0.5 * n


def _tet_centroid(points: np.ndarray, t: list[int]) -> np.ndarray:
    return points[t].mean(axis=0)


def build_tet_case(tmp_path: Path, points: np.ndarray, tets: list[list[int]],
                   boundary_patch: str = "wall") -> Path:
    """Write a pure-tet OpenFOAM polyMesh for a list of tetrahedra.

    Faces are assembled from the tets, wound outward (boundary) or
    owner -> neighbour (internal), internal-first + boundary per patch.
    """
    face_to_cells: dict[frozenset, list[int]] = {}
    face_verts: dict[frozenset, tuple] = {}
    for c, t in enumerate(tets):
        for face in [(t[0], t[1], t[2]), (t[0], t[1], t[3]),
                     (t[0], t[2], t[3]), (t[1], t[2], t[3])]:
            key = frozenset(face)
            face_to_cells.setdefault(key, []).append(c)
            face_verts[key] = tuple(sorted(face))

    internal: list[tuple[list[int], int, int]] = []
    boundary: list[tuple[list[int], int]] = []
    cell_cent = np.array([_tet_centroid(points, t) for t in tets])
    for key, cells in face_to_cells.items():
        if len(cells) == 1:
            o = cells[0]
            v = list(face_verts[key])
            n = _newell(points, v)
            d = points[list(face_verts[key])].mean(axis=0) - cell_cent[o]
            if float(n @ d) < 0:
                v.reverse()
            boundary.append((v, o))
        else:
            o, nb = sorted(cells)
            v = list(face_verts[key])
            n = _newell(points, v)
            d = cell_cent[nb] - cell_cent[o]
            if float(n @ d) < 0:
                v.reverse()
            internal.append((v, o, nb))

    internal.sort(key=lambda e: (e[1], e[2]))
    faces = [e[0] for e in internal]
    owner = [e[1] for e in internal]
    neigh = [e[2] for e in internal]
    n_int = len(faces)
    bstart = n_int
    for v, o in boundary:
        faces.append(v)
        owner.append(o)
    patches = [{
        "name": boundary_patch, "type": "patch",
        "nFaces": len(boundary), "startFace": bstart,
    }]

    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    fio.write_polymesh(poly, points.astype(np.float64), faces,
                       np.array(owner, dtype=np.int32),
                       np.array(neigh, dtype=np.int32), patches)
    read = fio.read_polymesh(poly)
    assert read[2].max() + 1 == len(tets), "helper wrote a broken tet case"
    return tmp_path


def _run_dual(case_dir: Path) -> DualPolyResult:
    return TetPolyDualConverter(case_dir, log=lambda m: None).run()


# ---------------------------------------------------------------------------
# tiny meshes
# ---------------------------------------------------------------------------

def test_single_tet_gives_one_cell_per_vertex(tmp_path):
    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    case = build_tet_case(tmp_path, pts, [[0, 1, 2, 3]])
    res = _run_dual(case)
    assert res.success, res.errors
    # one dual cell per primal vertex
    assert res.n_cells_after == 4
    assert res.min_cell_volume > 0.0
    # total domain volume == single tet volume == 1/6
    assert res.n_residual_tets == 0


def test_two_tets_share_face(tmp_path):
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [0, 1, 0],  # shared face base
        [0, 0, 1], [0, 0, -1],
    ], dtype=float)
    case = build_tet_case(tmp_path, pts, [[0, 1, 2, 3], [0, 1, 2, 4]])
    res = _run_dual(case)
    assert res.success, res.errors
    assert res.n_cells_after == 5  # 5 distinct primal vertices
    assert res.min_cell_volume > 0.0
    assert res.n_residual_tets == 0


def test_cube_invariants(tmp_path):
    # unit cube [0,1]^3 split into 6 tets pyramid onto the centre point
    c = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=float)
    o = np.array([0.5, 0.5, 0.5])
    pts = np.vstack([c, o])
    faces2d = [
        [[0, 1, 2, 3], [0, 4, 5, 1], [1, 5, 6, 2],
         [2, 6, 7, 3], [3, 7, 4, 0], [4, 7, 6, 5]],
    ][0]
    tets = []
    for f in faces2d:
        for tri in ((f[0], f[1], f[2]), (f[0], f[2], f[3])):
            tets.append([tri[0], tri[1], tri[2], 8])
    case = build_tet_case(tmp_path, pts, tets)
    before = None
    poly = case / "constant" / "polyMesh" / "points"
    before = poly.read_bytes()

    res = _run_dual(case)
    assert res.success, res.errors
    assert res.n_cells_after == 9  # 8 cube corners + centre
    assert res.n_residual_tets == 0
    assert res.min_cell_volume > 0.0
    # volume conserved: unit cube
    assert abs(res.volume_after - 1.0) < 1e-9, res.volume_after

    # reopen the written mesh and verify OpenFOAM ordering invariants
    pts2, faces, owner, neigh, patches = fio.read_polymesh(
        case / "constant" / "polyMesh"
    )
    n_int = len(neigh)
    assert np.all(owner[:n_int] < neigh[:n_int])
    # the cube's 6 quad faces are each split into 2 triangles, so there are
    # 12 primal boundary triangles -> exactly 3 boundary quads each
    assert sum(p["nFaces"] for p in patches) == 3 * 12
    assert len(patches) == 1 and patches[0]["name"] == "wall"
    # every cell has >= 4 faces and no face is degenerate
    nf = np.zeros(res.n_cells_after, dtype=int)
    np.add.at(nf, owner, 1)
    np.add.at(nf, neigh[:n_int], 1)
    assert nf.min() >= 4
    for f in faces:
        assert len(f) >= 3 and len(set(f)) == len(f)
    # unused points: none (the centre point must be used)
    used = set(v for f in faces for v in f)
    assert len(used) == len(pts2)

    # closure: sum of area vectors per cell ~ 0
    sf, cf = _face_geometry(pts2, faces)
    closure = np.zeros((res.n_cells_after, 3))
    np.add.at(closure, owner, sf)
    np.add.at(closure, neigh[:n_int], -sf[:n_int])
    mag = np.linalg.norm(sf, axis=1)
    scale = np.zeros(res.n_cells_after)
    np.add.at(scale, owner, mag)
    np.add.at(scale, neigh[:n_int], mag[:n_int])
    rel = np.linalg.norm(closure, axis=1) / np.maximum(scale, 1e-300)
    assert np.all(rel < 1e-8)


def test_rejects_non_tet_input(tmp_path):
    # a quad-based "prism-ish" cell: a single 4-sided face on a 5-vertex cell
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0.5, 0.5, 1],
    ], dtype=float)
    # build a case where one face is a quad -> not all faces triangles
    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    fio.write_polymesh(
        poly, pts,
        [[0, 1, 2, 3], [0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]],
        np.array([0, 0, 0, 0, 0], dtype=np.int32),
        np.array([-1, -1, -1, -1, -1], dtype=np.int32),
        [{"name": "wall", "type": "patch", "nFaces": 5, "startFace": 0}],
    )
    res = _run_dual(tmp_path)
    assert not res.success
    msg = " ".join(res.errors).lower()
    assert "tetrahedral" in msg


def test_write_failure_rollback(tmp_path):
    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    case = build_tet_case(tmp_path, pts, [[0, 1, 2, 3]])
    poly = case / "constant" / "polyMesh"
    original = {name: (poly / name).read_bytes()
                for name in ("points", "faces", "owner", "neighbour", "boundary")}

    import polyfoammesh.core.foam_mesh_io as _fio

    def boom(*a, **k):
        raise RuntimeError("simulated write failure")

    # Save the original reference BEFORE patching: restoring via `fio.write_faces`
    # after replacing the module attribute would re-read the patched boom and leak
    # it to every later test in the session.
    original_write_faces = _fio.write_faces
    _fio.write_faces = boom
    try:
        res = _run_dual(case)
    finally:
        _fio.write_faces = original_write_faces
    assert not res.success
    # original polyMesh untouched (atomic temp-dir write)
    for name, data in original.items():
        assert (poly / name).read_bytes() == data


# ---------------------------------------------------------------------------
# regression: in-process detector must agree with recorded checkMesh
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    # The in-process detector reproduces checkMesh EXACTLY on the pyramid
    # count (895/895).  Its non-orthogonality / skew counts use a cheaper
    # approximation of OpenFOAM's own methodology (302 vs 55, 11 vs 111 on
    # this mesh) — they only freeze the detector's own baseline, so a change
    # to the detector is caught without pretending it matches checkMesh.
    "expected", [{"pyramid": 895, "non_ortho_det": 302, "skew_det": 11}]
)
def test_detect_defects_matches_recorded_checkmesh(expected):
    npz = FIXDIR / "valve_dual.npz"
    if not npz.exists():
        pytest.skip("valve_dual.npz fixture missing — run "
                    "tools/poly_fixture_builder.py --regression-fixture")
    d = np.load(npz)
    pts = d["points"].astype(np.float64)
    sizes = d["face_sizes"]
    offsets = d["face_offsets"]
    verts = d["face_verts"]
    owner = d["owner"].astype(np.int64)
    neigh = d["neighbour"].astype(np.int64)
    n_int = int(d["n_int"])
    faces = [verts[offsets[i]:offsets[i + 1]].tolist() for i in range(len(sizes))]
    n_cells = int(max(owner.max(), neigh.max())) + 1

    sf, cf = _face_geometry(pts, faces)
    ctr, vol = _cell_centres(sf, cf, owner, neigh, n_int, n_cells)
    bad, counts = _detect_defects(pts, faces, sf, cf, ctr, owner, neigh, n_int, n_cells)

    # the one number that must match checkMesh exactly (docs: 895 / 895)
    assert counts["pyramid"] == expected["pyramid"], counts
    assert counts["non_ortho"] == expected["non_ortho_det"]
    assert counts["skew"] == expected["skew_det"]

    # the recorded fixture's real checkMesh output must still parse to the
    # same pyramid count (guards against a stale/regenerated fixture)
    import re
    txt = (FIXDIR / "valve_dual_checkmesh.txt").read_text(encoding="utf-8")
    pyr = re.search(r"Error in face pyramids:\s*(\d+)", txt)
    assert pyr and int(pyr.group(1)) == counts["pyramid"]

# ---------------------------------------------------------------------------
# non-planarity measurement and optional fix (planarity_report /
# fix_nonplanar_faces).  The perturbed cube is the "known non-planar mesh":
# its dual rings (one per primal edge) are genuinely non-planar, its boundary
# quads are planar by construction, and its total volume is exactly 1.
# ---------------------------------------------------------------------------

def _perturbed_cube():
    """Unit cube [0,1]^3 split into 12 tets with an off-centre apex o.

    The apex sits outside the symmetric centre, so the dual rings around the
    (corner, apex) primal edges are genuinely non-planar while every boundary
    quad stays planar.  Total domain volume == 1.0.
    """
    c = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=float)
    pts = np.vstack([c, np.array([0.5, 0.55, 0.45])])
    faces2d = [
        [0, 1, 2, 3], [0, 4, 5, 1], [1, 5, 6, 2],
        [2, 6, 7, 3], [3, 7, 4, 0], [4, 7, 6, 5],
    ]
    tets = []
    for f in faces2d:
        for tri in ((f[0], f[1], f[2]), (f[0], f[2], f[3])):
            tets.append([tri[0], tri[1], tri[2], 8])
    return pts, tets


def _read_dual_planarity(case_dir: Path) -> dict:
    pts, faces, owner, neigh, _ = fio.read_polymesh(case_dir / "constant" / "polyMesh")
    return planarity_report(pts, faces, owner, neigh, len(neigh))


def test_planarity_report_single_tet_all_planar(tmp_path):
    # a single tet's dual faces are 4-point rings [mid, fc(T1), c, fc(T2)]
    # which are coplanar by construction (the tet centroid is an affine
    # combination of the three boundary points) — nothing to flag
    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    case = build_tet_case(tmp_path, pts, [[0, 1, 2, 3]])
    _run_dual(case)
    rep = _read_dual_planarity(case)
    assert rep["n_faces"] == 18
    assert rep["n_nonplanar"] == 0
    assert rep["max_dev"] < 1e-9
    assert rep["n_degenerate"] == 0
    assert rep["n_nonplanar_internal"] == 0
    assert rep["n_nonplanar_boundary"] == 0
    # worst_faces lists the top-k deviations regardless of the threshold —
    # here they are all pure floating-point noise
    assert rep["worst_faces"]
    assert all(d < 1e-9 for _, d in rep["worst_faces"])


def test_planarity_report_flags_nonplanar_rings(tmp_path):
    # two tets sharing the face (0,1,2): the dual rings around the shared
    # edges run through 6 points (two tet centroids + the internal-face
    # centroid + three boundary points) and are NOT coplanar
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, -1],
    ], dtype=float)
    case = build_tet_case(tmp_path, pts, [[0, 1, 2, 3], [0, 1, 2, 4]])
    _run_dual(case)
    rep = _read_dual_planarity(case)
    assert rep["n_nonplanar"] == 2
    assert rep["n_nonplanar_internal"] == 2
    assert rep["n_nonplanar_boundary"] == 0
    assert rep["max_dev"] > 1e-3
    # worst-faces list is sorted descending
    devs = [d for _, d in rep["worst_faces"]]
    assert devs == sorted(devs, reverse=True)
    # tolerance math
    assert rep["tolerance"] == pytest.approx(rep["rel_tol"] * rep["bbox_diag"])


def test_planarity_report_perturbed_cube(tmp_path):
    pts, tets = _perturbed_cube()
    case = build_tet_case(tmp_path, pts, tets)
    res = _run_dual(case)
    assert res.success, res.errors
    rep = _read_dual_planarity(case)
    assert rep["n_nonplanar"] > 0, rep
    assert rep["pct_nonplanar"] == pytest.approx(
        100.0 * rep["n_nonplanar"] / rep["n_faces"]
    )
    # boundary quads are planar by construction — all flagged faces are internal
    assert rep["n_nonplanar_boundary"] == 0
    assert rep["n_nonplanar_internal"] == rep["n_nonplanar"]
    assert rep["max_dev"] > 1e-2  # clearly non-planar
    # a looser threshold flags fewer faces
    pts2, faces, owner, neigh, _ = fio.read_polymesh(case / "constant" / "polyMesh")
    rep2 = planarity_report(pts2, faces, owner, neigh, len(neigh), rel_tol=1e-2)
    assert rep2["n_nonplanar"] <= rep["n_nonplanar"]


def test_fix_triangulate_eliminates_nonplanar_faces(tmp_path):
    # the cfMesh approach: fan-triangulate every non-planar face from its
    # centroid.  Triangles are planar by construction, cell volumes/closure/
    # orientation are preserved (the fan tiles the face's area vector exactly).
    pts, tets = _perturbed_cube()
    case = build_tet_case(tmp_path, pts, tets)
    res = TetPolyDualConverter(
        case, log=lambda m: None, fix_nonplanar_faces="triangulate",
    ).run()
    assert res.success, res.errors
    rep = _read_dual_planarity(case)
    assert rep["n_nonplanar"] == 0, rep
    assert rep["max_dev"] < 1e-9
    # every previously non-planar face is now a triangle (planar faces are
    # copied through untouched, so the mesh still has quads etc.); the count
    # grew by sum(k-1) over the 15 non-planar faces (62 -> 122)
    pts2, faces, owner, neigh, _ = fio.read_polymesh(case / "constant" / "polyMesh")
    assert len(faces) == 122
    assert all(len(f) >= 3 for f in faces)
    # no non-triangular face may be non-planar anymore
    for f in faces:
        if len(f) > 3:
            dev = planarity_report(pts2, [f], rel_tol=0.0)["max_dev"]
            assert dev < 1e-9, f
    # cells stay polyhedral (9 cells), volume conserved, nothing regressed
    # (baseline defects are 0/0/0)
    assert res.n_cells_after == 9
    assert abs(res.volume_after - 1.0) < 1e-9
    assert res.min_cell_volume > 0.0
    assert res.defect_breakdown["pyramid"] == 0
    assert res.residual_defects == 0


def test_fix_planarize_keep_best_never_regresses(tmp_path):
    # keep-best contract (same criterion as the P1 poly smoother): the written
    # mesh is never worse than the unfixed one — defect counts never increase,
    # volumes stay positive, and the planarity deviation does not get worse.
    baseline_pts, tets = _perturbed_cube()
    base_case = build_tet_case(tmp_path / "base", baseline_pts, tets)
    _run_dual(base_case)
    base_rep = _read_dual_planarity(base_case)

    fix_case = build_tet_case(tmp_path / "fix", baseline_pts, tets)
    res = TetPolyDualConverter(
        fix_case, log=lambda m: None,
        fix_nonplanar_faces="planarize", planarize_iters=40,
    ).run()
    assert res.success, res.errors
    assert res.n_cells_after == 9
    assert abs(res.volume_after - 1.0) < 1e-9
    assert res.min_cell_volume > 0.0
    # keep-best on the in-process checkMesh replica (baseline is 0/0/0)
    assert res.residual_defects == 0
    assert res.defect_breakdown["pyramid"] == 0
    # planarity never regresses (max vertex deviation not larger than baseline)
    rep = _read_dual_planarity(fix_case)
    assert rep["max_dev"] <= base_rep["max_dev"] + 1e-12


def test_fix_none_changes_nothing(tmp_path):
    # fix_nonplanar_faces="none" must leave the output byte-identical to the
    # default converter, and the fix path must not even run.
    pts, tets = _perturbed_cube()
    a = build_tet_case(tmp_path / "a", pts, tets)
    b = build_tet_case(tmp_path / "b", pts, tets)
    r_default = _run_dual(a)
    r_none = TetPolyDualConverter(
        b, log=lambda m: None, fix_nonplanar_faces="none",
    ).run()
    assert r_default.success and r_none.success
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        assert (a / "constant" / "polyMesh" / name).read_bytes() == \
               (b / "constant" / "polyMesh" / name).read_bytes(), name


def test_fix_rejects_unknown_mode():
    with pytest.raises(ValueError):
        TetPolyDualConverter(Path("."), fix_nonplanar_faces="explode")


def test_planarity_cli_reports(capsys, tmp_path):
    pts, tets = _perturbed_cube()
    case = build_tet_case(tmp_path, pts, tets)
    _run_dual(case)
    rc = tet_poly_dual_main(["--planarity", str(case), "--top-k", "3"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "non-planarity report" in out
    assert "non-planar" in out
    assert "worst faces" in out
