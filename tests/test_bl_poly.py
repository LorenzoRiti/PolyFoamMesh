"""Tests for `core/bl_poly.py` — boundary layers on the barycentric-dual
polyhedral mesh (no WSL, no GMSH; tiny in-memory tet cases + the recorded
real-valve dual fixture).

Guarantees the engine's own invariants (which are what keep the produced
mesh valid for checkMesh / solvers):

- total volume conserved to machine precision (the prism stacks carve
  exactly the region the modified cells give up),
- every cell closed (signed area-vector sum ~ 0) with positive volume,
- internal faces upper-triangular (owner < neighbour),
- prism count = n_layers x wall faces (patches unchanged under
  apply_to_all=True; adjacent patches gain terminator faces under a
  partial selection),
- no unused points, deterministic output,
- partial-patch selection emits conforming terminator faces (FASE 1):
  the prism side faces at the BL/non-BL boundary become boundary faces of
  the adjacent patch, so the mesh stays valid without auto-closing to the
  whole boundary,
- invalid parameters / impossible layers fail cleanly and never write.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

import cfmesh_autogui.core.foam_mesh_io as fio
from cfmesh_autogui.core.bl_poly import PolyBoundaryLayerEngine, _cell_metrics

FIXDIR = Path(__file__).resolve().parent / "fixtures"
WORK_ROOT = Path("C:/cfmesh_bench/bl_poly_test")


def _cube_points_tets() -> tuple[np.ndarray, list[list[int]]]:
    c = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=float)
    pts = np.vstack([c, [[0.5, 0.5, 0.5]]])
    faces2d = [
        [0, 1, 2, 3], [0, 4, 5, 1], [1, 5, 6, 2],
        [2, 6, 7, 3], [3, 7, 4, 0], [4, 7, 6, 5],
    ]
    tets = []
    for f in faces2d:
        for tri in ((f[0], f[1], f[2]), (f[0], f[2], f[3])):
            tets.append([tri[0], tri[1], tri[2], 8])
    return pts, tets


def _build_dual_case(root: Path) -> Path:
    """Tiny tet cube -> barycentric dual -> written polyMesh. Returns case."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_tet_poly_dual import build_tet_case, _run_dual

    shutil.rmtree(root, ignore_errors=True)
    pts, tets = _cube_points_tets()
    case = build_tet_case(root, pts, tets)
    res = _run_dual(case)
    assert res.success, res.errors
    return case


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    shutil.rmtree(WORK_ROOT, ignore_errors=True)


def _mesh_ok(poly_dir: Path) -> None:
    """Re-read the written mesh and assert the BL-engine invariants."""
    points, faces, owner, neigh, patches = fio.read_polymesh(poly_dir)
    n_int = len(neigh)
    n_cells = int(max(owner.max(), neigh.max())) + 1
    # owner < neighbour for every internal face
    assert np.all(owner[:n_int] < neigh[:n_int])
    assert owner.min() >= 0 and neigh.min() >= 0
    assert owner.max() < n_cells and neigh.max() < n_cells
    # no unused points
    used = set()
    for f in faces:
        used.update(f)
    assert len(used) == len(points)
    # closure + positive volumes
    vols, closure, scale = _cell_metrics(points, faces, owner, neigh, n_cells, n_int)
    rel = np.linalg.norm(closure, axis=1) / np.maximum(scale, 1e-300)
    assert np.all(rel < 1e-8), f"cells not closed: {int((rel >= 1e-8).sum())}"
    assert np.all(vols > 0.0), "non-positive cell volume"
    return vols


# ---------------------------------------------------------------------------
# cube: full-boundary BL
# ---------------------------------------------------------------------------

