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

from cfmesh_autogui.core.boundary_reader import (
    count_cells, count_faces, count_points, parse_boundary, PatchInfo,
)
from cfmesh_autogui.core.case_setup import setup_case

SAMPLE_BOUNDARY = r"""/*--------------------------------*- C++ -*----------------------------------*\
| =========                 |                                                 |
| \\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\    /   O peration     | Version:  2512                                  |
|   \\  /    A nd           | Website:  www.openfoam.com                      |
|    \\/     M anipulation  |                                                 |
\*---------------------------------------------------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    arch        "LSB;label=32;scalar=64";
    class       polyBoundaryMesh;
    location    "constant/polyMesh";
    object      boundary;
}

3
(
inlet
{
    type wall;
    nFaces 755;
    startFace 23716;
}

outlet
{
    type wall;
    nFaces 755;
    startFace 24471;
}

wall
{
    type wall;
    nFaces 2334;
    startFace 25226;
}

)"""


def test_parse_boundary():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "boundary"
        path.write_text(SAMPLE_BOUNDARY)
        patches = parse_boundary(path)

    assert len(patches) == 3, f"Expected 3 patches, got {len(patches)}"

    inlet = patches[0]
    assert inlet.name == "inlet"
    assert inlet.patch_type == "wall"
    assert inlet.n_faces == 755
    assert inlet.start_face == 23716

    assert patches[1].name == "outlet"
    assert patches[2].name == "wall"
    assert patches[2].n_faces == 2334

    print("PASS: test_parse_boundary")


def test_setup_case():
    patches = [
        PatchInfo("inlet", "wall", 100, 0),
        PatchInfo("outlet", "wall", 200, 100),
        PatchInfo("wall", "wall", 500, 300),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        case_dir = Path(tmpdir)
        setup_case(case_dir, patches)

        ctrl = case_dir / "system" / "controlDict"
        assert ctrl.exists()
        assert "application" in ctrl.read_text()

        fv_sch = case_dir / "system" / "fvSchemes"
        assert fv_sch.exists()
        assert "divSchemes" in fv_sch.read_text()

        fv_sol = case_dir / "system" / "fvSolution"
        assert fv_sol.exists()
        assert "solvers" in fv_sol.read_text()

        p_file = case_dir / "0" / "p"
        assert p_file.exists()
        p_content = p_file.read_text()
        assert "inlet" in p_content
        assert "outlet" in p_content
        assert "wall" in p_content
        assert "zeroGradient" in p_content

        u_file = case_dir / "0" / "U"
        assert u_file.exists()
        u_content = u_file.read_text()
        assert "inlet" in u_content
        assert "outlet" in u_content
        assert "wall" in u_content
        assert "zeroGradient" in u_content

    print("PASS: test_setup_case")


# Real cartesianMesh output: cell counts are in the owner file's own FoamFile
# header comment. Values below (nPoints/nCells/nFaces) and the points/faces
# list counts are taken verbatim from a real WSL2 OpenFOAM 2512 run.
_OWNER_WITH_NOTE = """FoamFile
{
    version     2.0;
    format      ascii;
    arch        "LSB;label=32;scalar=64";
    note        "nPoints:11569  nCells:9184  nFaces:29900  nInternalFaces:28052";
    class       labelList;
    location    "constant/polyMesh";
    object      owner;
}
29900
(
0
0
0
)
"""

# polyDualMesh's polyhedral conversion output has no such note at all —
# verified live: cell count must come from max(owner, neighbour) + 1 instead
# (OpenFOAM cell indices are 0-based and contiguous). Values below are a
# reduced but structurally identical form of a real polyDualMesh case where
# checkMesh independently reported 896 cells.
_OWNER_NO_NOTE = """FoamFile
{
    version     2.0;
    format      ascii;
    arch        "LSB;label=32;scalar=64";
    class       labelList;
    location    "constant/polyMesh";
    object      owner;
}
4
(
0
1
893
2
)
"""

_NEIGHBOUR_NO_NOTE = """FoamFile
{
    version     2.0;
    format      ascii;
    class       labelList;
    location    "constant/polyMesh";
    object      neighbour;
}
2
(
1
895
)
"""

_POINTS_SAMPLE = """FoamFile
{
    version     2.0;
    format      ascii;
    class       vectorField;
    location    "constant/polyMesh";
    object      points;
}
3
(
(0 0 0)
(1 0 0)
(1 1 0)
)
"""

_FACES_SAMPLE = """FoamFile
{
    version     2.0;
    format      ascii;
    class       faceList;
    location    "constant/polyMesh";
    object      faces;
}
2
(
4(0 1 2 3)
3(1 2 3)
)
"""


def test_count_cells_uses_note_when_present():
    case = Path(tempfile.mkdtemp())
    poly = case / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "owner").write_text(_OWNER_WITH_NOTE, encoding="ascii")
    assert count_cells(case) == 9184
    print("PASS: test_count_cells_uses_note_when_present")


def test_count_cells_falls_back_to_max_index_without_note():
    """The owner file's own leading count (4) is the FACE count, not the
    cell count — using it directly (or any fixed-offset line-count guess)
    would be wrong. The real cell count is max(owner, neighbour) + 1 = 896."""
    case = Path(tempfile.mkdtemp())
    poly = case / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "owner").write_text(_OWNER_NO_NOTE, encoding="ascii")
    (poly / "neighbour").write_text(_NEIGHBOUR_NO_NOTE, encoding="ascii")
    assert count_cells(case) == 896
    print("PASS: test_count_cells_falls_back_to_max_index_without_note")


def test_count_cells_missing_polymesh_returns_zero():
    case = Path(tempfile.mkdtemp())
    assert count_cells(case) == 0
    print("PASS: test_count_cells_missing_polymesh_returns_zero")


def test_count_points_and_faces_use_declared_list_count():
    case = Path(tempfile.mkdtemp())
    poly = case / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "points").write_text(_POINTS_SAMPLE, encoding="ascii")
    (poly / "faces").write_text(_FACES_SAMPLE, encoding="ascii")
    assert count_points(case) == 3
    assert count_faces(case) == 2
    print("PASS: test_count_points_and_faces_use_declared_list_count")


if __name__ == "__main__":
    test_parse_boundary()
    test_setup_case()
    test_count_cells_uses_note_when_present()
    test_count_cells_falls_back_to_max_index_without_note()
    test_count_cells_missing_polymesh_returns_zero()
    test_count_points_and_faces_use_declared_list_count()
    print("\nAll Fase 4 tests passed.")

