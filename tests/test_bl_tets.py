"""W13: direct boundary layers on a tetrahedral polyMesh (opt-in)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from polyfoammesh.core.bl_tets import build_bl_on_tets  # noqa: E402
from polyfoammesh.core.foam_mesh_io import read_polymesh  # noqa: E402
from polyfoammesh.core.mesh_config import MeshConfig  # noqa: E402
from polyfoammesh.core.mesher_registry import MODE_BY_KEY  # noqa: E402
from test_bl_poly import _cube_points_tets, _mesh_ok  # noqa: E402
from test_tet_poly_dual import build_tet_case  # noqa: E402


def _cube_case(root: Path, patch: str = "wall") -> Path:
    pts, tets = _cube_points_tets()
    return build_tet_case(root, pts, tets, boundary_patch=patch)


def _n_cells(case: Path) -> int:
    _p, _f, owner, neigh, _patches = read_polymesh(
        case / "constant" / "polyMesh"
    )
    return int(max(owner.max(), neigh.max())) + 1


def test_direct_tet_bl_builds_prisms_and_keeps_tet_core(tmp_path):
    case = _cube_case(tmp_path / "cube")
    _p, _f, _o, _n, before_patches = read_polymesh(
        case / "constant" / "polyMesh"
    )
    wall_faces = sum(p["nFaces"] for p in before_patches)
    res = build_bl_on_tets(
        case, n_layers=3, first_height=0.02, apply_to_all=True,
    )
    assert res.success, res.errors
    assert res.n_prism_cells == 3 * wall_faces
    assert res.n_wall_faces == wall_faces
    assert res.coverage > 0.99
    assert res.provenance["dual_used"] is False
    # 12 tets + 36 prisms: the volume is still tetrahedral, not a dual.
    assert _n_cells(case) == 12 + 36
    _mesh_ok(case / "constant" / "polyMesh")
    _p, _f, _o, _n, after_patches = read_polymesh(
        case / "constant" / "polyMesh"
    )
    assert [(p["name"], p["nFaces"]) for p in after_patches] == [
        (p["name"], p["nFaces"]) for p in before_patches
    ]


def test_direct_tet_bl_selects_the_named_wall_patch(tmp_path):
    case = _cube_case(tmp_path / "cube_auto")
    res = build_bl_on_tets(
        case, n_layers=1, first_height=0.01, patch_names=None,
    )
    assert res.success, res.errors
    assert res.wall_patches == ["wall"]
    assert res.n_prism_cells == res.n_wall_faces
    assert res.report["per_patch"]["wall"]["bl_selected"] is True


def test_direct_tet_bl_refuses_when_no_wall_is_identified(tmp_path):
    case = _cube_case(tmp_path / "inlet_only", patch="inlet")
    with pytest.raises(ValueError, match="no safe wall patch"):
        build_bl_on_tets(case, n_layers=1, first_height=0.01)


def test_direct_tet_bl_does_not_change_the_opt_in_default():
    assert MeshConfig().mesher != "bl_on_tets"
    assert MODE_BY_KEY["bl_on_tets"].experimental is True
    assert MODE_BY_KEY["bl_on_tets"].default is False
    assert MeshConfig(mesher="bl_on_tets").mesher == "bl_on_tets"


def test_direct_tet_bl_reports_measured_h1(tmp_path):
    case = _cube_case(tmp_path / "cube_report")
    res = build_bl_on_tets(
        case, n_layers=2, first_height=0.01, apply_to_all=True,
    )
    assert res.success, res.errors
    assert res.h1_median is not None
    assert res.h1_ratio is not None
    assert 0.0 < res.h1_ratio < 3.0
    assert res.report["n_wall_faces"] == res.n_wall_faces


def test_direct_tet_bl_missing_polymesh_is_explicit(tmp_path):
    with pytest.raises(FileNotFoundError, match="complete polyMesh"):
        build_bl_on_tets(
            tmp_path / "missing", n_layers=1, first_height=0.01,
        )


def test_bl_on_tets_worker_emits_a_result(tmp_path):
    from polyfoammesh.core.openfoam_runner import BlOnTetsWorker

    case = _cube_case(tmp_path / "cube_worker")
    worker = BlOnTetsWorker(
        case, {"nLayers": 1, "firstLayerThickness": 0.01,
               "thicknessRatio": 1.2},
        apply_to_all=True,
    )
    results = []
    worker.finished.connect(results.append)
    worker.run()
    assert results
    assert results[0].success is True
    assert results[0].n_prism_cells > 0


def test_bl_on_tets_worker_is_cancellable(tmp_path):
    """A pre-cancelled worker must fail fast with 'cancelled', not run."""
    from polyfoammesh.core.openfoam_runner import BlOnTetsWorker

    case = _cube_case(tmp_path / "cube_cancel")
    worker = BlOnTetsWorker(
        case, {"nLayers": 5, "firstLayerThickness": 0.01,
               "thicknessRatio": 1.2},
        apply_to_all=True,
    )
    worker.cancel()
    results = []
    worker.finished.connect(results.append)
    worker.run()
    assert results
    assert results[0].success is False
    assert any("cancel" in str(e).lower() for e in results[0].errors)
