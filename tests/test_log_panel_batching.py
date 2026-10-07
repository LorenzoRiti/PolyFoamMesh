"""PHASE 1 log-path tests: batching + view-only coalescing.

Covers the freeze/lag regression where the GUI log panel rendered every one of
tens of thousands of GMSH "Improving ..." / runner "still running" lines with a
single QTextEdit insert each, in a document that grew without bound.

All tests run headless (QT_QPA_PLATFORM=offscreen). They assert:

* a 50k-line burst stays bounded by ``_MAX_LOG_LINES`` and finishes fast;
* normal lines appended after a burst are still visible;
* consecutive "still running" lines collapse to ONE visible line;
* a run of "Improving" lines collapses to one line with a running count;
* isolated low-rate lines render immediately, without the batching timer;
* tag-based colouring on normal lines is preserved.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from polyfoammesh.gui.design_tokens import ERROR_LIGHT
from polyfoammesh.gui.log_panel import _MAX_LOG_LINES, LogPanel


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _pump(ms: int = 20) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _drain(panel: LogPanel, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if (
            panel._pending == 0
            and not panel._buffer
            and not panel._flush_timer.isActive()
        ):
            return
        _pump(10)


def _mesh_mix(n: int) -> list[str]:
    """Realistic mesh log: ~90% Improving runs, 5% still-running, 5% normal."""
    lines: list[str] = []
    cycle = 0
    while len(lines) < n:
        for k in range(18):
            lines.append(
                f"Improving {7000 + cycle * 18 + k} tet. on "
                f"{k % 10 + 1}th (S & ER)"
            )
        lines.append(
            "[gmsh] ... still running (123s elapsed, no output for 18s)"
        )
        lines.append(f"[checkMesh] Time = {cycle * 0.01:.2f}, mesh OK.")
        cycle += 1
    return lines[:n]


def test_burst_50k_is_bounded_and_fast(qapp):
    panel = LogPanel()
    feed = _mesh_mix(50_000)

    t0 = time.perf_counter()
    for i, line in enumerate(feed):
        panel.append_log(line)
        if i % 1000 == 0:
            _pump(1)
    _drain(panel)
    elapsed = time.perf_counter() - t0

    assert elapsed < 5.0, f"50k-line burst too slow: {elapsed:.3f}s"
    assert panel.document().blockCount() <= _MAX_LOG_LINES
    # Coalescing must actually have reduced the rendered block count.
    assert panel.document().blockCount() < 10_000


def test_pure_normal_burst_respects_cap(qapp):
    panel = LogPanel()
    for i in range(_MAX_LOG_LINES + 2000):
        panel.append_log(f"normal line {i}")
        if i % 1000 == 0:
            _pump(1)
    _drain(panel)
    assert panel.document().blockCount() <= _MAX_LOG_LINES


def test_normal_line_visible_after_burst(qapp):
    panel = LogPanel()
    for i in range(20_000):
        panel.append_log(f"Improving {i} tet. on 6th (S & ER)")
        if i % 1000 == 0:
            _pump(1)
    _drain(panel)

    time.sleep(0.08)  # exceed _LOW_RATE_GAP_S so the line flushes promptly
    panel.append_log("[checkMesh] FINAL NORMAL LINE VISIBLE")
    assert "FINAL NORMAL LINE VISIBLE" in panel.toPlainText()


def test_still_running_replaces_previous(qapp):
    panel = LogPanel()
    for s in range(1, 21):
        panel.append_log(
            f"[gmsh] ... still running ({s}s elapsed, no output for 18s)"
        )
    _drain(panel)
    text = panel.toPlainText()
    assert text.count("still running") == 1, text
    # The surviving line is the most recent heartbeat.
    assert "20s elapsed" in text


def test_improving_run_collapses_with_count(qapp):
    panel = LogPanel()
    for i in range(100):
        panel.append_log(f"Improving {7000 + i} tet. on 6th (S & ER)")
    _drain(panel)
    lines = [ln for ln in panel.toPlainText().splitlines() if ln.strip()]
    improving = [ln for ln in lines if ln.startswith("Improving")]
    assert len(improving) == 1, lines
    assert "(100 lines)" in improving[0], improving[0]


def test_low_rate_line_flushes_without_timer(qapp):
    panel = LogPanel()
    assert not panel._flush_timer.isActive()
    panel.append_log("[checkMesh] prompt low-rate line")
    # Visible immediately: the small-queue low-rate path flushes synchronously.
    assert "prompt low-rate line" in panel.toPlainText()
    assert not panel._flush_timer.isActive()


def test_tag_colouring_preserved(qapp):
    panel = LogPanel()
    panel.append_log("[ERROR] something failed")
    block = panel.document().firstBlock()
    it = block.begin()
    fmt = it.fragment().charFormat()
    assert fmt.foreground().color().name().lower() == ERROR_LIGHT.lower()
