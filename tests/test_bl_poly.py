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
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _test_helpers import space_free_tmp_root  # noqa: E402

import polyfoammesh.core.foam_mesh_io as fio
from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine, _cell_metrics

FIXDIR = Path(__file__).resolve().parent / "fixtures"
WORK_ROOT = space_free_tmp_root() / "cfmesh_bench" / "bl_poly_test"


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
    # healthy cube corners keep all 3 layers (FASE 1 scaling) — the count
    # is n_layers x wall faces, derived from the input mesh
    assert r.n_prism_cells == 3 * n_wall_before  # 3 layers x 36 wall faces
    assert r.stats["layers_per_face_max"] == 3
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
    """Pins the DOCUMENTED valve behaviour (docs/residual_risks.md,
    'Polyhedral Conversion'): the valve carries the concave-defect class,
    so the BL engine must FAIL CLEANLY — every fallback scale attempted
    and reported, the mesh on disk left byte-identical (never a corrupt
    or half-written polyMesh), and no crash. The success path (volume
    conservation, prism counts) is pinned by the cube/venturi tests above.
    The test previously asserted r.success on the valve, i.e. the one
    behaviour the engine documents as impossible on this fixture — it sat
    in the slow bucket unexecuted (and, before the NpzFile hoist, would
    not even have terminated in a suite run).
    """
    npz = FIXDIR / "valve_dual.npz"
    if not npz.exists():
        pytest.skip("valve_dual.npz fixture not present")
    d = np.load(npz)
    points = d["points"].astype(np.float64)
    n_int = int(d["n_int"])
    # Hoist the npz members OUT of the loop: NpzFile.__getitem__ re-opens
    # and re-decompresses the member from the zip archive on EVERY access
    # (~24 MB of face_verts per call). Indexing d["face_verts"] once per
    # face turned this already-slow test into a ~4 h CPU-bound grind
    # (1.24 M faces x full decompress) that could never finish in a suite
    # run; captured once it runs in seconds.
    face_verts = d["face_verts"]
    face_sizes = d["face_sizes"].tolist()
    face_offsets = d["face_offsets"].tolist()
    faces = []
    for i in range(len(face_sizes)):
        s = face_offsets[i]
        faces.append(face_verts[s:s + face_sizes[i]].tolist())
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
    before = {
        p.name: p.read_bytes()
        for p in sorted(poly.iterdir()) if p.is_file()
    }

    r = PolyBoundaryLayerEngine(case).run(
        n_layers=2, first_height=1e-5, growth_rate=1.2, apply_to_all=True,
    )

    if r.success:
        # Healthy-geometry invariants, should the valve ever start passing.
        assert r.n_prism_cells > 0
        vols = _mesh_ok(poly)
        vol0 = float(_cell_metrics(points, faces, owner, neigh,
                                   int(max(owner.max(), neigh.max())) + 1, n_int)[0].sum())
        assert abs(float(vols.sum()) - vol0) < 1e-6 * max(vol0, 1e-30)
        return

    # Clean-failure invariants (the documented valve path):
    assert r.errors, "failure must be explained, not silent"
    assert r.n_prism_cells == 0
    # every fallback scale was attempted and its failure reported
    assert any("scale=" in w for w in r.warnings), r.warnings
    # the mesh on disk is byte-identical: a failed BL must never leave a
    # corrupt polyMesh behind
    after = {
        p.name: p.read_bytes()
        for p in sorted(poly.iterdir()) if p.is_file()
    }
    assert after == before


def test_bl_fase2_local_termination_drops_defective_faces():
    """FASE 2: faces flagged as concave (input pyramid violations) are
    reduced to ONE layer locally; the consistency fixpoint then flattens the
    connected region to the minimum count, so the shell stays covered
    (volume conserved), every cell stays closed, and the flagged spots no
    longer get the crushed multi-layer stack."""
    case = _build_dual_case(WORK_ROOT / "cube_drop")
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    n_int = len(neigh)
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    sel, used = eng._select_faces(faces, owner, patches, n_int, None, True)
    n_cells = int(max(owner.max(), neigh.max())) + 1
    vol0 = float(_cell_metrics(points, faces, owner, neigh, n_cells, n_int)[0].sum())
    # flag two wall faces as concave -> the connected region flattens to 1
    drop_bnd = {0, 1}
    b = eng._build(points, faces, owner, neigh, patches, n_int, sel,
                   3, 0.02, 1.2, 0.5, 0, drop_bnd)
    assert b["n_prism_cells"] == len(sel), (
        f"got {b['n_prism_cells']}, expected {len(sel)} (1 layer per face)"
    )
    assert b["layers_per_face_max"] == 1
    ok, msg = eng._validate(b, vol0)
    assert ok, msg


