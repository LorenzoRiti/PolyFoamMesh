"""Phase C/D unit tests for gmsh_wrapper's fallback sizing + optimizer policy.

Pure helpers: no Qt, no GMSH launch.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from polyfoammesh.core import gmsh_wrapper as gw  # noqa: E402


def test_derive_manual_equivalent_sizes_matches_phase_a_baseline():
    # venturi.step bbox volume ~5.4e-12 m^3, target 200_000 -> max ~3e-6 m,
    # the exact manual baseline that converged in Phase A (8.4 s).
    vol = 5.4e-12
    max_cell, min_cell = gw.derive_manual_equivalent_sizes(vol, 200_000)
    assert max_cell == pytest.approx((vol / 200_000) ** (1 / 3))
    assert max_cell == pytest.approx(3.0e-6, rel=0.05)
    assert min_cell == pytest.approx(max_cell * 0.2)


def test_derive_manual_equivalent_sizes_inverts_the_cell_estimate():
    vol, target = 1e-3, 500_000
    max_cell, _min_cell = gw.derive_manual_equivalent_sizes(vol, target)
    assert vol / (max_cell ** 3) == pytest.approx(target, rel=1e-6)


@pytest.mark.parametrize("bad", [0, -5, None])
def test_derive_manual_equivalent_sizes_never_divides_by_zero(bad):
    max_cell, min_cell = gw.derive_manual_equivalent_sizes(1e-9, bad)
    assert max_cell > 0 and min_cell > 0


def test_resolve_optimize_robust_default_off(monkeypatch):
    monkeypatch.delenv(gw.GMSH_ROBUST_OPT_ENV_VAR, raising=False)
    assert gw._resolve_optimize(robust=True) == 0


def test_resolve_optimize_robust_on_only_with_flag(monkeypatch):
    monkeypatch.setenv(gw.GMSH_ROBUST_OPT_ENV_VAR, "1")
    assert gw._resolve_optimize(robust=True) == 1
    monkeypatch.setenv(gw.GMSH_ROBUST_OPT_ENV_VAR, "0")
    assert gw._resolve_optimize(robust=True) == 0


def test_resolve_optimize_non_robust_keeps_historical_default(monkeypatch):
    monkeypatch.delenv("CFMESH_GMSH_OPTIMIZE", raising=False)
    assert gw._resolve_optimize(robust=False) == 1
    monkeypatch.setenv("CFMESH_GMSH_OPTIMIZE", "0")
    assert gw._resolve_optimize(robust=False) == 0


def test_manual_equiv_mode_env(monkeypatch):
    monkeypatch.delenv(gw.GMSH_MANUAL_EQUIV_ENV_VAR, raising=False)
    assert gw._gmsh_manual_equiv_mode() is False
    for val in ("1", "true", "YES", "on"):
        monkeypatch.setenv(gw.GMSH_MANUAL_EQUIV_ENV_VAR, val)
        assert gw._gmsh_manual_equiv_mode() is True
    monkeypatch.setenv(gw.GMSH_MANUAL_EQUIV_ENV_VAR, "0")
    assert gw._gmsh_manual_equiv_mode() is False
