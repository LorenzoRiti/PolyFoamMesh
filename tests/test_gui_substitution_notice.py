"""Lane C: a GUI-side substitution/fallback must produce a clear, visible
notice in the log panel — never silence.

Covers:
1. The `_log_substitution` helper lands a `[SUBSTITUTED]`-tagged message in
   the GUI log.
2. A real substitution code path (`_parallel_fallback`) drives the message
   into the log (parallel -> serial fallback).
3. The `[SUBSTITUTED]` token is visually highlighted (distinct color span).
4. The notice emitted from a worker thread (how RetryRunner streams its log)
   still lands in the GUI log panel.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from unittest import mock  # noqa: E402

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from polyfoammesh.gui.log_panel import LogPanel  # noqa: E402

import polyfoammesh.gui.main_window as mw  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _pump(ms: int = 20) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _build_fallback_stub(qapp):
    """A stub MainWindow carrying a REAL LogPanel + the REAL unbound
    substitution/fallback methods (pattern from test_cancel_meshing)."""
    log = LogPanel()
    stub = mock.Mock(
        _log=log,
        _runner=mock.Mock(),
        _case_dir=None,
        _params=mock.Mock(),
        _meshes=[],
        _original_shape=None,
        _current_scale=1.0,
    )
    stub._params.get_mesh_params.return_value = {
        "max_cell_size": 0.05,
        "min_cell_size": 0.01,
    }
    stub._log_substitution = mw.MainWindow._log_substitution.__get__(stub)
    stub._parallel_fallback = mw.MainWindow._parallel_fallback.__get__(stub)
    stub._connect_runner_signals = mw.MainWindow._connect_runner_signals.__get__(stub)
    stub._make_guarded_finished = mw.MainWindow._make_guarded_finished.__get__(stub)
    stub._make_fix_action = mw.MainWindow._make_fix_action.__get__(stub)
    return stub


def test_log_substitution_helper_emits_notice(qapp):
    log = LogPanel()
    win = mock.Mock(_log=log)
    win._log_substitution = mw.MainWindow._log_substitution.__get__(win)

    win._log_substitution(
        "Algoritmo sostituito: boundary layer disattivato "
        "(fallisce su questa geometria)."
    )

    text = log.toPlainText()
    assert "[SUBSTITUTED]" in text
    assert "boundary layer disattivato" in text


def test_parallel_fallback_emits_substitution_notice(qapp, monkeypatch):
    """Drive the real parallel->serial fallback path and assert the notice
    lands in the GUI log (the acceptance scenario)."""
    monkeypatch.setattr(mw, "QMessageBox", mock.Mock())
    stub = _build_fallback_stub(qapp)

    stub._parallel_fallback("timeout")

    text = stub._log.toPlainText()
    assert "[SUBSTITUTED]" in text
    assert "Passato a mesh seriale (single-core): risorse insufficienti per MPI." in text
    # The original WARN is kept alongside the substitution notice.
    assert "Parallel meshing failed (timeout)" in text
    assert stub._runner.run.called
    assert mw.QMessageBox.information.called


def test_substitution_token_is_highlighted_in_log_panel(qapp):
    from polyfoammesh.gui.design_tokens import INFO_LIGHT

    panel = LogPanel()
    panel.append_log(
        "[SUBSTITUTED] Algoritmo sostituito: boundary layer disattivato "
        "(fallisce su questa geometria)."
    )

    html = panel.toHtml()
    # The token is wrapped in its own highlighted span.
    assert "SUBSTITUTED]</span>" in html
    # Distinct color from the other log tokens.
    assert INFO_LIGHT.lower() in html.lower()


def test_substitution_from_worker_thread_appears_in_gui_log(qapp):
    """RetryRunner streams its log from a QThread; the notice must still
    land in the GUI log panel (cross-thread append)."""
    panel = LogPanel()
    line = (
        "[SUBSTITUTED] Algoritmo sostituito: boundary layer disattivato "
        "(fallisce su questa geometria)."
    )
    t = threading.Thread(target=panel.append_log, args=(line,), daemon=True)
    t.start()
    t.join(timeout=20)

    deadline = time.monotonic() + 10
    while panel._pending > 0 and time.monotonic() < deadline:
        _pump(10)

    assert "boundary layer disattivato" in panel.toPlainText()
    assert "[SUBSTITUTED]" in panel.toPlainText()
