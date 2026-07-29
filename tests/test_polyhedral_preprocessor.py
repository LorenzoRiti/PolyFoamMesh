"""Tests for the Polyhedral Preprocessor (Dual Mesh algorithm)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("polyhedral_preprocessor")

DualMeshConverter = _mod.DualMeshConverter
PolyMeshData = _mod.PolyMeshData
PolyQualityReport = _mod.PolyQualityReport
PolyhedralResult = _mod.PolyhedralResult
PolyhedralPreprocessor = _mod.PolyhedralPreprocessor
POLY_QUALITY_THRESHOLDS = _mod.POLY_QUALITY_THRESHOLDS
_poly_cell_volume = _mod._poly_cell_volume
_poly_cell_quality = _mod._poly_cell_quality
_face_normal = _mod._face_normal
export_polymesh = _mod.export_polymesh
_write_points = _mod._write_points
_write_faces = _mod._write_faces
_write_owner = _mod._write_owner
_write_neighbour = _mod._write_neighbour
_write_boundary = _mod._write_boundary
_of_header = _mod._of_header


# ---------------------------------------------------------------------------
# Fixtures: synthetic tetrahedral meshes
# ---------------------------------------------------------------------------

def _single_tet_mesh():
    """A single regular tetrahedron."""
    verts = np.array([
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)
    cells = np.array([[0, 1, 2, 3]], dtype=np.int64)
    return verts, cells


def _two_tet_mesh():
    """Two tetrahedra sharing a face, forming a triangular prism."""
    verts = np.array([
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        [1.0, 0.0, 1.0],
    ], dtype=np.float64)
    cells = np.array([
        [0, 1, 2, 3],
        [1, 2, 3, 4],
    ], dtype=np.int64)
    return verts, cells


def _regular_tet_grid(nx: int = 2, ny: int = 2, nz: int = 2):
    """A regular grid of tetrahedra (subdivided hexahedra)."""
    from itertools import product

    # Create a regular point grid
    xs = np.linspace(0, 1, nx + 1)
    ys = np.linspace(0, 1, ny + 1)
    zs = np.linspace(0, 1, nz + 1)
    points = []
    for z in zs:
        for y in ys:
            for x in xs:
                points.append([x, y, z])
    verts = np.array(points, dtype=np.float64)

    # Each hexahedron -> 5 or 6 tetrahedra
    cells = []
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                v0 = (k * (ny + 1) + j) * (nx + 1) + i
                v1 = v0 + 1
                v2 = ((k * (ny + 1) + j + 1) * (nx + 1)) + i
                v3 = v2 + 1
                v4 = ((k + 1) * (ny + 1) + j) * (nx + 1) + i
                v5 = v4 + 1
                v6 = ((k + 1) * (ny + 1) + j + 1) * (nx + 1) + i
                v7 = v6 + 1

                # 5-tet decomposition of a hex (more stable)
                cells.append([v0, v1, v3, v7])
                cells.append([v0, v1, v7, v4])
                cells.append([v0, v3, v2, v7])
                cells.append([v0, v2, v6, v7])
                cells.append([v0, v4, v7, v6])

    return verts, np.array(cells, dtype=np.int64)


def _quality_report_ok() -> PolyQualityReport:
    return PolyQualityReport(
        n_cells=1000, n_faces=5000, n_points=500,
        max_non_orthogonality=45.0, avg_non_orthogonality=8.0,
        max_skewness=2.5, avg_skewness=0.8,
        max_aspect_ratio=500, min_volume=1e-8,
        neg_cells=0, n_boundary_faces=500,
        passed=True,
    )


def _quality_report_fail() -> PolyQualityReport:
    return PolyQualityReport(
        n_cells=1000, n_faces=5000, n_points=500,
        max_non_orthogonality=85.0, avg_non_orthogonality=15.0,
        max_skewness=8.5, avg_skewness=2.0,
        max_aspect_ratio=2000, min_volume=-5e-10,
        neg_cells=3, n_boundary_faces=500,
        passed=False,
    )


# ===================================================================
# Tests: PolyQualityReport
# ===================================================================

def test_quality_report_defaults():
    r = PolyQualityReport()
    assert r.n_cells == 0
    assert r.n_faces == 0
    assert not r.passed


def test_quality_report_summary_pass():
    r = _quality_report_ok()
    s = r.summary()
    assert "PASS" in s
    assert "1,000" in s


def test_quality_report_summary_fail():
    r = _quality_report_fail()
    s = r.summary()
    assert "FAIL" in s
    assert "NegVol" in s


# ===================================================================
# Tests: PolyhedralResult
# ===================================================================

def test_polyhedral_result_defaults():
    r = PolyhedralResult()
    assert not r.success
    assert r.warnings == []
    assert r.errors == []


# ===================================================================
# Tests: DualMeshConverter
# ===================================================================

def test_dual_converter_init():
    verts, cells = _single_tet_mesh()
    dc = DualMeshConverter(verts, cells)
    assert dc.n_verts == 4
    assert dc.n_tets == 1


def test_dual_converter_init_invalid():
    import re
    try:
        DualMeshConverter(np.zeros((10, 2)), np.zeros((5, 4)))
    except ValueError as e:
        assert "must be (N,3)" in str(e)


def test_dual_converter_centroids():
    verts, cells = _single_tet_mesh()
    dc = DualMeshConverter(verts, cells)
    cents = dc.compute_centroids()
    assert cents.shape == (1, 3)
    expected = np.mean(verts, axis=0)
    np.testing.assert_allclose(cents[0], expected)


def test_dual_converter_adjacency_single():
    verts, cells = _single_tet_mesh()
    dc = DualMeshConverter(verts, cells)
    dc.compute_centroids()
    dc.build_adjacency()

    assert dc.vert_to_tets is not None
    assert dc.edge_to_tets is not None
    assert len(dc.vert_to_tets) == 4
    assert all(len(tl) == 1 for tl in dc.vert_to_tets)
    assert len(dc.edge_to_tets) == 6


def test_dual_converter_adjacency_two():
    verts, cells = _two_tet_mesh()
    dc = DualMeshConverter(verts, cells)
    dc.compute_centroids()
    dc.build_adjacency()

    assert dc.vert_to_tets is not None
    # Vertex 1 and 2 are shared by both tets
    assert len(dc.vert_to_tets[0]) == 1
    assert len(dc.vert_to_tets[1]) == 2
    assert len(dc.vert_to_tets[2]) == 2
    assert len(dc.vert_to_tets[3]) == 2
    assert len(dc.vert_to_tets[4]) == 1


def test_sort_angles():
    verts = np.array([
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, -1.0, 0.0],
    ], dtype=np.float64)
    centroids = np.array([
        [0.5, 0.0, 0.0],
        [0.5, 1.0, 0.0],
        [0.5, -1.0, 0.0],
    ], dtype=np.float64)
    indices = [0, 1, 2]

    result = DualMeshConverter._sort_angles(
        centroids, verts[0], verts[1], indices,
    )
    assert len(result) == 3
    # Should be sorted by angle around the edge (0,1) = x-axis


def test_dual_converter_convert_two_tets():
    verts, cells = _two_tet_mesh()
    dc = DualMeshConverter(verts, cells)
    result = dc.convert()

    assert isinstance(result, PolyMeshData)
    assert result.points.shape == (2, 3)  # 2 tet centroids
    assert result.cell_faces is not None


def test_dual_converter_convert_grid():
    verts, cells = _regular_tet_grid(2, 2, 2)
    dc = DualMeshConverter(verts, cells)
    result = dc.convert()

    assert isinstance(result, PolyMeshData)
    assert result.points.shape[1] == 3
    assert result.cell_faces is not None
    # Each original vertex should become a dual cell with faces
    assert len(result.cell_faces) > 0
    for ci, faces in enumerate(result.cell_faces):
        assert len(faces) >= 4, f"Dual cell {ci} has only {len(faces)} faces"


# ===================================================================
# Tests: Poly cell quality metrics
# ===================================================================

def test_face_normal():
    # A unit square in the XY plane
    pts = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float64)
    n = _face_normal(pts)
    np.testing.assert_allclose(n, [0, 0, 1], atol=1e-10)


def test_poly_cell_volume_hex():
    # A unit cube decomposed into a hex cell
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=np.float64)
    faces = [
        [0, 1, 2, 3],  # bottom
        [4, 5, 6, 7],  # top
        [0, 1, 5, 4],  # front
        [2, 3, 7, 6],  # back
        [1, 2, 6, 5],  # right
        [0, 3, 7, 4],  # left
    ]
    vol = _poly_cell_volume(pts, faces)
    assert abs(vol - 1.0) < 0.01


def test_poly_cell_quality_good():
    # A unit cube
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=np.float64)
    faces = [
        [0, 1, 2, 3], [4, 5, 6, 7],
        [0, 1, 5, 4], [2, 3, 7, 6],
        [1, 2, 6, 5], [0, 3, 7, 4],
    ]
    q = _poly_cell_quality(pts, faces)
    assert q["valid"]
    assert q["volume"] > 0.99
    assert q["non_ortho"] < 10
    assert q["skewness"] < 0.8  # cube has natural skew ~0.63


# ===================================================================
# Tests: Quality thresholds
# ===================================================================

def test_thresholds():
    assert POLY_QUALITY_THRESHOLDS["non_ortho_max"] == 70.0
    assert POLY_QUALITY_THRESHOLDS["non_ortho_avg"] == 10.0
    assert POLY_QUALITY_THRESHOLDS["skewness_max"] == 4.0
    assert POLY_QUALITY_THRESHOLDS["aspect_ratio_max"] == 1000.0


# ===================================================================
# Tests: OpenFOAM polyMesh writer
# ===================================================================

def test_of_header():
    h = _of_header("vectorField")
    assert "vectorField" in h
    assert "OpenFOAM" in h


def test_write_points():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
        _write_points(d, pts)
        content = (d / "points").read_text()
        assert "3" in content
        assert "0.0000000000e+00" in content


def test_write_faces():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        faces = [[0, 1, 2], [1, 2, 3]]
        _write_faces(d, faces)
        content = (d / "faces").read_text()
        assert "2" in content
        assert "3(0 1 2)" in content


def test_write_owner():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        owners = [0, 1, 0]
        _write_owner(d, owners)
        content = (d / "owner").read_text()
        assert "3" in content
        assert "1" in content


def test_write_neighbour():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        neighbours = [1, -1, 2]
        _write_neighbour(d, neighbours)
        content = (d / "neighbour").read_text()
        assert "3" in content
        assert "-1" in content


def test_write_boundary():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        patches = [
            {"name": "walls", "type": "wall", "nFaces": 100, "startFace": 0},
            {"name": "inlet", "type": "patch", "nFaces": 50, "startFace": 100},
        ]
        _write_boundary(d, patches)
        content = (d / "boundary").read_text()
        assert "walls" in content
        assert "inlet" in content
        assert "wall" in content


def test_export_polymesh():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "constant" / "polyMesh"
        pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], dtype=np.float64)
        faces = [[0, 1, 3], [1, 2, 3]]
        owners = [0, 0]
        neighbours = [-1, -1]
        patches = [{"name": "default", "type": "patch", "nFaces": 2, "startFace": 0}]

        result = export_polymesh(d, pts, faces, owners, neighbours, patches)
        assert result.exists()
        assert (result / "points").exists()
        assert (result / "faces").exists()
        assert (result / "owner").exists()
        assert (result / "neighbour").exists()
        assert (result / "boundary").exists()


# ===================================================================
# Tests: PolyhedralPreprocessor
# ===================================================================

def test_preprocessor_init():
    pp = PolyhedralPreprocessor()
    assert pp is not None
    assert pp.verbosity == 0


def test_detail_to_meshsize():
    bbox = 2.0
    assert PolyhedralPreprocessor._detail_to_meshsize("very_fine", bbox) == 0.04
    assert PolyhedralPreprocessor._detail_to_meshsize("fine", bbox) == 0.08
    assert PolyhedralPreprocessor._detail_to_meshsize("medium", bbox) == 0.16
    assert PolyhedralPreprocessor._detail_to_meshsize("coarse", bbox) == 0.30
    assert PolyhedralPreprocessor._detail_to_meshsize("very_coarse", bbox) == 0.50


def test_parse_checkmesh_ok():
    output = (
        "cells: 5000; faces: 25000; points: 2500\n"
        "Max non-orthogonality = 45.2 average = 12.1\n"
        "Max skewness = 2.3 average = 0.8\n"
        "Max aspect ratio = 450\n"
        "Min volume = 1.2e-08\n"
        "boundary 3000\n"
        "Mesh OK."
    )
    pp = PolyhedralPreprocessor()
    r = pp._parse_checkmesh(output)
    assert r.n_cells == 5000
    assert r.n_faces == 25000
    assert r.max_skewness == 2.3
    assert r.max_non_orthogonality == 45.2
    assert r.max_aspect_ratio == 450
    assert r.neg_cells == 0
    assert r.passed


def test_parse_checkmesh_fail():
    output = (
        "cells: 1000; faces: 5000; points: 500\n"
        "Max non-orthogonality = 85.0 average = 18.3\n"
        "Max skewness = 8.5 average = 1.5\n"
        "Max aspect ratio = 2000\n"
        "Min volume = -5.3e-10\n"
        "There are 3 cells with negative volume\n"
        "boundary 800\n"
        "Mesh NOT OK."
    )
    pp = PolyhedralPreprocessor()
    r = pp._parse_checkmesh(output)
    assert r.neg_cells == 3
    assert r.max_skewness == 8.5
    assert not r.passed


def test_write_tet_msh():
    verts = np.array([
        [0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
    ], dtype=np.float64)
    tets = np.array([[0, 1, 2, 3]], dtype=np.int64)

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "test.msh"
        result = PolyhedralPreprocessor.write_tet_msh(verts, tets, out)
        assert result.exists()
        content = result.read_text()
        assert "$MeshFormat" in content or "$Nodes" in content


# ===================================================================
# Tests: _build_flat_topology
# ===================================================================

def test_build_flat_topology():
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [0, 1, 1],
    ], dtype=np.float64)

    # Two tet cells sharing a face
    cell_faces = [
        [[0, 1, 2], [0, 1, 3], [1, 2, 3], [0, 2, 3]],     # cell 0
        [[1, 2, 3], [1, 3, 4], [2, 3, 5], [3, 4, 5]],     # cell 1
    ]
    mesh = PolyMeshData(
        points=pts, cell_faces=cell_faces,
        boundary_patches=[{"name": "default", "type": "patch", "nFaces": 6, "startFace": 0}],
    )

    all_faces, owners, neighbours = PolyhedralPreprocessor._build_flat_topology(mesh)

    assert len(all_faces) > 0
    assert len(owners) == len(all_faces)
    assert len(neighbours) == len(all_faces)


def test_compute_quality_in_process():
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=np.float64)
    faces = [
        [0, 1, 2, 3], [4, 5, 6, 7],
        [0, 1, 5, 4], [2, 3, 7, 6],
        [1, 2, 6, 5], [0, 3, 7, 4],
    ]
    mesh = PolyMeshData(points=pts, cell_faces=[faces])

    pp = PolyhedralPreprocessor()
    r = pp._compute_quality_in_process(mesh)
    assert r.n_cells == 1
    assert r.min_volume > 0
    assert r.passed


# ===================================================================
# Tests: PolyMeshData
# ===================================================================

def test_polymesh_data_defaults():
    m = PolyMeshData(points=np.zeros((10, 3)))
    assert m.n_cells == 0
    assert m.points.shape == (10, 3)


def test_polymesh_data_with_cells():
    m = PolyMeshData(
        points=np.zeros((10, 3)),
        cells=[[0, 1, 2, 3], [1, 2, 3, 4]],
    )
    assert m.n_cells == 2


def test_polymesh_data_with_faces():
    m = PolyMeshData(
        points=np.zeros((10, 3)),
        cell_faces=[[[0, 1, 2], [3, 4, 5]]],
    )
    assert m.n_cells == 1


# ===================================================================
# Tests: PolyhedralPreprocessor._optimize_poly_mesh
# ===================================================================

def test_optimize_poly_mesh_no_faces():
    m = PolyMeshData(points=np.zeros((10, 3)))
    pp = PolyhedralPreprocessor()
    result = pp._optimize_poly_mesh(m)
    assert result is m


def test_optimize_poly_mesh_simple():
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=np.float64)
    faces = [
        [0, 1, 2, 3], [4, 5, 6, 7],
        [0, 1, 5, 4], [2, 3, 7, 6],
        [1, 2, 6, 5], [0, 3, 7, 4],
    ]
    m = PolyMeshData(points=pts.copy(), cell_faces=[faces])

    pp = PolyhedralPreprocessor()
    result = pp._optimize_poly_mesh(m)
    assert result.points.shape == pts.shape


# ===================================================================
# Tests: Heal geometry fallback
# ===================================================================

def test_heal_geometry_no_pymeshlab(tmp_path, monkeypatch):
    """Test that healing gracefully handles missing PyMeshLab."""
    pp = PolyhedralPreprocessor()
    # Simulate ImportError by monkeypatching
    import builtins
    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pymeshlab":
            raise ImportError("No PyMeshLab")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    geom = tmp_path / "test.stl"
    geom.write_text("dummy stl content")
    with monkeypatch.context():
        result = pp._heal_geometry(geom)
    assert result == geom  # Falls back to original


if __name__ == "__main__":
    test_quality_report_defaults()
    test_quality_report_summary_pass()
    test_quality_report_summary_fail()
    test_polyhedral_result_defaults()
    test_dual_converter_init()
    test_dual_converter_init_invalid()
    test_dual_converter_centroids()
    test_dual_converter_adjacency_single()
    test_dual_converter_adjacency_two()
    test_sort_angles()
    test_dual_converter_convert_two_tets()
    test_dual_converter_convert_grid()
    test_face_normal()
    test_poly_cell_volume_hex()
    test_poly_cell_quality_good()
    test_thresholds()
    test_of_header()
    test_write_points()
    test_write_faces()
    test_write_owner()
    test_write_neighbour()
    test_write_boundary()
    test_export_polymesh()
    test_preprocessor_init()
    test_detail_to_meshsize()
    test_parse_checkmesh_ok()
    test_parse_checkmesh_fail()
    test_write_tet_msh()
    test_build_flat_topology()
    test_compute_quality_in_process()
    test_polymesh_data_defaults()
    test_polymesh_data_with_cells()
    test_polymesh_data_with_faces()
    test_optimize_poly_mesh_no_faces()
    test_optimize_poly_mesh_simple()
    test_heal_geometry_no_pymeshlab(Path(tempfile.mkdtemp()), None)
    print("ALL PASS")
