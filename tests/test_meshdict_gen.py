import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import os as _os
try:
    import OCP as _ocp
    _d = _os.path.dirname(_ocp.__file__)
    if _d not in _os.environ.get("PATH", ""):
        _os.environ["PATH"] = _d + _os.pathsep + _os.environ.get("PATH", "")
except Exception:
    pass

from polyfoammesh.core.meshdict_gen import build_meshdict_lines, write_meshdict


def test_basic_has_robust_flags():
    lines = build_meshdict_lines()
    content = "\n".join(lines)
    assert "keepCellsIntersectingBoundary 1" in content
    assert "allowDisconnected 1" in content
    assert "maxNumIterations 100" in content
    assert "surfaceFile" in content
    assert "maxCellSize" in content
    assert "minCellSize" in content
    print("PASS: robust flags present")


def test_basic_no_bl():
    lines = build_meshdict_lines()
    content = "\n".join(lines)
    assert "boundaryLayers" not in content
    print("PASS: no boundary layers by default")


def test_with_bl():
    import re
    lines = build_meshdict_lines(max_cell=0.1, bl_params={
        "nLayers": 3,
        "thicknessRatio": 1.2,             # growth ratio
        "firstLayerThickness": 5e-4,       # absolute metres
        "wallPatches": ["wall"],
    })
    content = "\n".join(lines)
    assert "boundaryLayers" in content
    assert "patchBoundaryLayers" in content
    assert re.search(r"nLayers\s+3;", content)
    # thicknessRatio must be the GROWTH ratio, not a fraction
    assert re.search(r"thicknessRatio\s+1\.2;", content)
    # the absolute first layer must actually be emitted, or the y+ target the
    # app computes is silently discarded by cfMesh
    assert re.search(r"maxFirstLayerThickness\s+0\.0005;", content)
    assert "optimiseLayer" in content
    assert "expansionRatio" not in content
    print("PASS: boundary layers use real cfMesh semantics")


def test_bl_fraction_as_thickness_ratio_does_not_collapse_layers():
    """Guard against the regression: a first-layer fraction (<=1) passed as
    thicknessRatio must NOT reach cfMesh as a sub-1 growth ratio (which
    collapses the layers). It is clamped to a valid growth ratio instead."""
    import re
    lines = build_meshdict_lines(max_cell=0.1, bl_params={
        "nLayers": 3, "thicknessRatio": 0.005, "wallPatches": ["wall"],
    })
    content = "\n".join(lines)
    m = re.search(r"thicknessRatio\s+([\d.]+);", content)
    assert m and float(m.group(1)) > 1.0, "growth ratio must stay > 1"


def test_with_patches():
    """Each patchCellSize entry must be a SUB-DICTIONARY containing
    `cellSize`, not a flat scalar (`"wall" 0.02;`) — cfMesh's
    checkMeshDict::updatePatchCellSize() reads patchCellSize/<patch>/
    cellSize and crashes with "FOAM FATAL ERROR: Attempt to return
    primitive entry ... as a sub-dictionary" on the flat form. Verified
    live against real OpenFOAM 2512: this crashed cartesianMesh on every
    single run that set a per-patch cell size — including the app's own
    default "Generate Mesh" path on the built-in test cylinder."""
    import re
    lines = build_meshdict_lines(patch_cell_size={"wall": 0.02, "inlet": 0.01})
    content = "\n".join(lines)
    assert '"wall" 0.02' not in content
    assert re.search(r'"wall"\s*\{\s*cellSize\s+0\.02;\s*\}', content)
    assert re.search(r'"inlet"\s*\{\s*cellSize\s+0\.01;\s*\}', content)
    print("PASS: patch cell sizes are real sub-dictionaries")


def test_write_meshdict():
    with tempfile.TemporaryDirectory() as tmp:
        out = write_meshdict(tmp)
        assert out.exists()
        content = out.read_text()
        assert "maxCellSize 0.05" in content
        assert "FoamFile" in content
    print("PASS: write_meshdict creates file")


def test_write_with_bl():
    with tempfile.TemporaryDirectory() as tmp:
        out = write_meshdict(
            tmp,
            max_cell_size=0.1,
            min_cell_size=0.01,
            bl_params={
                "nLayers": 5,
                "thicknessRatio": 1.3,
                "wallPatches": ["wall"],
            },
        )
        content = out.read_text()
        import re
        assert re.search(r"nLayers\s+5;", content)
        assert re.search(r"thicknessRatio\s+1\.3;", content)
    print("PASS: write with BL")


