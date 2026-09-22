"""Comparison corpus: the 10 geometries, their tiers and terrain labels.

Single source of truth for what run_comparison.py executes. Tier/terrain
semantics come from docs/superpowers/specs/2026-09-22-beat-snappy-benchmark-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
GEOMETRIES_DIR = _HERE / "geometries"
RESULTS_DIR = _HERE / "results"

RUN_TIMEOUT_S = 1500  # spec D6: hard cap per geometry x runner run

ALL_RUNNERS: tuple[str, ...] = ("hex", "poly", "gmsh_direct", "snappy_base", "snappy_expert")
OUR_RUNNERS: tuple[str, ...] = ("hex", "poly", "gmsh_direct")


@dataclass(frozen=True)
class GeometrySpec:
    name: str
    stl: str
    tiers: tuple[str, ...]
    terrain: tuple[str, ...]
    location_in_mesh: tuple[float, float, float] | None = None


CORPUS: list[GeometrySpec] = [
    GeometrySpec("cylinder", "cylinder.stl", ("fast", "full"), ("A",)),
    GeometrySpec("cube_duct", "cube_duct.stl", ("fast", "full"), ("A",)),
    GeometrySpec("groove1", "groove1.stl", ("fast", "full"), ("A", "B")),
    GeometrySpec("slot1", "slot1.stl", ("fast", "full"), ("A", "C")),
    GeometrySpec("venturi", "venturi.stl", ("fast", "full"), ("A", "B")),
    GeometrySpec("mini_valve", "mini_valve.stl", ("full",), ("B", "C")),
    GeometrySpec("thin_gap", "thin_gap.stl", ("fast", "full"), ("C",)),
    GeometrySpec("concave_bracket", "concave_bracket.stl", ("full",), ("C",)),
    GeometrySpec("dirty_cad", "dirty_cad.stl", ("full",), ("C",)),
    GeometrySpec("multi_inlet", "multi_inlet.stl", ("full",), ("B",)),
]


def geometry_path(spec: GeometrySpec) -> Path:
    return GEOMETRIES_DIR / spec.stl


def fast_tier() -> list[GeometrySpec]:
    return [s for s in CORPUS if "fast" in s.tiers]


def full_tier() -> list[GeometrySpec]:
    return list(CORPUS)
