"""cfMesh hardware budget: cartesianMesh runs INSIDE the WSL2 VM, which
has its own memory cap independent of the Windows host's real RAM (WSL2
defaults to 50% of host RAM). The budget must use whichever of the
host-based and WSL-based estimates is more restrictive — a host-only
budget can let a request through that the WSL2 VM cartesianMesh
actually runs in cannot satisfy (regression: a 32 GB host with WSL2
capped at ~15 GB let an 8-11M cell request past a host-only budget,
which then failed inside WSL2 during parallel decomposition).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core import hardware_budget as hb  # noqa: E402
from polyfoammesh.core import geometry as geom  # noqa: E402


def _mock_host(available_gb: float, total_gb: float):
    return (
        mock.patch.object(hb, "available_ram_bytes", return_value=int(available_gb * 1024**3)),
        mock.patch.object(hb, "total_ram_bytes", return_value=int(total_gb * 1024**3)),
    )


def test_wsl_unavailable_falls_back_to_host_only():
    with _mock_host(16.0, 32.0)[0], _mock_host(16.0, 32.0)[1], \
         mock.patch.object(hb, "wsl_ram_bytes", return_value=None):
        b = geom.cfmesh_cell_budget()
    assert b["wsl_max_cells"] is None
    assert b["max_cells"] == b["host_max_cells"]


def test_wsl_tighter_than_host_wins():
    """32 GB host, WSL2 capped at ~15 GB/~9 GB available (the exact
    numbers observed live) -> the WSL-derived budget must win, not the
    much larger host-derived one."""
    with _mock_host(11.8, 31.1)[0], _mock_host(11.8, 31.1)[1], \
         mock.patch.object(hb, "wsl_ram_bytes", return_value=(15 * 1024**3, 9 * 1024**3)):
        b = geom.cfmesh_cell_budget()
    assert b["wsl_max_cells"] < b["host_max_cells"]
    assert b["max_cells"] == b["wsl_max_cells"]


def test_explicit_override_bypasses_both():
    with _mock_host(16.0, 32.0)[0], _mock_host(16.0, 32.0)[1], \
         mock.patch.object(hb, "wsl_ram_bytes", return_value=(4 * 1024**3, 2 * 1024**3)):
        b = geom.cfmesh_cell_budget(max_cells_override=500_000)
    assert b["max_cells"] == 500_000
    assert b["explicit"] is True


def test_auto_budget_floor():
    with _mock_host(0.1, 0.2)[0], _mock_host(0.1, 0.2)[1], \
         mock.patch.object(hb, "wsl_ram_bytes", return_value=None):
        b = geom.cfmesh_cell_budget()
    assert b["max_cells"] >= 200_000