def test_bl_global_winding_solve_is_exact_after_flip():
    """The global parity solve (replacing the per-cell greedy repair) closes
    EVERY cell exactly after a deliberately flipped internal face — the old
    greedy converged to a local minimum on twisted cells (24 unclosed on the
    valve) and could cascade into a catastrophic global flip."""
    from polyfoammesh.core.bl_poly import _solve_global_windings

    case = _build_dual_case(WORK_ROOT / "cube_winding")
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    n_int = len(neigh)
    n_cells = int(max(owner.max(), neigh.max())) + 1
    # break the winding consistency of one internal face
    faces[0] = list(reversed(faces[0]))
    solved, stats = _solve_global_windings(points, faces, owner, neigh,
                                           n_int, n_cells)
    assert stats["n_conflicts"] == 0, stats
    assert stats["n_nonmanifold_edges"] == 0, stats
    vols, closure, scale = _cell_metrics(points, solved, owner, neigh,
                                         n_cells, n_int)
    rel = np.linalg.norm(closure, axis=1) / np.maximum(scale, 1e-300)
    assert np.all(rel < 1e-8), f"cells not closed: {int((rel >= 1e-8).sum())}"
    assert np.all(vols > 0.0), "non-positive cell volume"


def _reference_face_parity_graph(faces, owner, neigh, n_int):
    """Independent, unvectorized re-implementation of the graph construction
    that `_build_face_parity_graph` replaced — kept ONLY as a ground truth
    for the equivalence test below, never imported by production code.
    Deliberately written from the algorithm description, not copy-pasted,
    so it doesn't just re-check itself. Returns a normalized, sorted
    edge-list ``{(min(f1,f2), max(f1,f2)): parity}`` for easy comparison
    (the production function's own `adj` dict form is order-dependent)."""
    from collections import defaultdict

    edge_occ: dict[tuple[int, int], list[tuple[int, int, int]]] = defaultdict(list)
    for fi, f in enumerate(faces):
        m = len(f)
        fav = int(owner[fi])
        nav = int(neigh[fi]) if fi < n_int else -1
        for k in range(m):
            a, b = f[k], f[(k + 1) % m]
            key = (a, b) if a < b else (b, a)
            sd = +1 if (a, b) == key else -1
            edge_occ[key].append((fav, fi, sd))
            if nav >= 0:
                edge_occ[key].append((nav, fi, -sd))

    pairs = {}
    n_incompat = 0
    for _key, occs in edge_occ.items():
        by_cell: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for c, fi, d in occs:
            by_cell[c].append((fi, d))
        for _c, lst in by_cell.items():
            if len(lst) != 2:
                n_incompat += 1
                continue
            (f1, d1), (f2, d2) = lst
            if f1 == f2:
                continue
            parity = -d1 * d2
            pairs[(min(f1, f2), max(f1, f2))] = parity
    return pairs, n_incompat


def _adj_to_pairs(adj):
    """Normalize `_build_face_parity_graph`'s symmetric adjacency dict into
    the same `{(min,max): parity}` shape the reference function returns."""
    pairs = {}
    for f1, nbrs in adj.items():
        for f2, parity in nbrs:
            key = (min(f1, f2), max(f1, f2))
            if key in pairs:
                assert pairs[key] == parity, f"asymmetric parity for {key}"
            else:
                pairs[key] = parity
    return pairs


