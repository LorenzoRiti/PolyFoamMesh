"""Snappy base/expert dict builders: reproducible, versioned templates."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.snappy_baseline import (  # noqa: E402
    build_block_mesh_dict, build_snappy_dict, cell_size_to_level,
    find_location_in_mesh,
)


def _text(mode: str) -> str:
    return "\n".join(build_snappy_dict(mode, (0.0, 0.0, 0.0), 0.02, 0.005))


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        build_snappy_dict("expert2", (0, 0, 0), 0.02, 0.005)


def test_cell_size_to_level_halves_until_min_cell() -> None:
    assert cell_size_to_level(0.02, 0.02) == 1      # ratio 1 -> max(1, 0)
    assert cell_size_to_level(0.005, 0.02) == 2     # ratio 4 -> level 2
    assert cell_size_to_level(0.0, 0.02) == 1       # degenerate -> 1


def test_base_dict_is_manual_style() -> None:
    t = _text("base")
    assert "castellatedMesh true;" in t
    assert "snap            true;" in t or "snap true;" in t.replace("  ", " ")
    assert "addLayers       true;" in t or "addLayers true;" in t.replace("  ", " ")
    # base = pure surface refinement: NO explicit feature-edge file
    assert "surface.eMesh" not in t
    assert "resolveFeatureAngle 30;" in t
    # manual/tutorial layer defaults
    assert "relativeSizes true;" in t
    assert "nSurfaceLayers 5;" in t
    assert "expansionRatio 1.3;" in t
    assert "finalLayerThickness 0.3;" in t
    assert "minThickness 0.1;" in t


def test_expert_dict_adds_features_and_tuned_layers() -> None:
    t = _text("expert")
    assert "surface.eMesh" in t
    assert "resolveFeatureAngle 60;" in t
    assert "explicitFeatureSnap true;" in t
    assert "nSurfaceLayers 8;" in t
    assert "expansionRatio 1.2;" in t
    assert "maxThicknessToMedialRatio 0.35;" in t
    assert "nLayerIter 80;" in t


def test_both_modes_have_quality_controls_and_sane_cells() -> None:
    for mode in ("base", "expert"):
        t = _text(mode)
        assert "maxNonOrtho 65;" in t
        assert "maxInternalSkewness 4;" in t
        assert "maxGlobalCells" in t
        # OpenFOAM v2512 refuses snappyHexMeshDict without this entry
        # (FOAM FATAL IO ERROR: Entry 'allowFreeStandingZoneFaces' not found)
        assert "allowFreeStationZoneFaces" not in t  # typo guard
        assert "allowFreeStandingZoneFaces true;" in t
        # OF v2512 meshQualityControls requires these entries (the harness
        # smoke run failed with "Entry 'minFaceWeight' not found")
        for required in (
            "minFaceWeight", "minTwist", "minDeterminant", "minVolRatio",
            "minTriangleTwist",
        ):
            assert required in t, f"{required} missing from {mode} dict"
        # OF v2512 layerParameters.C reads addLayersControls via
        # meshRefinement::get -> the harness smoke run died with
        # "FOAM FATAL IO ERROR: Entry 'featureAngle' not found in
        # dictionary ... addLayersControls" (0 cells, snappy stage only).
        # Tutorial defaults: nGrow 0, featureAngle 60.
        assert "    nGrow 0;" in t, f"nGrow missing from {mode} dict"
        assert "    featureAngle 60;" in t, f"featureAngle missing from {mode} dict"


def test_prepare_snappy_case_writes_surface_feature_extract_dict(tmp_path) -> None:
    """OF v2512 surfaceFeatureExtract reads system/surfaceFeatureExtractDict."""
    import trimesh
    from benchmarks.snappy_baseline import prepare_snappy_case

    stl = tmp_path / "surface.stl"
    mesh = trimesh.creation.box(extents=(0.1, 0.1, 0.1))
    mesh.export(stl)
    case = tmp_path / "case"
    case.mkdir()
    prepare_snappy_case(case, stl, [mesh], "expert", 0.02, 0.005)
    d = case / "system" / "surfaceFeatureExtractDict"
    assert d.exists()
    text = d.read_text(encoding="ascii")
    assert "surface.stl" in text
    assert "extractionMethod extractFromSurface;" in text
    # base mode also gets the dict (harmless; used only when extract runs)
    assert (case / "constant" / "triSurface" / "surface.stl").exists()
    assert (case / "system" / "blockMeshDict").exists()
    assert (case / "system" / "snappyHexMeshDict").exists()


def test_block_mesh_dict_has_hex_block_scaled_by_max_cell() -> None:
    lines = build_block_mesh_dict((0, 0, 0), (0.1, 0.1, 0.05), 0.02)
    t = "\n".join(lines)
    assert "blocks" in t and "hex (0 1 2 3 4 5 6 7)" in t
    assert "simpleGrading (1 1 1)" in t
    # 0.1/0.02 = 5, 0.05/0.02 = 2.5 -> ceil -> at least (5 5 3)
    assert "(5 5 3)" in t or "(5 5 2)" in t


def test_find_location_returns_interior_point_for_cube() -> None:
    import trimesh
    cube = trimesh.creation.box(extents=(0.1, 0.1, 0.1))
    loc = find_location_in_mesh([cube])
    # at minimum inside bbox
    assert all(abs(c) <= 0.05 + 1e-9 for c in loc)
