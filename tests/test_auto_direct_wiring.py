"""W18 direct leg + compare leg: wiring guards (repo precedent:
source-inspection tests).

Full offscreen auto runs are minutes-long and WSL-dependent, so like
test_cancel_meshing.py this pins the wiring structurally:
- the dual launcher branches to direct-BL when the auto flag is set;
- all three failure points consult the direct leg after the mesher chain;
- a weak-valid mesh opens one compare challenge, keep-best;
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
    for token in ("_auto_direct_bl", "_auto_direct_done", "_auto_last_tet_ok",
                  "_auto_compare_done", "_auto_compare_candidate",
                  "_last_dual_residual"):
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
        "_auto_compare_done = False",
        "_auto_compare_candidate = None",
        "_bl_on_tets_worker",
    ):
        assert token in cancel, token
    reset = _src("_on_reset")
    assert "_auto_restarting = False" in reset
    assert "_auto_direct_bl = False" in reset
    assert "_auto_compare_candidate = None" in reset


def test_compare_trigger_fires_once_on_weak_valid():
    src = _src("_maybe_auto_compare")
    for token in (
        "is_weak_auto_result",
        "auto_compare_next",
        "_auto_compare_done",
        "_auto_compare_candidate",
        "_auto_restarting",
        "_on_run_meshing",
    ):
        assert token in src, token


def test_dual_success_stashes_residuals_for_compare():
    src = _src("_on_gmsh_polydual_finished")
    assert "_last_dual_residual" in src
    assert "residual_defects" in src


def test_pass_path_resolves_then_maybe_challenges():
    """Keep-best order: the challenger's verdict is decided before any
    new challenge opens (no challenge-on-challenge, no lost candidate)."""
    src = _src("_on_checkmesh_finished")
    assert "_maybe_auto_compare_resolve" in src
    assert "_maybe_auto_compare" in src
    assert src.index("_maybe_auto_compare_resolve") < src.index("_launch_decomposepar")


def test_failure_paths_restore_candidate_first():
    """Tet/dual/checkMesh failures and the direct leg all restore a
    pending weak-valid candidate before anything else: a valid mesh
    beats no mesh."""
    for name in (
        "_maybe_auto_checkmesh_fallback",
        "_maybe_auto_dual_fallback",
        "_maybe_auto_direct_bl",
    ):
        assert "_restore_auto_compare_candidate" in _src(name), name
    assert "_restore_auto_compare_candidate" in _src("_on_gmsh_volume_failed")


def test_compare_restore_revalidates():
    """Restoring the incumbent re-runs checkMesh on its case so the shown
    verdict always matches the shown mesh (no stale-report swap)."""
    src = _src("_restore_auto_compare_candidate")
    for token in (
        "_case_dir",
        "_current_mesher_type",
        "_poly_was_converted",
        "_launch_checkmesh",
        "_auto_compare_candidate = None",
    ):
        assert token in src, token


def test_one_place_advances_the_auto_chain():
    """Dual/checkMesh/convert fallbacks all go through the single
    advance helper — no duplicated restart bookkeeping can drift."""
    helper = _src("_advance_auto_chain")
    for token in (
        "_auto_engine_used",
        "_auto_forced_key",
        "_auto_restarting = True",
        "_auto_engine_remaining",
        "_on_run_meshing",
    ):
        assert token in helper, token
    for name in ("_maybe_auto_dual_fallback", "_maybe_auto_checkmesh_fallback"):
        src = _src(name)
        assert "_advance_auto_chain" in src, name
        assert "_auto_forced_key = target" not in src, name


def test_convert_failure_advances_the_chain():
    """A gmshToFoam failure on an auto run must not dead-end in a dialog:
    the next engine (or the incumbent candidate) takes over, declared."""
    src = _src("_continue_gmsh_direct")
    assert "_advance_auto_chain" in src
    assert "_restore_auto_compare_candidate" in src
    assert "Conversion Failed" in src


def test_volume_result_has_a_3d_element_gate():
    """A surface-only MSH must be treated as a tet failure (chain
    advance), never cached and accepted only to die at gmshToFoam."""
    src = _src("_on_gmsh_volume_result")
    for token in ("_has_volume_elements", "_on_gmsh_volume_failed"):
        assert token in src, token
    assert src.index("_has_volume_elements") < src.index("_store_tet_mesh_cache")


def test_auto_fix_budget_resets_on_a_fresh_user_run():
    """The auto-fix counter used to live for the whole session, so after
    two retries every later user run was denied one."""
    src = _src("_on_run_meshing")
    assert "_quality_fix_attempts = 0" in src
    assert "_auto_restarting" in src
    # The fTetWild discrete-surface fallback is one-shot per RUN (reset).
    assert "_ftw_stl_fallback_done = False" in src


def test_ftetwild_surface_fallback_exports_patch_stl_once():
    """Measured on the user's DN1000: the GMSH surface stage alone fails
    on hostile CAD (periodic surface) after minutes of edge-splitting
    loops — the tet engine never even starts.  One declared attempt per
    run re-feeds fTetWild the discrete patch meshes already in memory
    (run_volume accepts an STL input directly)."""
    src = _src("_on_gmsh_volume_failed")
    for token in (
        '"Impossible to mesh periodic surface"',
        "export_multisolid_ascii_stl",
        "_ftw_stl_fallback_done = True",
        "_FTW_PATCH_STL_NAME",
        "_start_gmsh_volume_worker",
    ):
        assert token in src, token
    assert "surface fallback" in src
    reset_src = _src("_on_run_meshing")
    assert "_ftw_stl_fallback_done = True" not in reset_src


def test_compare_candidate_requires_a_real_dict():
    """Restoring/matching must act only on a recorded candidate, never on
    an arbitrary truthy attribute (test stubs, unset state)."""
    for name in ("_restore_auto_compare_candidate", "_maybe_auto_compare_resolve"):
        src = _src(name)
        assert "isinstance(cand, dict)" in src, name


def test_claim_unique_dir_never_reuses_a_directory(tmp_path):
    """Two runs starting in the same second must not share a case dir."""
    first = mw.claim_unique_dir(tmp_path, "gmsh_direct_20260101_120000")
    second = mw.claim_unique_dir(tmp_path, "gmsh_direct_20260101_120000")
    third = mw.claim_unique_dir(tmp_path, "gmsh_direct_20260101_120000")
    assert first != second != third != first
    assert second.name.endswith("_2")
    assert third.name.endswith("_3")
    for path in (first, second, third):
        assert path.is_dir()


def test_claim_unique_dir_never_raises_on_exhaustion(tmp_path):
    """The last resort is a random suffix, not a crash: a run must never
    die because the numeric names are exhausted."""
    for n in range(3):
        (tmp_path / ("case_1" if n == 0 else f"case_1_{n + 1}")).mkdir()
    got = mw.claim_unique_dir(tmp_path, "case_1", limit=3)
    assert got.is_dir()
    assert got.name.startswith("case_1_")
    assert got.name != "case_1_2" and got.name != "case_1_3"


def test_every_timestamped_case_dir_is_claimed_atomically():
    """Every timestamped case dir goes through claim_unique_dir, never a
    bare mkdir(exist_ok=True) on a second-resolution name."""
    text = inspect.getsource(mw)
    for stem in ("gmsh_direct_", "case_", "autopoly_", "native_poly_"):
        assert f'claim_unique_dir(root, f"{stem}{{ts}}")' in text, stem
    # The only place allowed to create a dir with exist_ok=True is the
    # helper itself (and the per-run subdirs under an already-claimed dir).
    assert text.count('self._case_dir.mkdir(parents=True, exist_ok=True)') == 0


def test_auto_mesher_honours_the_panel_pin():
    """The Advanced-tab pin wins over the geometry policy (strict chain);
    an unusable pin aborts loudly instead of silently unpinning."""
    src = _src("_resolve_auto_mesher")
    for token in (
        "get_tet_engine_override",
        "pinned_polymesh_plan",
        "invalid_tet_pin",
    ):
        assert token in src, token
    run_src = _src("_on_run_meshing")
    assert "invalid_tet_pin" in run_src


def test_tet_surface_fallback_plan_is_declared_at_start():
    """The hostile-CAD surface fallback is declared BEFORE it fires, in all
    three plan branches (pin, tunnel, geometry preflight)."""
    plan = _src("_log_tet_surface_fallback_plan")
    assert "discrete patch STL" in plan
    assert "one attempt" in plan
    resolver = _src("_resolve_auto_mesher")
    assert resolver.count("_log_tet_surface_fallback_plan") >= 3, (
        "each ftetwild_poly primary branch must declare the fallback plan"
    )


def test_explicit_bypass_is_declared_at_run_start():
    """A restored explicit key bypasses CTA+policy+pin while the buttons
    still show Polymesh: the run must say so loudly (with the remedy),
    never swap silently."""
    src = _src("_on_run_meshing")
    assert "get_explicit_mesher_key" in src
    assert "BYPASSED" in src


def test_startup_reports_which_code_runs():
    """Reports are only actionable with the build id: the GUI prints it
    at startup next to the version."""
    init_src = inspect.getsource(mw.MainWindow.__init__)
    assert "build_id" in init_src


def test_demo_action_is_wired():
    """One-click demo: Tools menu offers it; it builds the synthetic
    cylinder (no geometry files — nothing reserved can leak) with BL on.
    """
    setup_src = inspect.getsource(mw.MainWindow._setup_menu)
    assert "Demo" in setup_src
    assert "_on_load_demo_case" in setup_src
    action_src = inspect.getsource(mw.MainWindow._on_load_demo_case)
    for token in (
        "create_test_cylinder",
        "_load_geometry",
        "set_bl_enabled",
    ):
        assert token in action_src, token
    assert "venturi" not in action_src.lower()