def _make_ragged_synthetic_mesh():
    """A tiny hand-built mesh with mixed face degrees (tri + quad + pent),
    an internal face and a boundary face sharing edges, and a deliberately
    non-manifold edge (three faces sharing one edge-cell pairing) — exactly
    the kind of irregular input the real dual mesh produces, but small
    enough to hand-verify."""
    owner = np.array([0, 0, 0, 1, 0], dtype=np.int64)
    neigh = np.array([1], dtype=np.int64)  # face 0 is the only internal face
    faces = [
        [0, 1, 2, 3],       # face 0: internal, quad, owner=0 neigh=1
        [0, 3, 4],          # face 1: boundary tri, owner=0, shares edge (0,3) w/ face 0
        [4, 3, 2, 5, 6],    # face 2: boundary pentagon, owner=0
        [1, 0, 5],          # face 3: boundary tri, owner=1
        [0, 3, 7],          # face 4: boundary tri, owner=0, ALSO shares edge (0,3)
    ]
    return faces, owner, neigh


def test_face_parity_graph_matches_reference_on_synthetic_ragged_mesh():
    """The vectorized graph builder must match the independent reference
    loop exactly (same pairs, same parities, same non-manifold count) on a
    small hand-built mesh with mixed face degrees and a genuine
    non-manifold edge — not just on "nice" quad-only input."""
    from polyfoammesh.core.bl_poly import _build_face_parity_graph

    faces, owner, neigh = _make_ragged_synthetic_mesh()
    n_int = len(neigh)

    ref_pairs, ref_incompat = _reference_face_parity_graph(faces, owner, neigh, n_int)
    adj, n_incompat = _build_face_parity_graph(faces, owner, neigh, n_int)
    got_pairs = _adj_to_pairs(adj)

    assert n_incompat == ref_incompat
    assert got_pairs == ref_pairs


def test_global_windings_vectorized_graph_matches_reference_on_valve_dual():
    """Same equivalence check as above, on the real recorded valve dual
    fixture — irregular face degrees at real scale, not just a hand-built
    toy — plus an end-to-end sanity check that the production solve using
    this graph still closes every cell with zero conflicts."""
    from polyfoammesh.core.bl_poly import (
        _build_face_parity_graph,
        _solve_global_windings,
    )

    npz = FIXDIR / "valve_dual.npz"
    if not npz.exists():
        pytest.skip("valve_dual.npz fixture not present")
    data = np.load(npz, allow_pickle=True)
    points = data["points"]
    offsets = data["face_offsets"]
    verts = data["face_verts"]
    faces = [
        verts[offsets[i]:offsets[i + 1]].tolist() for i in range(len(offsets) - 1)
    ]
    owner = data["owner"]
    neigh = data["neighbour"]
    n_int = len(neigh)
    n_cells = int(max(owner.max(), neigh.max())) + 1

    ref_pairs, ref_incompat = _reference_face_parity_graph(faces, owner, neigh, n_int)
    adj, n_incompat = _build_face_parity_graph(faces, owner, neigh, n_int)
    got_pairs = _adj_to_pairs(adj)

    assert n_incompat == ref_incompat
    assert got_pairs == ref_pairs

    _solved, stats = _solve_global_windings(points, faces, owner, neigh,
                                            n_int, n_cells)
    assert stats["n_conflicts"] == 0, stats


def test_bl_zero_concave_binary_drop_keeps_full_stack_elsewhere():
    """Binary local termination (opt-in): the flagged faces get ZERO layers
    (no shell), every other face keeps the FULL stack — unlike the fixpoint
    path, which floods the whole region to the minimum count.  Cells stay
    closed and positive; the shell under the dropped faces is deliberately
    given up (documented domain change), so volume conservation is NOT
    asserted here."""
    case = _build_dual_case(WORK_ROOT / "cube_zero_concave")
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    n_int = len(neigh)
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    sel, used = eng._select_faces(faces, owner, patches, n_int, None, True)
    n_cells = int(max(owner.max(), neigh.max())) + 1
    drop_bnd = {0, 1}
    b = eng._build(points, faces, owner, neigh, patches, n_int, sel,
                   3, 0.02, 1.2, 0.5, 0, drop_bnd, zero_concave=True)
    assert b["layers_per_face_min"] == 0
    assert b["layers_per_face_max"] == 3
    assert b["n_prism_cells"] == (len(sel) - 2) * 3, b["n_prism_cells"]
    vols, closure, scale = _cell_metrics(
        b["points"], b["faces"], np.asarray(b["owner"]),
        np.asarray(b["neigh"]), int(max(b["owner"].max(), b["neigh"].max())) + 1,
        int(b["n_int"]),
    )
    rel = np.linalg.norm(closure, axis=1) / np.maximum(scale, 1e-300)
    assert np.all(rel < 1e-8), f"cells not closed: {int((rel >= 1e-8).sum())}"
    assert np.all(vols > 0.0), "non-positive cell volume"


