"""Refinement boxes reach fTetWild through the volume sizing field."""
import numpy as np
import pytest

pv = pytest.importorskip("pyvista")
from polyfoammesh.core.ftetwild_wrapper import _build_sizing_field  # noqa: E402


def _surf():
    return pv.Box(bounds=(0, 1, 0, 1, 0, 1)).triangulate()


def test_zone_refines_inside_box_only():
    zone = {"type": "box", "cell_size": 0.05, "xmin": 0.0, "xmax": 0.3,
            "ymin": 0.0, "ymax": 0.3, "zmin": 0.0, "zmax": 0.3}
    grid, st = _build_sizing_field(_surf(), 0.25, None, zones=[zone],
                                   thickness=False)
    assert grid is not None and st["zone_points"] > 0
    vals = np.asarray(grid.point_data["target_edge"])
    pts = np.asarray(grid.points)
    inside = (pts <= 0.3).all(axis=1)
    assert vals[inside].min() == pytest.approx(0.05)
    far = (pts >= 0.7).all(axis=1)
    assert vals[far].min() == pytest.approx(0.25)


def test_no_zone_no_refinement_when_thickness_off():
    grid, st = _build_sizing_field(_surf(), 0.25, None, zones=None,
                                   thickness=False)
    assert st["zone_points"] == 0
    assert np.asarray(grid.point_data["target_edge"]).max() == pytest.approx(0.25)
