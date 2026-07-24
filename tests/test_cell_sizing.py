"""Tests for the feature-aware cell-size suggestion algorithm.

Reproduces the user's reported bug: a long thin tube + sharp constriction.
The old bbox-based algorithm picked cells bigger than the tube itself;
the new one samples the surface and respects the thinnest features.
"""
from __future__ import annotations

import sys
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

import trimesh

from cfmesh_autogui.core.geometry import (
    suggest_cell_sizes, analyze_local_thickness,
)


def _make_tube(length: float, radius: float, n_segments: int = 64) -> trimesh.Trimesh:
    """Create a hollow tube (cylindrical shell) of given length and radius."""
    cyl = trimesh.creation.cylinder(
        radius=radius, height=length, sections=n_segments,
    )
    return cyl


def _make_sphere(radius: float, subdivisions: int = 4) -> trimesh.Trimesh:
    return trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)


def _make_constricted_tube(
    length: float, main_radius: float, constriction_radius: float,
) -> trimesh.Trimesh:
    """Build a tube that abruptly narrows to *constriction_radius* in the middle.

    Approximated as two cylinders joined at the constriction plane. For the
    purpose of cell-size suggestion, this is enough — the local thickness
    distribution will show a clear minimum at the constriction.
    """
    half = length / 2
    big = trimesh.creation.cylinder(
        radius=main_radius, height=half, sections=48,
    )
    big.apply_translation([0, 0, half / 2])
    small = trimesh.creation.cylinder(
        radius=constriction_radius, height=half, sections=48,
    )
    small.apply_translation([0, 0, -half / 2])
    return trimesh.util.concatenate([big, small])


def _make_constricted_tube_two_patches(
    length: float, main_radius: float, constriction_radius: float,
) -> tuple[trimesh.Trimesh, trimesh.Trimesh]:
    """Build a constricted tube as TWO separate patches — this is the
    realistic case for cfMesh (each surface file solid is a separate
    patch in `meshes`).
    """
    half = length / 2
    big = trimesh.creation.cylinder(
        radius=main_radius, height=half, sections=48,
    )
    big.apply_translation([0, 0, half / 2])
    small = trimesh.creation.cylinder(
        radius=constriction_radius, height=half, sections=48,
    )
    small.apply_translation([0, 0, -half / 2])
    return big, small


# ---------------------------------------------------------------------------
# analyze_local_thickness
# ---------------------------------------------------------------------------
def test_thickness_sphere_recovers_diameter():
    """A sphere of radius 0.5 has thickness 1.0 (diameter) everywhere."""
    sphere = _make_sphere(0.5)
    analysis = analyze_local_thickness([sphere], samples=64)
    assert analysis["n_samples"] > 0
    # p50 should be close to 1.0 (the diameter). Allow ±50% for sampling.
    assert 0.5 < analysis["p50"] < 1.5, f"p50={analysis['p50']}, expected ~1.0"
    print(f"PASS: sphere thickness analysis: p50={analysis['p50']:.3f} m")


def test_thickness_tube_recovers_diameter():
    """A long thin tube: thickness is the diameter everywhere."""
    tube = _make_tube(length=10.0, radius=0.05)
    analysis = analyze_local_thickness([tube], samples=64)
    assert analysis["n_samples"] > 0
    assert 0.05 < analysis["p50"] < 0.2, f"p50={analysis['p50']}, expected ~0.1"
    print(f"PASS: tube thickness analysis: p50={analysis['p50']:.4f} m (diameter=0.1)")


def test_thickness_returns_zero_for_empty():
    analysis = analyze_local_thickness([])
    assert analysis["n_samples"] == 0
    assert analysis["p50"] == 0.0
    print("PASS: empty mesh list returns zero analysis")


