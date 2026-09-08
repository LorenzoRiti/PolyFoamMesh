"""Cross-module consistency of the wall-patch decision.

Four code paths decide "which patches may carry prismatic boundary layers",
and they used to disagree — ``bl_engine`` used a wall-keyword allowlist with
a fallback that treated *every* patch as a wall, ``bc_editor`` used exact
matches, ``case_setup`` only knew inlet/outlet/wall, and the poly worker had
its own third tier that fell back to "apply to ALL".

The visible failure was prisms extruded into inlets and outlets. checkMesh
accepts that, so it only surfaced when the solver diverged.

These tests pin the invariant that matters: **all of them now agree, and
when no wall can be identified the answer is "skip the BL", never "apply it
everywhere".** Hermetic — no WSL, no OpenFOAM, no network, no Qt loop.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from polyfoammesh.commercial.bl_engine import BLEngine
from polyfoammesh.core import openfoam_runner as orm
from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine
from polyfoammesh.core.openfoam_runner import poly_bl_patch_selection
from polyfoammesh.core.patch_roles import split_wall_patches

_FOAM_HEADER = (
    "FoamFile\n{\n"
    "    version     2.0;\n"
    "    format      ascii;\n"
    "    class       polyBoundaryMesh;\n"
    "    object      boundary;\n"
    "}\n\n"
)

# Patches whose names carry no keyword at all (what GMSH emits) mixed with
# patches that are positively known to be open boundaries.
MIXED_PATCHES = [("surface_0", "patch"), ("inlet", "patch"), ("outlet", "patch")]
# Every patch is a known non-wall — the old "apply to ALL" trap.
ALL_OPEN_PATCHES = [
    ("inlet", "patch"),
    ("outlet", "patch"),
    ("symmetry", "symmetry"),
]


def _write_boundary(case_dir: Path, patches: list[tuple[str, str]]) -> None:
    """Write a boundary file with no points/faces.

    Without points/faces the geometric inference cannot succeed, which is
    exactly the condition that used to trigger the apply-to-ALL fallback.
    """
    poly = case_dir / "constant" / "polyMesh"
    poly.mkdir(parents=True, exist_ok=True)
    body = "".join(
        f"    {name}\n    {{\n"
        f"        type            {ptype};\n"
        f"        nFaces          2;\n"
        f"        startFace       {2 + i * 2};\n"
        f"    }}\n"
        for i, (name, ptype) in enumerate(patches)
    )
    (poly / "boundary").write_text(
        f"{_FOAM_HEADER}{len(patches)}\n(\n{body})\n", encoding="ascii",
    )


@pytest.fixture
def case_dir(tmp_path):
    return tmp_path / "case"


@pytest.fixture
def no_name_detection(monkeypatch):
    monkeypatch.setattr(orm, "poly_wall_patch_names", lambda case_dir: [])


def test_mixed_patches_all_paths_agree_on_the_single_wall(case_dir, no_name_detection):
    _write_boundary(case_dir, MIXED_PATCHES)
    names = [n for n, _ in MIXED_PATCHES]

    # 1. the shared classifier
    assert split_wall_patches(names)[0] == ["surface_0"]
    # 2. the BL parameter engine
    assert BLEngine().detect_wall_patches(names) == ["surface_0"]
    # 3. the poly worker's single decision point
    selected, apply_to_all, _ = poly_bl_patch_selection(case_dir)
    assert selected == ["surface_0"]
    assert apply_to_all is False


def test_selected_patch_is_what_bl_poly_actually_extrudes(case_dir, tmp_path):
    """The names handed to the extrusion engine exclude the open patches.

    Closes the loop: agreement at the name level is only useful if the
    geometry-level selection honours it.
    """
    patches = [
        {"name": "surface_0", "type": "patch", "nFaces": 2, "startFace": 2},
        {"name": "inlet", "type": "patch", "nFaces": 2, "startFace": 4},
    ]
    engine = PolyBoundaryLayerEngine(tmp_path / "case")
    # _select_faces only needs len(faces); owner is unused.
    selected, used = engine._select_faces(
        faces=[0] * 6,
        owner=None,
        patches=patches,
        n_int=2,
        patch_names=["surface_0"],
        apply_to_all=False,
    )
    assert selected == [0, 1], "only the wall patch's faces may be extruded"
    assert used == ["surface_0"]
    assert "inlet" not in used


def test_all_open_patches_skip_the_bl_everywhere(case_dir, no_name_detection):
    """The regression this file exists for.

    Every patch is a known inlet/outlet/symmetry and no wall can be found.
    The historical behaviour extruded prisms into all three; the correct
    behaviour is to skip the boundary layers entirely.
    """
    _write_boundary(case_dir, ALL_OPEN_PATCHES)
    names = [n for n, _ in ALL_OPEN_PATCHES]

    assert split_wall_patches(names)[0] == []
    assert BLEngine().detect_wall_patches(names) == []

    selected, apply_to_all, notes = poly_bl_patch_selection(case_dir)
    assert selected is None, "must signal 'skip', never 'apply everywhere'"
    assert apply_to_all is False
    assert any("skipped" in n for n in notes)


def test_explicit_user_override_still_means_every_patch(case_dir):
    """The override is the user's call and is honoured literally.

    Guarding against over-correction: the fix must not make it impossible to
    deliberately put layers on all patches.
    """
    _write_boundary(case_dir, ALL_OPEN_PATCHES)
    selected, apply_to_all, _ = poly_bl_patch_selection(
        case_dir, apply_to_all_override=True,
    )
    assert apply_to_all is True
    assert selected == []


def test_numpy_backed_selection_matches_python_selection(case_dir, no_name_detection):
    """bl_poly indexes patches into a numpy array — verify no off-by-one.

    A silent off-by-one in patch_of would extrude from the inlet while the
    name-level checks above all look correct.
    """
    _write_boundary(case_dir, MIXED_PATCHES)
    selected, _, _ = poly_bl_patch_selection(case_dir)
    assert selected == ["surface_0"]

    patches = [
        {"name": name, "type": ptype, "nFaces": 2, "startFace": 2 + i * 2}
        for i, (name, ptype) in enumerate(MIXED_PATCHES)
    ]
    engine = PolyBoundaryLayerEngine(case_dir)
    faces_idx, used = engine._select_faces(
        faces=[0] * 8,
        owner=None,
        patches=patches,
        n_int=2,
        patch_names=selected,
        apply_to_all=False,
    )
    assert np.array_equal(np.asarray(faces_idx), np.asarray([0, 1]))
    assert used == ["surface_0"]
