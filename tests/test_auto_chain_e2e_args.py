"""auto_chain_e2e --geom argument parsing (fast, no engine runs).

Guards the --geom override added for validating the real user geometry
(the DN1000 patch STL produced by the GUI fallback) through the same
chain the GUI runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from auto_chain_e2e import GEO, _parse_args  # noqa: E402


def test_default_case_venturi_with_corpus_geometry():
    case, wd, geom = _parse_args([])
    assert case == "venturi"
    assert wd == Path("C:/polyfoammesh_auto_venturi")
    assert geom == GEO / "venturi.stl"


def test_case_and_workdir_positions():
    case, wd, geom = _parse_args(["cylinder", "D:/tmp/x"])
    assert case == "cylinder"
    assert wd == Path("D:/tmp/x")
    assert geom == GEO / "cylinder.stl"


def test_geom_override_wins_over_the_corpus():
    case, wd, geom = _parse_args(
        ["dn1000_real", "C:/out", "--geom", "C:/in/patches.ascii.stl"],
    )
    assert case == "dn1000_real"
    assert wd == Path("C:/out")
    assert geom == Path("C:/in/patches.ascii.stl")


def test_geom_override_with_defaults():
    case, wd, geom = _parse_args(["--geom", "X:/g.stl"])
    assert case == "venturi"
    assert wd == Path("C:/polyfoammesh_auto_venturi")
    assert geom == Path("X:/g.stl")
