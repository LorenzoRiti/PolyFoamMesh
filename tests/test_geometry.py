import sys
import os
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import os as _os
try:
    import OCP as _ocp
    _d = _os.path.dirname(_ocp.__file__)
    if _d not in _os.environ.get("PATH", ""):
        _os.environ["PATH"] = _d + _os.pathsep + _os.environ.get("PATH", "")
except Exception:
    pass

from polyfoammesh.core.geometry import (
    create_test_cylinder,
    classify_faces,
    tessellate_patches,
)
from polyfoammesh.core.stl_writer import (
    export_multisolid_stl,
    validate_stl_solids,
    export_surface_file,
)

cq = pytest.importorskip("cadquery")
trimesh = pytest.importorskip("trimesh")


def test_tessellate_closed_curved_body_is_watertight():
    """Regression: OCC tessellation emits coincident duplicate vertices at
    the seams/poles of closed curved faces (sphere, torus). The degenerate
    pole triangles used to survive the tessellator, so every topologically
    closed curved body failed check_watertight() as "leaky" and was refused
    by the GUI pre-flight (documented in docs/dev/notes/NIGHT_LOOP_PROMPT.md).
    """
    bodies = [
        cq.Workplane("XY").sphere(100.0),
    ]
    for body in bodies:
        patches = classify_faces(body.val())
        meshes = tessellate_patches(patches)
        m = trimesh.util.concatenate(meshes)
        assert len(m.faces) > 0
        assert m.is_watertight, (
            f"{type(body.val()).__name__} tessellated to a non-watertight mesh "
            f"({len(m.faces)} faces, euler={m.euler_number})"
        )
        assert m.is_winding_consistent


def test_cylinder_face_classification():
    cyl = create_test_cylinder()
    shape = cyl.val()
    patches = classify_faces(shape)

    names = [name for name, _ in patches]
    assert "inlet" in names, f"Expected 'inlet' in patches, got {names}"
    assert "outlet" in names, f"Expected 'outlet' in patches, got {names}"
    assert "wall" in names, f"Expected 'wall' in patches, got {names}"

    for name, faces in patches:
        assert len(faces) > 0, f"Patch '{name}' has no faces"
    print("PASS: test_cylinder_face_classification")


def test_tessellation():
    cyl = create_test_cylinder()
    shape = cyl.val()
    patches = classify_faces(shape)
    meshes = tessellate_patches(patches)

    assert len(meshes) == 3, f"Expected 3 meshes, got {len(meshes)}"
    for m in meshes:
        assert len(m.faces) > 0, f"Mesh '{m.metadata.get('name')}' has no faces"
        assert len(m.vertices) > 0, f"Mesh '{m.metadata.get('name')}' has no vertices"
    print("PASS: test_tessellation")


def test_stl_multisolid_export(tmp_path):
    cyl = create_test_cylinder()
    shape = cyl.val()
    patches = classify_faces(shape)
    meshes = tessellate_patches(patches)

    # NEVER write into the tracked sample_cad/ directory: overwriting the
    # shipped samples on every test run made git report spurious
    # modifications after each local run (tessellation output is not
    # byte-stable across runs/versions).
    test_stl = tmp_path / "cylinder_test.stl"
    export_multisolid_stl(meshes, test_stl)

    assert test_stl.exists(), "STL file was not created"
    content = test_stl.read_text()

    for expected in ["solid inlet", "solid outlet", "solid wall"]:
        assert expected in content, f"Missing '{expected}' in STL"
    for expected in ["endsolid inlet", "endsolid outlet", "endsolid wall"]:
        assert expected in content, f"Missing '{expected}' in STL"

    solids = validate_stl_solids(test_stl)
    assert "inlet" in solids, f"inlet missing in validate_stl_solids: {solids}"
    assert "outlet" in solids, f"outlet missing in validate_stl_solids: {solids}"
    assert "wall" in solids, f"wall missing in validate_stl_solids: {solids}"
    print("PASS: test_stl_multisolid_export")


def test_fallback_ascii_export_preserves_patch_names_for_ftetwild(tmp_path):
    """The fTetWild surface fallback MUST keep per-patch names: binary
    STL loses ``solid NAME`` blocks, and ``load_surface_solids`` can only
    recover names from ASCII — this is exactly why
    ``export_multisolid_ascii_stl`` exists alongside the auto-switching
    variant (which goes binary above a 10 MB estimate and would flatten
    the patches to one)."""
    import trimesh

    from polyfoammesh.core.ftetwild_wrapper import load_surface_solids
    from polyfoammesh.core.stl_writer import export_multisolid_ascii_stl

    def _patch(name: str) -> trimesh.Trimesh:
        box = trimesh.creation.box(bounds=((-0.1, -0.1, 0), (0.1, 0.1, 0.02)))
        mesh = box.subdivide().subdivide()  # ~3k faces, fast
        mesh.metadata["name"] = name
        return mesh

    out = tmp_path / "fallback.stl"
    export_multisolid_ascii_stl([_patch("wall"), _patch("inlet")], out)
    tris, names = load_surface_solids(out)
    assert set(names) == {"wall", "inlet"}, f"names lost: {set(names)}"
    assert len(tris) > 0
    content = out.read_text(encoding="ascii", errors="replace")
    assert "facet normal" in content  # ASCII, name-bearing


def test_surface_file_export(tmp_path):
    cyl = create_test_cylinder()
    shape = cyl.val()
    patches = classify_faces(shape)
    meshes = tessellate_patches(patches)

    # Same as above: never mutate the tracked sample_cad/ tree from tests.
    case_dir = tmp_path / "test_case"
    export_surface_file(meshes, case_dir)

    stl = case_dir / "constant" / "triSurface" / "surface.stl"
    assert stl.exists(), f"surface.stl not found at {stl}"

    solids = validate_stl_solids(stl)
    assert len(solids) == 3, f"Expected 3 solids, got {len(solids)}: {solids}"
    print("PASS: test_surface_file_export")


if __name__ == "__main__":
    import tempfile
    test_cylinder_face_classification()
    test_tessellation()
    with tempfile.TemporaryDirectory() as _td:
        _tp = Path(_td)
        test_stl_multisolid_export(_tp)
        test_surface_file_export(_tp)
    print("\nAll Fase 1 tests passed.")

