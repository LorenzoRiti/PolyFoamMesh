"""Direct OpenFOAM case export: a clean, runnable case folder.

User request: alongside the BaramFlow export (which validates 0/ fields
and refuses incomplete cases) there must be a DIRECT export for running
OpenFOAM itself - the mesh plus whatever else exists, without the
BaramFlow requirements, and without the app's heavy regenerable
artifacts (processor*, VTK_view, mesh.msh, tet backup).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from polyfoammesh.core.openfoam_export import export_openfoam_case


def _make_case(root: Path) -> Path:
    case = root / "mycase"
    poly = case / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        (poly / name).write_text("// dummy", encoding="utf-8")
    (case / "constant" / "polyMesh_tet_backup").mkdir()
    (case / "constant" / "polyMesh_tet_backup" / "points").write_text(
        "// tet backup", encoding="utf-8"
    )
    (case / "constant" / "transportProperties").write_text(
        "// props", encoding="utf-8"
    )
    system = case / "system"
    system.mkdir()
    for name in ("controlDict", "fvSchemes", "fvSolution"):
        (system / name).write_text("// sys", encoding="utf-8")
    zero = case / "0"
    zero.mkdir()
    (zero / "p").write_text("// p", encoding="utf-8")
    (case / "processor0").mkdir()
    (case / "processor0" / "constant").mkdir()
    (case / "VTK_view").mkdir()
    (case / "VTK_view" / "mesh.vtp").write_text("vtp", encoding="utf-8")
    (case / "mesh.msh").write_text("msh", encoding="utf-8")
    return case


def test_exports_mesh_system_and_fields_without_app_artifacts(tmp_path):
    case = _make_case(tmp_path)
    dest = export_openfoam_case(case, tmp_path / "out")

    assert dest == (tmp_path / "out" / "mycase").resolve()
    poly = dest / "constant" / "polyMesh"
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        assert (poly / name).is_file()
    assert (dest / "constant" / "transportProperties").is_file()
    assert (dest / "system" / "controlDict").is_file()
    assert (dest / "0" / "p").is_file()
    # App artifacts are left out (large, regenerable).
    assert not (dest / "processor0").exists()
    assert not (dest / "VTK_view").exists()
    assert not (dest / "mesh.msh").exists()
    assert not (dest / "constant" / "polyMesh_tet_backup").exists()


def test_mesh_only_case_exports_without_zero_dir(tmp_path):
    """No 0/ fields is fine for a direct OpenFOAM export (unlike BaramFlow)."""
    case = _make_case(tmp_path)
    for f in (case / "0").iterdir():
        f.unlink()
    (case / "0").rmdir()

    dest = export_openfoam_case(case, tmp_path / "out")
    assert (dest / "constant" / "polyMesh" / "points").is_file()
    assert not (dest / "0").exists()


def test_missing_mesh_raises_value_error(tmp_path):
    case = tmp_path / "nocase"
    (case / "constant").mkdir(parents=True)
    with pytest.raises(ValueError, match="No OpenFOAM mesh"):
        export_openfoam_case(case, tmp_path / "out")


def test_same_destination_raises(tmp_path):
    case = _make_case(tmp_path)
    with pytest.raises(ValueError, match="Destination must differ"):
        export_openfoam_case(case, tmp_path)
