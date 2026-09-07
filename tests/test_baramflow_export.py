"""BaramFlow export must ship only cases that will actually open.

The requirement is "esportare mesh per baramflow". BaramFlow opens native
OpenFOAM cases, so the risk is not the copy but shipping an *inconsistent* case:
a mesh patch with no boundary condition in a 0/ field makes OpenFOAM abort on
load. These tests build real case folders on disk and check the validator
catches that, not merely missing files.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from polyfoammesh.core.baramflow_export import (
    export_case,
    validate_case,
)

BOUNDARY = """\
FoamFile { version 2.0; format ascii; class polyBoundaryMesh; object boundary; }
3
(
    inlet  { type patch; nFaces 10; startFace 100; }
    outlet { type patch; nFaces 10; startFace 110; }
    wall   { type wall;  nFaces 40; startFace 120; }
)
"""


def _field(patch_names) -> str:
    bf = "\n".join(
        f"    {n} {{ type zeroGradient; }}" for n in patch_names
    )
    return (
        "FoamFile { version 2.0; format ascii; class volScalarField; object p; }\n"
        "dimensions [0 2 -2 0 0 0 0];\n"
        "internalField uniform 0;\n"
        "boundaryField\n{\n" + bf + "\n}\n"
    )


def _build_case(root: Path, field_patches=("inlet", "outlet", "wall")) -> Path:
    poly = root / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    for name in ("points", "faces", "owner", "neighbour"):
        (poly / name).write_text("0\n(\n)\n")
    (poly / "boundary").write_text(BOUNDARY)

    sysd = root / "system"
    sysd.mkdir()
    for name in ("controlDict", "fvSchemes", "fvSolution"):
        (sysd / name).write_text("FoamFile { object %s; }\n" % name)

    zero = root / "0"
    zero.mkdir()
    (zero / "p").write_text(_field(field_patches))
    (zero / "U").write_text(_field(field_patches))
    return root


def test_complete_consistent_case_validates(tmp_path):
    case = _build_case(tmp_path / "case")
    v = validate_case(case)
    assert v.ok, v.summary()
    assert v.patches == {"inlet": "patch", "outlet": "patch", "wall": "wall"}


def test_missing_field_is_reported(tmp_path):
    case = _build_case(tmp_path / "case")
    (case / "0" / "U").unlink()
    v = validate_case(case)
    assert not v.ok
    assert any("0/U" in m for m in v.missing_files)


def test_patch_without_boundary_condition_is_caught(tmp_path):
    """The core integration check: a mesh patch absent from a field's
    boundaryField would make BaramFlow/OpenFOAM abort on load."""
    # Fields only cover inlet+wall; the mesh also has an outlet.
    case = _build_case(tmp_path / "case", field_patches=("inlet", "wall"))
    v = validate_case(case)
    assert not v.ok
    assert any("outlet" in issue for issue in v.issues)


def test_export_refuses_an_invalid_case(tmp_path):
    case = _build_case(tmp_path / "case", field_patches=("inlet", "wall"))
    with pytest.raises(ValueError):
        export_case(case, tmp_path / "out")


def test_export_copies_a_valid_case(tmp_path):
    case = _build_case(tmp_path / "case")
    dest_parent = tmp_path / "out"
    dest_parent.mkdir()
    out = export_case(case, dest_parent)
    assert out.is_dir()
    assert (out / "constant" / "polyMesh" / "boundary").is_file()
    assert (out / "0" / "U").is_file()
    assert (out / "system" / "controlDict").is_file()


def test_export_refuses_to_overwrite_the_source(tmp_path):
    case = _build_case(tmp_path / "case")
    # dest_parent/case.name would equal the source
    with pytest.raises(ValueError):
        export_case(case, case.parent)
