"""Tests for the runnable-case generation (core/case_setup.py).

The historical template produced a case that could never run: zero inlet
velocity, no ``0/nut``, plain zeroGradient walls, ``fixedValue 0`` omega at
outlets, and — found by actually running it — a ``writeInterval`` larger than
``endTime`` that silently wrote no time directory at all. These tests pin the
fixes; they need no OpenFOAM (setup_case only writes files).
"""
from __future__ import annotations

import re

from cfmesh_autogui.core.boundary_reader import PatchInfo
from cfmesh_autogui.core.case_setup import setup_case

PATCHES = [
    PatchInfo("surface_1", "wall", 100, 0),
    PatchInfo("surface_2", "patch", 10, 100),
    PatchInfo("surface_3", "patch", 10, 110),
]
ROLES = {"surface_1": "wall", "surface_2": "inlet", "surface_3": "outlet"}


def _read(case_dir, rel: str) -> str:
    return (case_dir / rel).read_text(encoding="ascii", errors="replace")


def test_write_interval_clamped_to_end_time(tmp_path):
    setup_case(
        tmp_path, PATCHES, inlet_velocity=(1.0, 0.0, 0.0),
        end_time=50, write_interval=100,
    )
    ctrl = _read(tmp_path, "system/controlDict")
    assert re.search(r"^endTime\s+50;", ctrl, re.MULTILINE)
    assert re.search(r"^writeInterval\s+50;", ctrl, re.MULTILINE), (
        "writeInterval must be clamped to endTime or the solver writes no time "
        "directory at all (silently losing the solution)."
    )


def test_runnable_case_has_all_fields_and_wall_functions(tmp_path):
    setup_case(
        tmp_path, PATCHES, inlet_velocity=(1.0, 0.0, 0.0),
        end_time=100, write_interval=100, patch_roles=ROLES,
    )
    for f in ("p", "U", "k", "omega", "nut"):
        assert (tmp_path / "0" / f).exists(), f"missing 0/{f}"
    # omega dimension is 1/s, not 1/m^2 — a runtime abort if wrong.
    omega = (tmp_path / "0" / "omega").read_text(encoding="ascii")
    assert "dimensions      [0 0 -1 0 0 0 0];" in omega
    # Wall patches must carry wall functions; gmshToFoam types them 'patch'
    # and the solver aborts at startup unless the boundary says 'wall'.
    u = (tmp_path / "0" / "U").read_text(encoding="ascii")
    assert "fixedValue" in u and "uniform (1 0 0);" in u
    nut = (tmp_path / "0" / "nut").read_text(encoding="ascii")
    assert "nutkWallFunction" in nut
    # Inlet/outlet BCs for a constriction must tolerate reversed flow.
    assert "inletOutlet" in u


def test_fv_schemes_wall_dist_entry_present(tmp_path):
    setup_case(tmp_path, PATCHES)
    schemes = _read(tmp_path, "system/fvSchemes")
    assert re.search(r"wallDist\s*\{[^}]*method\s+meshWave;", schemes), (
        "kOmegaSST needs wallDist method meshWave in OpenFOAM 2512."
    )


def test_historical_placeholder_still_writes_no_velocity(tmp_path):
    # The old path (inlet_velocity=None) must keep working — a case the user
    # edits by hand. It deliberately has no fixed inlet.
    setup_case(tmp_path, PATCHES)
    u = (tmp_path / "0" / "U").read_text(encoding="ascii")
    assert "uniform (1 0 0);" not in u
