"""Tests for GMSH refinement-zone fields (core/gmsh_wrapper.py).

Verifies that manual refinement zones â€” the new 3D boxes and the legacy
spheres â€” are turned into the correct GMSH background fields (Box/Ball) with
refinement-only semantics (VOut = -1 so a zone never coarsens outside itself).
Requires gmsh, but no OpenFOAM and no GUI.
"""
from __future__ import annotations

import gmsh
import pytest

from polyfoammesh.core.gmsh_wrapper import _add_refinement_zone_fields


@pytest.fixture(scope="module")
def gmodel():
    gmsh.initialize()
    # Do not actually mesh anything â€” fields can exist on an empty model.
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
