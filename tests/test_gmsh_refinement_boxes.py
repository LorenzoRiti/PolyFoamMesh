"""Tests for GMSH refinement-zone fields (core/gmsh_wrapper.py).

Verifies that manual refinement zones — the new 3D boxes and the legacy
spheres — are turned into the correct GMSH background fields (Box/Ball) with
refinement-only semantics (VOut = -1 so a zone never coarsens outside itself).
Requires gmsh, but no OpenFOAM and no GUI.
"""
from __future__ import annotations

import gmsh
import pytest

from polyfoammesh.core.gmsh_wrapper import (
    _add_refinement_zone_fields,
    _combine_zone_fields_with_bg,
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
    # Refinement only: it must not impose a size outside the box.
    assert gmodel.model.mesh.field.getNumber(tags[0], "VOut") == -1


def test_sphere_zone_creates_ball_field(gmodel):
    tags = _add_refinement_zone_fields(gmodel, [{
        "centre": (0.5, 0.5, 0.5), "radius": 0.25, "cell_size": 0.01,
    }])
    assert len(tags) == 1
    assert gmodel.model.mesh.field.getType(tags[0]) == "Ball"
    assert gmodel.model.mesh.field.getNumber(tags[0], "VIn") == pytest.approx(0.01)
    assert gmodel.model.mesh.field.getNumber(tags[0], "VOut") == -1


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