def test_cube_bl_full_boundary_invariants():
    case = _build_dual_case(WORK_ROOT / "cube_full")
    poly = case / "constant" / "polyMesh"
    before = fio.read_polymesh(poly)
    vol0 = float(_cell_metrics(before[0], before[1], before[2], before[3],
                               int(max(before[2].max(), before[3].max())) + 1,
                               len(before[3]))[0].sum())
    n_wall_before = sum(p["nFaces"] for p in before[4])

    eng = PolyBoundaryLayerEngine(case)
    r = eng.run(n_layers=3, first_height=0.02, growth_rate=1.2, apply_to_all=True)
    assert r.success, r.errors
    assert r.n_prism_cells == 3 * n_wall_before  # 3 layers x 36 wall faces
    assert r.total_thickness > 0.0

    after = fio.read_polymesh(poly)
    vols = _mesh_ok(poly)
    # volume conserved
    assert abs(float(vols.sum()) - vol0) < 1e-9 * max(vol0, 1e-30)
    # patches unchanged (same names / face counts, boundary still starts at n_int)
    assert [(p["name"], p["nFaces"]) for p in after[4]] == \
           [(p["name"], p["nFaces"]) for p in before[4]]
    assert all(p["startFace"] == len(after[3]) for p in after[4])


def test_cube_bl_deterministic():
    case = _build_dual_case(WORK_ROOT / "cube_det")
    eng = PolyBoundaryLayerEngine(case)
    r1 = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2, apply_to_all=True)
    p1 = fio.read_points(case / "constant" / "polyMesh" / "points")
    case2 = _build_dual_case(WORK_ROOT / "cube_det2")
    r2 = PolyBoundaryLayerEngine(case2).run(
        n_layers=2, first_height=0.01, growth_rate=1.2, apply_to_all=True,
    )
    p2 = fio.read_points(case2 / "constant" / "polyMesh" / "points")
    assert r1.success and r2.success
    assert np.array_equal(p1, p2)


# ---------------------------------------------------------------------------
# partial-patch selection: terminator faces keep the mesh conforming (FASE 1)
# ---------------------------------------------------------------------------

def test_bl_partial_patch_terminators():
    """BL requested only on 'wall' — the face set is used AS-IS (FASE 1):
    at the BL/non-BL boundary every prism side face lies in the plane of
    the adjacent (unselected) wall face and becomes a BOUNDARY face of
    that patch owned by the prism; the unselected wall faces move their
    shared wall vertices to the last layer, so every cell stays closed,
    only the named patch gets prisms (no more auto-close), and the patch
    that gained terminator faces grows in nFaces."""
    case = _build_dual_case(WORK_ROOT / "cube_seam")
    poly = case / "constant" / "polyMesh"
    # split the single 'wall' patch into wall (z=0 quads) + inlet (the rest):
    # OpenFOAM patches are contiguous, so reorder boundary faces.
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    n_int = len(neigh)
    bnd = [(n_int + bi, faces[n_int + bi]) for bi in range(len(faces) - n_int)]
    wall = [bi for bi, f in bnd if points[f].T[2].max() <= 1e-12]
    inlet = [bi for bi, f in bnd if points[f].T[2].max() > 1e-12]
    assert wall and inlet
    order = wall + inlet
    new_bnd_faces = [faces[bi] for bi in order]
    new_bnd_owner = [owner[bi] for bi in order]
    faces2 = faces[:n_int] + new_bnd_faces
    owner2 = np.concatenate([owner[:n_int], new_bnd_owner])
    patches2 = [
        {"name": "wall", "type": "patch", "nFaces": len(wall), "startFace": n_int},
        {"name": "inlet", "type": "patch", "nFaces": len(inlet),
         "startFace": n_int + len(wall)},
    ]
    fio.write_polymesh(poly, points, faces2, owner2, neigh, patches2)
    n_wall_before = len(wall)

    eng = PolyBoundaryLayerEngine(case)
    r = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                patch_names=["wall"], apply_to_all=False)
    assert r.success, r.errors
    # ONLY the named patch got prisms — no auto-close anymore; the prism
    # count derives from the INPUT mesh (wall faces x layers), never a
    # hardcoded number (the dual boundary may collapse in the future)
    assert r.wall_patches == ["wall"]
    assert r.n_prism_cells == 2 * n_wall_before
    n_term = r.stats["n_terminator_faces"]
    assert n_term > 0

    after = fio.read_polymesh(poly)
    n_int_after = len(after[3])
    # terminators are BOUNDARY faces: the wall patch face count is
    # unchanged (prism bottoms only) and the inlet patch (the one without
    # BL) grew by exactly the terminator faces.  n_int grows only by the
    # prism-to-prism side faces between ADJACENT selected quads (the same
    # faces that exist in the full-BL case), never by the terminators.
    assert np.all(after[2][:n_int_after] < after[3])
    names_after = {p["name"]: p for p in after[4]}
    names_before = {p["name"]: p for p in patches2}
    assert names_after["wall"]["nFaces"] == names_before["wall"]["nFaces"]
    assert names_after["inlet"]["nFaces"] == \
        names_before["inlet"]["nFaces"] + n_term
    # patches tile the boundary exactly (startFace sequential from n_int)
    assert after[4][0]["startFace"] == n_int_after
    s = n_int_after
    for p in after[4]:
        assert p["startFace"] == s
        s += p["nFaces"]
    assert s == len(after[1])
    _mesh_ok(poly)