def test_bl_local_termination_run_succeeds_on_healthy_cube():
    """The opt-in iterative local termination path (run(..., 
    local_termination=True)) must succeed on a healthy mesh with no
    exclusions, and report zero excluded faces."""
    case = _build_dual_case(WORK_ROOT / "cube_lt")
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    res = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                  apply_to_all=True, local_termination=True)
    assert res.success, (res.errors, res.warnings)
    assert res.n_prism_cells > 0
    assert res.stats.get("local_excluded_faces") == 0
    assert "local_excluded_faces" in res.stats
    assert res.stats.get("local_termination_mode") == "carry"
    _mesh_ok(case / "constant" / "polyMesh")


def test_bl_local_termination_decoupled_mode_on_healthy_cube():
    """local_termination="decoupled" must succeed on a healthy mesh at the
    REQUESTED scale (1.0, full thickness), with zero exclusions and the
    mode recorded in the stats — on healthy input it matches the legacy
    carry mode (resetting on scale change only matters after a scale
    actually fails)."""
    case = _build_dual_case(WORK_ROOT / "cube_lt_decoupled")
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    res = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                  apply_to_all=True, local_termination="decoupled")
    assert res.success, (res.errors, res.warnings)
    assert res.n_prism_cells > 0
    assert res.stats.get("local_excluded_faces") == 0
    assert res.stats.get("scale") == 1.0, res.stats
    assert res.stats.get("local_termination_mode") == "decoupled"
    _mesh_ok(case / "constant" / "polyMesh")


def test_bl_local_termination_rejects_unknown_mode():
    case = _build_dual_case(WORK_ROOT / "cube_lt_bad_mode")
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    res = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                  apply_to_all=True, local_termination="bogus")
    assert not res.success
    assert any("unknown local_termination mode" in e for e in res.errors)


def test_bl_local_termination_max_rounds_param():
    """local_max_rounds is validated and plumbed through to the loop: a
    raised value runs (healthy cube unaffected) and is recorded in stats;
    out-of-range values are rejected."""
    case = _build_dual_case(WORK_ROOT / "cube_lt_rounds")
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    res = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                  apply_to_all=True, local_termination="decoupled",
                  local_max_rounds=6)
    assert res.success, (res.errors, res.warnings)
    assert res.stats.get("local_termination_max_rounds") == 6
    res2 = PolyBoundaryLayerEngine(case, log=lambda m: None).run(
        n_layers=2, first_height=0.01, growth_rate=1.2,
        apply_to_all=True, local_termination="decoupled",
        local_max_rounds=0)
    assert not res2.success
    assert any("local_max_rounds" in e for e in res2.errors)


def test_bl_local_termination_decoupled_vertex_on_healthy_cube():
    """H4: the vertex-exclusion mode is a no-op on a healthy mesh —
    success at scale 1.0 round 0, zero exclusions, mode recorded."""
    case = _build_dual_case(WORK_ROOT / "cube_lt_vertex")
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    res = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                  apply_to_all=True, local_termination="decoupled_vertex")
    assert res.success, (res.errors, res.warnings)
    assert res.n_prism_cells > 0
    assert res.stats.get("local_excluded_faces") == 0
    assert res.stats.get("scale") == 1.0
    assert res.stats.get("local_termination_mode") == "decoupled_vertex"


def test_bl_local_height_retry_noop_on_healthy_cube():
    """local_height_retry=True must be byte-identical to False on a
    healthy mesh: nothing ever fails, so _retry_local_height is never
    even called (the 'if gained:' guard skips it entirely)."""
    case = _build_dual_case(WORK_ROOT / "cube_ht_retry")
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    res = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                  apply_to_all=True, local_termination="decoupled_vertex",
                  local_height_retry=True)
    assert res.success, (res.errors, res.warnings)
    assert res.stats.get("local_excluded_faces") == 0
    assert res.stats.get("scale") == 1.0


