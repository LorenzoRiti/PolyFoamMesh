"""Mesh export must actually work — including on realistic (non-toy) meshes.

Two real bugs this pins down, both verified live before the fix:

1. `meshio.read(polyMesh_dir, file_format="openfoam")` — the code path the
   Export menu used to take — raises `ReadError: Unknown file format
   'openfoam'` in meshio 5.3.5. Every export, in every format, always failed.

2. Even reading a plain VTU with meshio breaks on a mesh with MIXED cell types
   (`ValueError: Cannot handle combinations of polyhedra with other cells`),
   which is the common case for cfMesh/cartesianMesh output (octree refinement
   transitions produce polyhedra alongside hexahedra) — not just a corner case.

`export_mesh()` avoids both: PyVista (built on VTK, not meshio) reads any
OpenFOAM cell type; non-VTU formats triangulate every cell to tetrahedra before
handing off to meshio, so mixed polyhedra/hexahedra meshes export correctly.

WSL/OpenFOAM-gated: skips cleanly where cartesianMesh isn't available.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:  # cadquery/OCP native libs need their dir on PATH on Windows
    import OCP as _ocp

    _d = os.path.dirname(_ocp.__file__)
    if _d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

import pytest

from cfmesh_autogui.config import OFConfig

cq = pytest.importorskip("cadquery")
pytest.importorskip("meshio")
pytest.importorskip("pyvista")

from cfmesh_autogui.core.boundary_reader import parse_boundary  # noqa: E402
from cfmesh_autogui.core.case_setup import setup_case  # noqa: E402
from cfmesh_autogui.core.geometry import (  # noqa: E402
    classify_faces,
    create_test_cylinder,
    tessellate_patches,
)
from cfmesh_autogui.core.mesh_export import EXPORT_FORMATS, export_mesh  # noqa: E402
from cfmesh_autogui.core.meshdict_gen import write_meshdict  # noqa: E402
from cfmesh_autogui.core.stl_writer import export_surface_file  # noqa: E402

WORK_ROOT = Path("C:/cfmesh_bench/export_test")

CONTROL_DICT = """\
FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }
application cartesianMesh;
startFrom startTime; startTime 0; stopAt endTime; endTime 1000; deltaT 1;
writeControl timeStep; writeInterval 1; purgeWrite 0; writeFormat binary;
writePrecision 6; writeCompression on; timeFormat general; timePrecision 6;
runTimeModifiable true;
"""


def _wsl_available() -> bool:
    try:
        return OFConfig().validate()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _wsl_available(), reason="OpenFOAM/WSL not available"
)


@pytest.fixture(scope="module")
def meshed_case():
    """A real cfMesh case with a cube-minus-cube-obstacle: this reliably
    produces polyhedral cells at the refinement transition, which is exactly
    the case that used to break export."""
    import subprocess

    cfg = OFConfig()
    case = WORK_ROOT / "case"
    shutil.rmtree(WORK_ROOT, ignore_errors=True)
    case.mkdir(parents=True)

    outer = cq.Workplane("XY").box(1.0, 1.0, 1.0)
    inner = cq.Workplane("XY").box(0.3, 0.3, 0.3)
    meshes = tessellate_patches(classify_faces(outer.cut(inner).val()))
    names = [m.metadata.get("name", "wall") for m in meshes]

    export_surface_file(meshes, case)
    write_meshdict(case, 0.1, 0.03, patch_names=names)
    (case / "system" / "controlDict").write_text(CONTROL_DICT, encoding="ascii")

    full = cfg._build_wsl_cmd(
        f"source {cfg._quoted_linux_path(cfg.env_script)} 2>/dev/null; "
        f"cd {cfg._quoted_linux_path(case)} && cartesianMesh > log.mesh 2>&1"
    )
    r = subprocess.run(full, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, "cartesianMesh failed to build the fixture mesh"

    # foamToVTK (like checkMesh) refuses to run without fvSchemes/fvSolution.
    patches = parse_boundary(case / "constant" / "polyMesh" / "boundary")
    setup_case(case, patches)

    yield case
    shutil.rmtree(WORK_ROOT, ignore_errors=True)


@pytest.mark.parametrize("fmt", list(EXPORT_FORMATS))
def test_export_every_format_on_a_mesh_with_mixed_cell_types(meshed_case, fmt):
    ext, _mf, _desc = EXPORT_FORMATS[fmt]
    out = WORK_ROOT / f"export{ext}"
    result = export_mesh(meshed_case, fmt, out)
    assert result == out
    assert out.is_file()
    assert out.stat().st_size > 0


def test_unknown_format_raises_a_clear_error(meshed_case):
    with pytest.raises(ValueError, match="Unknown export format"):
        export_mesh(meshed_case, "not_a_real_format", WORK_ROOT / "x.xyz")


@pytest.mark.parametrize("fmt", ["cgns", "vtu", "su2", "gmsh_msh", "abaqus_inp"])
def test_commercial_exporter_delegates_to_core_mesh_export(meshed_case, fmt):
    """commercial/exporter.py used to hand-parse polyMesh/faces and pass the
    result to meshio as if faces WERE cells (faces list point connectivity,
    not cell connectivity — wrong topology entirely), and its CGNS handler
    shelled out to a nonexistent `foamToCGNS` utility. It now delegates to
    core.mesh_export.export_mesh(), the same verified path the GUI's Export
    Mesh menu uses. Confirmed here on a realistic mesh with mixed
    polyhedra/hexahedra, not just a trivial single-cell-type box."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _test_helpers import load_commercial_module

    mod = load_commercial_module("exporter")
    ex = mod.MeshExporter()
    ext = mod.EXPORT_FORMAT_REGISTRY[fmt]["ext"]
    out = WORK_ROOT / f"commercial_export_{fmt}{ext}"
    result = ex.export(meshed_case, fmt=fmt, output_path=out)

    assert result.success, result.error
    assert result.cell_count > 0
    assert result.file_size_bytes > 0
    assert Path(result.output_path).is_file()
