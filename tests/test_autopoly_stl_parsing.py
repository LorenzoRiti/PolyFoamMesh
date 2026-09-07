"""Tests for ASCII STL parsing in the autopoly bridge (no-trimesh fallback).

``_load_stl_native`` is the pure-numpy path used when trimesh is unavailable;
it used to raise NotImplementedError for ASCII STL. These tests pin the
parser's behaviour and its (vertices, triangles, patch_ids) contract.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("autopoly_bridge")
_parse_stl_ascii = _mod._parse_stl_ascii
_load_stl_native = _mod._load_stl_native

# A single tetrahedron with vertices A(0,0,0), B(1,0,0), C(0,1,0), D(0,0,1).
# Each facet is wound so its normal points outward (away from the interior).
_TETRA_ASCII = b"""\
solid tetra
  facet normal 0 0 -1
    outer loop
      vertex 0 0 0
      vertex 0 1 0
      vertex 1 0 0
    endloop
  endfacet
  facet normal 0 -1 0
    outer loop
      vertex 0 0 0
      vertex 1 0 0
      vertex 0 0 1
    endloop
  endfacet
  facet normal -1 0 0
    outer loop
      vertex 0 0 0
      vertex 0 0 1
      vertex 0 1 0
    endloop
  endfacet
  facet normal 1 1 1
    outer loop
      vertex 1 0 0
      vertex 0 1 0
      vertex 0 0 1
    endloop
  endfacet
endsolid tetra
"""

# Expected after vertex dedup (insertion order: A, C, B, D).
_EXPECTED_TRIANGLES = {(0, 1, 2), (0, 2, 3), (0, 3, 1), (2, 1, 3)}


def _assert_tetra(vertices, triangles, patch_ids):
    assert vertices.shape == (4, 3)
    assert vertices.dtype == np.float64
    assert triangles.shape == (4, 3)
    assert triangles.dtype == np.int32
    assert patch_ids.shape == (4,)
    assert (patch_ids == 0).all()
    assert {tuple(t) for t in triangles.tolist()} == _EXPECTED_TRIANGLES
    # All four unique vertices present.
    assert {tuple(v) for v in vertices.tolist()} == {
        (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
    }


def test_parse_stl_ascii_tetrahedron():
    vertices, triangles, patch_ids = _parse_stl_ascii(_TETRA_ASCII)
    _assert_tetra(vertices, triangles, patch_ids)


def test_parse_stl_ascii_case_insensitive():
    data = _TETRA_ASCII.upper()
    vertices, triangles, patch_ids = _parse_stl_ascii(data)
    _assert_tetra(vertices, triangles, patch_ids)


def test_parse_stl_ascii_without_solid_wrapper():
    # Some files in the wild omit the solid/endsolid wrapper entirely.
    body = _TETRA_ASCII.split(b"solid tetra\n", 1)[1].rsplit(b"endsolid tetra\n", 1)[0]
    vertices, triangles, patch_ids = _parse_stl_ascii(body)
    _assert_tetra(vertices, triangles, patch_ids)


def test_parse_stl_ascii_without_normals():
    # Normals are optional; drop every "facet normal ..." line.
    lines = [
        ln for ln in _TETRA_ASCII.splitlines()
        if not ln.strip().startswith(b"facet normal")
    ]
    vertices, triangles, patch_ids = _parse_stl_ascii(b"\n".join(lines))
    _assert_tetra(vertices, triangles, patch_ids)


def test_parse_stl_ascii_empty_raises():
    with np.testing.assert_raises(ValueError):
        _parse_stl_ascii(b"")


def test_parse_stl_ascii_truncated_raises():
    # Facet with only two vertices — must raise, not silently drop it.
    bad = b"""solid bad
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 1 0 0
    endloop
  endfacet
endsolid bad
"""
    with np.testing.assert_raises(ValueError):
        _parse_stl_ascii(bad)


def test_parse_stl_ascii_missing_endfacet_raises():
    bad = b"""solid bad
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 1 0 0
      vertex 0 1 0
    endloop
endsolid bad
"""
    with np.testing.assert_raises(ValueError):
        _parse_stl_ascii(bad)


def test_parse_stl_ascii_no_facets_raises():
    with np.testing.assert_raises(ValueError):
        _parse_stl_ascii(b"solid empty\nendsolid empty\n")


def test_load_stl_native_ascii_dispatch(tmp_path):
    """_load_stl_native must route ASCII files to the ASCII parser."""
    path = tmp_path / "tetra.stl"
    path.write_bytes(_TETRA_ASCII)
    vertices, triangles, patch_ids = _load_stl_native(path)
    _assert_tetra(vertices, triangles, patch_ids)


def test_load_stl_native_binary_dispatch(tmp_path):
    """_load_stl_native must still route binary files to the binary parser."""
    # Same tetrahedron as binary STL: 4 facets, 12 vertices (no dedup in file).
    verts = [
        (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0),
        (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0),
        (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
    ]
    buf = bytearray(80)  # header
    buf += struct.pack("<I", 4)
    for i in range(0, len(verts), 3):
        buf += struct.pack("<3f", 0.0, 0.0, 0.0)  # normal (ignored)
        for v in verts[i:i + 3]:
            buf += struct.pack("<3f", *v)
        buf += struct.pack("<H", 0)  # attribute byte count
    path = tmp_path / "tetra_bin.stl"
    path.write_bytes(bytes(buf))

    vertices, triangles, patch_ids = _load_stl_native(path)
    _assert_tetra(vertices, triangles, patch_ids)


if __name__ == "__main__":
    test_parse_stl_ascii_tetrahedron()
    test_parse_stl_ascii_case_insensitive()
    test_parse_stl_ascii_without_solid_wrapper()
    test_parse_stl_ascii_without_normals()
    test_parse_stl_ascii_empty_raises()
    test_parse_stl_ascii_truncated_raises()
    test_parse_stl_ascii_missing_endfacet_raises()
    test_parse_stl_ascii_no_facets_raises()
    print("ALL PASS")