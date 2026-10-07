"""Tests for GMSH refinement-zone fields (core/gmsh_wrapper.py).

Verifies that manual refinement zones — the new 3D boxes and the legacy
spheres — are turned into the correct GMSH background fields (Box/Ball).
Ungraded zones keep ``VOut = a huge outside size`` so a zone never coarsens
outside itself once MIN-combined; graded zones (``_prepare_refinement_zones``)
get a finite VOut equal to the background plus a ``Thickness`` so the
transition blends instead of stepping. Requires gmsh, but no OpenFOAM and no
GUI.
"""
from __future__ import annotations

import gmsh
import pytest

from polyfoammesh.core.gmsh_wrapper import (
    _ZONE_MAX_REFINEMENT,
    _ZONE_OUTSIDE_SIZE,
    _add_refinement_zone_fields,
    _combine_zone_fields_with_bg,
    _lower_length_min_for_zones,
    _prepare_refinement_zones,
)


@pytest.fixture(scope="module")
def gmodel():
    gmsh.initialize()
    # Do not actually mesh anything — fields can exist on an empty model.
    yield gmsh
    gmsh.finalize()


def test_box_zone_creates_box_field(gmodel):
    tags = _add_refinement_zone_fields(gmodel, [{
        "type": "box",
        "xmin": 0.1, "xmax": 0.9,
        "ymin": 0.2, "ymax": 0.8,
        "zmin": 0.0, "zmax": 1.0,
        "cell_size": 0.0025,
    }])
    assert len(tags) == 1
    assert gmodel.model.mesh.field.getType(tags[0]) == "Box"
    assert gmodel.model.mesh.field.getNumber(tags[0], "XMin") == pytest.approx(0.1)
    assert gmodel.model.mesh.field.getNumber(tags[0], "XMax") == pytest.approx(0.9)
    assert gmodel.model.mesh.field.getNumber(tags[0], "VIn") == pytest.approx(0.0025)
    # Refinement only: outside the box the field must ask for a HUGE size so
    # MIN-combining with the background field leaves the surroundings alone.
    # (It used to be -1, which the Min field took literally — GMSH then tried
    # to reach a negative target size and never finished. See
    # _ZONE_OUTSIDE_SIZE.)
    assert gmodel.model.mesh.field.getNumber(tags[0], "VOut") == \
        pytest.approx(_ZONE_OUTSIDE_SIZE)


def test_sphere_zone_creates_ball_field(gmodel):
    tags = _add_refinement_zone_fields(gmodel, [{
        "centre": (0.5, 0.5, 0.5), "radius": 0.25, "cell_size": 0.01,
    }])
    assert len(tags) == 1
    assert gmodel.model.mesh.field.getType(tags[0]) == "Ball"
    assert gmodel.model.mesh.field.getNumber(tags[0], "VIn") == pytest.approx(0.01)
    assert gmodel.model.mesh.field.getNumber(tags[0], "VOut") == \
        pytest.approx(_ZONE_OUTSIDE_SIZE)


def test_graded_box_zone_sets_finite_vout_and_thickness(gmodel):
    """A prepared (graded) zone must set a finite VOut + a Thickness.

    With VOut=1e22 the VIn->VOut ramp collapses (the huge value is reached
    after ~0 distance), so ``min(background, field)`` steps straight back to
    the background and the boundary stays a hard rectangular edge. Graded
    zones therefore carry ``background_size`` and a finite ``VOut``.
    """
    tags = _add_refinement_zone_fields(gmodel, [{
        "type": "box", "xmin": 0.1, "xmax": 0.9, "ymin": 0.2, "ymax": 0.8,
        "zmin": 0.0, "zmax": 1.0, "cell_size": 0.01,
        "thickness": 0.08, "background_size": 0.03,
    }])
    assert len(tags) == 1
    assert gmodel.model.mesh.field.getNumber(tags[0], "Thickness") == pytest.approx(0.08)
    assert gmodel.model.mesh.field.getNumber(tags[0], "VOut") == pytest.approx(0.03)
    assert gmodel.model.mesh.field.getNumber(tags[0], "VIn") == pytest.approx(0.01)