# ---------------------------------------------------------------------------
# clamp / fallback
# ---------------------------------------------------------------------------

def test_bl_clamps_huge_first_layer():
    """A first-layer height far larger than the local cell must be clamped
    per wall vertex (inversion guard) instead of producing negative cells."""
    case = _build_dual_case(WORK_ROOT / "cube_huge")
    eng = PolyBoundaryLayerEngine(case)
    r = eng.run(n_layers=3, first_height=10.0, growth_rate=1.2, apply_to_all=True)
    assert r.success, r.errors
    # the effective total thickness must be far below the requested 10 m
    assert r.total_thickness < 1.0
    vols = _mesh_ok(case / "constant" / "polyMesh")
    assert np.all(vols > 0.0)


def test_bl_rejects_bad_params_and_leaves_mesh_untouched():
    case = _build_dual_case(WORK_ROOT / "cube_bad")
    poly = case / "constant" / "polyMesh"
    before = (poly / "points").read_bytes()
    eng = PolyBoundaryLayerEngine(case)
    for kw in (
        dict(n_layers=0),
        dict(first_height=0.0),
        dict(growth_rate=0.9),  # below the uniform-layer minimum of 1.0
    ):
        r = eng.run(apply_to_all=True, **kw)
        assert not r.success
        assert r.errors
    assert (poly / "points").read_bytes() == before, "mesh must be untouched"


# ---------------------------------------------------------------------------
# recorded real-valve dual mesh (1.05M points)
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_valve_fixture_bl_invariants():
    npz = FIXDIR / "valve_dual.npz"
    if not npz.exists():
        pytest.skip("valve_dual.npz fixture not present")
    d = np.load(npz)
    points = d["points"].astype(np.float64)
    n_int = int(d["n_int"])
    faces = []
    for i in range(len(d["face_sizes"])):
        s = d["face_offsets"][i]
        faces.append(d["face_verts"][s:s + d["face_sizes"][i]].tolist())
    owner = d["owner"].astype(np.int64)
    neigh = d["neighbour"].astype(np.int64)
    patches = [{
        "name": "wall", "type": "patch",
        "nFaces": len(faces) - n_int, "startFace": n_int,
    }]
    case = WORK_ROOT / "valve_bl"
    shutil.rmtree(case, ignore_errors=True)
    (case / "constant").mkdir(parents=True, exist_ok=True)
    poly = case / "constant" / "polyMesh"
    fio.write_polymesh(poly, points, faces, owner, neigh, patches)

    r = PolyBoundaryLayerEngine(case).run(
        n_layers=2, first_height=1e-5, growth_rate=1.2, apply_to_all=True,
    )
    assert r.success, r.errors
    assert r.n_prism_cells > 0
    vols = _mesh_ok(poly)
    vol0 = float(_cell_metrics(points, faces, owner, neigh,
                               int(max(owner.max(), neigh.max())) + 1, n_int)[0].sum())
    assert abs(float(vols.sum()) - vol0) < 1e-6 * max(vol0, 1e-30)
