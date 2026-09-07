"""Regression: the viewer must never triangulate polyhedral boundary faces.

Two independent code paths can render the mesh, and BOTH used to shred the
polygons into triangles, which is what made a 100% polyhedral mesh look
like a tetrahedral one on screen:

  1. ``read_openfoam_mesh_patches`` — the manual OpenFOAM parser used as a
     fallback while foamToVTK is still running (or when it is unavailable).
     It fanned every face into triangles (quad -> 2 tris, n-gon -> n-2),
     so the barycentric dual's quad boundary showed only its diagonals.
  2. ``ViewerWidget._prepare_internal_vtu_grid`` — covered separately in
     tests/test_viewer_surface_only.py.

This file covers (1) at the level that matters: given boundary faces that
are quads and pentagons, the PolyData handed to the plotter must still
contain quads and pentagons.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pv = pytest.importorskip("pyvista")


def _face_sizes(pd) -> set[int]:
    return {len(cell.point_ids) for cell in pd.cell}


def test_polydata_face_stream_preserves_polygons():
    """The exact face-stream construction read_openfoam_mesh_patches now
    uses: a flat [n, v0..vn-1, m, w0..wm-1, ...] array. Guards the encoding
    itself — if this ever regressed to a triangle fan, face sizes would
    collapse to {3}."""
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],          # quad
        [2, 0, 0], [3, 0, 0], [3.5, 1, 0], [2.5, 1.5, 0], [1.8, 1, 0],  # pentagon
    ], dtype=float)
    faces = np.array([4, 0, 1, 2, 3, 5, 4, 5, 6, 7, 8], dtype=np.int32)
    pd = pv.PolyData(pts, faces)
    assert pd.n_cells == 2
    assert _face_sizes(pd) == {4, 5}
    assert 3 not in _face_sizes(pd)


def test_manual_parser_emits_real_polygons(tmp_path, monkeypatch):
    """End-to-end through read_openfoam_mesh_patches with a stubbed
    polyMesh whose boundary is quads: the returned PolyData must contain
    quads, not pairs of triangles."""
    from cfmesh_autogui.gui import viewer_widget as vw

    points = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=float)
    # two quad boundary faces
    all_faces = [[0, 1, 2, 3], [4, 5, 6, 7]]
    patches = [{"name": "wall", "startFace": 0, "nFaces": 2}]

    poly_dir = tmp_path / "constant" / "polyMesh"
    poly_dir.mkdir(parents=True)
    (poly_dir / "faces").write_bytes(b"stub")

    monkeypatch.setattr(vw, "_poly_dir_state", lambda _d: ("stub", 1))
    monkeypatch.setattr(vw, "_load_vtk_patches", lambda *a, **k: None)
    monkeypatch.setattr(vw, "_cache_set", lambda _k, _v: None)
    monkeypatch.setattr(vw, "_parse_of_points", lambda _p: points)
    monkeypatch.setattr(vw, "_parse_of_faces", lambda _p: all_faces)
    monkeypatch.setattr(vw, "_parse_boundary", lambda _p: patches)
    vw._MESH_CACHE.clear()

    result = vw.read_openfoam_mesh_patches(tmp_path)
    assert result is not None, "manual parser returned nothing — stubs are stale"
    pd = result["wall"]
    assert _face_sizes(pd) == {4}, (
        f"boundary quads were triangulated: face sizes {_face_sizes(pd)}"
    )
