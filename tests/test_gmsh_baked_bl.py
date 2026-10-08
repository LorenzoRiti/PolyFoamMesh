"""GMSH-baked boundary layer: field population, .msh probing, mixed dual.

Covers the four contracts added for the GMSH-baked BL work:

1. the ``BoundaryLayer`` field curve selection (wall-boundary curves,
   deduplicated) — ``_curves_of_surfaces`` / ``_select_bl_curves``;
2. the ``.msh`` element-type probe (type 6 = prism) —
   ``count_msh_element_types``;
3. mixed tet+prism dual handling: prisms survive verbatim, the tet core is
   dualized, the interface is stitched, and the result is closed / positive /
   conforming — exact cell and face counts on synthetic meshes;
4. the worker's skip logic when the input already carries a GMSH-baked BL.

The synthetic meshes are pure OpenFOAM polyMesh built in memory (no GMSH, no
WSL), so the fast suite never depends on the mesher.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

import polyfoammesh.core.foam_mesh_io as fio  # noqa: E402
from polyfoammesh.core.gmsh_wrapper import (  # noqa: E402
    _curves_of_surfaces,
    _select_bl_curves,
    count_msh_element_types,
    gmsh_baked_bl_enabled,
)
from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402


# ---------------------------------------------------------------------------
# 1. BoundaryLayer field curve selection
# ---------------------------------------------------------------------------

class _FakeModel:
    def __init__(self, surfaces, boundary):
        self._surfaces = surfaces          # [(2, tag), ...]
        self._boundary = boundary          # surface tag -> [(dim, tag), ...]

    def getEntities(self, dim):
        return [(d, t) for d, t in self._surfaces if d == dim]

    def getBoundary(self, dim_tags, **kwargs):
        out = []
        for _d, t in dim_tags:
            out.extend(self._boundary.get(int(t), []))
        return out

    def getBoundingBox(self, *args):
        raise RuntimeError("no B-Rep bbox in this stub")


class _FakeGmsh:
    def __init__(self, model):
        self.model = model


def test_curves_of_surfaces_dedupes_and_orders():
    model = _FakeModel(
        surfaces=[(2, 1), (2, 2)],
        boundary={1: [(1, 10), (1, 11)], 2: [(1, 11), (1, 12)]},
    )
    assert _curves_of_surfaces(_FakeGmsh(model), [1, 2]) == [10, 11, 12]


def test_curves_of_surfaces_empty_without_surfaces():
    model = _FakeModel(surfaces=[], boundary={})
    assert _curves_of_surfaces(_FakeGmsh(model), []) == []


def test_select_bl_curves_uses_wall_surfaces():
    # getBoundingBox raises -> _infer_wall_surface_tags falls back to all
    # surfaces, so every bounding curve is selected (the "at minimum all
    # model curves of dimension 1 that bound surfaces" contract).
    model = _FakeModel(
        surfaces=[(2, 1), (2, 2)],
        boundary={1: [(1, 10), (1, 11)], 2: [(1, 11), (1, 12)]},
    )
    assert _select_bl_curves(_FakeGmsh(model)) == [10, 11, 12]


# ---------------------------------------------------------------------------
# 2. .msh element-type counting
# ---------------------------------------------------------------------------

def _write_msh22(path: Path) -> None:
    path.write_text(
        "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n"
        "$Nodes\n1\n1 0 0 0\n$EndNodes\n"
        "$Elements\n6\n"
        "1 2 2 0 1 1 2 3\n"
        "2 2 2 0 1 2 3 4\n"
        "3 6 2 0 1 1 2 3 4 5 6\n"
        "4 6 2 0 1 1 2 3 4 5 6\n"
        "5 4 2 0 1 1 2 3 4\n"
        "6 4 2 0 1 1 2 3 4\n"
        "$EndElements\n",
        encoding="ascii",
    )


def test_count_msh_element_types(tmp_path):
    msh = tmp_path / "mesh.msh"
    _write_msh22(msh)
    assert count_msh_element_types(msh) == {2: 2, 6: 2, 4: 2}


def test_count_msh_element_types_missing_file(tmp_path):
    assert count_msh_element_types(tmp_path / "nope.msh") == {}


def test_gmsh_baked_bl_env_gate(monkeypatch):
    monkeypatch.delenv("POLYFOAMMESH_GMSH_BL", raising=False)
    assert gmsh_baked_bl_enabled() is False
    for val in ("1", "true", "YES", "on"):
        monkeypatch.setenv("POLYFOAMMESH_GMSH_BL", val)
        assert gmsh_baked_bl_enabled() is True
    monkeypatch.setenv("POLYFOAMMESH_GMSH_BL", "0")
    assert gmsh_baked_bl_enabled() is False


# ---------------------------------------------------------------------------
# 3. mixed tet + prism dual handling
# ---------------------------------------------------------------------------

def _newell(points, verts):
    p = points[list(verts)]
    n = np.zeros(3)
    for i in range(len(p)):
        j = (i + 1) % len(p)
        n[0] += (p[i, 1] - p[j, 1]) * (p[i, 2] + p[j, 2])
        n[1] += (p[i, 2] - p[j, 2]) * (p[i, 0] + p[j, 0])
        n[2] += (p[i, 0] - p[j, 0]) * (p[i, 1] + p[j, 1])
    return 0.5 * n


def build_mixed_case(case_dir: Path, points: np.ndarray, cells) -> Path:
    """Write a mixed tet+prism OpenFOAM polyMesh from per-cell face lists.

    Each cell is a list of faces (each face a list of vertex ids, any
    winding); faces are oriented outward from their cell and internal faces
    owner -> neighbour, exactly like the pure-tet test helper.
    """
    cents = [
        points[np.unique([v for f in faces for v in f])].mean(axis=0)
        for faces in cells
    ]
    face_to_cells: dict[frozenset, list[int]] = {}
    face_verts: dict[frozenset, list[int]] = {}
    for c, faces in enumerate(cells):
        for f in faces:
            key = frozenset(f)
            face_to_cells.setdefault(key, []).append(c)
            v = list(f)
            if float(_newell(points, v) @ (points[v].mean(axis=0) - cents[c])) < 0:
                v.reverse()
            face_verts[key] = v

    internal: list[tuple[list[int], int, int]] = []
    boundary: list[tuple[list[int], int]] = []
    for key, cs in face_to_cells.items():
        if len(cs) == 1:
            boundary.append((face_verts[key], cs[0]))
        else:
            o, nb = sorted(cs)
            v = face_verts[key]
            if float(_newell(points, v) @ (cents[nb] - cents[o])) < 0:
                v.reverse()
            internal.append((v, o, nb))
    internal.sort(key=lambda e: (e[1], e[2]))
    faces = [e[0] for e in internal]
    owner = [e[1] for e in internal]
    neigh = [e[2] for e in internal]
    n_int = len(faces)
    for v, o in boundary:
        faces.append(v)
        owner.append(o)
    patches = [{
        "name": "wall", "type": "wall",
        "nFaces": len(boundary), "startFace": n_int,
    }]
    poly = case_dir / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    fio.write_polymesh(
        poly, points.astype(np.float64), faces,
        np.array(owner, dtype=np.int32),
        np.array(neigh, dtype=np.int32), patches,
    )
    return case_dir


def _cell_face_counts(faces, owner, neigh, n_cells):
    nf = np.zeros(n_cells, dtype=np.int64)
    np.add.at(nf, owner, 1)
    np.add.at(nf, neigh, 1)
    return nf


def _assert_conforming(case_dir: Path, n_cells: int) -> None:
    pts, faces, owner, neigh, patches = fio.read_polymesh(
        case_dir / "constant" / "polyMesh"
    )
    n_int = len(neigh)
    assert np.all(owner[:n_int] < neigh[:n_int])
    # every cell is closed (area-vector sum ~ 0) and has a positive volume
    sf = np.zeros((len(faces), 3))
    for i, f in enumerate(faces):
        sf[i] = _newell(pts, f)
    closure = np.zeros((n_cells, 3))
    np.add.at(closure, owner, sf)
    np.add.at(closure, neigh, -sf[:n_int])
    scale = np.zeros(n_cells)
    mag = np.linalg.norm(sf, axis=1)
    np.add.at(scale, owner, mag)
    np.add.at(scale, neigh, mag[:n_int])
    rel = np.linalg.norm(closure, axis=1) / np.maximum(scale, 1e-300)
    assert np.all(rel < 1e-8)
    assert np.all(_cell_face_counts(faces, owner, neigh, n_cells) >= 5)
    assert sum(p["nFaces"] for p in patches) == len(faces) - n_int


def test_mixed_single_prism_and_tet(tmp_path):
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [0, 1, 1],
        [0, 0, 2],
    ], dtype=float)
    A, B, C, Ap, Bp, Cp, D = range(7)
    prism = [[A, B, C], [Ap, Bp, Cp],
             [A, B, Bp, Ap], [B, C, Cp, Bp], [C, A, Ap, Cp]]
    tet = [[Ap, Bp, Cp], [Ap, Bp, D], [Bp, Cp, D], [Cp, Ap, D]]
    case = build_mixed_case(tmp_path, pts, [prism, tet])

    res = TetPolyDualConverter(case, log=lambda _m: None).run()
    assert res.success, res.errors
    assert res.n_tets_before == 1
    assert res.n_prisms_before == 1
    assert res.n_prism_cells_after == 1
    # one dual cell per tet-core vertex (4) + the verbatim prism
    assert res.n_cells_after == 5
    assert res.n_residual_tets == 0
    assert res.min_cell_volume > 0.0
    # tet (1/6) + prism (1/2) = 2/3
    assert abs(res.volume_before - 2.0 / 3.0) < 1e-12
    assert abs(res.volume_after - res.volume_before) < 1e-9

    _assert_conforming(case, res.n_cells_after)

    pts2, faces, owner, neigh, patches = fio.read_polymesh(
        case / "constant" / "polyMesh"
    )
    n_int = len(neigh)
    # the prism is the last cell (id 4); its top triangle became 3 internal
    # quads owned by the three dual cells of the interface triangle.
    prism_id = 4
    assert int(np.count_nonzero(neigh == prism_id)) == 3
    assert np.all(owner[:n_int][neigh == prism_id] < prism_id)
    # bottom triangle + 3 side quads split into 3 triangles each + 3 top quads
    nf = _cell_face_counts(faces, owner, neigh, res.n_cells_after)
    assert nf[prism_id] == 13
    assert len(patches) == 1 and patches[0]["name"] == "wall"


def test_mixed_two_layer_prism_stack(tmp_path):
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [0, 1, 0],
        [0, 0, 0.5], [1, 0, 0.5], [0, 1, 0.5],
        [0, 0, 1], [1, 0, 1], [0, 1, 1],
        [0, 0, 2],
    ], dtype=float)
    A, B, C, A2, B2, C2, A3, B3, C3, D = range(10)
    p0 = [[A, B, C], [A2, B2, C2],
          [A, B, B2, A2], [B, C, C2, B2], [C, A, A2, C2]]
    p1 = [[A2, B2, C2], [A3, B3, C3],
          [A2, B2, B3, A3], [B2, C2, C3, B3], [C2, A2, A3, C3]]
    tet = [[A3, B3, C3], [A3, B3, D], [B3, C3, D], [C3, A3, D]]
    case = build_mixed_case(tmp_path, pts, [p0, p1, tet])

    res = TetPolyDualConverter(case, log=lambda _m: None).run()
    assert res.success, res.errors
    assert res.n_tets_before == 1
    assert res.n_prisms_before == 2
    assert res.n_prism_cells_after == 2
    assert res.n_cells_after == 6  # 4 dual + 2 prisms
    assert res.min_cell_volume > 0.0
    assert abs(res.volume_after - res.volume_before) < 1e-9
    _assert_conforming(case, res.n_cells_after)

    _pts2, _faces, _owner, neigh, _patches = fio.read_polymesh(
        case / "constant" / "polyMesh"
    )
    # the two prisms are ids 4 and 5 and share exactly one internal face
    shared = ((neigh == 5) & (_owner[: len(neigh)] == 4)).sum()
    assert int(shared) == 1


def test_mixed_multitet_core_interface_owner(tmp_path):
    """A core with more than one tet: the interface triangle belongs to a
    specific core tet, and the core polyMesh owner written for it must be
    that tet's local id (not a placeholder) or the nested converter's
    per-cell face count breaks."""
    # prism bottom at z=-1, top triangle ABC at z=0
    pts = np.array([
        [0, 0, -1], [1, 0, -1], [0, 1, -1],
        [0, 0, 0], [1, 0, 0], [0, 1, 0],
        [0, 0, 1], [0.25, -0.25, 0.25],
    ], dtype=float)
    A0, B0, C0, A, B, C, D, E = range(8)
    prism = [[A0, B0, C0], [A, B, C],
             [A0, B0, B, A], [B0, C0, C, B], [C0, A0, A, C]]
    tet1 = [[A, B, C], [A, B, D], [B, C, D], [C, A, D]]
    tet2 = [[A, B, D], [A, B, E], [B, D, E], [A, D, E]]
    # cell order puts the interface tet (tet1) at core local id 1
    case = build_mixed_case(tmp_path, pts, [tet2, tet1, prism])

    res = TetPolyDualConverter(case, log=lambda _m: None).run()
    assert res.success, res.errors
    assert res.n_tets_before == 2
    assert res.n_prisms_before == 1
    assert res.n_prism_cells_after == 1
    # 5 tet-core vertices + 1 prism = 6 cells
    assert res.n_cells_after == 6
    assert res.min_cell_volume > 0.0
    assert abs(res.volume_after - res.volume_before) < 1e-9
    _assert_conforming(case, res.n_cells_after)


def test_mixed_rejects_unknown_cell_shape(tmp_path):
    # one cell with a single quad face and four triangles: neither a tet nor
    # a prism -> clear refusal, original mesh untouched.
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0.5, 0.5, 1],
    ], dtype=float)
    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    fio.write_polymesh(
        poly, pts,
        [[0, 1, 2, 3], [0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]],
        np.array([0, 0, 0, 0, 0], dtype=np.int32),
        np.array([-1, -1, -1, -1, -1], dtype=np.int32),
        [{"name": "wall", "type": "patch", "nFaces": 5, "startFace": 0}],
    )
    res = TetPolyDualConverter(tmp_path, log=lambda _m: None).run()
    assert not res.success
    assert "tetrahedral" in " ".join(res.errors).lower()


# ---------------------------------------------------------------------------
# 4. worker skip logic (GMSH-baked BL already present)
# ---------------------------------------------------------------------------

def test_count_polymesh_prism_cells(tmp_path):
    pytest.importorskip("numpy")
    from polyfoammesh.core.openfoam_runner import count_polymesh_prism_cells

    # pure tet mesh -> 0 prisms
    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    poly = tmp_path / "tetcase" / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    fio.write_polymesh(
        poly, pts,
        [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]],
        np.array([0, 0, 0, 0], dtype=np.int32),
        np.array([-1, -1, -1, -1], dtype=np.int32),
        [{"name": "wall", "type": "wall", "nFaces": 4, "startFace": 0}],
    )
    assert count_polymesh_prism_cells(tmp_path / "tetcase") == 0

    # mixed prism+tet -> 1 prism
    pts2 = np.array([
        [0, 0, 0], [1, 0, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [0, 1, 1], [0, 0, 2],
    ], dtype=float)
    A, B, C, Ap, Bp, Cp, D = range(7)
    prism = [[A, B, C], [Ap, Bp, Cp],
             [A, B, Bp, Ap], [B, C, Cp, Bp], [C, A, Ap, Cp]]
    tet = [[Ap, Bp, Cp], [Ap, Bp, D], [Bp, Cp, D], [Cp, Ap, D]]
    mixed = build_mixed_case(tmp_path / "mixedcase", pts2, [prism, tet])
    assert count_polymesh_prism_cells(mixed) == 1


def _run_worker_with_bl(tmp_path, monkeypatch, *, result_prisms, detect_prisms):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from unittest import mock

    from polyfoammesh.core.openfoam_runner import DualPolyWorker

    case = tmp_path / "case"
    poly = case / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "points").write_text("dummy", encoding="utf-8")

    class _Result:
        success = True
        errors: list = []
        n_tets_before = 10
        n_prisms_before = result_prisms
        n_cells_after = 8
        n_prism_cells_after = result_prisms
        n_residual_tets = 0
        stage_times = {"total": 0.1}

    class _RecordingConverter:
        def __init__(self, *a, **k):
            pass

        def run(self):
            return _Result()

    class _ExplodingBLEngine:
        def __init__(self, *a, **k):
            raise AssertionError("in-house BL must be skipped")

    logs: list[str] = []
    worker = DualPolyWorker(
        case,
        bl_params={"nLayers": 5, "firstLayerThickness": 0.001,
                   "thicknessRatio": 1.2},
    )
    worker.log_line.connect(logs.append)

    import polyfoammesh.core.openfoam_runner as _ofr

    monkeypatch.setattr(_ofr, "count_polymesh_prism_cells", lambda _c: detect_prisms)
    with mock.patch(
        "polyfoammesh.core.tet_poly_dual.TetPolyDualConverter",
        _RecordingConverter,
    ), mock.patch(
        "polyfoammesh.core.bl_poly.PolyBoundaryLayerEngine",
        _ExplodingBLEngine,
    ):
        worker.run()

    assert any("GMSH-baked BL detected" in m for m in logs), logs


def test_worker_skips_inhouse_bl_from_result_prism_count(tmp_path, monkeypatch):
    # the converter reports it kept prisms (the stitched case, whose split
    # side quads defeat the shape detector)
    _run_worker_with_bl(tmp_path, monkeypatch, result_prisms=4, detect_prisms=0)


def test_worker_skips_inhouse_bl_from_polymesh_detection(tmp_path, monkeypatch):
    # a prism-bearing polyMesh that reached the worker without the mixed path
    _run_worker_with_bl(tmp_path, monkeypatch, result_prisms=0, detect_prisms=4)

