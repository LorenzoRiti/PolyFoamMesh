import sys
import os
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

from cfmesh_autogui.core.geometry import (
    create_test_cylinder,
    classify_faces,
    tessellate_patches,
)
from cfmesh_autogui.core.stl_writer import (
    export_multisolid_stl,
    validate_stl_solids,
    export_surface_file,
)


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


def test_stl_multisolid_export():
    cyl = create_test_cylinder()
    shape = cyl.val()
    patches = classify_faces(shape)
    meshes = tessellate_patches(patches)

    test_stl = Path(__file__).resolve().parents[1] / "sample_cad" / "cylinder_test.stl"
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


def test_surface_file_export():
    cyl = create_test_cylinder()
    shape = cyl.val()
    patches = classify_faces(shape)
    meshes = tessellate_patches(patches)

    case_dir = Path(__file__).resolve().parents[1] / "sample_cad" / "test_case"
    export_surface_file(meshes, case_dir)

    stl = case_dir / "constant" / "triSurface" / "surface.stl"
    assert stl.exists(), f"surface.stl not found at {stl}"

    solids = validate_stl_solids(stl)
    assert len(solids) == 3, f"Expected 3 solids, got {len(solids)}: {solids}"
    print("PASS: test_surface_file_export")


if __name__ == "__main__":
    test_cylinder_face_classification()
    test_tessellation()
    test_stl_multisolid_export()
    test_surface_file_export()
    print("\nAll Fase 1 tests passed.")

