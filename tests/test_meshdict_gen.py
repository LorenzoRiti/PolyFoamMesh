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

from cfmesh_autogui.core.meshdict_gen import build_meshdict_lines, write_meshdict


def test_basic_has_robust_flags():
    lines = build_meshdict_lines()
    content = "\n".join(lines)
    assert "keepCellsIntersectingBoundary 1" in content
    assert "allowDisconnected 0" in content
    assert "maxNumIterations 15" in content
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
    lines = build_meshdict_lines(patch_cell_size={"wall": 0.02, "inlet": 0.01})
    content = "\n".join(lines)
    assert '"wall" 0.02' in content
    assert '"inlet" 0.01' in content
    print("PASS: patch cell sizes present")


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


if __name__ == "__main__":
    test_basic_has_robust_flags()
    test_basic_no_bl()
    test_with_bl()
    test_with_patches()
    test_write_meshdict()
    test_write_with_bl()
    print("\nALL TESTS PASSED")

