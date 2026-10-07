from __future__ import annotations

import re
import threading
import time

from PySide6.QtCore import Q_ARG, QMetaObject, Qt, QTimer, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QTextEdit

from polyfoammesh.gui.design_tokens import (
    ERROR_LIGHT,
    INFO_LIGHT,
    PRIMARY_500,
    SUCCESS_LIGHT,
    WARNING_LIGHT,
)

_MAX_LOG_LINES = 10000
# Max cross-thread appends queued but not yet rendered before lines are
# dropped. Without this, a flood of worker log lines (e.g. a pathological
# cartesianMesh run emitting thousands of warnings/sec) saturates the GUI
# thread, which never returns to the event loop: white screen, Windows
# "Not Responding" hang, perceived crash.
_BURST_PENDING_CAP = 2000

# Burst batching: worker lines are accumulated and inserted in chunks on this
# timer instead of one QTextEdit insert per line. A single QTextEdit.append()
# is cheap once the document is capped, but 50k of them are not — batching
# plus the coalescing below turns a flood into a few thousand inserts.
_BATCH_INTERVAL_MS = 120
# While at most this many lines are buffered, an append is rendered
# immediately so ordinary logging stays prompt — until the synchronous streak
# below is exhausted (i.e. a real flood has started), after which lines batch.
_SMALL_QUEUE = 3
# A gap larger than this since the previous append marks a low-rate stream
# (as opposed to a burst, where appends arrive back-to-back and must batch).
_LOW_RATE_GAP_S = 0.05
# A short burst of normal lines (e.g. a WARN followed by a [SUBSTITUTED]
# notice) must appear without waiting for the batching timer, so up to this
# many consecutive small-buffer appends are flushed synchronously. A sustained
# flood exhausts the streak within microseconds and then batches. The streak is
# reset by an idle gap or by a timer/forced flush.
_SYNC_FLUSH_STREAK = 16
# Safety valve: if the batching timer cannot fire (GUI event loop starved by
# a pathological flood) the buffer is flushed in a chunk at this size so it
# can never grow without bound.
_MAX_BUFFER_BEFORE_FLUSH = 5000

# GMSH progress spam and runner heartbeats. These are the lines that flood the
# widget; they are coalesced in the *view only* (see module docstring note).
_IMPROVING_RE = re.compile(r"^\s*Improving\b")
_STILL_RUNNING_RE = re.compile(r"still running.*elapsed", re.IGNORECASE)


