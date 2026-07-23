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

from cfmesh_autogui.core.boundary_reader import parse_boundary, PatchInfo
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


if __name__ == "__main__":
    test_parse_boundary()
    test_setup_case()
    print("\nAll Fase 4 tests passed.")

