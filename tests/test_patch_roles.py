"""Tests for the canonical patch-role classifier (core/patch_roles.py).

This module is the single source of truth for "is this boundary patch a
wall?" — the answer decides where prismatic boundary layers get extruded.
These tests are hermetic: no WSL, no OpenFOAM, no network.
"""
from __future__ import annotations

from cfmesh_autogui.core.patch_roles import (
    NON_WALL_ROLES,
    ROLE_EMPTY,
    ROLE_INLET,
    ROLE_OUTLET,
    ROLE_SYMMETRY,
    ROLE_UNKNOWN,
    ROLE_WALL,
    classify_patch,
    is_non_wall_patch,
    is_wall_patch,
    match_role,
    split_wall_patches,
)


def test_role_constants():
    assert ROLE_WALL == "wall"
    assert ROLE_INLET == "inlet"
    assert ROLE_OUTLET == "outlet"
    assert ROLE_SYMMETRY == "symmetry"
    assert ROLE_EMPTY == "empty"
    assert ROLE_UNKNOWN == "unknown"
    assert NON_WALL_ROLES == frozenset(
        {ROLE_INLET, ROLE_OUTLET, ROLE_SYMMETRY, ROLE_EMPTY}
    )


def test_classify_walls():
    for name in (
        "wall", "walls", "wall_001", "blade", "body", "hull", "wing",
        "foil", "surface", "surface_0", "boundary", "pressure_side",
        "suction_side", "patch.wall_1", "default",
    ):
        assert classify_patch(name) == ROLE_WALL, name


def test_classify_inlet():
    for name in (
        "inlet", "Inlet-02", "velocity_inlet", "velocityInlet",
        "pressure_inlet", "inflow", "ingresso", "entrance",
    ):
        assert classify_patch(name) == ROLE_INLET, name


def test_classify_outlet():
    for name in (
        "outlet", "pressure_outlet", "mass_flow_outlet", "outflow",
        "uscita", "exit", "opening", "farfield", "far_field",
    ):
        assert classify_patch(name) == ROLE_OUTLET, name


def test_classify_symmetry():
    for name in (
        "symmetry", "symmetryPlane", "symmetry_plane", "periodic",
        "cyclic", "wedge", "interface", "porous",
    ):
        assert classify_patch(name) == ROLE_SYMMETRY, name


def test_classify_empty():
    assert classify_patch("empty") == ROLE_EMPTY


def test_non_wall_role_wins_over_wall_keyword():
    # Precedence rule: a known non-wall role beats a wall keyword, so a
    # name containing both is treated as non-wall.
    assert classify_patch("inlet_wall") == ROLE_INLET
    assert classify_patch("wall_inlet") == ROLE_INLET


def test_is_wall_patch():
    assert is_wall_patch("wall")
    assert is_wall_patch("body")
    assert is_wall_patch("surface_0")
    assert not is_wall_patch("inlet")
    assert not is_wall_patch("outlet")
    assert not is_wall_patch("symmetry")


def test_is_non_wall_patch():
    assert is_non_wall_patch("inlet")
    assert is_non_wall_patch("outlet")
    assert is_non_wall_patch("symmetry")
    assert is_non_wall_patch("empty")
    assert not is_non_wall_patch("wall")
    assert not is_non_wall_patch("body")


def test_split_wall_patches():
    walls, excluded = split_wall_patches(
        ["inlet", "wall", "outlet", "symmetry", "blade"]
    )
    assert walls == ["wall", "blade"]
    assert excluded == [
        ("inlet", "inlet"),
        ("outlet", "outlet"),
        ("symmetry", "symmetry"),
    ]


def test_split_no_walls_returns_empty_not_everything():
    walls, excluded = split_wall_patches(["inlet", "outlet", "symmetry"])
    assert walls == [], "No walls found must mean no BL candidates"
    assert [n for n, _ in excluded] == ["inlet", "outlet", "symmetry"]


# ---------------------------------------------------------------------------
# match_role — the "does the name say anything at all?" primitive.
#
# classify_patch() defaults a silent name to wall, which is the right answer
# for BL extrusion but the wrong one for callers that can consult geometry.
# match_role() returns None instead, so those callers stay reachable.
# ---------------------------------------------------------------------------


def test_match_role_returns_none_when_name_is_silent():
    # "surface_*" is what GMSH emits for every patch — including the inlet —
    # so it must stay silent and let geometry decide. "solid_body_7" is NOT
    # silent: "body" is a positive wall keyword.
    for name in ("surface_0", "surface_12", "default", "random_name",
                 "boundary", "patch_3", "blockage"):
        assert match_role(name) is None, name
    assert match_role("solid_body_7") == ROLE_WALL


def test_match_role_detects_positive_wall_signal():
    for name in ("wall", "blade", "body", "wing", "housing", "casing",
                 "stator", "rotor", "nacelle", "piston", "valve"):
        assert match_role(name) == ROLE_WALL, name


def test_match_role_agrees_with_classify_when_name_is_not_silent():
    for name in ("inlet", "pressure_outlet", "symmetry", "empty", "wall",
                 "blade", "Inlet-02"):
        assert match_role(name) == classify_patch(name), name


def test_classify_keeps_wall_default_for_silent_names():
    # BL decisions have no fallback, so they still get the safe majority.
    for name in ("surface_0", "default", "random_name"):
        assert classify_patch(name) == ROLE_WALL, name
        assert is_wall_patch(name)


def test_silent_name_is_wall_for_bl_but_no_signal_for_inference():
    # The whole point of the two-level API: the same name is "wall" when a
    # decision is mandatory and "unknown" when geometry can still answer.
    name = "surface_0"
    assert classify_patch(name) == ROLE_WALL
    assert match_role(name) is None
    assert split_wall_patches([name]) == ([name], [])