def test_bl_local_height_retry_rejects_without_decoupled_vertex():
    case = _build_dual_case(WORK_ROOT / "cube_ht_retry_bad")
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    res = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                  apply_to_all=True, local_height_retry=True)
    assert not res.success
    assert any("local_height_retry" in e for e in res.errors)

    case2 = _build_dual_case(WORK_ROOT / "cube_ht_retry_bad2")
    eng2 = PolyBoundaryLayerEngine(case2, log=lambda m: None)
    res2 = eng2.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                    apply_to_all=True, local_termination="decoupled",
                    local_height_retry=True)
    assert not res2.success
    assert any("local_height_retry" in e for e in res2.errors)


def test_bl_vertex_height_scale_noop_by_default():
    """_build's vertex_height_scale=None (default) must be byte-identical
    to passing an empty dict or a dict of all-1.0 -- a direct regression
    guard on the no-op contract before relying on it for the retry."""
    case_a = _build_dual_case(WORK_ROOT / "cube_vhs_a")
    case_b = _build_dual_case(WORK_ROOT / "cube_vhs_b")
    eng_a = PolyBoundaryLayerEngine(case_a, log=lambda m: None)
    eng_b = PolyBoundaryLayerEngine(case_b, log=lambda m: None)
    pts, faces, owner, neigh, patches = fio.read_polymesh(
        case_a / "constant" / "polyMesh")
    n_int = len(neigh)
    sel, _ = eng_a._select_faces(faces, owner, patches, n_int, None, True)
    built_a = eng_a._build(pts, faces, owner, neigh, patches, n_int, sel,
                           2, 0.01, 1.2, 0.5)
    built_b = eng_b._build(pts, faces, owner, neigh, patches, n_int, sel,
                           2, 0.01, 1.2, 0.5, vertex_height_scale={})
    np.testing.assert_array_equal(built_a["points"], built_b["points"])


def test_bl_normal_method_rejects_unknown_value():
    case = _build_dual_case(WORK_ROOT / "cube_nm_bad")
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    res = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                  apply_to_all=True, normal_method="bogus")
    assert not res.success
    assert any("unknown normal_method" in e for e in res.errors)


def test_bl_normal_method_rejects_dual_convexity_combo():
    """most_visible is only implemented for concavity_criterion=angle_fade
    (the default) — dual_convexity does not compute per-vertex face-normal
    fans, so there is nothing for the min-max search to run on."""
    case = _build_dual_case(WORK_ROOT / "cube_nm_combo")
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    res = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                  apply_to_all=True, normal_method="most_visible",
                  concavity_criterion="dual_convexity")
    assert not res.success
    assert any("most_visible" in e for e in res.errors)


def test_bl_normal_method_most_visible_succeeds_on_healthy_cube():
    """most_visible must build a valid BL mesh on the easy healthy-cube
    case with the same topology (prism count) as area_weighted — the cube
    has corner wall vertices with 3 mutually orthogonal incident face
    normals, so the two methods legitimately pick DIFFERENT extrusion
    directions there (min-max vs area-weighted average), which is why
    this only checks structural equivalence, not identical geometry."""
    case_aw = _build_dual_case(WORK_ROOT / "cube_nm_aw")
    res_aw = PolyBoundaryLayerEngine(case_aw, log=lambda m: None).run(
        n_layers=2, first_height=0.01, growth_rate=1.2, apply_to_all=True,
        normal_method="area_weighted",
    )
    case_mv = _build_dual_case(WORK_ROOT / "cube_nm_mv")
    res_mv = PolyBoundaryLayerEngine(case_mv, log=lambda m: None).run(
        n_layers=2, first_height=0.01, growth_rate=1.2, apply_to_all=True,
        normal_method="most_visible",
    )
    assert res_aw.success and res_mv.success, (res_aw.errors, res_mv.errors)
    assert res_aw.n_prism_cells == res_mv.n_prism_cells
    assert res_mv.stats.get("min_cell_volume", 0.0) > 0.0
    _mesh_ok(case_mv / "constant" / "polyMesh")


