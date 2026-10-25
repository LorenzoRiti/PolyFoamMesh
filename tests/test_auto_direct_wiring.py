"""W18 direct leg: wiring guards (repo precedent: source-inspection tests).

Full offscreen auto runs are minutes-long and WSL-dependent, so like
test_cancel_meshing.py this pins the wiring structurally:
- the dual launcher branches to direct-BL when the auto flag is set;
- all three failure points consult the direct leg after the mesher chain;
- the decision itself stays in tested pure code (test_auto_policy).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.gui import main_window as mw  # noqa: E402


def _src(name: str) -> str:
    return inspect.getsource(getattr(mw.MainWindow, name))


def test_dual_launcher_skips_to_direct_bl():
    src = _src("_launch_gmsh_poly_dual")
    assert "_auto_direct_bl" in src
    assert "_launch_bl_on_tets" in src


def test_all_three_failure_points_try_direct_leg():
    for name in (
        "_on_gmsh_polydual_failed",
        "_on_gmsh_polydual_finished",
        "_on_checkmesh_finished",
    ):
        assert "_maybe_auto_direct_bl" in _src(name), name


def test_direct_leg_registers_chain_and_guards():
    src = _src("_maybe_auto_direct_bl")
    for token in (
        "_auto_run_active",
        "_auto_engine_remaining",
        "_auto_direct_done",
        "_auto_forced_key",
        "_auto_restarting",
        "get_bl_params",
        "auto_direct_bl_next",
    ):
        assert token in src, token


def test_tet_success_records_last_engine_for_direct_leg():
    src = _src("_on_gmsh_volume_result")
    assert "_auto_last_tet_ok" in src


def test_fresh_run_resets_direct_leg_state():
    src = _src("_on_run_meshing")
    for token in ("_auto_direct_bl", "_auto_direct_done", "_auto_last_tet_ok"):
        assert token in src, token


def test_direct_leg_is_single_shot():
    """The direct leg must fire exactly once per run: dispatch consumes
    the flag and records _auto_direct_applied, and the post-BL PASS path
    skips the dual when it is set (no re-BL loop, no dual-on-BL)."""
    launch = _src("_launch_gmsh_poly_dual")
    assert "_auto_direct_applied = True" in launch
    assert "_auto_direct_bl = False" in launch
    checkmesh = _src("_on_checkmesh_finished")
    assert "_auto_direct_applied" in checkmesh


def test_stale_dual_failure_cannot_restart():
    """A late dual failure after Cancel/new run must not resurrect auto."""
    src = _src("_on_gmsh_polydual_failed")
    assert "_polydual_run_id" in src
    assert "!= self._run_id" in src


def test_bl_callbacks_are_run_guarded():
    src = _src("_launch_bl_on_tets")
    assert "_bl_on_tets_run_id" in src
    for name in ("_on_bl_on_tets_finished", "_on_bl_on_tets_failed"):
        s = _src(name)
        assert "_bl_on_tets_run_id" in s, name
        assert "!= self._run_id" in s, name


def test_cancel_and_reset_clear_auto_state():
    cancel = _src("_on_cancel_meshing")
    for token in (
        "_auto_restarting = False",
        "_auto_direct_bl = False",
        "_auto_forced_key = None",
        "_auto_last_tet_ok = None",
        "_bl_on_tets_worker",
    ):
        assert token in cancel, token
    reset = _src("_on_reset")
    assert "_auto_restarting = False" in reset
    assert "_auto_direct_bl = False" in reset
