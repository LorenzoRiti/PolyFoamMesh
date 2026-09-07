"""Tests for the cross-section-aware bulk sizing (core/gmsh_wrapper.py).

The automatic path used to size the bulk off the LARGEST bounding-box
dimension — on an elongated valve (3.0 x 0.255 x 0.255 m) that put 9 cm cells
across a 25.5 cm passage, i.e. under 3 cells across the bore. The fix sizes
the bulk from the CROSS-SECTION (median bbox dimension) divided by
``cells_across``. These tests lock the constants and the resolution formula
without needing a live GMSH session.
"""
from __future__ import annotations

from polyfoammesh.core.gmsh_wrapper import _GMSH_DETAIL

# Parte4.stp bounding box (metres): 3.0 x 0.255 x 0.255.
VALVE = {"max_extent": 3.0, "cross_scale": 0.255}


def test_every_detail_level_defines_cells_across():
    for level, df in _GMSH_DETAIL.items():
        assert df.get("cells_across", 0) > 0, level
        assert df["min_size"] < df["max_size"], level


def test_cells_across_monotonic_with_fineness():
    vals = [_GMSH_DETAIL[k]["cells_across"]
            for k in ("very_coarse", "coarse", "medium", "fine", "very_fine")]
    assert vals == sorted(vals)


def test_medium_valve_bulk_resolution():
    """The headline number from the fix: ~1.28 cm cells across a 25.5 cm
    bore (20 cells), not 9 cm / 3 cells."""
    df = _GMSH_DETAIL["medium"]
    coarse_max_cross = VALVE["cross_scale"] / df["cells_across"]
    old_extent_based = VALVE["max_extent"] * 0.02 * df["max_mult"]
    # 0.255 / 20 = 0.01275 m = 1.275 cm.
    assert round(coarse_max_cross, 5) == 0.01275
    # The new size is genuinely a cross-section size: far smaller than the
    # old length-derived one, and <= 20x finer.
    assert coarse_max_cross < old_extent_based
    assert old_extent_based / coarse_max_cross > 5


def test_fine_valve_bulk_resolution():
    df = _GMSH_DETAIL["fine"]
    coarse_max_cross = VALVE["cross_scale"] / df["cells_across"]
    assert round(coarse_max_cross, 5) == 0.00797  # 32 cells across 0.255 m


def test_cubic_domain_conservative():
    """On a cubic domain the cross-section size must NOT be finer than the
    old extent-based rule — the min() must simply pick the extent one."""
    cube = {"max_extent": 1.0, "cross_scale": 1.0}
    df = _GMSH_DETAIL["medium"]
    coarse_max_cross = cube["cross_scale"] / df["cells_across"]       # 0.05
    old_extent_based = cube["max_extent"] * 0.02 * df["max_mult"]     # 0.03
    assert coarse_max_cross > old_extent_based
    assert min(coarse_max_cross, old_extent_based) == old_extent_based