class LogPanel(QTextEdit):
    """Read-only log view with burst batching and view-only coalescing.

    Thread-safety contract is unchanged: ``append_log`` may be called from any
    thread. Cross-thread calls are marshalled to the GUI thread through a
    QueuedConnection and are capped by ``_BURST_PENDING_CAP``; excess lines are
    dropped and summarised with a "... N lines suppressed ..." note.

    Two performance measures live here, both purely visual:

    * **Batching** — lines received on the GUI thread are buffered and flushed
      on a ~120 ms timer. An isolated line (idle stream, small buffer) is
      flushed synchronously so normal logging stays prompt.
    * **Coalescing** — consecutive ``[gmsh] ... still running ...`` lines
      replace the previous one in place, and a run of consecutive
      ``Improving ...`` lines collapses to a single line carrying a running
      ``(N lines)`` count. Only the widget view is affected; no log file,
      telemetry stream or signal mirrors individual lines (verified), so full
      fidelity is preserved everywhere it is actually consumed.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 10))
        self.setPlaceholderText("Log output will appear here...")
        self._log_lock = threading.Lock()
        # Qt trims the oldest blocks once the cap is reached — O(1), unlike
        # the previous cursor-walk prune that made every append during a
        # flood O(cap). Each rendered line is a real block (QTextEdit.append),
        # so the cap is actually enforced.
        self.document().setMaximumBlockCount(_MAX_LOG_LINES)
        self._pending = 0  # cross-thread appends queued, not yet rendered
        self._dropped = 0  # lines dropped during a burst

        # --- GUI-thread render state -------------------------------------
        self._buffer: list[str] = []
        self._last_append_ts = 0.0
        self._sync_streak = 0
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(_BATCH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._on_timer_flush)

        # Coalescing state, persisted across flushes so a run split by a flush
        # boundary still collapses into a single block.
        self._last_kind = "normal"  # "normal" | "still" | "improving"
        self._improving_count = 0

    # ------------------------------------------------------------------ API

    @Slot(str)
    def append_log(self, text: str):
        """Thread-safe log append with highlighting, batching and burst
        protection.

        When invoked from a non-main thread the actual text insertion is
        dispatched to the GUI thread via invokeMethod with a QueuedConnection
        (prevents the Qt heap corruption that occurred when inserting
        directly from a QThread worker). If more than ``_BURST_PENDING_CAP``
        appends are already queued, the line is dropped and counted — the
        GUI keeps rendering at a sustainable pace instead of freezing.
        """
        if threading.current_thread() is not threading.main_thread():
            with self._log_lock:
                if self._pending >= _BURST_PENDING_CAP:
                    self._dropped += 1
                    return
                self._pending += 1
            QMetaObject.invokeMethod(
                self, "append_log", Qt.QueuedConnection,
                Q_ARG(str, text),
            )
            return

        now = time.monotonic()
        if (now - self._last_append_ts) > _LOW_RATE_GAP_S:
            # Idle gap: this is low-rate logging, not a flood — reset the
            # synchronous streak so the next small cluster is prompt.
            self._sync_streak = 0
        self._last_append_ts = now

        with self._log_lock:
            if self._pending > 0:
                self._pending -= 1
            if self._dropped:
                dropped = self._dropped
                self._dropped = 0
                self._buffer.append(
                    f"... {dropped} lines suppressed (log stream too "
                    "intense) ..."
                )

        self._buffer.append(text)

        if (
            len(self._buffer) <= _SMALL_QUEUE
            and self._sync_streak < _SYNC_FLUSH_STREAK
        ):
            # Small cluster of normal lines: render now so normal logging is
            # prompt and never waits for the batching timer. Once the streak
            # is exhausted a sustained flood batches below.
            self._sync_streak += 1
            self._flush()
        elif len(self._buffer) >= _MAX_BUFFER_BEFORE_FLUSH:
            # Timer starved by a flood: bound the buffer, flush a chunk now.
            self._sync_streak = 0
            self._flush()
        else:
            self._ensure_flush_timer()

    def clear_log(self):
        self._flush_timer.stop()
        self.clear()
        with self._log_lock:
            self._pending = 0
            self._dropped = 0
        self._buffer = []
        self._last_kind = "normal"
        self._improving_count = 0
        self._sync_streak = 0

    # ------------------------------------------------------------- internals

    def _ensure_flush_timer(self) -> None:
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _on_timer_flush(self) -> None:
        self._sync_streak = 0
        self._flush()

    def _flush(self) -> None:
        """Render every buffered line, coalescing consecutive progress runs."""
        self._flush_timer.stop()
        if not self._buffer:
            return
        lines = self._buffer
        self._buffer = []
        self._render_batch(lines)
        scrollbar = self.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    def _render_batch(self, lines: list[str]) -> None:
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            if _IMPROVING_RE.match(line):
                j = i
                while j + 1 < n and _IMPROVING_RE.match(lines[j + 1]):
                    j += 1
                self._render_improving(lines[j], j - i + 1)
                i = j + 1
            elif _STILL_RUNNING_RE.search(line):
                j = i
                while j + 1 < n and _STILL_RUNNING_RE.search(lines[j + 1]):
                    j += 1
                self._render_still_running(lines[j])
                i = j + 1
            else:
                self._render_normal(line)
                i += 1

    def _render_normal(self, text: str) -> None:
        self.append(self._line_to_html(text))
        self._last_kind = "normal"
        self._improving_count = 0

    def _render_improving(self, text: str, run: int) -> None:
        if self._last_kind == "improving":
            self._improving_count += run
            html = self._improving_html(text, self._improving_count)
            self._replace_last_block(html)
        else:
            self._improving_count = run
            self.append(self._improving_html(text, self._improving_count))
        self._last_kind = "improving"

    def _render_still_running(self, text: str) -> None:
        html = self._line_to_html(text)
        if self._last_kind == "still":
            # Replace the previous heartbeat in place — only one visible.
            self._replace_last_block(html)
        else:
            self.append(html)
        self._last_kind = "still"
        self._improving_count = 0

    def _improving_html(self, text: str, count: int) -> str:
        from html import escape

        suffix = escape(f"  ({count} lines)")
        return self._line_to_html(text) + suffix

    def _replace_last_block(self, html: str) -> None:
        """Rewrite the last rendered block in place (coalescing)."""
        block = self.document().lastBlock()
        if block is None or not block.isValid():
            self.append(html)
            return
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.StartOfBlock)
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertHtml(html)

    @staticmethod
    def _line_to_html(text: str) -> str:
        from html import escape

        html = escape(str(text))
        html = html.replace(
            "[ERROR]",
            f'<span style="color:{ERROR_LIGHT};font-weight:bold">[ERROR]</span>',
        )
        html = html.replace(
            "[WARN]",
            f'<span style="color:{WARNING_LIGHT};font-weight:bold">[WARN]</span>',
        )
        html = html.replace(
            "[SUBSTITUTED]",
            f'<span style="color:{INFO_LIGHT};font-weight:bold">[SUBSTITUTED]</span>',
        )
        html = html.replace(
            "[DONE]",
            f'<span style="color:{SUCCESS_LIGHT};font-weight:bold">[DONE]</span>',
        )
        html = html.replace(
            "[error]", f'<span style="color:{ERROR_LIGHT}">[error]</span>'
        )
        html = html.replace(
            "[warn]", f'<span style="color:{WARNING_LIGHT}">[warn]</span>'
        )
        html = html.replace(
            "[suggestion]",
            f'<span style="color:{PRIMARY_500}">[suggestion]</span>',
        )
        html = html.replace(
            "[FIX]", f'<span style="color:{SUCCESS_LIGHT};font-weight:bold">[FIX]</span>'
        )
        return html

    def _insert_line(self, text: str) -> None:
        """Immediately render one line (kept for compatibility/tests)."""
        self.append(self._line_to_html(text))
        scrollbar = self.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())
