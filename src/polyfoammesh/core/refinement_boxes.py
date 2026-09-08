"""Refinement-box model for manual mesh refinement.

A "refinement box" is an axis-aligned cuboid the user places in the 3D viewer
and sizes with drag arrows, carrying a simple integer *level*:

    level 1  -> target cell size = base size   (no extra refinement)
    level 2  -> base / 2            (cells halved per axis)
    level 3  -> base / 4            (cells quartered)
    level N  -> base / 2**(N-1)

This is the "in a simple way I decide where to refine" knob: the user only
ever picks a box position/size (with the arrows) and a level; the cell size is
derived, never typed.
"""

from __future__ import annotations

import math

# Box dict schema (keys that must be present, in the GMSH zone vocabulary):
#   type: "box"
#   xmin, xmax, ymin, ymax, zmin, zmax: float metres
#   level: int >= 1
#   cell_size: float metres (derived from level, kept so the mesh layer is
#              oblivious to the level concept)
BOX_KEYS = ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")


def box_cell_size(level: int, base_size: float) -> float:
    """Target cell size inside a box of the given *level*.

    level 1 = base (no refinement), 2 = half, 3 = quarter, ... Each extra
    level halves the previous cell size (roughly 8x the cells per box).
    """
    level = max(int(level), 1)
    return float(base_size) / float(2 ** (level - 1))


def suggest_box(bbox_min: tuple, bbox_max: tuple,
                fraction: float = 0.25, level: int = 2,
                base_size: float = 0.01) -> dict:
    """A default box of *fraction* of the model bounding box, centred on it.

    Sized relative to the geometry so the very first box is usable immediately;
    the user then drags the arrows to actually place it.
    """
    lo = [float(v) for v in bbox_min]
    hi = [float(v) for v in bbox_max]
    ext = [hi[i] - lo[i] for i in range(3)]
    c = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
    half = [max(e * fraction / 2.0, 1e-6) for e in ext]
    box = {
        "type": "box",
        "xmin": c[0] - half[0], "xmax": c[0] + half[0],
        "ymin": c[1] - half[1], "ymax": c[1] + half[1],
        "zmin": c[2] - half[2], "zmax": c[2] + half[2],
        "level": int(level),
        "cell_size": box_cell_size(level, base_size),
    }
    return box


def normalize_box(box: dict) -> dict:
    """Enforce min < max on every axis (drag clamps already, but be safe)."""
    for i in range(3):
        lo_ = BOX_KEYS[2 * i]
        hi_ = BOX_KEYS[2 * i + 1]
        if box[lo_] >= box[hi_]:
            box[hi_] = box[lo_] + 1e-6
    if "cell_size" not in box or not box.get("cell_size"):
        box["cell_size"] = box_cell_size(box.get("level", 2), 0.01)
    return box


def boxes_to_zones(boxes: list) -> list:
    """Box dicts are already the GMSH zone schema — return them verbatim
    (normalized). Kept as a named helper so callers read clearly."""
    return [normalize_box(dict(b)) for b in boxes]


def apply_level(box: dict, level: int, base_size: float) -> dict:
    """Change a box's level, recomputing its target cell size."""
    box = dict(box)
    box["level"] = int(level)
    box["cell_size"] = box_cell_size(int(level), base_size)
    return box


def label_for(box: dict) -> str:
    """Short human label for the panel list."""
    x0, x1 = box["xmin"], box["xmax"]
    y0, y1 = box["ymin"], box["ymax"]
    z0, z1 = box["zmin"], box["zmax"]
    w, h, d = x1 - x0, y1 - y0, z1 - z0
    return (f"Box L{int(box.get('level', 2))} "
            f"({w:.3g}×{h:.3g}×{d:.3g} m @ "
            f"{box['cell_size']:.4g} m)")


def base_bulk_size(cross_scale: float, cells_across: int,
                   fallback: float = 0.01) -> float:
    """Cross-section bulk cell size used as the level-1 base (mirrors the
    adaptive sizing rule: cross_scale / cells_across)."""
    if cells_across and cells_across > 0 and math.isfinite(cross_scale) \
            and cross_scale > 0:
        return cross_scale / float(cells_across)
    return float(fallback)
