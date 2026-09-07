"""Regression test for mesh_converter.py face orientation (the KNOWN BUG).

The converter used to emit meshes where real GMSH tet models came out with
~40-100% negative-volume cells and thousands of "incorrectly oriented face"
checkMesh errors. Two independent defects caused it:

1. The per-cell face templates were not all wound outward, so OpenFOAM's
   divergence-theorem volume computation went negative for whole cells.
   (An earlier attempt that flipped two tet faces in isolation made it
   worse — 45% -> 99.7% negative-volume cells — because it reversed the
   two faces that were already outward and left the inward ones.)
2. Internal faces were stored with the NEIGHBOUR's winding while the owner
   was the first cell, so the stored normal pointed neighbour -> owner,
   against OpenFOAM's owner -> neighbour convention.

This test builds a valid two-tet mesh (one internal face, six boundary
faces), converts it through the real msh_to_of_polymesh() and checks every
OpenFOAM convention directly: positive cell volumes, owner->neighbour
normals on the internal face, and outward normals on boundary faces.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pytest

meshio = pytest.importorskip("meshio")

from polyfoammesh.core.mesh_converter import msh_to_of_polymesh  # noqa: E402

# Two tetrahedra sharing the base triangle {0,1,2}: one apex above the
# plane (vertex 3), one below (vertex 4). Both are ordered with positive
# GMSH volume, exactly as a real generator would emit them. The whole
# assembly is offset from the origin so that no face passes through it:
# with a face through the origin, inward-facing template windings can
# contribute zero to the divergence-theorem volume and accidentally
# cancel out (real meshes are never arranged like that).
POINTS = np.array(
    [
        [0.37, -0.21, 0.55],
        [1.37, -0.21, 0.55],
        [0.37, 0.79, 0.55],
        [0.37, -0.21, 1.55],
        [0.37, -0.21, -0.45],
    ],
    dtype=np.float64,
)

_TETS = np.array([[0, 1, 2, 3], [0, 2, 1, 4]])

# Boundary triangles of both tets (everything except the shared base).
_BOUNDARY_TRIS = np.array(
    [
        [0, 1, 3], [1, 2, 3], [0, 3, 2],  # tet A (apex +z)
        [0, 2, 4], [2, 1, 4], [0, 4, 1],  # tet B (apex -z)
    ]
)


def _area_vector(points: np.ndarray, fv) -> np.ndarray:
    v = points[list(fv)]
    if len(fv) == 3:
        return 0.5 * np.cross(v[1] - v[0], v[2] - v[0])
    return 0.5 * (
        np.cross(v[1] - v[0], v[2] - v[0]) + np.cross(v[2] - v[0], v[3] - v[0])
    )


def _list_tokens(path: Path) -> list[str]:
    text = path.read_text()
    # Start at the FoamFile dict opening brace. This is format-agnostic:
    # mesh_converter's legacy header is a /* ... */ banner followed by
    # "FoamFile { ... }", while foam_mesh_io writes a plain "FoamFile { ... }"
    # with no banner — in both cases the first '{' opens the FoamFile dict,
    # and _parse_list_file/_parse_faces locate its closing '}' from there.
    first = text.find("{")
    body = text[first:]
    return [
        ln.strip() for ln in body.splitlines()
        if ln.strip() and not ln.strip().startswith("//")
    ]


def _parse_list_file(path: Path, as_float: bool = False):
    """Parse one of the simple ASCII polyMesh list files."""
    tokens = _list_tokens(path)
    close = tokens.index("}")  # closing brace of the FoamFile block
    n = int(tokens[close + 1])
    assert tokens[close + 2] == "("
    values: list = []
    idx = close + 3
    while len(values) < n:
        tok = tokens[idx]
        if tok == ")":
            break
        if as_float:
            values.append([float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", tok)])
        else:
            values.append(int(tok))
        idx += 1
    return values


def _parse_faces(path: Path) -> list[list[int]]:
    tokens = _list_tokens(path)
    close = tokens.index("}")
    n = int(tokens[close + 1])
    faces: list[list[int]] = []
    idx = close + 3
    while len(faces) < n:
        tok = tokens[idx]
        if tok == ")":
            break
        nv, rest = tok.split("(", 1)
        faces.append([int(v) for v in rest.rstrip(")").split()])
        idx += 1
    return faces


def _parse_boundary(path: Path) -> list[dict]:
    tokens = _list_tokens(path)
    close = tokens.index("}")
    n = int(tokens[close + 1])
    patches: list[dict] = []
    i = close + 3
    while i < len(tokens) and len(patches) < n:
        if tokens[i] in ("(", ")"):
            i += 1
            continue
        if "{" in tokens[i]:
            i += 1
            continue
        patch = {"name": tokens[i]}
        i += 1
        while i < len(tokens) and "}" not in tokens[i]:
            key, _, val = tokens[i].rstrip(";").partition(" ")
            try:
                patch[key] = int(val)
            except ValueError:
                patch[key] = val
            i += 1
        patches.append(patch)
        i += 1
    return patches


def test_conversion_produces_openfoam_valid_orientation(tmp_path):
    msh_path = tmp_path / "two_tets.msh"
    cells = [("triangle", _BOUNDARY_TRIS), ("tetra", _TETS)]
    m = meshio.Mesh(
        POINTS,
        cells,
        cell_data={
            "gmsh:physical": [
                np.full(len(_BOUNDARY_TRIS), 1),
                np.full(len(_TETS), 0),
            ]
        },
        field_data={"wall": np.array([1, 2])},
    )
    meshio.write(msh_path, m, file_format="gmsh22")

    poly = msh_to_of_polymesh(msh_path, tmp_path / "case")
    assert (poly / "points").exists()

    points = np.array(_parse_list_file(poly / "points", as_float=True), dtype=np.float64)
    assert points.shape == POINTS.shape
    assert np.allclose(points, POINTS)

    faces = _parse_faces(poly / "faces")
    owners = _parse_list_file(poly / "owner")
    neighbours = _parse_list_file(poly / "neighbour")
    boundary = _parse_boundary(poly / "boundary")
    n_cells = max(owners) + 1

    def cell_faces(cell: int) -> list[int]:
        out = []
        for fi, o in enumerate(owners):
            if o == cell or (neighbours[fi] >= 0 and neighbours[fi] == cell):
                out.append(fi)
        return out

    # --- every cell must have a positive signed volume ---
    assert n_cells == 2
    for cell in range(n_cells):
        cf = cell_faces(cell)
        vol = 0.0
        for fi in cf:
            n_vec = _area_vector(points, faces[fi])
            if owners[fi] != cell:
                n_vec = -n_vec  # neighbour sees the reversed normal
            ctr = points[faces[fi]].mean(axis=0)
            vol += float(np.dot(n_vec, ctr))
        assert vol / 3.0 > 0, f"cell {cell} volume must be positive, got {vol / 3.0}"

    # --- the single internal face: normal must point owner -> neighbour ---
    internal = [i for i, nb in enumerate(neighbours) if nb >= 0]
    assert len(internal) == 1, f"expected 1 internal face, got {len(internal)}"
    fi = internal[0]
    o, n = owners[fi], neighbours[fi]
    n_vec = _area_vector(points, faces[fi])
    owner_verts = [0, 1, 2, 3] if o == 0 else [0, 1, 2, 4]
    neigh_verts = [0, 1, 2, 3] if n == 0 else [0, 1, 2, 4]
    toward_neighbour = points[neigh_verts].mean(axis=0) - points[owner_verts].mean(axis=0)
    assert np.dot(n_vec, toward_neighbour) > 0, (
        "internal face normal does not point from owner to neighbour"
    )

    # --- every boundary face: normal must point away from its owner ---
    assert len(boundary) == 1 and boundary[0]["name"] == "wall"
    assert boundary[0]["nFaces"] == 6
    n_boundary = 0
    for fi in range(len(owners)):
        if neighbours[fi] >= 0:
            continue
        n_boundary += 1
        owner = owners[fi]
        owner_verts = [0, 1, 2, 3] if owner == 0 else [0, 1, 2, 4]
        owner_centroid = points[owner_verts].mean(axis=0)
        face_center = points[faces[fi]].mean(axis=0)
        n_vec = _area_vector(points, faces[fi])
        assert np.dot(n_vec, owner_centroid - face_center) < 0, (
            f"boundary face {fi} normal does not point away from its owner"
        )
    assert n_boundary == 6
