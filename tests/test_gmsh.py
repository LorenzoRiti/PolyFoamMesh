"""Tests for GMSH wrapper and mesh converter (no GMSH runtime needed)."""
import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import os as _os
try:
    import OCP as _ocp
    _d = _os.path.dirname(_ocp.__file__)
    if _d not in _os.environ.get("PATH", ""):
        _os.environ["PATH"] = _d + _os.pathsep + _os.environ.get("PATH", "")
except Exception:
    pass

from cfmesh_autogui.core.mesh_converter import (
    _of_header, _TETRA_FACES, _HEX_FACES, _WEDGE_FACES, _PYRAMID_FACES,
    _CELL_FACE_MAP, _3D_CELL_TYPES,
)
from cfmesh_autogui.core.gmsh_wrapper import _GMSH_DETAIL


# ------------------------------------------------------------------
# Issue 10: _of_header format
# ------------------------------------------------------------------
def test_of_header_produces_valid_openfoam():
    """Header must contain FoamFile, version, and matching class/object."""
    for class_type in ("faceList", "labelList", "primitiveEntry", "polyBoundaryMesh"):
        hdr = _of_header(class_type)
        assert "FoamFile" in hdr
        assert "version     2.0" in hdr
        assert f"class       {class_type}" in hdr
        assert f"object      {class_type}" in hdr
        assert "location    \"constant/polyMesh\"" in hdr
        # Verify braces are balanced { }
        opens = hdr.count("{")
        closes = hdr.count("}")
        assert opens == closes, (
            f"Header for {class_type} has {opens} '{{' but {closes} '}}'"
        )
    print("PASS: _of_header produces valid OF headers for all 4 types")


# ------------------------------------------------------------------
# Issue 1: Cell face topology
# ------------------------------------------------------------------
def test_tetra_has_4_faces():
    """Tetrahedra must have exactly 4 faces, all triangles."""
    assert len(_TETRA_FACES) == 4
    for f in _TETRA_FACES:
        assert len(f) == 3, f"Tetra face should be a triangle: {f}"
        assert all(0 <= v < 4 for v in f), f"Vertex index out of range: {f}"
    print("PASS: tetra has 4 triangular faces")


def test_hex_has_6_faces():
    """Hexahedra must have exactly 6 faces, all quads."""
    assert len(_HEX_FACES) == 6
    for f in _HEX_FACES:
        assert len(f) == 4, f"Hex face should be a quad: {f}"
        assert all(0 <= v < 8 for v in f), f"Vertex index out of range: {f}"
    print("PASS: hex has 6 quadrilateral faces")


def test_wedge_has_5_faces():
    """Wedge/prism must have 5 faces: 2 triangles + 3 quads."""
    assert len(_WEDGE_FACES) == 5
    tri_count = sum(1 for f in _WEDGE_FACES if len(f) == 3)
    quad_count = sum(1 for f in _WEDGE_FACES if len(f) == 4)
    assert tri_count == 2, f"Expected 2 triangular faces, got {tri_count}"
    assert quad_count == 3, f"Expected 3 quadrilateral faces, got {quad_count}"
    assert all(0 <= v < 6 for f in _WEDGE_FACES for v in f)
    print("PASS: wedge has 2 tri + 3 quad faces")


def test_pyramid_has_5_faces():
    """Pyramid must have 5 faces: 1 quad + 4 triangles."""
    assert len(_PYRAMID_FACES) == 5
    tri_count = sum(1 for f in _PYRAMID_FACES if len(f) == 3)
    quad_count = sum(1 for f in _PYRAMID_FACES if len(f) == 4)
    assert tri_count == 4, f"Expected 4 triangular faces, got {tri_count}"
    assert quad_count == 1, f"Expected 1 quadrilateral face, got {quad_count}"
    assert all(0 <= v < 5 for f in _PYRAMID_FACES for v in f)
    print("PASS: pyramid has 1 quad + 4 tri faces")


def test_cell_face_map_has_all_3d_types():
    """_CELL_FACE_MAP must cover all 3D types from _3D_CELL_TYPES."""
    for type_id in _3D_CELL_TYPES:
        assert type_id in _CELL_FACE_MAP, (
            f"Missing cell type {type_id} in _CELL_FACE_MAP"
        )
    # Also check surface types
    assert 2 in _CELL_FACE_MAP  # triangle
    assert 3 in _CELL_FACE_MAP  # quad
    print("PASS: _CELL_FACE_MAP covers all 3D and surface element types")


# ------------------------------------------------------------------
# Issue 2: _GMSH_DETAIL constants
# ------------------------------------------------------------------
def test_gmsh_detail_has_all_levels():
    """_GMSH_DETAIL must define coarse, medium, fine."""
    for level in ("coarse", "medium", "fine"):
        assert level in _GMSH_DETAIL, f"Missing detail level '{level}'"
        d = _GMSH_DETAIL[level]
        for key in ("curv_angle", "min_mult", "max_mult", "vol_mult"):
            assert key in d, f"Missing key '{key}' in {level}"
            assert isinstance(d[key], (int, float)), f"{key} is not numeric in {level}"
        # Sanity: curv_angle decreases with detail
    assert _GMSH_DETAIL["coarse"]["curv_angle"] > _GMSH_DETAIL["medium"]["curv_angle"]
    assert _GMSH_DETAIL["medium"]["curv_angle"] > _GMSH_DETAIL["fine"]["curv_angle"]
    print("PASS: _GMSH_DETAIL has coarse/medium/fine with valid curv_angle ordering")


def test_gmsh_detail_multipliers_positive():
    """All multiplier values must be positive."""
    for level, d in _GMSH_DETAIL.items():
        for key in ("min_mult", "max_mult", "vol_mult"):
            assert d[key] > 0, f"{level}.{key} = {d[key]} should be > 0"
    print("PASS: all _GMSH_DETAIL multipliers are positive")


# ------------------------------------------------------------------
# Suggestion 1: Mesh stats helper
# ------------------------------------------------------------------
def test_mesh_stats_readability():
    """Verify the stats format used in Suggestion 1 would work."""
    points = 4250
    faces = 18340
    cells = 12100
    msg = f"polyMesh: {points:,} points, {faces:,} faces, {cells:,} cells"
    assert "4,250" in msg
    assert "18,340" in msg
    assert "12,100" in msg
    print(f"PASS: mesh stats format: {msg}")

