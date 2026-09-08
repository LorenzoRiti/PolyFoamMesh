"""GMSH hardware budget: an explicit "very fine" cell target must never
let GMSH run away and OOM — it is capped by the machine's RAM-derived
floor (regression: moving the Mesh Fineness slider to 20M cells on a
normal machine could blow up the mesh process)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unittest import mock  # noqa: E402

from polyfoammesh.core import gmsh_wrapper as gw  # noqa: E402


def _mock_ram(available_gb: float, total_gb: float):
    return mock.patch.object(
        gw, "_available_ram_bytes", return_value=int(available_gb * 1024**3)
    ), mock.patch.object(
        gw, "_total_ram_bytes", return_value=int(total_gb * 1024**3)
    )


def test_explicit_target_capped_by_ram():
    """20M target on a small machine -> capped well below 20M."""
    with _mock_ram(4.0, 8.0)[0], _mock_ram(4.0, 8.0)[1]:
        b = gw._hardware_budget(20_000_000)
    assert b["is_explicit_target"] is True
    # 8 GB total -> 25% /1KB = 2M cell floor; 4 GB avail -> 50%/1KB = 2M.
    assert b["max_cells"] <= 2_200_000
    assert b["max_cells"] >= 2_000_000


def test_small_target_not_capped():
    """A modest explicit target below the RAM floor is respected exactly."""
    with _mock_ram(16.0, 32.0)[0], _mock_ram(16.0, 32.0)[1]:
        b = gw._hardware_budget(300_000)
    assert b["max_cells"] == 300_000


def test_auto_budget_no_override():
    """No override: budget derives from RAM, above a 200K safety floor."""
    with _mock_ram(16.0, 32.0)[0], _mock_ram(16.0, 32.0)[1]:
        b = gw._hardware_budget()
    assert b["max_cells"] >= 200_000