def test_manual_refinement_box_schema_emits_real_box():
    """A 3D-viewer refinement box (refinement_boxes.py schema, no 'centre'
    key) must not crash and must be written as the exact box it is — centre
    = midpoint and lengthX/Y/Z = the real extents, not a cube of the
    diagonal."""
    box = {
        "type": "box",
        "xmin": 0.0, "xmax": 0.4,
        "ymin": -0.1, "ymax": 0.1,
        "zmin": 0.0, "zmax": 0.2,
        "level": 2,
        "cell_size": 0.005,
    }
    lines = build_meshdict_lines(object_refinements=[box])
    content = "\n".join(lines)
    assert "refinementBox_0" in content
    assert "centre  (0.2 -0.0 0.1);" in content or "centre  (0.2 0.0 0.1);" in content
    assert "lengthX 0.4;" in content
    assert "lengthY 0.2;" in content
    assert "lengthZ 0.2;" in content
    assert "cellSize 0.005;" in content
    print("PASS: 3D box zone written with real extents")


def test_mixed_legacy_and_box_zones_no_crash():
    """Throat auto-zones (legacy centre/radius) and viewer boxes can share
    one object_refinements list without a KeyError."""
    refs = [
        {"centre": (0.0, 0.0, 0.5), "radius": 0.15, "cell_size": 0.008},
        {"type": "box",
         "xmin": 0.0, "xmax": 0.4, "ymin": -0.1, "ymax": 0.1,
         "zmin": 0.0, "zmax": 0.2, "level": 2, "cell_size": 0.005},
    ]
    content = "\n".join(build_meshdict_lines(object_refinements=refs))
    assert "refinementBox_0" in content
    assert "refinementBox_1" in content
    assert "centre  (0.0 0.0 0.5);" in content
    assert "lengthX 0.4;" in content
    print("PASS: legacy + box zones coexist")


def test_malformed_refinement_zone_is_skipped_not_fatal():
    """A zone without 'centre' (or otherwise malformed) must never abort the
    whole meshDict write: it is skipped with a warning and the rest is still
    emitted."""
    refs = [
        {"center": (0, 0, 0), "radius": 0.1, "cell_size": 0.01},  # typo: no 'centre'
        {"type": "box", "xmin": "n/a", "xmax": 0.4, "ymin": 0, "ymax": 1,
         "zmin": 0, "zmax": 1, "cell_size": 0.005},
        {"centre": (0.0, 0.0, 0.5), "radius": 0.15, "cell_size": 0.008},
    ]
    content = "\n".join(build_meshdict_lines(object_refinements=refs))
    # only the valid legacy zone survives
    assert content.count("refinementBox_") == 1
    assert "refinementBox_0" in content
    assert "centre  (0.0 0.0 0.5);" in content
    print("PASS: malformed zones skipped, write continues")


def test_legacy_zone_still_cube():
    """Backward compatibility: legacy {centre,radius,cell_size} zones keep
    the historical cube semantics (lengthX = lengthY = lengthZ = 2*radius)."""
    refs = [{"centre": (0.0, 0.0, 0.5), "radius": 0.1, "cell_size": 0.01}]
    content = "\n".join(build_meshdict_lines(object_refinements=refs))
    assert "lengthX 0.2;" in content
    assert "lengthY 0.2;" in content
    assert "lengthZ 0.2;" in content
    print("PASS: legacy cube semantics preserved")




# ---------------------------------------------------------------------------
# localRefinement / edgeMeshRefinement / workflowControls (cfMesh features
# that were never used — verified against the installed cartesianMesh binary
# before implementation, per the project rule of not trusting dictionary keys
# on faith).
# ---------------------------------------------------------------------------


def test_local_refinement_emits_levels_not_metres():
    """localRefinement refines the VOLUME near a patch in cfMesh's native
    currency: octree LEVELS (additionalRefinementLevels), never absolute
    cell sizes — the latter crashed real geometry (commit 9904d65)."""
    import re
    lines = build_meshdict_lines(
        local_refinement={
            "wall": {"additional_refinement_levels": 2, "refinement_thickness": 0.02},
        },
    )
    content = "\n".join(lines)
    assert "localRefinement" in content
    assert re.search(r'"wall"\s*\{', content)
    assert "additionalRefinementLevels 2;" in content
    assert "refinementThickness 0.02;" in content
    # levels, never metres-as-levels: no cellSize key inside localRefinement
    lr = content.split("localRefinement")[1].split("}")[0]
    assert "cellSize" not in lr
    print("PASS: localRefinement in octree-level currency")