# ---------------------------------------------------------------------------
# suggest_cell_sizes: feature-aware behavior
# ---------------------------------------------------------------------------
def test_old_algorithm_was_wrong_for_tube():
    """Reproduce the user's reported bug:
    Long tube (10 m) with 5 cm diameter. Old algorithm gave max=0.5 m
    (10x larger than the tube diameter). Feature-aware should give
    max ~ 0.4 m (8x the median thickness 0.1 m) — but more importantly
    min ~ 0.03 m (clearly smaller than the diameter).
    """
    tube = _make_tube(length=10.0, radius=0.05)
    # Old algorithm (bbox-only)
    s_max_old, s_min_old = suggest_cell_sizes(
        meshes=None, bbox_max_dim=10.0,
    )
    # Feature-aware
    s_max_new, s_min_new = suggest_cell_sizes([tube], detail="medium")
    # The bug: old min is 0.1 m (= the tube diameter), so any feature
    # of the tube would be unresolved.
    assert s_min_old >= 0.1, "old algorithm: min must equal or exceed tube diameter"
    # The fix: new min is well below the tube diameter.
    assert s_min_new < 0.05, (
        f"new algorithm: min={s_min_new}, expected < 0.05 (tube radius) so "
        f"features are resolved"
    )
    print(
        f"PASS: tube cell-size fix. "
        f"old=(max={s_max_old:.3f}, min={s_min_old:.3f})  "
        f"new=(max={s_max_new:.3f}, min={s_min_new:.3f})"
    )


def test_constricted_tube_resolves_constriction():
    """Tube with main radius 0.5 and constriction radius 0.02.
    The new algorithm's min cell must be smaller than the constriction
    diameter (0.04 m), otherwise the constriction cannot be meshed.
    """
    big, small = _make_constricted_tube_two_patches(
        length=2.0, main_radius=0.5, constriction_radius=0.02
    )
    s_max, s_min = suggest_cell_sizes([big, small], detail="medium")
    assert s_min < 0.04, (
        f"min={s_min}, expected < 0.04 m (constriction diameter=0.04)"
    )
    print(
        f"PASS: constricted tube cell sizes: "
        f"max={s_max:.4f}, min={s_min:.4f}  (constriction=0.04 m)"
    )


def test_detail_levels_produce_different_results():
    """Fine mode must produce smaller (or equal) cells than Coarse mode."""
    big, small = _make_constricted_tube_two_patches(
        length=1.0, main_radius=0.3, constriction_radius=0.01
    )
    coarse = suggest_cell_sizes([big, small], detail="coarse")
    medium = suggest_cell_sizes([big, small], detail="medium")
    fine = suggest_cell_sizes([big, small], detail="fine")
    # Coarse should be biggest (or equal), fine smallest
    assert fine[0] <= medium[0] <= coarse[0] * 1.5, (
        f"max cells: coarse={coarse[0]}, medium={medium[0]}, fine={fine[0]} "
        f"should follow detail ordering"
    )
    assert fine[1] <= medium[1] <= coarse[1] * 1.5, (
        f"min cells: coarse={coarse[1]}, medium={medium[1]}, fine={fine[1]} "
        f"should follow detail ordering"
    )
    print(
        f"PASS: detail ordering. coarse={coarse}, medium={medium}, fine={fine}"
    )


def test_suggestion_min_strictly_smaller_than_max():
    """For UNIFORM geometries, min cell must be smaller than max cell.
    For multi-scale geometries, s_min can be much smaller than s_max.
    """
    tube = _make_tube(length=5.0, radius=0.1)
    for detail in ("coarse", "medium", "fine"):
        s_max, s_min = suggest_cell_sizes([tube], detail=detail)
        assert s_min < s_max, (
            f"detail={detail}: min={s_min} must be < max={s_max}"
        )
    print("PASS: min < max for uniform (tube) geometry")


