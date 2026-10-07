"""A long mesh run must be a decision, not a surprise.

cartesianMesh prints nothing for minutes at a time, so an accidentally fine
setting is indistinguishable from a hang. The benchmark's thin_gap case needed
3.3M cells and 90 s with no warning at all before this guard existed.
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from polyfoammesh.gui.main_window import (  # noqa: E402
    MainWindow,
    _space_free_cases_root,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def guard(app, monkeypatch):
    """Bind the guard to a stub carrying only the thresholds it reads."""
    asked: list[str] = []

    def fake_question(_parent, _title, text, *a, **kw):
        asked.append(text)
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    stub = types.SimpleNamespace(
        LARGE_MESH_CELLS=MainWindow.LARGE_MESH_CELLS,
        LARGE_MESH_SECONDS=MainWindow.LARGE_MESH_SECONDS,
    )
    return stub, asked


def test_small_mesh_starts_without_asking(guard):
    stub, asked = guard
    assert MainWindow._confirm_large_mesh(stub, 50_000, 10.0, 0.1, 0.05) is True
    assert asked == []


def test_huge_cell_count_asks_first(guard):
    stub, asked = guard
    assert MainWindow._confirm_large_mesh(stub, 3_300_000, 90.0, 0.005, 0.0005) is False
    assert len(asked) == 1


def test_long_runtime_asks_even_when_cell_count_is_modest(guard):
    """Either threshold alone is enough — a slow run is just as surprising."""
    stub, asked = guard
    assert MainWindow._confirm_large_mesh(stub, 100_000, 600.0, 0.01, 0.001) is False
    assert len(asked) == 1


def test_prompt_names_the_knob_that_controls_the_cost(guard):
    """A warning the user can't act on is just noise: show the cell sizes."""
    stub, asked = guard
    MainWindow._confirm_large_mesh(stub, 5_000_000, 120.0, 0.005, 0.0005)
    text = asked[0]
    assert "5,000,000" in text
    assert "0.005" in text and "0.0005" in text


# ---------------------------------------------------------------------------
# Linux phase 2: platform-neutral case root + process kill + OOM advice
# ---------------------------------------------------------------------------

def test_space_free_cases_root_has_no_spaces():
    root = _space_free_cases_root()
    assert " " not in str(root)
    assert root.name == "cfmesh_cases"


def _kill_stub() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        _of_config=types.SimpleNamespace(wsl_distro="Ubuntu"),
    )


def test_kill_processes_posix_never_invokes_wsl(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "platform", "linux")

    MainWindow._kill_wsl_processes(_kill_stub())

    cmd = captured["cmd"]
    assert cmd[0] == "bash"
    assert not any("wsl" in str(part).lower() for part in cmd)
    # Same pkill payload as the historical WSL call.
    assert "pkill -9 -f cartesianMesh" in cmd[-1]
    assert "pkill -9 -f mpirun" in cmd[-1]


def test_kill_processes_windows_uses_wsl_exe(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "platform", "win32")

    MainWindow._kill_wsl_processes(_kill_stub())

    cmd = captured["cmd"]
    assert cmd[0] == "wsl.exe"
    assert cmd[1:4] == ["-d", "Ubuntu", "--"]


def _advice_stub() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        LARGE_MESH_CELLS=MainWindow.LARGE_MESH_CELLS,
        LARGE_MESH_SECONDS=MainWindow.LARGE_MESH_SECONDS,
        _params=None,
    )


def test_memory_advice_windows_keeps_wsl2(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        QMessageBox, "critical",
        staticmethod(lambda _p, _t, text, *a, **k: captured.update(text=text)),
    )
    monkeypatch.setattr(sys, "platform", "win32")

    assert MainWindow._confirm_large_mesh(
        _advice_stub(), 10**12, 10.0, 0.005, 0.0005,
    ) is False
    text = captured["text"]
    assert "WSL2" in text
    assert "wsl --set-memory Ubuntu 32G" in text


def test_memory_advice_linux_is_native(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        QMessageBox, "critical",
        staticmethod(lambda _p, _t, text, *a, **k: captured.update(text=text)),
    )
    monkeypatch.setattr(sys, "platform", "linux")

    assert MainWindow._confirm_large_mesh(
        _advice_stub(), 10**12, 10.0, 0.005, 0.0005,
    ) is False
    text = captured["text"]
    assert "free -h" in text
    assert "ulimit" in text
    assert "WSL2" not in text
    assert ".wslconfig" not in text


class _Log:
    def __init__(self):
        self.messages: list[str] = []

    def append_log(self, msg):
        self.messages.append(msg)


def _of_check_stub() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        _openfoam_available=None,
        _wsl_check_run_id=7,
        _run_id=7,
        _log=_Log(),
        _status=types.SimpleNamespace(showMessage=lambda _m: None),
        _params=types.SimpleNamespace(set_all_enabled=lambda _v: None),
    )


def test_openfoam_not_found_dialog_windows_keeps_wsl(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda _p, _t, text, *a, **k: captured.update(text=text)),
    )
    monkeypatch.setattr(sys, "platform", "win32")

    MainWindow._on_wsl_check_finished(_of_check_stub(), False)

    assert "via WSL2" in captured["text"]
    assert "WSL2 Ubuntu" in captured["text"]


def test_openfoam_not_found_dialog_linux_is_native(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda _p, _t, text, *a, **k: captured.update(text=text)),
    )
    monkeypatch.setattr(sys, "platform", "linux")

    MainWindow._on_wsl_check_finished(_of_check_stub(), False)

    assert "WSL2" not in captured["text"]
    assert "on PATH" in captured["text"]
