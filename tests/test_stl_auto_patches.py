import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import trimesh

from cfmesh_autogui.core.geometry import split_single_stl_patch


def _cylinder_along_z() -> trimesh.Trimesh:
    cyl = trimesh.creation.cylinder(radius=0.5, height=2.0, sections=48)
    cyl.merge_vertices()
    cyl.metadata["name"] = "pipe"
    return cyl


def test_cylinder_stl_splits_into_inlet_outlet_wall():
    mesh = _cylinder_along_z()
    parts = split_single_stl_patch(mesh)

    names = sorted(p.metadata["name"] for p in parts)
    assert names == ["inlet", "outlet", "wall"], f"got {names}"

    # Watertightness preserved: total surface area must match the original
    area_orig = float(mesh.area)
    area_parts = sum(float(p.area) for p in parts)
    assert abs(area_parts - area_orig) < 1e-6 * max(area_orig, 1.0)

    # Cap placement: inlet at min Z, outlet at max Z
    by_name = {p.metadata["name"]: p for p in parts}
    zmin = mesh.bounds[0][2]
    zmax = mesh.bounds[1][2]
    assert abs(by_name["inlet"].bounds[1][2] - zmin) < 1e-6
    assert abs(by_name["outlet"].bounds[0][2] - zmax) < 1e-6


def test_sphere_stl_stays_single_patch():
    sph = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
    sph.merge_vertices()
    sph.metadata["name"] = "ball"

    parts = split_single_stl_patch(sph)
    assert len(parts) == 1
    assert parts[0].metadata["name"] == "ball"


def test_box_splits_with_caps():
    box = trimesh.creation.box(extents=[1.0, 1.0, 3.0])
    box.merge_vertices()
    box.metadata["name"] = "boxy"

    parts = split_single_stl_patch(box)
    names = sorted(p.metadata["name"] for p in parts)
    assert names == ["inlet", "outlet", "wall"], f"got {names}"


def test_original_mesh_never_mutated():
    mesh = _cylinder_along_z()
    before_faces = len(mesh.faces)
    before_name = mesh.metadata["name"]
    split_single_stl_patch(mesh)
    assert len(mesh.faces) == before_faces
    assert mesh.metadata["name"] == before_name
