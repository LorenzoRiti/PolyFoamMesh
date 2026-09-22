"""Corpus manifest: names, tiers, terrain labels, STL presence."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.corpus import (  # noqa: E402
    ALL_RUNNERS, CORPUS, OUR_RUNNERS, RUN_TIMEOUT_S,
    fast_tier, full_tier, geometry_path,
)

EXPECTED_NAMES = {
    "cylinder", "cube_duct", "groove1", "slot1", "venturi",
    "mini_valve", "thin_gap", "concave_bracket", "dirty_cad", "multi_inlet",
}


def test_corpus_has_exactly_the_ten_spec_geometries() -> None:
    assert {s.name for s in CORPUS} == EXPECTED_NAMES
    assert len(CORPUS) == 10


def test_runners_split_ours_vs_snappy() -> None:
    assert OUR_RUNNERS == ("hex", "poly", "gmsh_direct")
    assert set(OUR_RUNNERS) | {"snappy_base", "snappy_expert"} == set(ALL_RUNNERS)
    assert set(OUR_RUNNERS) <= set(ALL_RUNNERS)


def test_run_timeout_is_25_minutes() -> None:
    assert RUN_TIMEOUT_S == 1500


def test_fast_tier_is_geoms_1_to_5_plus_7() -> None:
    fast_names = {s.name for s in fast_tier()}
    assert fast_names == {"cylinder", "cube_duct", "groove1", "slot1", "venturi", "thin_gap"}


def test_full_tier_is_everything_and_superset_of_fast() -> None:
    full_names = {s.name for s in full_tier()}
    assert full_names == EXPECTED_NAMES
    assert {s.name for s in fast_tier()} <= full_names


def test_tiers_and_terrain_labels_are_valid() -> None:
    for spec in CORPUS:
        assert set(spec.tiers) <= {"fast", "full"}, spec.name
        assert "full" in spec.tiers, spec.name  # full always includes everything
        assert set(spec.terrain) <= {"A", "B", "C"}, spec.name
        assert spec.terrain, spec.name


def test_geometry_path_points_into_benchmarks_geometries() -> None:
    spec = CORPUS[0]
    p = geometry_path(spec)
    assert p.parent.name == "geometries"
    assert p.suffix == ".stl"