def test_local_refinement_malformed_skipped_not_fatal():
    """Bad entries (negative thickness, non-int levels) are skipped with a
    warning; valid siblings still emitted; an all-bad dict yields no block."""
    content = "\n".join(build_meshdict_lines(
        local_refinement={
            "bad": {"additional_refinement_levels": 1, "refinement_thickness": -1},
            "ok": {"additional_refinement_levels": 1, "refinement_thickness": 0.01},
        },
    ))
    assert "localRefinement" in content
    assert '"ok"' in content
    assert "bad" not in content
    # all-invalid -> no empty block left behind
    content2 = "\n".join(build_meshdict_lines(
        local_refinement={
            "x": {"additional_refinement_levels": 0, "refinement_thickness": 0.01},
        },
    ))
    assert "localRefinement" not in content2
    print("PASS: malformed localRefinement skipped")


def test_edge_mesh_refinement_requires_real_edge_file():
    """edgeMeshRefinement takes an OpenFOAM edgeMesh file (eMesh/obj/vtk).
    The .fms surface file is NOT accepted (cartesianMesh: 'Unknown edge
    format fms') — the emitted block must carry the edgeFile path so the
    caller controls which edge mesh to point at."""
    import re
    lines = build_meshdict_lines(
        edge_mesh_refinement=[
            {"edge_file": "constant/triSurface/surface.eMesh", "levels": 1},
        ],
    )
    content = "\n".join(lines)
    assert "edgeMeshRefinement" in content
    assert "edgeFile" in content
    assert 'edgeFile "constant/triSurface/surface.eMesh";' in content
    assert "additionalRefinementLevels 1;" in content
    assert ".fms" not in content
    print("PASS: edgeMeshRefinement emits edgeFile")


def test_edge_mesh_refinement_malformed_skipped():
    """An entry without edge_file is skipped; only valid ones emitted."""
    content = "\n".join(build_meshdict_lines(
        edge_mesh_refinement=[
            {"edge_file": None, "levels": 1},
            {"edge_file": "x.eMesh", "levels": 2},
        ],
    ))
    assert "edgeMeshRefinement" in content
    # numbering counts VALID entries only: the invalid one is skipped,
    # so the first valid entry is edgeRefinement_0 with levels=2
    assert "edgeRefinement_0" in content
    assert "edgeRefinement_1" not in content
    assert "additionalRefinementLevels 2;" in content
    print("PASS: malformed edgeMeshRefinement skipped")


def test_workflow_controls_stop_after():
    """workflowControls/stopAfter lets a run stop after a named phase —
    the hook the pipeline front needs to inspect a partial mesh."""
    lines = build_meshdict_lines(workflow_stop_after="refineBoundaryLayers")
    content = "\n".join(lines)
    assert "workflowControls" in content
    assert 'stopAfter "refineBoundaryLayers";' in content
    # default: no workflow block
    assert "workflowControls" not in "\n".join(build_meshdict_lines())
    print("PASS: workflowControls stopAfter")


def test_write_meshdict_with_features():
    """write_meshdict passes the new params through to the file on disk."""
    with tempfile.TemporaryDirectory() as tmp:
        out = write_meshdict(
            tmp,
            max_cell_size=0.3, min_cell_size=0.1,
            local_refinement={
                "wall": {"additional_refinement_levels": 1, "refinement_thickness": 0.05},
            },
            edge_mesh_refinement=[{"edge_file": "surf.eMesh", "levels": 1}],
            workflow_stop_after="refineBoundaryLayers",
        )
        content = out.read_text()
        assert "localRefinement" in content
        assert "edgeMeshRefinement" in content
        assert "workflowControls" in content
        assert 'stopAfter "refineBoundaryLayers";' in content
    print("PASS: write_meshdict with features")

if __name__ == "__main__":
    test_basic_has_robust_flags()
    test_basic_no_bl()
    test_with_bl()
    test_with_patches()
    test_write_meshdict()
    test_write_with_bl()
    print("\nALL TESTS PASSED")

