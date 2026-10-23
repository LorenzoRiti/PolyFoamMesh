"""W11: physical-region to OpenFOAM-patch mapping at the source.

These tests use synthetic multi-region .msh files with meshio, so they do
not need WSL/OpenFOAM.  They pin the root defect: untagged 2D surface
elements must not erase the owning 3D region tag, and 2D/3D names may share
a numeric tag without overwriting each other.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

meshio = pytest.importorskip("meshio")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core import foam_mesh_io  # noqa: E402
from polyfoammesh.core.mesh_converter import msh_to_of_polymesh  # noqa: E402


def _two_tets():
    points = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0],
        [3.0, 0.0, 0.0], [4.0, 0.0, 0.0], [3.0, 1.0, 0.0], [3.0, 0.0, 1.0],
    ], dtype=np.float64)
    return points, np.array([[0, 1, 2, 3], [4, 5, 6, 7]], dtype=np.int64)


def _write_msh(tmp_path, *, triangles=None, tri_tags=None, tet_tags,
               field_data):
    points, tets = _two_tets()
    cells = [("tetra", tets)]
    cell_data = {"gmsh:physical": [np.asarray(tet_tags, dtype=np.int64)]}
    if triangles is not None:
        cells.insert(0, ("triangle", np.asarray(triangles, dtype=np.int64)))
        cell_data["gmsh:physical"].insert(
            0, np.asarray(tri_tags, dtype=np.int64),
        )
    path = tmp_path / "multi.msh"
    meshio.write(
        path,
        meshio.Mesh(
            points, cells, cell_data=cell_data, field_data=field_data,
        ),
        file_format="gmsh22",
    )
    return path


def _read_patches(case):
    return foam_mesh_io.read_polymesh(case / "constant" / "polyMesh")


def _boundary_count(faces, neigh) -> int:
    internal = sum(1 for n in neigh if int(n) >= 0)
    return len(faces) - internal


def test_volume_tags_recover_regions_without_2d_groups(tmp_path):
    path = _write_msh(
        tmp_path, tet_tags=[1, 2],
        field_data={
            "region_a": np.array([1, 3]),
            "region_b": np.array([2, 3]),
        },
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(path, case)
    _, faces, owner, neigh, patches = _read_patches(case)
    names = {p["name"]: p["nFaces"] for p in patches}
    assert names == {"region_a": 4, "region_b": 4}
    assert all(int(n) >= 0 for n in neigh)
    assert sum(names.values()) == _boundary_count(faces, neigh)


def test_untagged_surface_does_not_shadow_volume_region(tmp_path):
    points, tets = _two_tets()
    # Six boundary triangles per disconnected tet, all physically untagged.
    tris = []
    for t in tets:
        a, b, c, d = t
        tris.extend([[a, c, b], [a, b, d], [b, c, d], [a, d, c]])
    path = _write_msh(
        tmp_path, triangles=tris, tri_tags=[0] * len(tris),
        tet_tags=[1, 2],
        field_data={
            "region_a": np.array([1, 3]),
            "region_b": np.array([2, 3]),
        },
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(path, case)
    _, faces, _owner, neigh, patches = _read_patches(case)
    names = {p["name"]: p["nFaces"] for p in patches}
    assert names == {"region_a": 4, "region_b": 4}
    assert "surface_0" not in names
    assert sum(names.values()) == _boundary_count(faces, neigh)


def test_2d_and_3d_same_tag_do_not_overwrite_names(tmp_path):
    points, tets = _two_tets()
    a, b, c, d = tets[0]
    tris = [[a, c, b], [a, b, d], [b, c, d], [a, d, c]]
    path = _write_msh(
        tmp_path, triangles=tris, tri_tags=[1] * 4, tet_tags=[1, 2],
        field_data={
            "wall": np.array([1, 2]),
            "fluid": np.array([1, 3]),
            "region_b": np.array([2, 3]),
        },
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(path, case)
    _, _faces, _owner, _neigh, patches = _read_patches(case)
    names = {p["name"] for p in patches}
    assert "wall" in names
    assert "region_b" in names
    assert "fluid" not in names  # the surface group owns tag 1 here
    assert len(names) == len(patches)  # no duplicate name


def test_surface_and_volume_tag_collision_keeps_both_patches(tmp_path):
    points, tets = _two_tets()
    # Tet A is region 1 with no 2D groups (all faces fall back to `fluid`).
    # Tet B is region 2; one of its faces carries surface tag 1 named
    # `wall`, while the rest are untagged and fall back to `solid`.
    a, b, c, d = tets[1]
    tris = [[a, c, b]]
    path = _write_msh(
        tmp_path, triangles=tris, tri_tags=[1], tet_tags=[1, 2],
        field_data={
            "wall": np.array([1, 2]),
            "fluid": np.array([1, 3]),
            "solid": np.array([2, 3]),
        },
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(path, case)
    _, faces, _owner, neigh, patches = _read_patches(case)
    names = {p["name"]: p["nFaces"] for p in patches}
    assert names == {"fluid": 4, "solid": 3, "wall": 1}
    assert sum(names.values()) == _boundary_count(faces, neigh)


def test_duplicate_sanitized_names_merge_into_one_patch(tmp_path):
    path = _write_msh(
        tmp_path, tet_tags=[1, 2],
        field_data={
            "wall-a": np.array([1, 3]),
            "wall a": np.array([2, 3]),
        },
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(path, case)
    _, faces, _owner, neigh, patches = _read_patches(case)
    assert len(patches) == 1
    assert patches[0]["name"] == "wall_a"
    assert patches[0]["nFaces"] == 8
    assert patches[0]["nFaces"] == _boundary_count(faces, neigh)


def test_nonmanifold_face_fails_with_precise_error(tmp_path):
    points = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0], [0.0, 0.0, -1.0], [0.0, 0.0, 2.0],
    ], dtype=np.float64)
    tets = np.array(
        [[0, 1, 2, 3], [0, 2, 1, 4], [0, 1, 2, 5]], dtype=np.int64,
    )
    path = tmp_path / "nonmanifold.msh"
    meshio.write(
        path,
        meshio.Mesh(
            points, [("tetra", tets)],
            cell_data={"gmsh:physical": [np.array([1, 1, 1])]},
            field_data={"fluid": np.array([1, 3])},
        ),
        file_format="gmsh22",
    )
    with pytest.raises(RuntimeError, match="Non-manifold mesh"):
        msh_to_of_polymesh(path, tmp_path / "case")


def test_untagged_mesh_gets_default_with_all_boundary_faces(tmp_path):
    path = _write_msh(
        tmp_path, tet_tags=[0, 0], field_data={},
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(path, case)
    _, faces, _owner, neigh, patches = _read_patches(case)
    assert len(patches) == 1
    assert patches[0]["name"] == "default"
    assert patches[0]["nFaces"] == _boundary_count(faces, neigh)


def test_per_patch_report_lists_selected_and_cap_patches(tmp_path):
    from polyfoammesh.core.openfoam_runner import boundary_layer_report

    path = _write_msh(
        tmp_path, tet_tags=[1, 2],
        field_data={
            "region_a": np.array([1, 3]),
            "region_b": np.array([2, 3]),
        },
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(path, case)
    rep = boundary_layer_report(
        case, requested_h1=0.01,
        bl_patch_names=["region_a"],
    )
    assert rep["n_boundary_faces"] == 8
    assert rep["n_wall_faces"] == 4
    assert set(rep["per_patch"]) == {"region_a", "region_b"}
    assert rep["per_patch"]["region_a"]["bl_selected"] is True
    assert rep["per_patch"]["region_b"]["bl_selected"] is False
    assert rep["per_patch"]["region_b"]["coverage"] == 0.0


def test_zero_config_selection_uses_volume_region_names(tmp_path):
    from polyfoammesh.core.openfoam_runner import poly_bl_patch_selection

    path = _write_msh(
        tmp_path, tet_tags=[1, 2],
        field_data={
            "region_a": np.array([1, 3]),
            "region_b": np.array([2, 3]),
        },
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(path, case)
    selected, apply_to_all, notes = poly_bl_patch_selection(case)
    assert selected is not None
    assert set(selected) == {"region_a", "region_b"}
    assert apply_to_all is False


def test_viewer_patch_map_matches_report_patch_names(tmp_path):
    from polyfoammesh.core.openfoam_runner import boundary_layer_report
    from polyfoammesh.gui.viewer_widget import read_openfoam_mesh_patches

    path = _write_msh(
        tmp_path, tet_tags=[1, 2],
        field_data={
            "region_a": np.array([1, 3]),
            "region_b": np.array([2, 3]),
        },
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(path, case)
    viewer_patches = read_openfoam_mesh_patches(case)
    report = boundary_layer_report(
        case, requested_h1=0.01, bl_patch_names=["region_a"],
    )
    assert viewer_patches is not None
    assert set(viewer_patches) == set(report["per_patch"])
    assert set(report["per_patch"]) == {"region_a", "region_b"}


def test_shared_wall_classifier_keeps_pressure_side_as_wall():
    from polyfoammesh.core.patch_roles import is_wall_patch

    # W11 zero-config: the old GUI blocklist dropped any name containing
    # "pressure", including a blade pressure side (a wall).
    assert is_wall_patch("pressure_side") is True
    assert is_wall_patch("pressure_inlet") is False
    assert is_wall_patch("wall_02") is True


def test_patch_names_are_sanitized_and_nonempty(tmp_path):
    path = _write_msh(
        tmp_path, tet_tags=[1, 2], field_data={},
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(
        path, case, boundary_patches={1: "hot fluid!", 2: "  "},
    )
    _, _faces, _owner, _neigh, patches = _read_patches(case)
    names = [p["name"] for p in patches]
    assert all(names)
    assert set(names) == {"hot_fluid", "patch_2"}
    assert len(names) == len(set(names))


def test_zero_config_explicit_mapping_still_wins_and_is_sanitized(tmp_path):
    path = _write_msh(
        tmp_path, tet_tags=[1, 2], field_data={},
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(
        path, case, boundary_patches={1: "hot fluid", 2: "cold-fluid"},
    )
    _, _faces, _owner, _neigh, patches = _read_patches(case)
    assert {p["name"] for p in patches} == {"hot_fluid", "cold_fluid"}
