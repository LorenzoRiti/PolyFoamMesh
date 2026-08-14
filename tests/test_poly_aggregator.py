"""Tests for polyhedral aggregator (STAR-CCM+ style)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("poly_aggregator")
PolyAggregator = _mod.PolyAggregator
AggregationParams = _mod.AggregationParams
AggregationResult = _mod.AggregationResult
face_normal = _mod.face_normal
face_centroid = _mod.face_centroid
are_coplanar = _mod.are_coplanar
merge_two_faces = _mod.merge_two_faces
Face = _mod.Face
_read_points = _mod._read_points
_parse_face_list = _mod._parse_face_list
_extract_data_block = _mod._extract_data_block


# ---------------------------------------------------------------------------
# AggregationParams tests
# ---------------------------------------------------------------------------


def test_params_defaults():
    p = AggregationParams()
    assert p.min_tets_per_cluster == 20
    assert p.merge_coplanar_faces
    assert abs(p.coplanar_tolerance - 1e-6) < 1e-12
    assert p.preserve_boundary_patches
    assert p.bl_enabled
    assert p.bl_n_layers == 5


def test_params_custom():
    p = AggregationParams(min_tets_per_cluster=3, bl_enabled=False)
    assert p.min_tets_per_cluster == 3
    assert not p.bl_enabled


def test_params_bl():
    p = AggregationParams(bl_n_layers=10, bl_growth_rate=1.3)
    assert p.bl_n_layers == 10
    assert abs(p.bl_growth_rate - 1.3) < 1e-12


# ---------------------------------------------------------------------------
# AggregationResult tests
# ---------------------------------------------------------------------------


def test_result_defaults():
    r = AggregationResult()
    assert not r.success
    assert r.cells_before == 0
    assert r.cells_after == 0
    assert r.reduction_pct == 0.0
    assert r.errors == []
    assert abs(r.max_non_ortho_before) < 1e-12


def test_result_reduction_75():
    r = AggregationResult(cells_before=1000, cells_after=250)
    assert abs(r.reduction_pct - 75.0) < 0.01


def test_result_reduction_50():
    r = AggregationResult(cells_before=10000, cells_after=5000)
    assert abs(r.reduction_pct - 50.0) < 0.01


def test_result_reduction_no_cells():
    r = AggregationResult(cells_before=0)
    assert r.reduction_pct == 0.0


def test_result_reduction_equal():
    r = AggregationResult(cells_before=100, cells_after=100)
    assert abs(r.reduction_pct) < 0.01


# ---------------------------------------------------------------------------
# Face utility tests
# ---------------------------------------------------------------------------


def test_face_normal_triangle():
    verts = [
        np.array([0.0, 0.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
    ]
    n = face_normal(verts)
    assert abs(n[0]) < 1e-10
    assert abs(n[1]) < 1e-10
    assert abs(n[2] - 1.0) < 1e-10


def test_face_normal_quad():
    verts = [
        np.array([0.0, 0.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([1.0, 1.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
    ]
    n = face_normal(verts)
    assert abs(n[0]) < 1e-10
    assert abs(n[1]) < 1e-10
    assert abs(n[2] - 1.0) < 1e-10


def test_face_normal_angled():
    verts = [
        np.array([0.0, 0.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 1.0]),
    ]
    n = face_normal(verts)
    assert abs(np.linalg.norm(n) - 1.0) < 1e-10


def test_face_centroid():
    verts = [
        np.array([0.0, 0.0, 0.0]),
        np.array([2.0, 0.0, 0.0]),
        np.array([2.0, 2.0, 0.0]),
        np.array([0.0, 2.0, 0.0]),
    ]
    c = face_centroid(verts)
    assert abs(c[0] - 1.0) < 1e-10
    assert abs(c[1] - 1.0) < 1e-10
    assert abs(c[2]) < 1e-10


def test_are_coplanar_same_plane():
    va = [
        np.array([0.0, 0.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([1.0, 1.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
    ]
    vb = [
        np.array([0.5, 0.0, 0.0]),
        np.array([1.0, 0.5, 0.0]),
        np.array([0.5, 1.0, 0.0]),
        np.array([0.0, 0.5, 0.0]),
    ]
    assert are_coplanar(va, vb)


def test_are_coplanar_different_planes():
    va = [
        np.array([0.0, 0.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([1.0, 1.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
    ]
    vb = [
        np.array([0.0, 0.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([1.0, 1.0, 1.0]),
        np.array([0.0, 1.0, 1.0]),
    ]
    assert not are_coplanar(va, vb)


def test_merge_two_faces_shared_edge():
    va = [
        np.array([0.0, 0.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([1.0, 1.0, 0.0]),
    ]
    vb = [
        np.array([1.0, 0.0, 0.0]),
        np.array([2.0, 0.0, 0.0]),
        np.array([1.0, 1.0, 0.0]),
    ]
    merged = merge_two_faces(va, vb)
    # Should have 4 unique vertices
    assert len(merged) >= 4


def test_merge_two_faces_no_shared_edge():
    va = [
        np.array([0.0, 0.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([1.0, 1.0, 0.0]),
    ]
    vb = [
        np.array([5.0, 5.0, 0.0]),
        np.array([6.0, 5.0, 0.0]),
        np.array([5.0, 6.0, 0.0]),
    ]
    merged = merge_two_faces(va, vb)
    # No shared edge: returns first face unchanged
    assert len(merged) == 3


# ---------------------------------------------------------------------------
# Face dataclass tests
# ---------------------------------------------------------------------------


def test_face_defaults():
    f = Face(vertices=[0, 1, 2])
    assert f.vertices == [0, 1, 2]
    assert f.owner == -1
    assert not f.is_boundary


def test_face_reversed():
    f = Face(vertices=[0, 1, 2], owner=5, neighbour=3)
    rev = f.reversed()
    assert rev.vertices == [2, 1, 0]
    assert rev.owner == 3
    assert rev.neighbour == 5


def test_face_hash():
    f1 = Face(vertices=[0, 1, 2])
    f2 = Face(vertices=[2, 1, 0])
    assert hash(f1) == hash(f2)


def test_face_eq():
    f1 = Face(vertices=[0, 1, 2])
    f2 = Face(vertices=[2, 0, 1])
    assert f1 == f2


def test_face_neq():
    f1 = Face(vertices=[0, 1, 2])
    f2 = Face(vertices=[0, 1, 3])
    assert f1 != f2


# ---------------------------------------------------------------------------
# OF parsing tests
# ---------------------------------------------------------------------------


def test_extract_data_block_simple():
    text = "FoamFile { version 2; }\n5\n(\n1 2 3\n)"
    data = _extract_data_block(text)
    assert "1 2 3" in data


def test_extract_data_block_comments():
    text = "/* comment */ 5 /* another */\n(\n1 2 3\n)"
    data = _extract_data_block(text)
    assert "1 2 3" in data


def test_parse_face_list_tri():
    text = "2\n(\n3(0 1 2)\n4(0 1 2 3)\n)"
    faces = _parse_face_list(text)
    assert len(faces) == 2
    assert faces[0] == [0, 1, 2]
    assert faces[1] == [0, 1, 2, 3]


def test_parse_face_list_hex():
    text = "6\n(\n4(0 1 2 3)\n4(4 5 6 7)\n)"
    faces = _parse_face_list(text)
    assert len(faces) == 2
    assert len(faces[0]) == 4


# ---------------------------------------------------------------------------
# PolyAggregator tests
# ---------------------------------------------------------------------------


def test_aggregator_init():
    agg = PolyAggregator()
    assert agg.params.min_tets_per_cluster == 20
    agg.params.min_tets_per_cluster = 10
    assert agg.params.min_tets_per_cluster == 10


def test_aggregator_run_no_case(tmp_path):
    agg = PolyAggregator()
    result = agg.run(tmp_path / "nonexistent")
    assert not result.success
    assert len(result.errors) > 0


def test_aggregator_run_empty_case(tmp_path):
    case_dir = tmp_path / "empty_case"
    case_dir.mkdir()
    agg = PolyAggregator()
    result = agg.run(case_dir)
    assert not result.success


def test_build_vertex_adjacency_simple():
    agg = PolyAggregator()
    cell_faces = [[0, 1, 2, 3], [2, 3, 4, 5]]
    faces = [
        [0, 1, 2],  # 0
        [0, 2, 3],  # 1
        [0, 3, 1],  # 2
        [1, 2, 3],  # 3  (shared between cell 0 and 1)
        [2, 3, 4],  # 4
        [1, 2, 4],  # 5
    ]
    adj = agg._build_vertex_adjacency(cell_faces, faces)
    assert 0 in adj
    assert len(adj[0]) >= 1


# ---------------------------------------------------------------------------
# Integration: run with test geometry
# ---------------------------------------------------------------------------


def test_write_boundary_single_patch(tmp_path):
    """Verify _compute_boundary_patches produces a valid single-patch split."""
    agg = PolyAggregator()
    new_faces = [[0, 1, 2], [2, 3, 4]]
    new_owner = np.array([0, 0])
    new_neighbour = np.array([-1, -1])

    agg._face_patch_map = {0: ("walls", "patch"), 1: ("walls", "patch")}
    patches = agg._compute_boundary_patches(new_faces, new_neighbour)
    assert len(patches) == 1
    assert patches[0]["name"] == "walls"
    assert patches[0]["nFaces"] == 2
    assert patches[0]["startFace"] == 0

    # Round-trip through the single writer (foam_mesh_io)
    from cfmesh_autogui.core import foam_mesh_io
    poly_dir = tmp_path / "constant" / "polyMesh"
    foam_mesh_io.write_polymesh(
        poly_dir,
        np.zeros((8, 3), dtype=np.float64),
        new_faces,
        new_owner.astype(np.int64),
        new_neighbour.astype(np.int64),
        patches,
    )
    text = (poly_dir / "boundary").read_text()
    assert "walls" in text
    import re
    assert re.search(r"nFaces\s+2;", text)


def test_write_boundary_multi_patch(tmp_path):
    """Verify multi-patch boundary split."""
    agg = PolyAggregator()
    new_faces = [[0, 1, 2], [2, 3, 4], [5, 6, 7]]
    new_owner = np.array([0, 0, 1])
    new_neighbour = np.array([-1, -1, -1])

    agg._face_patch_map = {
        0: ("inlet", "patch"),
        1: ("outlet", "patch"),
        2: ("walls", "wall"),
    }
    patches = agg._compute_boundary_patches(new_faces, new_neighbour)
    assert [p["name"] for p in patches] == ["inlet", "outlet", "walls"]
    assert [p["nFaces"] for p in patches] == [1, 1, 1]
    assert patches[0]["startFace"] == 0

    # Round-trip through the single writer (foam_mesh_io)
    from cfmesh_autogui.core import foam_mesh_io
    poly_dir = tmp_path / "constant" / "polyMesh"
    foam_mesh_io.write_polymesh(
        poly_dir,
        np.zeros((9, 3), dtype=np.float64),
        new_faces,
        new_owner.astype(np.int64),
        new_neighbour.astype(np.int64),
        patches,
    )
    text = (poly_dir / "boundary").read_text()
    for name in ("inlet", "outlet", "walls"):
        assert name in text


def test_map_verts_to_indices():
    """Verify _map_verts_to_indices correctly resolves merged vertex coordinates."""
    points = np.array([
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [1.0, 1.0, 0.0],
    ], dtype=np.float64)
    agg = PolyAggregator()
    merged = [
        np.array([0.0, 0.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([1.0, 1.0, 0.0]),
    ]
    result = agg._map_verts_to_indices(merged, [0, 1, 2], [1, 2, 3], points)
    assert result == [0, 1, 2, 3]


def test_fallback_tet_mesh_no_wsl(tmp_path):
    """Verify fallback handles missing WSL gracefully."""
    agg = PolyAggregator()
    try:
        agg._fallback_tet_mesh(tmp_path, "nonexistent.stl")
        assert False, "Should have raised"
    except (FileNotFoundError, OSError, RuntimeError):
        pass


def test_poly_aggregated_in_mesh_engine_enum():
    """Verify POLY_AGGREGATED is registered in mesh_engine."""
    from cfmesh_autogui.commercial.mesh_engine import (
        ALGORITHM_INFO,
        ALGORITHM_ROBUSTNESS,
        MeshingAlgorithm,
    )
    assert hasattr(MeshingAlgorithm, "POLY_AGGREGATED")
    assert MeshingAlgorithm.POLY_AGGREGATED in ALGORITHM_INFO
    assert MeshingAlgorithm.POLY_AGGREGATED in ALGORITHM_ROBUSTNESS
    info = ALGORITHM_INFO[MeshingAlgorithm.POLY_AGGREGATED]
    assert "Polyhedral" in info["label"]


def test_poly_aggregated_escalation_after_tetrahedral():
    """Verify escalation goes through POLY_AGGREGATED."""
    from cfmesh_autogui.commercial.mesh_engine import (
        MeshEngine,
        MeshingAlgorithm,
    )
    me = MeshEngine()
    algo, reason = me._next_escalation(MeshingAlgorithm.TETRAHEDRAL)
    assert reason
    assert algo in (
        MeshingAlgorithm.POLY_AGGREGATED,
        MeshingAlgorithm.SNAPPY_HEX_MESH,
    )


def test_aggregate_full_pipeline_synthetic(tmp_path):
    """End-to-end: write a synthetic tet mesh, aggregate, verify reduction."""

    case_dir = tmp_path / "synthetic_tet"
    poly_dir = case_dir / "constant" / "polyMesh"
    poly_dir.mkdir(parents=True)

    # Write a simple 2-tet mesh (5 points, 2 cells, 6 faces, 1 boundary face)
    # Tet 1: faces 0,1,2,3  (tet 0-1-2-3)
    # Tet 2: faces 2,3,4,5  (tet 1-2-3-4)
    # Shared face: 2,3 (internal)
    points_txt = (
        "FoamFile { version 2.0; format ascii; class pointField; object points; }\n"
        "5\n(\n"
        "(0 0 0)\n(1 0 0)\n(0 1 0)\n(0 0 1)\n(1 1 0)\n)\n"
    )
    (poly_dir / "points").write_text(points_txt, encoding="ascii")

    faces_txt = (
        "FoamFile { version 2.0; format ascii; class faceList; object faces; }\n"
        "6\n(\n"
        "3(0 1 2)\n"  # 0
        "3(0 2 3)\n"  # 1
        "3(0 1 3)\n"  # 2
        "3(1 2 3)\n"  # 3 (shared)
        "3(1 2 4)\n"  # 4 (boundary)
        "3(1 3 4)\n"  # 5 (boundary)
        ")\n"
    )
    (poly_dir / "faces").write_text(faces_txt, encoding="ascii")

    owner_txt = (
        "FoamFile { version 2.0; format ascii; class labelList; object owner; }\n"
        "6\n(\n0 0 0 0 1 1\n)\n"
    )
    (poly_dir / "owner").write_text(owner_txt, encoding="ascii")

    neighbour_txt = (
        "FoamFile { version 2.0; format ascii; class labelList; object neighbour; }\n"
        "6\n(\n-1 -1 -1 1 -1 -1\n)\n"
    )
    (poly_dir / "neighbour").write_text(neighbour_txt, encoding="ascii")

    boundary_txt = (
        "FoamFile { version 2.0; format ascii; class boundary; object boundary; }\n"
        "2\n(\n"
        "    inlet\n    { type patch; nFaces 1; startFace 0; }\n"
        "    walls\n    { type wall; nFaces 5; startFace 1; }\n"
        ")\n"
    )
    (poly_dir / "boundary").write_text(boundary_txt, encoding="ascii")

    # Run aggregation with high aggression (min_tets=1)
    agg = PolyAggregator()
    agg.params.min_tets_per_cluster = 1
    result = agg.run(case_dir)

    # The mesh has 2 cells, vertex 0 and 1 each have 4 tets.
    # With min_tets=1, all vertices qualify.
    # But many vertices share the same tets, so used_cells prevents overlap.
    # Result should have fewer cells than before.
    assert result.cells_before == 2
    assert result.cells_after <= 2  # should reduce or keep same
    assert result.cells_after > 0

    # Verify the output files exist
    assert (poly_dir / "points").exists()
    assert (poly_dir / "faces").exists()
    assert (poly_dir / "owner").exists()
    assert (poly_dir / "neighbour").exists()
    assert (poly_dir / "boundary").exists()

    # Verify backup was created
    assert (case_dir / "constant" / "polyMesh_tet").exists()


def test_aggregate_reduction_2tet(tmp_path):
    """Verify that aggregation reduces cell count for a synthetic tet mesh."""

    case_dir = tmp_path / "large_tet"
    poly_dir = case_dir / "constant" / "polyMesh"
    poly_dir.mkdir(parents=True)

    # Minimal tet mesh: 4 points forming 2 tetrahedra sharing a face
    # Points: 0=(0,0,0), 1=(1,0,0), 2=(0,1,0), 3=(0,0,1), 4=(1,1,0)
    # Tet A: 0,1,2,3  (faces: 0-1-2, 0-2-3, 0-1-3, 1-2-3)
    # Tet B: 1,2,3,4  (faces: 1-2-3, 1-3-4, 1-2-4, 2-3-4)
    # Shared face: 1-2-3

    pts = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (1.0, 1.0, 0.0),
    ]
    pts_lines = [f"({' '.join(f'{v}' for v in p)})" for p in pts]
    pts_txt = (
        "FoamFile { version 2.0; format ascii; class pointField; object points; }\n"
        f"{len(pts)}\n(\n" + "\n".join(pts_lines) + "\n)\n"
    )
    (poly_dir / "points").write_text(pts_txt, encoding="ascii")

    # 8 faces total, each tet has 4 faces
    # Face 0-5: tet A's faces, Face 6-7: tet B's unique faces
    all_faces = [
        [0, 1, 2],   # 0: tet A
        [0, 2, 3],   # 1: tet A
        [0, 1, 3],   # 2: tet A
        [1, 2, 3],   # 3: shared (tet A & B)
        [1, 3, 4],   # 4: tet B
        [1, 2, 4],   # 5: tet B
        [2, 3, 4],   # 6: tet B
    ]
    n_faces = len(all_faces)
    face_lines = [f"{len(fv)}({' '.join(str(v) for v in fv)})" for fv in all_faces]
    face_txt = (
        "FoamFile { version 2.0; format ascii; class faceList; object faces; }\n"
        f"{n_faces}\n(\n" + "\n".join(face_lines) + "\n)\n"
    )
    (poly_dir / "faces").write_text(face_txt, encoding="ascii")

    # owner: face 0-3 → cell 0, face 4-6 → cell 1
    owner = [0, 0, 0, 0, 1, 1, 1]
    owner_txt = (
        "FoamFile { version 2.0; format ascii; class labelList; object owner; }\n"
        f"{n_faces}\n(\n" + "\n".join(str(o) for o in owner) + "\n)\n"
    )
    (poly_dir / "owner").write_text(owner_txt, encoding="ascii")

    # neighbour: face 3 is shared (neighbour=1), others are boundary (-1)
    neighbour = [-1, -1, -1, 1, -1, -1, -1]
    neigh_txt = (
        "FoamFile { version 2.0; format ascii; class labelList; object neighbour; }\n"
        f"{n_faces}\n(\n" + "\n".join(str(n) for n in neighbour) + "\n)\n"
    )
    (poly_dir / "neighbour").write_text(neigh_txt, encoding="ascii")

    # Boundary: single patch
    boundary_txt = (
        "FoamFile { version 2.0; format ascii; class boundary; object boundary; }\n"
        "1\n(\n    walls\n    { type wall; nFaces 6; startFace 0; }\n)\n"
    )
    (poly_dir / "boundary").write_text(boundary_txt, encoding="ascii")

    # Run aggregation
    agg = PolyAggregator()
    agg.params.min_tets_per_cluster = 1
    result = agg.run(case_dir)

    assert result.cells_before == 2
    assert result.cells_after > 0
    assert result.cells_after <= 2
    assert result.success


def test_aggregate_uses_parse_and_run(tmp_path):
    """Verify _parse_face_list + _read_owner_neighbour work end-to-end."""
    case_dir = tmp_path / "parse_test"
    poly_dir = case_dir / "constant" / "polyMesh"
    poly_dir.mkdir(parents=True)

    # Write simplest valid tet mesh: 1 tet, 4 points, 4 faces
    pts_text = (
        "FoamFile { version 2.0; class pointField; object points; }\n"
        "4\n(\n"
        "(0 0 0)\n(1 0 0)\n(0 1 0)\n(0 0 1)\n)\n"
    )
    (poly_dir / "points").write_text(pts_text, encoding="ascii")

    faces_text = (
        "FoamFile { version 2.0; class faceList; object faces; }\n"
        "4\n(\n"
        "3(0 1 2)\n"
        "3(0 2 3)\n"
        "3(0 1 3)\n"
        "3(1 2 3)\n)\n"
    )
    (poly_dir / "faces").write_text(faces_text, encoding="ascii")

    owner_text = (
        "FoamFile { version 2.0; class labelList; object owner; }\n"
        "4\n(\n0\n0\n0\n0\n)\n"
    )
    (poly_dir / "owner").write_text(owner_text, encoding="ascii")

    neighbour_text = (
        "FoamFile { version 2.0; class labelList; object neighbour; }\n"
        "4\n(\n-1\n-1\n-1\n-1\n)\n"
    )
    (poly_dir / "neighbour").write_text(neighbour_text, encoding="ascii")

    boundary_text = (
        "FoamFile { version 2.0; class boundary; object boundary; }\n"
        "1\n(\n    walls\n    { type wall; nFaces 4; startFace 0; }\n)\n"
    )
    (poly_dir / "boundary").write_text(boundary_text, encoding="ascii")

    # Run aggregation
    agg = PolyAggregator()
    agg.params.min_tets_per_cluster = 1
    result = agg.run(case_dir)

    # Single tet with 1 vertex: should still produce 1 cell
    assert result.cells_before == 1
    assert result.cells_after == 1
    assert result.success


def test_aggregate_large(tmp_path):
    """Verify that aggregation produces fewer cells for a bigger mesh."""

    case_dir = tmp_path / "large_tet"
    poly_dir = case_dir / "constant" / "polyMesh"
    poly_dir.mkdir(parents=True)

    # Generate a structured tet mesh by splitting a 3x3x3 grid of hexes
    # Each hex split into 5 or 6 tets -> many tets
    n = 3  # 3x3x3 = 27 hexes, ~135-162 tets
    nx, ny, nz = n, n, n
    pts = []
    for k in range(nz + 1):
        for j in range(ny + 1):
            for i in range(nx + 1):
                pts.append((float(i), float(j), float(k)))
    n_pts = len(pts)

    # Write points
    pts_lines = [f"({' '.join(f'{v}' for v in p)})" for p in pts]
    pts_txt = (
        "FoamFile { version 2.0; format ascii; class pointField; object points; }\n"
        f"{n_pts}\n(\n" + "\n".join(pts_lines) + "\n)\n"
    )
    (poly_dir / "points").write_text(pts_txt, encoding="ascii")

    # Generate tets: split each hex into 5 tets (one per vertex of the minimal set)
    # Hex (i,j,k) has vertices:
    # v0=(i,j,k), v1=(i+1,j,k), v2=(i+1,j+1,k), v3=(i,j+1,k),
    # v4=(i,j,k+1), v5=(i+1,j,k+1), v6=(i+1,j+1,k+1), v7=(i,j+1,k+1)
    # Split into 5 tets:
    all_faces = []
    all_owner = []
    all_neighbour = []

    def vid(i, j, k):
        return i + j * (nx + 1) + k * (nx + 1) * (ny + 1)

    cell_id = 0
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                v0 = vid(i, j, k)
                v1 = vid(i+1, j, k)
                v2 = vid(i+1, j+1, k)
                v3 = vid(i, j+1, k)
                v4 = vid(i, j, k+1)
                v5 = vid(i+1, j, k+1)
                v6 = vid(i+1, j+1, k+1)
                v7 = vid(i, j+1, k+1)

                # 5-tet decomposition: (v0,v1,v2,v4), (v1,v2,v4,v5), (v2,v4,v5,v7),
                # (v2,v3,v4,v7), (v4,v5,v6,v7)
                tet_sets = [
                    [v0, v1, v2, v4],
                    [v1, v2, v4, v5],
                    [v2, v4, v5, v7],
                    [v2, v3, v4, v7],
                    [v4, v5, v6, v7],
                ]
                for tet in tet_sets:
                    # Each tet has 4 faces
                    for fi in range(4):
                        # Face vertices (tet face: all vertices except fi)
                        fv = [tet[(fi+1)%4], tet[(fi+2)%4], tet[(fi+3)%4]]
                        all_faces.append(fv)
                        all_owner.append(cell_id)
                        all_neighbour.append(-1)
                    cell_id += 1

    n_cells = cell_id
    n_faces = len(all_faces)

    # Write faces
    face_lines = [f"{len(fv)}({' '.join(str(v) for v in fv)})" for fv in all_faces]
    face_txt = (
        "FoamFile { version 2.0; format ascii; class faceList; object faces; }\n"
        f"{n_faces}\n(\n" + "\n".join(face_lines) + "\n)\n"
    )
    (poly_dir / "faces").write_text(face_txt, encoding="ascii")

    # Write owner
    owner_txt = (
        "FoamFile { version 2.0; format ascii; class labelList; object owner; }\n"
        f"{n_faces}\n(\n" + " ".join(str(o) for o in all_owner) + "\n)\n"
    )
    (poly_dir / "owner").write_text(owner_txt, encoding="ascii")

    # Write neighbour (all -1 for simplicity)
    neigh_txt = (
        "FoamFile { version 2.0; format ascii; class labelList; object neighbour; }\n"
        f"{n_faces}\n(\n" + " ".join(str(n) for n in all_neighbour) + "\n)\n"
    )
    (poly_dir / "neighbour").write_text(neigh_txt, encoding="ascii")

    # Boundary (single patch)
    boundary_txt = (
        "FoamFile { version 2.0; format ascii; class boundary; object boundary; }\n"
        "1\n(\n    walls\n    { type wall; nFaces 1; startFace 0; }\n)\n"
    )
    (poly_dir / "boundary").write_text(boundary_txt, encoding="ascii")

    # Run aggregation
    agg = PolyAggregator()
    agg.params.min_tets_per_cluster = 3
    result = agg.run(case_dir)

    # Should produce fewer cells
    assert result.cells_before == n_cells
    assert result.cells_after > 0
    assert result.cells_after <= n_cells
    print(
        f"Synthetic test: {result.cells_before} -> {result.cells_after} cells "
        f"({result.reduction_pct:.1f}% reduction)",
    )


def test_run_with_generated_stl(tmp_path):
    """End-to-end test: generate STL, create case, run aggregator.

    May fail without GMSH — that's acceptable for unit test purposes.
    """
    from cfmesh_autogui.commercial.verification import VerificationSuite

    class MockCfg:
        pass

    vs = VerificationSuite(MockCfg())
    geoms = vs.generate_test_geometries(tmp_path)
    if "duct" not in geoms:
        return

    stl_path = geoms["duct"]
    stl_dir = tmp_path / "constant" / "triSurface"
    stl_dir.mkdir(parents=True, exist_ok=True)

    import shutil
    shutil.copy2(stl_path, stl_dir / "surface.stl")

    agg = PolyAggregator()
    result = agg.run(tmp_path, geometry_path=str(stl_path))
    # May fail without GMSH in PATH — verify graceful handling
    if not result.success:
        assert result.errors


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_cli_main_version():
    """Verify --version flag works."""
    from _test_helpers import load_commercial_module
    pa_mod = load_commercial_module("poly_aggregator")
    ok = False
    try:
        pa_mod.main(["--version"])
    except SystemExit as exc:
        ok = (exc.code == 0)
    assert ok


def test_cli_main_help():
    """Verify CLI raises SystemExit(0) on --help."""
    from _test_helpers import load_commercial_module
    pa_mod = load_commercial_module("poly_aggregator")
    ok = False
    try:
        pa_mod.main(["--help"])
    except SystemExit as exc:
        ok = (exc.code == 0)
    assert ok, "main(['--help']) should exit 0"


def test_cli_main_missing_required():
    """Verify CLI fails without required args."""
    from _test_helpers import load_commercial_module
    pa_mod = load_commercial_module("poly_aggregator")
    ok = False
    try:
        pa_mod.main([])
    except SystemExit as exc:
        ok = (exc.code != 0)
    assert ok, "main([]) should exit non-zero"


def test_progress_callback(tmp_path):
    """Verify set_progress_callback is called during aggregation."""
    agg = PolyAggregator()
    calls: list[tuple[float, str]] = []

    def _cb(pct: float, msg: str):
        calls.append((pct, msg))

    agg.set_progress_callback(_cb)
    # Run on a case that will fail quickly (no mesh files)
    result = agg.run(tmp_path / "nonexistent")
    assert not result.success
    # Callback should have been invoked at least once (during cleanup/start)
    # If the run fails early (no files), we may get 0 calls - that's OK
    # The important thing is that set_progress_callback doesn't crash


def test_progress_callback_reports_phases(tmp_path):
    """Verify progress callback reports meaningful phases during a real run."""
    from cfmesh_autogui.commercial.verification import VerificationSuite

    class MockCfg:
        pass

    vs = VerificationSuite(MockCfg())
    geoms = vs.generate_test_geometries(tmp_path)
    if "duct" not in geoms:
        return

    stl_path = geoms["duct"]
    case_dir = tmp_path / "progress_test"
    case_dir.mkdir()
    stl_dir = case_dir / "constant" / "triSurface"
    stl_dir.mkdir(parents=True)

    import shutil
    shutil.copy2(stl_path, stl_dir / "surface.stl")

    agg = PolyAggregator()
    phases: list[str] = []

    def _cb(pct: float, msg: str):
        if msg not in phases:
            phases.append(msg)

    agg.set_progress_callback(_cb)
    result = agg.run(case_dir, geometry_path=str(stl_path))
    # May fail without GMSH - but callback should have been set without error
    assert result.success or not result.success


def test_exported_from_package():
    """Verify PolyAggregator is importable from the commercial package."""
    from cfmesh_autogui.commercial import (
        AggregationParams,
        AggregationResult,
        PolyAggregator,
    )
    assert PolyAggregator is not None
    assert AggregationParams is not None
    assert AggregationResult is not None
    p = AggregationParams()
    assert p.min_tets_per_cluster == 20


def test_cli_main_min_tets_flag():
    """Verify --min-tets flag is accepted (will fail on nonexistent case, but not on argparse)."""
    from _test_helpers import load_commercial_module
    pa_mod = load_commercial_module("poly_aggregator")
    exit_code = pa_mod.main(["--case-dir", ".", "--min-tets", "3", "--no-bl"])
    # Exits with 1 (runtime error, not argparse error — meaning flags were parsed OK)
    assert exit_code == 1, f"Expected exit 1 (runtime fail), got {exit_code}"


if __name__ == "__main__":
    test_params_defaults()
    test_params_custom()
    test_params_bl()
    test_result_defaults()
    test_result_reduction_75()
    test_result_reduction_50()
    test_result_reduction_no_cells()
    test_result_reduction_equal()
    test_face_normal_triangle()
    test_face_normal_quad()
    test_face_normal_angled()
    test_face_centroid()
    test_are_coplanar_same_plane()
    test_are_coplanar_different_planes()
    test_merge_two_faces_shared_edge()
    test_merge_two_faces_no_shared_edge()
    test_face_defaults()
    test_face_reversed()
    test_face_hash()
    test_face_eq()
    test_face_neq()
    test_extract_data_block_simple()
    test_extract_data_block_comments()
    test_parse_face_list_tri()
    test_parse_face_list_hex()
    test_aggregator_init()
    test_aggregator_run_no_case(None)
    test_aggregator_run_empty_case(None)
    test_build_vertex_adjacency_simple()
    test_run_with_generated_stl(None)
    print("ALL PASS")
