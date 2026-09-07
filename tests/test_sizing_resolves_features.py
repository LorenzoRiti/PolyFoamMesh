"""The sizing algorithm must actually see small geometric features.

Two bugs made it blind, both caught by the benchmark showing a constricted pipe
and a plain pipe producing the *identical* cell count:

1. `_sample_thickness_one_mesh` took the FARTHEST ray intersection (`.max()`)
   in both directions, so it measured the extent of the whole model rather than
   the local wall-to-wall distance. Every sample collapsed to the same number.
2. The min-cell size came from the p5/p10 percentile. A feature that matters is
   usually small in *area* — a 0.06 m throat in a 0.2 m rod covers ~3% of the
   surface — so p5 never moved off the bulk value.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:  # cadquery/OCP native libs need their dir on PATH on Windows
    import OCP as _ocp

    _d = os.path.dirname(_ocp.__file__)
    if _d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

import pytest

cq = pytest.importorskip("cadquery")

from cfmesh_autogui.core.geometry import (  # noqa: E402
    analyze_local_thickness,
    classify_faces,
    suggest_cell_sizes,
    tessellate_patches,
)

# Fixture dimensions are authored in cadquery's native MILLIMETRES and
# the test asserts in METRES — the app's internal unit contract (cadquery
# normalizes to mm; tessellate_patches converts mm -> m). This was the
# documented pre-existing failure (residual_risks.md): the fixtures used
# ROD_RADIUS=0.1 (mm) while the assertions expected 0.2 m, i.e. a rod
# 1000x smaller than the test believed. The fixtures are now authored at
# metre scale in mm (100 mm = 0.1 m) so both sides agree.
ROD_RADIUS_MM = 100      # 0.1 m
THROAT_RADIUS_MM = 30    # 0.03 m
ROD_RADIUS = 0.1
THROAT_RADIUS = 0.03


def _rod():
    return tessellate_patches(
        classify_faces(cq.Workplane("XY").circle(ROD_RADIUS_MM).extrude(1000).val())
    )


def _constricted_rod():
    lower = cq.Workplane("XY").circle(ROD_RADIUS_MM).extrude(450)
    waist = cq.Workplane("XY").circle(THROAT_RADIUS_MM).extrude(100).translate((0, 0, 450))
    upper = cq.Workplane("XY").circle(ROD_RADIUS_MM).extrude(450).translate((0, 0, 550))
    return tessellate_patches(classify_faces(lower.union(waist).union(upper).val()))


def test_thickness_measures_local_distance_not_model_extent():
    """A plain rod's local thickness is its diameter, everywhere."""
    a = analyze_local_thickness(_rod(), samples=768)
    assert a["n_samples"] > 0
    assert a["p50"] == pytest.approx(2 * ROD_RADIUS, rel=0.05)


def test_constriction_is_visible_in_the_thickness_distribution():
    """p1 must pick up the throat; p50 must still describe the bulk."""
    a = analyze_local_thickness(_constricted_rod(), samples=768)
    assert a["p1"] == pytest.approx(2 * THROAT_RADIUS, rel=0.1)
    assert a["p50"] == pytest.approx(2 * ROD_RADIUS, rel=0.1)
    # The whole point: the distribution is not flat.
    assert a["p1"] < a["p50"] * 0.5


def test_constriction_drives_a_smaller_min_cell():
    """The regression the benchmark exposed: both pipes sized identically."""
    _, plain_min = suggest_cell_sizes(_rod(), detail="medium")
    _, constricted_min = suggest_cell_sizes(_constricted_rod(), detail="medium")
    assert constricted_min < plain_min, (
        f"constricted rod must get a finer min cell than a plain rod "
        f"(got {constricted_min} vs {plain_min})"
    )


def test_min_cell_can_resolve_the_throat():
    """At least ~2 cells across the narrowest passage."""
    _, min_cell = suggest_cell_sizes(_constricted_rod(), detail="medium")
    assert min_cell <= (2 * THROAT_RADIUS) / 2.0