def test_graded_sphere_zone_sets_thickness(gmodel):
    tags = _add_refinement_zone_fields(gmodel, [{
        "centre": (0.5, 0.5, 0.5), "radius": 0.2, "cell_size": 0.01,
        "thickness": 0.05, "background_size": 0.03,
    }])
    assert gmodel.model.mesh.field.getNumber(tags[0], "Thickness") == pytest.approx(0.05)
    assert gmodel.model.mesh.field.getNumber(tags[0], "VOut") == pytest.approx(0.03)


def test_prepare_zones_clamps_excessive_refinement():
    """A target 10x finer than the background is clamped to the documented
    max ratio (4x per axis), and a graded thickness is derived. A tiny box
    keeps the cell budget out of the way so the ratio clamp is isolated."""
    prepared = _prepare_refinement_zones(
        [{"type": "box", "xmin": 0.0, "xmax": 0.01, "ymin": 0.0, "ymax": 0.01,
          "zmin": 0.0, "zmax": 0.01, "cell_size": 0.003}],
        background_size=0.03,
    )
    z = prepared[0]
    assert z["cell_size"] == pytest.approx(0.03 / _ZONE_MAX_REFINEMENT)
    assert z["background_size"] == pytest.approx(0.03)
    assert z["thickness"] > 0


def test_prepare_zones_keeps_reasonable_refinement_untouched():
    prepared = _prepare_refinement_zones(
        [{"type": "box", "xmin": 0.45, "xmax": 0.55, "ymin": 0.45, "ymax": 0.55,
          "zmin": 0.45, "zmax": 0.55, "cell_size": 0.01}],
        background_size=0.03,
    )
    assert prepared[0]["cell_size"] == pytest.approx(0.01)
    # thickness = min(4*0.03, 0.5*0.1) = 0.05
    assert prepared[0]["thickness"] == pytest.approx(0.05)


def test_prepare_zones_does_not_mutate_input():
    original = {"type": "box", "xmin": 0, "xmax": 1, "ymin": 0, "ymax": 1,
                "zmin": 0, "zmax": 1, "cell_size": 0.001}
    _prepare_refinement_zones([original], background_size=0.05)
    assert original["cell_size"] == pytest.approx(0.001)
    assert "thickness" not in original


def test_prepare_zones_coarsens_to_fit_cell_budget():
    """An enormous zone must be coarsened until the cells it ADDS over the
    background fit the budget floor, instead of exploding the mesh."""
    from polyfoammesh.core.gmsh_wrapper import (
        _TET_VOLUME_PER_EDGE3, _ZONE_MAX_CELLS_DEFAULT,
    )
    prepared = _prepare_refinement_zones(
        [{"type": "box", "xmin": 0.0, "xmax": 1.0, "ymin": 0.0, "ymax": 1.0,
          "zmin": 0.0, "zmax": 1.0, "cell_size": 0.01}],
        background_size=0.03,
    )
    z = prepared[0]
    t = z["cell_size"]
    assert t < 0.03  # still finer than the background (real refinement)
    added = (1.0 / _TET_VOLUME_PER_EDGE3) * (1.0 / t ** 3 - 1.0 / 0.03 ** 3)
    assert added <= _ZONE_MAX_CELLS_DEFAULT * 1.05


def test_zones_lower_characteristic_length_min(gmodel):
    """A zone target finer than the active floor must drop the floor.

    GMSH applies Mesh.CharacteristicLengthMin as a hard floor AFTER the
    background field, so without this a zone asking for 0.0125 under a
    0.015 floor is silently clamped back to the bulk size.
    """
    gmodel.option.setNumber("Mesh.CharacteristicLengthMin", 0.015)
    new_floor = _lower_length_min_for_zones(
        gmodel, [{"type": "box", "cell_size": 0.0125}]
    )
    assert new_floor == pytest.approx(0.0125)
    assert gmodel.option.getNumber("Mesh.CharacteristicLengthMin") == \
        pytest.approx(0.0125)


