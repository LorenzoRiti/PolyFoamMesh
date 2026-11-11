"""A sheet-metal part loaded as the fluid domain must be flagged before the
run (Vaysa_lame: 102 m2 of 6 mm plate -> ~53M cells, GMSH 'frozen')."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core.geometry import thin_domain_warnings  # noqa: E402


def test_sheet_metal_part_is_flagged():
    # measured on Vaysa_lame_001.stp
    out = thin_domain_warnings(0.309, 102.5, (7.05, 10.3, 2.5), 52_989_965)
    assert any("THIN SHELL" in m for m in out)
    assert any("53 million" in m for m in out)


def test_ordinary_fluid_domain_is_quiet():
    # final.step: 3 m pipe, 253 mm bore, 0.122 m3, 2.48 m2
    assert thin_domain_warnings(0.1218, 2.48, (3.0, 0.2533, 0.2533), 173_483) == []
