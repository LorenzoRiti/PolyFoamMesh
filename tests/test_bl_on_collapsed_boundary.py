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

Also confirmed at production scale on a real 102,080-cell mesh, BOTH ways:
    exact:    462,846 boundary faces -> BL ok, 1,388,538 prisms, 340 s
    collapse:  80,111 boundary faces -> BL ok,   240,333 prisms, 312 s
so the collapse cuts boundary-layer cells 5.8x at essentially the same
wall time. An earlier note here claimed BL ran out of memory at this
scale; that was wrong — the runs had simply not been waited out. The
fixture below stays small so the test itself remains fast.
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
        # Name the patches so the per-patch (wall-only) BL test below has
        # something real to select between — that is the production default,
        # since layers must not be extruded on inlet/outlet.
        for _dim, tag in gmsh.model.getEntities(2):
            com = gmsh.model.occ.getCenterOfMass(2, tag)
            if abs(com[2]) < 1e-6:
                name = "inlet"
            elif abs(com[2] - 1.0) < 1e-6:
                name = "outlet"
            else:
                name = "wall"
            grp = gmsh.model.addPhysicalGroup(2, [tag])
            gmsh.model.setPhysicalName(2, grp, name)
        vols = gmsh.model.getEntities(3)
        gvol = gmsh.model.addPhysicalGroup(3, [vols[0][1]])
        gmsh.model.setPhysicalName(3, gvol, "internal")
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


def test_wall_only_bl_works_on_collapsed_boundary(tmp_path, cylinder_msh):
    """COLLAPSE + per-patch (wall-only) BL — the production default, since
    boundary layers must not be extruded on inlet/outlet.

    This combination failed outright before the volume tolerance in
    bl_poly._validate was relaxed: closure, positive volumes and the
    face-pyramid criterion all passed, but the volume-conservation check
    (then held at 1e-6) rejected every height-scale attempt over a 1.6e-4
    relative difference. That difference is quadrature error, not a mesh
    defect: cell volume is computed from face centroids and Newell area
    vectors, which are exact only for PLANAR faces, and the collapsed
    boundary is n-gons that generally are not planar.
    """
    case = tmp_path / "wallonly"
    res, _shapes = _build(case, cylinder_msh, collapse=True)

    _pts, _faces, _owner, _neigh, patches = fio.read_polymesh(
        case / "constant" / "polyMesh")
    by_name = {p["name"]: p["nFaces"] for p in patches}
    assert "wall" in by_name and "inlet" in by_name, (
        f"patches did not survive the collapse: {by_name}"
    )
    assert all(n > 0 for n in by_name.values()), f"empty patch after collapse: {by_name}"

    bl = PolyBoundaryLayerEngine(case, log=lambda _m: None).run(
        n_layers=3, first_height=0.004, growth_rate=1.2,
        patch_names=["wall"], apply_to_all=False,
    )
    assert bl.success, f"wall-only BL failed on collapsed boundary: {bl.errors}"
    # exactly n_layers stacks per wall face, and nothing from inlet/outlet
    assert bl.n_prism_cells == 3 * by_name["wall"]
    assert bl.wall_patches == ["wall"]


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