def test_bl_normal_method_smoothed_accepts_dual_convexity_combo():
    """Lane C: 'smoothed' is unlocked under concavity_criterion=
    dual_convexity (the blend weight reads angle_fade, which carries the
    dual signal there). Must run cleanly on the healthy cube with the same
    topology as area_weighted; most_visible+dual stays rejected (separate
    test above)."""
    case = _build_dual_case(WORK_ROOT / "cube_nm_smooth_combo")
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    res = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                  apply_to_all=True, normal_method="smoothed",
                  concavity_criterion="dual_convexity")
    assert res.success, (res.errors, res.warnings)
    assert res.stats.get("concavity_criterion") == "dual_convexity"
    assert res.n_prism_cells > 0
    assert res.stats.get("min_cell_volume", 0.0) > 0.0
    _mesh_ok(case / "constant" / "polyMesh")


def test_bl_normal_method_smoothed_succeeds_on_healthy_cube():
    """'smoothed' (Alauzet normal smoothing, adapted -- see
    notes/normal_smoothing_reasoning.md) must build a valid BL mesh on the
    healthy cube with the same topology as area_weighted. On a flat cube
    face every incident normal at an edge-interior wall vertex already
    agrees with its neighbours, so Laplacian smoothing is near-identity
    there; corner vertices (3 orthogonal incident normals, angle_fade < 1)
    DO get blended toward their smoothed neighbours, same caveat as the
    most_visible test above -- checked structurally, not geometrically."""
    case_aw = _build_dual_case(WORK_ROOT / "cube_nm_sm_aw")
    res_aw = PolyBoundaryLayerEngine(case_aw, log=lambda m: None).run(
        n_layers=2, first_height=0.01, growth_rate=1.2, apply_to_all=True,
        normal_method="area_weighted",
    )
    case_sm = _build_dual_case(WORK_ROOT / "cube_nm_sm")
    res_sm = PolyBoundaryLayerEngine(case_sm, log=lambda m: None).run(
        n_layers=2, first_height=0.01, growth_rate=1.2, apply_to_all=True,
        normal_method="smoothed",
    )
    assert res_aw.success and res_sm.success, (res_aw.errors, res_sm.errors)
    assert res_aw.n_prism_cells == res_sm.n_prism_cells
    assert res_sm.stats.get("min_cell_volume", 0.0) > 0.0
    _mesh_ok(case_sm / "constant" / "polyMesh")


def test_laplacian_smooth_normal_field_matches_hand_computed_star():
    """Direct unit test of the smoothing primitive against the
    hand-verified 5-vertex star from
    scratchpad/verify_normal_smoothing.py: a divergent 'ridge' normal at
    the centre must move toward its 4 unanimous neighbours' consensus
    direction, stay unit length, and converge to a stable fixed point."""
    from polyfoammesh.core.bl_poly import _laplacian_smooth_normal_field

    normals = np.zeros((5, 3))
    idx = {"c": 0, "a": 1, "b": 2, "d": 3, "e": 4}
    normals[idx["c"]] = [1.0, 0.0, 0.0]
    for k in ("a", "b", "d", "e"):
        normals[idx[k]] = [0.0, 0.0, 1.0]
    adjacency = {
        idx["c"]: {idx["a"], idx["b"], idx["d"], idx["e"]},
        idx["a"]: {idx["c"]}, idx["b"]: {idx["c"]},
        idx["d"]: {idx["c"]}, idx["e"]: {idx["c"]},
    }
    wall_verts = list(idx.values())

    result_20 = _laplacian_smooth_normal_field(normals, wall_verts, adjacency, iters=20)
    result_100 = _laplacian_smooth_normal_field(normals, wall_verts, adjacency, iters=100)
    for v in wall_verts:
        assert np.linalg.norm(result_20[v]) == pytest.approx(1.0, abs=1e-9)
    # converged to a stable fixed point by iteration 20 (matches the
    # hand-verified script's own measurement)
    np.testing.assert_allclose(result_20[idx["c"]], result_100[idx["c"]], atol=1e-6)
    angle_c = np.degrees(np.arccos(np.clip(result_20[idx["c"]] @ [0, 0, 1], -1, 1)))
    assert 20.0 < angle_c < 45.0, angle_c  # moved from 90deg toward the neighbours' 0deg


