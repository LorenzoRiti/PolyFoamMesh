"""Pure parts of tools/compare_mesher_bl.py: no meshing and no WSL."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "tools" / "compare_mesher_bl.py"
_SPEC = importlib.util.spec_from_file_location("compare_mesher_bl", _PATH)
cmp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cmp)

_TABLE = """
patch  faces    layers   overall thickness
                         [m]       [%]
-----  -----    ------   ---       ---
{rows}

Layer mesh : cells:100
"""


def test_snappy_layers_are_face_weighted_and_use_last_table():
    first = _TABLE.format(rows="wall  10  9.0  0.1  99.0")
    last = _TABLE.format(rows="wall  300  4.0  0.01  80.0\nbody  100  0.0  0  0.0")
    parsed = cmp.parse_snappy_layers(first + last)
    assert parsed == {"faces": 400, "avg_layers": 3.0, "thickness_pct": 60.0}


def test_snappy_without_table_is_unknown_but_zero_layers_is_no_bl():
    assert cmp.parse_snappy_layers("snappyHexMesh ended") is None
    row = {"engine": "snappy", "bl": cmp.parse_snappy_layers(_TABLE.format(rows="w 5 0 0 0"))}
    assert row["bl"]["faces"] == 5
    assert not cmp.has_bl(row)


def test_summary_does_not_credit_fallback_or_poly_without_bl():
    rows = [
        {"case": "a", "engine": "gmsh", "status": "DONE", "verdict": "Mesh OK",
         "bl": {"prisms": 10}, "non_ortho": 40.0, "skew": 2.0, "seconds": 5.0},
        {"case": "b", "engine": "gmsh", "status": "DONE", "verdict": "Mesh OK",
         "bl": {"prisms": 0}},
        {"case": "c", "engine": "gmsh", "status": "DONE", "verdict": "Mesh OK",
         "bl": {"prisms": 10}, "fallback_used": True},
        {"case": "d", "engine": "gmsh", "status": "FAILED", "detail": "tet"},
        {"case": "e", "engine": "gmsh", "status": "SKIPPED"},
    ]
    assert cmp.summarize(rows)["gmsh"] == {
        "runs": 4, "fallback_not_credited": 1, "mesh_ok": 2, "mesh_ok_with_bl": 1,
        "median_non_ortho": 40.0, "median_skew": 2.0, "median_seconds": 5.0,
    }


def test_dry_run_excludes_negative_case_and_executes_nothing(tmp_path, capsys):
    assert "non_watertight" not in cmp.default_cases()
    work = tmp_path / "work"
    assert cmp.main(["--dry-run", "--cases", "pipe", "--workdir", str(work)]) == 0
    assert "3 runs, nothing executed" in capsys.readouterr().out
    assert not work.exists()
