"""The boundary-layer engine must keep working on a COLLAPSED dual boundary.

``core/bl_poly.py`` was written and validated against the EXACT dual
boundary: three PLANAR quads per primal boundary triangle. The production
poly path now asks the converter for the collapsed boundary instead (one
polygonal face per boundary vertex — pentagons/hexagons/heptagons, and not
planar in general), because that is what makes the mesh actually read as
polyhedral rather than as the input triangulation with a Y inside each
triangle.

That makes "does BL still work on n-gon wall faces?" the single riskiest
interaction of the collapse change, so it gets its own test.

Measured on a small GMSH cylinder (this fixture):
    exact:    2,292 boundary faces (all quads) -> BL ok, 6,876 prisms
    collapse:   426 boundary faces (5-, 6-, 7-gons) -> BL ok, 1,278 prisms
i.e. BL succeeds either way and the prism count drops by the same ~5.4x
factor as the boundary face count (bl_poly extrudes one stack per face).

Also confirmed at production scale, on a real 102,080-cell mesh with the
collapsed boundary: BL succeeded with 240,333 prism cells in ~325 s (one
height-scale retry, 2 face-pyramid violations on the first attempt). Slow,
but it completes — an earlier note in this file claimed it ran out of
memory there, which was wrong: the run had simply not been waited out.
The fixture here stays small so the test itself remains fast.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

gmsh = pytest.importorskip("gmsh")

from cfmesh_autogui.core import foam_mesh_io as fio  # noqa: E402
from cfmesh_autogui.core.bl_poly import PolyBoundaryLayerEngine  # noqa: E402
from cfmesh_autogui.core.mesh_converter import msh_to_of_polymesh  # noqa: E402
from cfmesh_autogui.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402

CONTROL_DICT = (
    "FoamFile\n{\n version 2.0;\n format ascii;\n class dictionary;\n"
    " object controlDict;\n}\napplication checkMesh;\nstartFrom startTime;\n"
    "startTime 0;\nstopAt endTime;\nendTime 1;\ndeltaT 1;\n"
    "writeControl timeStep;\nwriteInterval 1;\npurgeWrite 0;\n"
    "writeFormat ascii;\nwritePrecision 8;\ntimeFormat general;\n"
    "timePrecision 6;\nrunTimeModifiable true;\n"
)


@pytest.fixture(scope="module")
def cylinder_msh(tmp_path_factory) -> Path:
    """A small tetrahedral cylinder, straight from GMSH (no WSL)."""
    out = tmp_path_factory.mktemp("bl_collapse") / "cyl.msh"
    gmsh.initialize()
    try:
        gmsh.model.add("cyl")
        gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, 1, 0.3)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", 0.09)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", 0.05)
        gmsh.model.mesh.generate(3)
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh.write(str(out))
    finally:
        gmsh.finalize()
    return out


def _build(case: Path, msh: Path, collapse: bool):
    (case / "system").mkdir(parents=True, exist_ok=True)
    (case / "system" / "controlDict").write_text(CONTROL_DICT, encoding="ascii")
    msh_to_of_polymesh(msh, case)
    kw = (
        dict(collapse_smooth_edges=True, boundary_feature_angle=40.0,
             collapse_volume_tolerance=0.10)
        if collapse else {}
    )
    res = TetPolyDualConverter(case, log=lambda _m: None, **kw).run()
    assert res.success, res.errors
    pts, faces, owner, neigh, patches = fio.read_polymesh(
        case / "constant" / "polyMesh")
    shapes = Counter(len(f) for f in faces[len(neigh):])
    return res, shapes


def test_collapsed_boundary_is_made_of_real_polygons(tmp_path, cylinder_msh):
    """Sanity for the test below: the collapsed boundary really is n-gons,
    not quads — otherwise the BL check would prove nothing."""
    _res, shapes = _build(tmp_path / "collapse", cylinder_msh, collapse=True)
    assert set(shapes) - {3, 4}, f"expected polygons beyond tris/quads, got {dict(shapes)}"
    assert shapes[5] + shapes[6] > 0, f"expected pentagons/hexagons, got {dict(shapes)}"


def test_boundary_layer_still_succeeds_on_collapsed_boundary(tmp_path, cylinder_msh):
    """The actual risk: bl_poly extrudes prism stacks from wall faces that
    are now non-planar n-gons instead of planar quads."""
    case = tmp_path / "collapse"
    res, shapes = _build(case, cylinder_msh, collapse=True)

    bl = PolyBoundaryLayerEngine(case, log=lambda _m: None).run(
        n_layers=3, first_height=0.004, growth_rate=1.2, apply_to_all=True,
    )
    assert bl.success, f"BL failed on collapsed boundary: {bl.errors}"
    assert bl.n_prism_cells == 3 * res.n_boundary_faces
    assert bl.total_thickness > 0


def test_collapse_cuts_boundary_faces_and_therefore_bl_cells(tmp_path, cylinder_msh):
    """bl_poly extrudes ONE prism stack per boundary face, so collapsing
    the boundary cuts boundary-layer cell count by the same factor."""
    exact_res, exact_shapes = _build(tmp_path / "exact", cylinder_msh, collapse=False)
    coll_res, _ = _build(tmp_path / "collapse", cylinder_msh, collapse=True)

    assert set(exact_shapes) == {4}, "exact dual boundary should be all quads"
    assert coll_res.n_boundary_faces < exact_res.n_boundary_faces / 3, (
        f"expected a large reduction, got {exact_res.n_boundary_faces} -> "
        f"{coll_res.n_boundary_faces}"
    )