def test_zones_never_raise_the_floor(gmodel):
    """A zone coarser than the floor must not coarsen the whole mesh."""
    gmodel.option.setNumber("Mesh.CharacteristicLengthMin", 0.005)
    new_floor = _lower_length_min_for_zones(
        gmodel, [{"type": "box", "cell_size": 0.02}]
    )
    assert new_floor == pytest.approx(0.005)
    assert gmodel.option.getNumber("Mesh.CharacteristicLengthMin") == \
        pytest.approx(0.005)


def test_mixed_zones_combine(gmodel):
    tags = _add_refinement_zone_fields(gmodel, [
        {"type": "box", "xmin": 0, "xmax": 1, "ymin": 0, "ymax": 1,
         "zmin": 0, "zmax": 1, "cell_size": 0.005},
        {"centre": (0, 0, 0), "radius": 0.3, "cell_size": 0.02},
    ])
    types = sorted(gmodel.model.mesh.field.getType(t) for t in tags)
    assert types == ["Ball", "Box"]


def test_missing_box_coord_is_rejected_not_crashed(gmodel):
    tags = _add_refinement_zone_fields(gmodel, [
        {"type": "box", "xmin": 0, "xmax": 1, "ymin": 0, "ymax": 1,
         "zmin": 0, "cell_size": 0.005},  # zmax missing
    ])
    assert tags == []


# ---------------------------------------------------------------------------
# Regression: manual refinement zones must MIN-combine with the geometry
# adaptive background field, not REPLACE it (docs/dev/
# solution_adaptive_handoff.md §1.4). Drawing one manual box used to discard
# curvature/small-feature/gap sizing for the whole mesh.
# ---------------------------------------------------------------------------
def _adaptive_bg(gmodel) -> int:
    """Stand-in for the field _configure_adaptive_sizing installs."""
    f = gmodel.model.mesh.field.add("MathEval")
    gmodel.model.mesh.field.setString(f, "F", "0.05")
    gmodel.model.mesh.field.setAsBackgroundMesh(f)
    return f


def test_single_zone_min_combined_with_existing_bg(gmodel):
    bg = _adaptive_bg(gmodel)
    zone = _add_refinement_zone_fields(gmodel, [
        {"type": "box", "xmin": 0, "xmax": 1, "ymin": 0, "ymax": 1,
         "zmin": 0, "zmax": 1, "cell_size": 0.005},
    ])
    combined = _combine_zone_fields_with_bg(gmodel, zone, bg)
    # Two size sources -> a Min field, NOT the bare zone field.
    assert combined != zone[0]
    assert gmodel.model.mesh.field.getType(combined) == "Min"
    assert list(gmodel.model.mesh.field.getNumbers(combined, "FieldsList")) == [bg, zone[0]]
    # ... and it is actually the active background mesh (a Min field).
    assert gmodel.model.mesh.field.getType(combined) == "Min"


def test_multiple_zones_keep_existing_bg_in_the_min(gmodel):
    bg = _adaptive_bg(gmodel)
    zones = _add_refinement_zone_fields(gmodel, [
        {"type": "box", "xmin": 0, "xmax": 1, "ymin": 0, "ymax": 1,
         "zmin": 0, "zmax": 1, "cell_size": 0.005},
        {"centre": (0, 0, 0), "radius": 0.3, "cell_size": 0.01},
    ])
    combined = _combine_zone_fields_with_bg(gmodel, zones, bg)
    fields = list(gmodel.model.mesh.field.getNumbers(combined, "FieldsList"))
    assert fields[0] == bg
    assert fields[1:] == zones


def test_zones_without_bg_stay_bare_zone_field(gmodel):
    zone = _add_refinement_zone_fields(gmodel, [
        {"type": "box", "xmin": 0, "xmax": 1, "ymin": 0, "ymax": 1,
         "zmin": 0, "zmax": 1, "cell_size": 0.005},
    ])
    combined = _combine_zone_fields_with_bg(gmodel, zone, None)
    assert combined == zone[0]


def test_no_zones_leaves_bg_untouched(gmodel):
    bg = _adaptive_bg(gmodel)
    combined = _combine_zone_fields_with_bg(gmodel, [], bg)
    assert combined == bg
