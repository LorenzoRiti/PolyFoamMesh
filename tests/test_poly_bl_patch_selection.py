"""Tests for ``openfoam_runner.poly_bl_patch_selection`` — the single decision
point for which boundary patches the poly boundary layer may touch.

The behaviour under test is the last tier: when neither the boundary
type/name nor the geometric inference can identify a wall, the old code
applied the BL to **every** boundary patch — extruding prisms into inlets
and outlets on exactly the geometries where the least is known. checkMesh
accepts that, so the user only finds out when the solver diverges.

Hermetic: no WSL, no OpenFOAM, no network, no Qt event loop.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cfmesh_autogui.core import openfoam_runner as orm
from cfmesh_autogui.core.openfoam_runner import poly_bl_patch_selection

_FOAM_HEADER = (
    "FoamFile\n{\n"
    "    version     2.0;\n"
    "    format      ascii;\n"
    "    class       polyBoundaryMesh;\n"
    "    object      boundary;\n"
    "}\n\n"
)


def _write_boundary(case_dir: Path, patches: list[tuple[str, str]]) -> Path:
    """Write a minimal ``constant/polyMesh/boundary``.

    No points/faces are written, so ``infer_patch_roles`` (the geometric
    tier) cannot succeed — that is what forces the last tier under test.
    """
    poly = case_dir / "constant" / "polyMesh"
    poly.mkdir(parents=True, exist_ok=True)
    body = "".join(
        f"    {name}\n    {{\n"
        f"        type            {ptype};\n"
        f"        nFaces          4;\n"
        f"        startFace       {i * 4};\n"
        f"    }}\n"
        for i, (name, ptype) in enumerate(patches)
    )
    path = poly / "boundary"
    path.write_text(f"{_FOAM_HEADER}{len(patches)}\n(\n{body})\n", encoding="ascii")
    return path


@pytest.fixture
def case_dir(tmp_path):
    return tmp_path / "case"


@pytest.fixture
def no_name_detection(monkeypatch):
    """Force the type/name tier to fail, so the last tier is exercised."""
    monkeypatch.setattr(orm, "poly_wall_patch_names", lambda case_dir: [])


def test_explicit_override_means_all_patches(case_dir):
    # An explicit user override is the user's call and is honoured literally.
    names, apply_to_all, notes = poly_bl_patch_selection(
        case_dir, apply_to_all_override=True,
    )
    assert apply_to_all is True
    assert names == []
    assert notes == []


def test_boundary_type_wall_is_found_by_the_first_tier(case_dir):
    # type == "wall" must win even when the name says nothing ("body" is a
    # wall keyword but the boundary type is the authoritative signal).
    _write_boundary(case_dir, [("body", "wall"), ("inlet", "patch")])
    names, apply_to_all, _ = poly_bl_patch_selection(case_dir)
    assert names == ["body"]
    assert apply_to_all is False


def test_last_tier_excludes_known_non_walls(case_dir, no_name_detection):
    # GMSH-style unnamed patches + no inferable geometry: extrude from the
    # unnamed patches, never from the ones positively known to be open.
    _write_boundary(
        case_dir,
        [("surface_0", "patch"), ("inlet", "patch"), ("outlet", "patch")],
    )
    names, apply_to_all, notes = poly_bl_patch_selection(case_dir)
    assert names == ["surface_0"], names
    assert apply_to_all is False
    assert any("inlet" in n and "outlet" in n for n in notes)


def test_last_tier_skips_rather_than_extruding_everywhere(
    case_dir, no_name_detection,
):
    # The regression: every patch is a known non-wall. The old behaviour
    # applied the BL to all three; it must now be skipped outright.
    _write_boundary(
        case_dir,
        [("inlet", "patch"), ("outlet", "patch"), ("symmetry", "symmetry")],
    )
    names, apply_to_all, notes = poly_bl_patch_selection(case_dir)
    assert names is None, "must signal 'skip the BL', not 'apply everywhere'"
    assert apply_to_all is False
    assert any("skipped" in n for n in notes)


def test_unreadable_boundary_never_crashes_and_never_extrudes_everywhere(
    case_dir, no_name_detection,
):
    # No boundary file at all: no names means no candidates means skip.
    names, apply_to_all, notes = poly_bl_patch_selection(case_dir)
    assert names is None
    assert apply_to_all is False
    assert any("skipped" in n for n in notes)


def test_all_wall_keyword_patches_are_kept(case_dir, no_name_detection):
    # Positive wall signal in the *name* (no "wall" substring, no wall type)
    # must still survive the last tier.
    _write_boundary(
        case_dir,
        [("zeta_body", "patch"), ("alpha_casing", "patch"), ("inlet", "patch")],
    )
    names, _, _ = poly_bl_patch_selection(case_dir)
    assert names is not None
    assert set(names) == {"zeta_body", "alpha_casing"}
    assert len(names) == 2