def test_bl_local_exclude_widen_param():
    """local_exclude_widen is validated and plumbed: a raised value runs
    (healthy cube unaffected, since the widen step is a no-op when no
    exclusion is collected); out-of-range values are rejected. The flag is
    also recorded in stats for observability."""
    case = _build_dual_case(WORK_ROOT / "cube_lt_widen")
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    res = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                  apply_to_all=True, local_termination="decoupled",
                  local_exclude_widen=2)
    assert res.success, (res.errors, res.warnings)
    assert res.stats.get("local_termination_max_rounds") == 3
    assert res.n_prism_cells > 0
    res2 = PolyBoundaryLayerEngine(case, log=lambda m: None).run(
        n_layers=2, first_height=0.01, growth_rate=1.2,
        apply_to_all=True, local_termination="decoupled",
        local_exclude_widen=4)
    assert not res2.success
    assert any("local_exclude_widen" in e for e in res2.errors)


def test_bl_local_termination_early_exit_intermediates_noop_on_healthy():
    """early_exit_intermediates=True is a no-op on a healthy mesh:
    success at scale 1.0 round 0, zero exclusions, flag recorded. The
    flag is the only difference vs. the legacy decoupled run."""
    case = _build_dual_case(WORK_ROOT / "cube_lt_skip")
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    res = eng.run(n_layers=2, first_height=0.01, growth_rate=1.2,
                  apply_to_all=True, local_termination="decoupled",
                  early_exit_intermediates=True)
    assert res.success, (res.errors, res.warnings)
    assert res.n_prism_cells > 0
    assert res.stats.get("local_excluded_faces") == 0
    assert res.stats.get("scale") == 1.0, res.stats
    assert res.stats.get("local_termination_mode") == "decoupled"
    assert res.stats.get("local_termination_early_exit_intermediates") is True
    # the skip flag is only announced in warnings AFTER scale 1.0 round 0
    # has failed; on a healthy cube that never happens, so no such warning.
    assert not any("early-exit-intermediates: skipping" in w
                   for w in res.warnings)
    _mesh_ok(case / "constant" / "polyMesh")


def test_face_geometry_chunked_matches_unchunked():
    """Chunked _face_geometry (Lane A RAM fix) is bitwise-identical to the
    single-block path on a ragged non-planar face set, including faces that
    straddle chunk boundaries."""
    from polyfoammesh.core.bl_poly import _face_geometry, _face_geometry_block

    rng = np.random.default_rng(7)
    pts = rng.random((60, 3))
    faces = []
    i = 0
    for size in (3, 4, 5, 6, 7, 4, 3, 5):
        faces.append([(i + k) % 60 for k in range(size)])
        i += size - 1
    faces = faces * 5  # 40 faces, mixed sizes
    sf_ref, cf_ref = _face_geometry_block(pts, faces)
    for chunk in (1, 7, 13, 10**9):
        sf, cf = _face_geometry(pts, faces, _chunk=chunk)
        np.testing.assert_array_equal(sf, sf_ref)
        np.testing.assert_array_equal(cf, cf_ref)


def _reference_guided_filter(fn, fc, fa, fe, neighbours,
                             sigma_r=0.35, sigma_s_scale=1.5):
    """Deliberately naive, independently-written joint-bilateral reference
    for `_guided_filter_wall_normals` (direct double loop from the docstring
    formula, no shared code path)."""
    n = len(fn)
    out = np.zeros_like(fn)
    for bi in range(n):
        ring = [bi] + [j for j in neighbours[bi] if j != bi]
        if len(ring) == 1:
            out[bi] = fn[bi]
            continue
        dots = [float(fn[bi] @ fn[j]) for j in ring]
        order = sorted(range(len(ring)), key=lambda k: -dots[k])
        best = (fn[bi], 3.0)
        cands = [[bi, ring[order[1]]], ring]
        for cand in cands:
            m = np.mean([fn[j] for j in cand], axis=0)
            nm = float(np.linalg.norm(m))
            if nm <= 1e-300:
                continue
            m = m / nm
            spread = 1.0 - min(float(fn[j] @ m) for j in cand)
            if spread < best[1]:
                best = (m, spread)
        g_self = best[0]
        acc = np.zeros(3)
        for j in ring:
            gj = g_self if j == bi else fn[j]
            d2 = float(np.sum((fc[j] - fc[bi]) ** 2))
            sig_s = max(sigma_s_scale * fe[bi], 1e-300)
            gd = float(np.sum((gj - g_self) ** 2))
            w = (fa[j] * np.exp(-d2 / (2.0 * sig_s * sig_s))
                 * np.exp(-gd / (2.0 * sigma_r * sigma_r)))
            acc = acc + w * fn[j]
        nrm = float(np.linalg.norm(acc))
        out[bi] = acc / nrm if nrm > 1e-300 else fn[bi]
    return out