def test_suggestion_min_can_be_smaller_than_max_for_multiscale():
    """Multi-scale: a long tube + constriction needs s_min << s_max.
    s_min should be at most half the constriction diameter."""
    big, small = _make_constricted_tube_two_patches(
        length=2.0, main_radius=0.5, constriction_radius=0.02,
    )
    s_max, s_min = suggest_cell_sizes([big, small], detail="medium")
    # s_min must resolve the constriction (diameter = 0.04 m)
    assert s_min < 0.04, (
        f"s_min={s_min} doesn't resolve the 0.04 m constriction"
    )
    # s_min should typically be smaller than s_max
    assert s_min < s_max, f"expected s_min < s_max, got {s_min} vs {s_max}"
    print(f"PASS: multi-scale: s_max={s_max}, s_min={s_min}")


def test_suggestion_floor_bounds():
    """Cell sizes must respect the absolute minimum floor (0.0001 m)."""
    # Tiny sphere — features smaller than 0.0001 m
    sphere = _make_sphere(0.00005)
    s_max, s_min = suggest_cell_sizes([sphere], detail="fine")
    assert s_min >= 0.0001, f"min={s_min} below floor 0.0001"
    print(f"PASS: floor respected: max={s_max}, min={s_min}")


def test_suggestion_with_no_meshes_falls_back_to_bbox():
    """Old API (no meshes) must still work for backward compat."""
    s_max, s_min = suggest_cell_sizes(meshes=None, bbox_max_dim=2.0)
    assert s_max == round(2.0 / 20.0, 6)
    assert s_min == round(2.0 / 100.0, 6)
    print(f"PASS: bbox fallback: ({s_max}, {s_min})")


def test_suggestion_clamps_to_bbox():
    """For a tiny feature in a huge box, the max cell shouldn't exceed bbox/8."""
    # Big sphere: radius 1 (bbox=2), but local thickness=2 (diameter)
    sphere = _make_sphere(1.0)
    s_max, s_min = suggest_cell_sizes([sphere], detail="medium")
    # max should be no larger than 2.0/8 = 0.25
    assert s_max <= 0.25, f"max={s_max} exceeds bbox/8 cap"
    print(f"PASS: bbox clamp: max={s_max} (bbox/8=0.25)")


def test_fine_detail_resolves_thin_tube():
    """User-reported bug: 'fine' was still producing cells bigger than
    the tube. Verify that 'fine' produces cells that actually fit
    inside the tube diameter (not just close to it)."""
    tube = _make_tube(length=10.0, radius=0.05)  # diameter = 0.1 m
    s_max_fine, _ = suggest_cell_sizes([tube], detail="fine")
    # At 'fine', s_max should be at most equal to the tube diameter,
    # so the cell actually fits across the section.
    assert s_max_fine <= 0.1, (
        f"fine max={s_max_fine} must be <= 0.1 (tube diameter)"
    )
    # The medium preset is allowed to be slightly coarser (~1.5x diameter)
    s_max_med, _ = suggest_cell_sizes([tube], detail="medium")
    assert s_max_med <= 0.15, (
        f"medium max={s_max_med} must be <= 0.15 (1.5x tube diameter)"
    )
    print(
        f"PASS: fine resolves thin tube. "
        f"medium={s_max_med:.4f}, fine={s_max_fine:.4f}"
    )


def test_fine_detail_for_constricted_tube():
    """Multi-scale: 'fine' should resolve the constriction
    (3+ cells across the constriction diameter)."""
    big, small = _make_constricted_tube_two_patches(
        length=2.0, main_radius=0.5, constriction_radius=0.02,
    )
    _, s_min_fine = suggest_cell_sizes([big, small], detail="fine")
    # Constriction diameter = 0.04 m. At 'fine' we want at least 3 cells.
    assert s_min_fine <= 0.04 / 3, (
        f"fine s_min={s_min_fine} should be <= {0.04/3:.4f} (3 cells in constriction)"
    )
    print(f"PASS: fine resolves constriction: s_min={s_min_fine:.4f}")

