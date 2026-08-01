"""Tests for geometric patch-role inference (core/case_setup.py).

GMSH names every boundary patch ``surface_N`` — the name carries no role
information, so ``infer_patch_roles`` classifies inlet/outlet/wall from the
mesh geometry itself. These tests build a tiny synthetic pipe polyMesh (a
box, ASCII format) whose roles are known by hand and assert the inference
recovers them. No OpenFOAM is required.
"""
from __future__ import annotations

import pytest

from cfmesh_autogui.core.boundary_reader import PatchInfo, parse_boundary
from cfmesh_autogui.core.case_setup import (
    infer_patch_roles,
    mesh_bounds,
    set_wall_patch_types,
    suggest_flow_direction,
)

# Box pipe: x in [0,1], y in [-0.1,0.1], z in [-0.1,0.1].
_CORNERS = [
    (0.0, -0.1, -0.1), (0.0, -0.1, 0.1), (0.0, 0.1, -0.1), (0.0, 0.1, 0.1),
    (1.0, -0.1, -0.1), (1.0, -0.1, 0.1), (1.0, 0.1, -0.1), (1.0, 0.1, 0.1),
]
# Faces ordered: 4 walls then inlet (x=0) then outlet (x=1). Winding chosen
# so OpenFOAM's outward convention holds (normal points out of the domain).
_FACES = [
    (0, 1, 3, 2),  # y = -0.1 plane (outward -y)
    (4, 6, 7, 5),  # y =  0.1 plane (outward +y)
    (0, 4, 5, 1),  # z = -0.1 plane (outward -z)
    (2, 3, 7, 6),  # z =  0.1 plane (outward +z)
    (2, 0, 1, 3),  # x = 0     inlet cap (outward -x)
    (6, 7, 5, 4),  # x = 1     outlet cap (outward +x)
]
_PATCHES = [
    ("surface_1", "patch", 4, 0),
    ("surface_2", "patch", 1, 4),
    ("surface_3", "patch", 1, 5),
]


def _write_polymesh(case_dir) -> list[PatchInfo]:
    poly = case_dir / "constant" / "polyMesh"
    poly.mkdir(parents=True, exist_ok=True)

    def _header(obj: str, n: int, body: str) -> str:
        return (
            f"FoamFile {{ version 2.0; format ascii; class {obj}; "
            f"object {obj}; }}\n"
            f"{n}\n(\n{body})\n"
        )

    pts = "\n".join(f"({x} {y} {z})" for x, y, z in _CORNERS)
    (poly / "points").write_text(_header("vectorField", len(_CORNERS), pts),
                                 encoding="ascii")
    faces = "\n".join(
        f"4({a} {b} {c} {d})" for a, b, c, d in _FACES
    )
    (poly / "faces").write_text(_header("faceList", len(_FACES), faces),
                                encoding="ascii")
    boundary = (
        "FoamFile { version 2.0; format ascii; class polyBoundaryMesh; "
        "object boundary; }\n"
        "3\n(\n"
    )
    for name, ptype, n_faces, start in _PATCHES:
        boundary += (
            f"    {name}\n    {{\n"
            f"        type            {ptype};\n"
            f"        nFaces          {n_faces};\n"
            f"        startFace       {start};\n"
            "    }\n"
        )
    boundary += ")\n"
    (poly / "boundary").write_text(boundary, encoding="ascii")
    return parse_boundary(poly / "boundary")


def test_infer_patch_roles_recovers_inlet_outlet_wall(tmp_path):
    patches = _write_polymesh(tmp_path)
    roles = infer_patch_roles(tmp_path, patches, flow_direction=(1.0, 0.0, 0.0))
    assert roles == {"surface_1": "wall", "surface_2": "inlet",
                     "surface_3": "outlet"}


def test_infer_patch_roles_respects_named_patches(tmp_path):
    # A patch literally named "inlet" must stay inlet regardless of geometry.
    patches = _write_polymesh(tmp_path)
    patches = [
        PatchInfo("inlet", p.patch_type, p.n_faces, p.start_face)
        if p.name == "surface_2" else p
        for p in patches
    ]
    roles = infer_patch_roles(tmp_path, patches, flow_direction=(1.0, 0.0, 0.0))
    assert roles["inlet"] == "inlet"
    assert roles["surface_3"] == "outlet"


def test_set_wall_patch_types_writes_wall(tmp_path):
    patches = _write_polymesh(tmp_path)
    set_wall_patch_types(tmp_path, ["surface_1"])
    text = (tmp_path / "constant" / "polyMesh" / "boundary").read_text(
        encoding="ascii"
    )
    block = text.split("surface_1", 1)[1].split("}", 1)[0]
    assert "type            wall;" in block
    block3 = text.split("surface_3", 1)[1].split("}", 1)[0]
    assert "type            patch;" in block3  # untouched


def test_mesh_bounds_and_flow_direction(tmp_path):
    _write_polymesh(tmp_path)
    assert mesh_bounds(tmp_path) == pytest.approx(
        (0.0, 1.0, -0.1, 0.1, -0.1, 0.1)
    )
    assert suggest_flow_direction(tmp_path) == (1.0, 0.0, 0.0)
