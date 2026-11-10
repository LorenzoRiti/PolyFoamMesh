"""Auto must not wait for GMSH for minutes when fTetWild is still to try
(DN1000: 6+ min of GMSH retries, then failure; fTetWild 30 s)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from polyfoammesh.core import openfoam_runner as ofr  # noqa: E402
from polyfoammesh.gui import main_window as mw  # noqa: E402


def _win(mesher="gmsh_direct_poly", plan_fallback=None, auto=False, remaining=()):
    win = SimpleNamespace(
        _current_mesher_type=mesher,
        _auto_tet_plan=(SimpleNamespace(fallback=plan_fallback)
                        if plan_fallback else None),
        _auto_run_active=auto,
    )
    win._auto_engine_remaining = lambda: list(remaining)
    return win


def _budget(win):
    return mw.MainWindow._auto_gmsh_budget_s(win)


def test_budget_only_when_ftetwild_is_still_to_try(monkeypatch):
    monkeypatch.delenv("POLYFOAMMESH_AUTO_GMSH_BUDGET_S", raising=False)
    assert _budget(_win(plan_fallback="ftetwild")) == mw._AUTO_GMSH_BUDGET_S
    assert _budget(_win(auto=True, remaining=["ftetwild_poly"])) == mw._AUTO_GMSH_BUDGET_S
    # explicit GMSH, or nothing left to switch to: full limits
    assert _budget(_win()) == 0.0
    assert _budget(_win(auto=True, remaining=[])) == 0.0
    # fTetWild running: the budget is for GMSH only
    assert _budget(_win(mesher="ftetwild_poly", plan_fallback="ftetwild")) == 0.0


def test_budget_env_override(monkeypatch):
    monkeypatch.setenv("POLYFOAMMESH_AUTO_GMSH_BUDGET_S", "45")
    assert _budget(_win(plan_fallback="ftetwild")) == 45.0
    monkeypatch.setenv("POLYFOAMMESH_AUTO_GMSH_BUDGET_S", "0")
    assert _budget(_win(plan_fallback="ftetwild")) == 0.0


def test_worker_stops_at_the_budget(monkeypatch, tmp_path):
    seen = {}

    def fake_stream(cmd, cwd, timeout_s, **kwargs):
        seen["timeout"] = timeout_s
        return 1, [], [], True          # timed out

    monkeypatch.setattr(ofr, "_stream_subprocess", fake_stream)
    w = ofr.GmshVolumeWorker("part.stp", tmp_path / "mesh.msh", "medium",
                             wall_budget_s=30.0)
    errors: list[str] = []
    w.failed.connect(errors.append)
    w.run()

    assert seen["timeout"] <= 30.0
    assert errors and "Auto time budget" in errors[0]


def test_worker_without_budget_keeps_the_hard_ceiling(monkeypatch, tmp_path):
    seen = {}

    def fake_stream(cmd, cwd, timeout_s, **kwargs):
        seen["timeout"] = timeout_s
        return 1, [], [], True

    monkeypatch.setattr(ofr, "_stream_subprocess", fake_stream)
    w = ofr.GmshVolumeWorker("part.stp", tmp_path / "mesh.msh", "medium")
    errors: list[str] = []
    w.failed.connect(errors.append)
    w.run()

    assert seen["timeout"] == ofr.GmshVolumeWorker.VOLUME_TIMEOUT_S
    assert errors and "exceeded" in errors[0] and "budget" not in errors[0]
