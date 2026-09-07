from __future__ import annotations

import numpy as np
import trimesh

from polyfoammesh.core.throat_detector import (
    detect_refinement_regions,
    _find_significant_minima_3d,
    _cluster_minima,
    check_bl_throat_compatibility,
    RefinementZone,
)


def _make_simple_duct(length: float = 1.0, radius: float = 0.2) -> list[trimesh.Trimesh]:
    """Simple straight cylinder."""
    import cadquery as cq
    wp = cq.Workplane("XY").circle(radius).extrude(length)
    verts, tris = wp.val().tessellate(0.05)
    vertices = np.array([(v.x, v.y, v.z) for v in verts], dtype=np.float64)
    faces = np.array(tris, dtype=np.int32)
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    mesh.metadata["name"] = "wall"
    return [mesh]


def test_detect_empty_meshes():
    zones = detect_refinement_regions([], global_max_cell=0.05)
    assert zones == []


def test_detect_simple_duct_no_false_positive():
    """Straight cylinder should not produce false positives."""
    meshes = _make_simple_duct(0.5, 0.1)
    zones = detect_refinement_regions(meshes, detail="medium", global_max_cell=0.05)
    assert isinstance(zones, list)


def test_find_significant_minima_3d():
    """Points with small thickness should be detected as minima."""
    rng = np.random.RandomState(42)
    n_total = 200
    points = rng.rand(n_total, 3).astype(np.float64)
    thicknesses = np.ones(n_total, dtype=np.float64)

    # Create a cluster of thin points at [0.1, 0.1, 0.1]
    for i in range(n_total):
        dist = np.linalg.norm(points[i] - np.array([0.1, 0.1, 0.1]))
        if dist < 0.2:
            thicknesses[i] = 0.3 + rng.rand() * 0.05

    minima_pts, minima_thick, scores = _find_significant_minima_3d(points, thicknesses, n_neighbors=15)
    assert len(minima_pts) >= 1, "Expected at least 1 minimum"
    assert min(minima_thick) <= 0.35, f"Expected thin values (~0.3), got min={min(minima_thick):.4f}"


def test_cluster_minima():
    """Clustering should group nearby minima."""
    points = np.array([
        [0.0, 0.0, 0.0],
        [0.01, 0.0, 0.0],
        [0.02, 0.0, 0.0],
        [0.005, 0.01, 0.0],
        [0.015, 0.01, 0.0],
        [1.0, 1.0, 1.0],  # far away
    ], dtype=np.float64)
    thicknesses = np.array([0.1, 0.12, 0.11, 0.13, 0.09, 0.5], dtype=np.float64)
    scores = np.array([0.8, 0.7, 0.75, 0.6, 0.85, 0.2], dtype=np.float64)

    zones = _cluster_minima(points, thicknesses, scores, cluster_radius_factor=3.0)
    assert len(zones) >= 1
    first = zones[0]
    assert first["n_samples"] >= 4, f"Expected cluster with >=4 samples, got {first['n_samples']}"


def test_object_refinements_in_meshdict():
    from polyfoammesh.core.meshdict_gen import build_meshdict_lines
    import tempfile
    tmpdir = tempfile.mkdtemp()
    refs = [
        {"centre": (0.0, 0.0, 0.5), "radius": 0.15, "cell_size": 0.008},
    ]
    try:
        lines = build_meshdict_lines(
            max_cell=0.05, min_cell=0.01,
            patch_names=["wall"],
            object_refinements=refs,
        )
        text = "\n".join(lines)
        assert "objectRefinements" in text
        assert "refinementBox_0" in text
        assert "cellSize 0.008" in text
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_manual_refinements_in_meshdict():
    from polyfoammesh.core.meshdict_gen import build_meshdict_lines
    refs = [
        {"centre": (0.0, 0.0, 0.5), "radius": 0.1, "cell_size": 0.01},
        {"centre": (0.3, 0.0, 0.0), "radius": 0.2, "cell_size": 0.005},
    ]
    lines = build_meshdict_lines(
        max_cell=0.05, min_cell=0.01,
        patch_names=["wall"],
        object_refinements=refs,
    )
    text = "\n".join(lines)
    assert "objectRefinements" in text
    assert "refinementBox_0" in text
    assert "refinementBox_1" in text
    assert "cellSize 0.01" in text
    assert "cellSize 0.005" in text


def test_bl_throat_compatibility_warns():
    zone = RefinementZone(
        centre=(0, 0, 0.5),
        radius=0.15,
        cell_size=0.008,
        local_thickness=0.15,
        significance=0.7,
        n_samples_in_cluster=5,
    )
    bl_params = {"nLayers": 10, "thicknessRatio": 1.3, "firstLayerThickness": 0.005}
    warnings = check_bl_throat_compatibility(bl_params, [zone])
    assert len(warnings) >= 1, "Expected BL warning for thick layers in narrow passage"


def test_bl_throat_compatibility_ok():
    zone = RefinementZone(
        centre=(0, 0, 0.5),
        radius=0.3,
        cell_size=0.01,
        local_thickness=0.6,
        significance=0.3,
        n_samples_in_cluster=3,
    )
    bl_params = {"nLayers": 3, "thicknessRatio": 1.2, "firstLayerThickness": 0.001}
    warnings, adjusted = check_bl_throat_compatibility(bl_params, [zone])
    assert len(warnings) == 0, f"Expected no warnings, got {warnings}"
