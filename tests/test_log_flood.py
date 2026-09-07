"""Flood-protection tests: a pathological mesh run emits thousands of log
lines/sec; the GUI must stay responsive and bounded (white-screen hang
regression — see .slim/deepwork/freeze-meshing-valve.md).

Covers the two protection layers:
1. LogPanel burst guard + O(1) block trimming.
2. _stream_subprocess live-line throttle (full lines still captured).
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from polyfoammesh.gui.log_panel import LogPanel


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _pump(ms: int = 20) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_log_panel_flood_drops_burst_and_stays_bounded(qapp):
    from polyfoammesh.gui.log_panel import _BURST_PENDING_CAP

    panel = LogPanel()

    def flood():
        for i in range(50000):
            panel.append_log(f"warning line {i}")

    # Flood WITHOUT pumping: the queued cross-thread appends must hit the
    # burst cap and drop instead of piling up unboundedly.
    t = threading.Thread(target=flood, daemon=True)
    t.start()
    t.join(timeout=20)
    assert panel._pending <= _BURST_PENDING_CAP
    assert panel._dropped > 0, "burst guard never dropped any line"

    # Drain the queue; the GUI must catch up and show the suppression note.
    deadline = time.monotonic() + 10
    while panel._pending > 0 and time.monotonic() < deadline:
        _pump(10)
    assert panel._pending == 0, "queued appends never drained"
    assert panel._dropped == 0, "suppression note not consumed"
    assert "suppressed" in panel.toPlainText()


def test_log_panel_suppression_note_is_visible_after_flood(qapp):
    panel = LogPanel()

    def burst():
        for i in range(2500):
            panel.append_log(f"burst {i}")

    t = threading.Thread(target=burst, daemon=True)
    t.start()
    t.join(timeout=20)
    assert panel._dropped > 0

    deadline = time.monotonic() + 10
    while panel._pending > 0 and time.monotonic() < deadline:
        _pump(10)
    assert panel._pending == 0
    assert "suppressed" in panel.toPlainText()
    assert panel._dropped == 0


def test_stream_subprocess_throttles_live_lines_but_captures_all(qapp):
    from polyfoammesh.core.openfoam_runner import _stream_subprocess

    child = (
        "import sys; "
        "[sys.stdout.write('warning %d\\n' % i) for i in range(5000)]"
    )
    cmd = [sys.executable, "-c", child]
    live: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        rc, stdout_lines, _stderr_lines, timed_out = _stream_subprocess(
            cmd, run_cwd=tmp, timeout_s=60, on_line=live.append,
        )

    assert rc == 0 and not timed_out
    # ALL lines are captured for the final report/error analysis.
    assert len(stdout_lines) == 5000
    # But the live forwarding is throttled (50 lines/s max) and notes the
    # suppression — the GUI never sees a 5000-line burst in one go.
    assert len(live) < 500, f"live lines not throttled: {len(live)}"
    assert any("suppressed" in line for line in live)
