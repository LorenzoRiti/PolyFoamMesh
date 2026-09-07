"""Tests for the geometry pipeline module."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("geometry_pipeline")
GeometryInfo = _mod.GeometryInfo
HealReport = _mod.HealReport
FeatureReport = _mod.FeatureReport
GeometryPipelineResult = _mod.GeometryPipelineResult
GeometryPipeline = _mod.GeometryPipeline


def test_geometry_info_defaults():
    g = GeometryInfo()
    assert g.detected_unit == "m"
    assert g.bbox == (0, 0, 0)
    assert g.watertight


def test_geometry_info_with_data():
    g = GeometryInfo(
        file_path="/tmp/test.step", format="step", n_patches=3,
        patch_names=["wall", "inlet", "outlet"],
    )
    assert g.file_path == "/tmp/test.step"
    assert g.n_patches == 3


def test_heal_report_defaults():
    h = HealReport()
    assert h.holes_filled == 0
    assert h.slivers_removed == 0
    assert not h.watertight


def test_feature_report_defaults():
    f = FeatureReport()
    assert f.n_sharp_edges == 0
    assert f.min_curvature_radius == 0.0
    assert f.n_gap_regions == 0


def test_pipeline_result_defaults():
    r = GeometryPipelineResult()
    assert not r.success
    assert r.errors == []


def test_pipeline_init():
    gp = GeometryPipeline()
    assert gp.MAX_GAP == 0.0001
    assert gp.MAX_HOLE == 0.005


def test_pipeline_run_nonexistent():
    gp = GeometryPipeline()
    r = gp.run("Z:\\nonexistent.step", heal=False, extract_features=False, classify_patches=False)
    assert not r.success
    assert len(r.errors) > 0


def test_pipeline_detect_unit():
    gp = GeometryPipeline()
    import trimesh

    # Small mesh -> mm
    small = trimesh.creation.box(extents=[0.005, 0.005, 0.005])
    assert gp._detect_unit([small]) == "mm"

    # Normal -> m
    normal = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    assert gp._detect_unit([normal]) == "m"

    # Large -> cm
    large = trimesh.creation.box(extents=[500, 500, 500])
    assert gp._detect_unit([large]) == "cm"


def test_unit_scales():
    from cfmesh_autogui.commercial.geometry_pipeline import _UNIT_SCALES
    assert _UNIT_SCALES["mm"] == 0.001
    assert _UNIT_SCALES["m"] == 1.0
    assert _UNIT_SCALES["inch"] == 0.0254


def test_import_stl_multisolid():
    gp = GeometryPipeline()
    import trimesh, tempfile, os

    # Create a multi-solid STL
    box1 = trimesh.creation.box(extents=[1, 1, 1])
    box1.metadata["name"] = "solid_a"
    box2 = trimesh.creation.box(extents=[1, 1, 1])
    box2.metadata["name"] = "solid_b"
    box2.apply_translation([2, 0, 0])

    tmp = Path(tempfile.mkdtemp(dir=os.environ.get("TEMP", "/tmp")))
    stl_path = tmp / "multisolid.stl"
    combined = trimesh.util.concatenate([box1, box2])
    combined.export(str(stl_path), file_type="stl")

    meshes = gp._import_stl(stl_path)
    assert len(meshes) >= 1
    # Cleanup
    import shutil; shutil.rmtree(tmp, ignore_errors=True)


def test_extract_features():
    gp = GeometryPipeline()
    import trimesh
    mesh = trimesh.creation.box(extents=[1, 1, 1])
    report = gp._extract_features([mesh])
    assert isinstance(report, FeatureReport)
    assert report.n_sharp_edges >= 0


def test_classify_patches():
    gp = GeometryPipeline()
    import trimesh
    m1 = trimesh.creation.box(extents=[1, 1, 1])
    m1.metadata["name"] = "body"
    m2 = trimesh.creation.box(extents=[.5, .5, .5])
    m2.metadata["name"] = "something"
    m2.apply_translation([1.5, 0, 0])

    geo = GeometryInfo(bbox=(2.0, 1.0, 1.0))
    gp._classify_patches([m1, m2], geo)
    # Both should be classified as wall or symmetry
    assert m1.metadata["name"] in ("wall", "symmetry", "inlet", "outlet")


def test_heal_report_face_reduction():
    r = HealReport(original_faces=1000, final_faces=800)
    reduction = (1 - r.final_faces / r.original_faces) * 100
    assert round(reduction, 1) == 20.0


if __name__ == "__main__":
    import os, shutil
    test_geometry_info_defaults()
    test_geometry_info_with_data()
    test_heal_report_defaults()
    test_feature_report_defaults()
    test_pipeline_result_defaults()
    test_pipeline_init()
    test_pipeline_run_nonexistent()
    test_pipeline_detect_unit()
    test_unit_scales()
    test_import_stl_multisolid()
    test_extract_features()
    test_classify_patches()
    test_heal_report_face_reduction()
    # Cleanup
    tmp = Path(os.environ.get("TEMP", "/tmp"))
    for d in tmp.glob("tmp*"):
        if d.is_dir(): shutil.rmtree(d, ignore_errors=True)
    print("ALL PASS")