def test_guided_filter_matches_reference_and_preserves_ridge():
    """Lane C2: the guided filter matches an independent brute-force
    implementation, preserves a 90-degree ridge (faces on opposite sides
    keep ~90°-apart normals — the property Laplacian smoothing destroys)
    and leaves flat regions near-untouched."""
    from polyfoammesh.core.bl_poly import _guided_filter_wall_normals

    # two flat quads (z=0 plane) + two quads folded up 90° (y=0 plane),
    # sharing the x-axis ridge edge through vertices 2,3.
    pts = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0],
        [0.0, 1.0, 0.0], [1.0, 1.0, 0.0], [2.0, 1.0, 0.0],
        [0.0, 0.0, 1.0], [1.0, 0.0, 1.0], [2.0, 0.0, 1.0],
    ])
    flat = [[0, 1, 4, 3], [1, 2, 5, 4]]
    wall = [[0, 6, 7, 1], [1, 7, 8, 2]]
    faces = flat + wall
    fn, fc, fa, fe = [], [], [], []
    for f in faces:
        p = pts[np.asarray(f)]
        e1 = p[1] - p[0]
        e2 = p[2] - p[1]
        nv = np.cross(e1, e2)
        nm = float(np.linalg.norm(nv))
        fn.append(nv / nm)
        fc.append(p.mean(axis=0))
        fa.append(0.5 * nm)
        fe.append(float(np.mean([np.linalg.norm(p[(k + 1) % 4] - p[k])
                                 for k in range(4)])))
    fn, fc, fa, fe = (np.array(fn), np.array(fc), np.array(fa),
                      np.array(fe))
    neighbours = [[1, 2], [0, 3], [0, 3], [1, 2]]
    got = _guided_filter_wall_normals(fn, fc, fa, fe, neighbours)
    ref = _reference_guided_filter(fn, fc, fa, fe, neighbours)
    np.testing.assert_allclose(got, ref, rtol=1e-9, atol=1e-12)
    # ridge preserved: flat face 0 vs folded face 2 stay ~90° apart
    ang = float(np.degrees(np.arccos(
        np.clip(got[0] @ got[2], -1.0, 1.0))))
    assert 80.0 < ang < 100.0, ang
    # flat region untouched: face 0 ~ face 1 still parallel
    assert float(got[0] @ got[1]) > 0.999
    # unit length everywhere
    np.testing.assert_allclose(np.linalg.norm(got, axis=1),
                               np.ones(4), atol=1e-12)


def test_bl_normal_method_guided_succeeds_on_healthy_cube():
    """'guided' must build a valid BL mesh on the healthy cube with the
    same topology (prism count) as area_weighted."""
    case_aw = _build_dual_case(WORK_ROOT / "cube_nm_gd_aw")
    res_aw = PolyBoundaryLayerEngine(case_aw, log=lambda m: None).run(
        n_layers=2, first_height=0.01, growth_rate=1.2, apply_to_all=True,
        normal_method="area_weighted",
    )
    case_gd = _build_dual_case(WORK_ROOT / "cube_nm_gd")
    res_gd = PolyBoundaryLayerEngine(case_gd, log=lambda m: None).run(
        n_layers=2, first_height=0.01, growth_rate=1.2, apply_to_all=True,
        normal_method="guided",
    )
    assert res_aw.success and res_gd.success, (res_aw.errors, res_gd.errors)
    assert res_aw.n_prism_cells == res_gd.n_prism_cells
    assert res_gd.stats.get("min_cell_volume", 0.0) > 0.0
    _mesh_ok(case_gd / "constant" / "polyMesh")
