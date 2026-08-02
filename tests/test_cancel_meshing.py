"""Cancel must stop EVERY running meshing worker/thread.

Regression: the Cancel handler under "Generate Mesh" used to leave several
background workers running (feature detect, poly dual, decompose, export,
foamToVTK...), so a new run overlapped the old one. This test pins the
contract by stubbing the MainWindow collaborators the handler touches and
verifying .cancel() is invoked on each known worker.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject  # noqa: E402
from unittest import mock  # noqa: E402

import cfmesh_autogui.gui.main_window as mw  # noqa: E402


_all_worker_attrs = {
    "_gmsh_worker", "_gmsh_conv_worker", "_wsl_check_worker",
    "_feature_worker", "_parallel_worker", "_polydual_worker",
    "_checkmesh_worker", "_quality_fix_worker", "_decompose_worker",
    "_watertight_worker", "_export_worker", "_samr_worker",
}


class _Cancellable(QObject):
    def __init__(self):
        super().__init__()
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _Log:
    def append_log(self, *a, **k):
        pass


def _build_stub():
    grid = mock.Mock(
        _runner=mock.Mock(),
        _case_dir=None,
        _run_id=0,
        _params=mock.Mock(),
        _progress=mock.Mock(),
        _ribbon_btns={"cancel": mock.Mock()},
        _status=mock.Mock(),
        _log=_Log(),
    )
    grid._runner.is_running = False
    grid._viewer = mock.Mock()
    grid._viewer.cancel_vtk_process = mock.Mock()
    grid._kill_wsl_processes = mock.Mock()
    # Attach the REAL cancel handler (unbound method) to the stub object.
    grid._on_cancel_meshing = mw.MainWindow._on_cancel_meshing.__get__(grid)
    return grid


def test_cancel_calls_cancel_on_all_known_workers():
    stub = _build_stub()
    workers = {attr: _Cancellable() for attr in _all_worker_attrs}
    for attr, w in workers.items():
        setattr(stub, attr, w)
    stub._on_cancel_meshing()
    for attr, w in workers.items():
        assert w.cancelled, f"cancel() NOT called on {attr}"


def test_cancel_kills_viewer_vtk_and_wsl():
    stub = _build_stub()
    stub._on_cancel_meshing()
    stub._kill_wsl_processes.assert_called_once()
    stub._viewer.cancel_vtk_process.assert_called_once()


def test_cancel_resets_ui_state():
    stub = _build_stub()
    stub._on_cancel_meshing()
    stub._params.set_meshing_state.assert_called_once_with(False)
    stub._params.set_all_enabled.assert_called_once_with(True)
    stub._ribbon_btns["cancel"].setVisible.assert_called_once_with(False)


def test_cancel_without_workers_is_safe():
    stub = _build_stub()
    stub._on_cancel_meshing()  # must not raise
