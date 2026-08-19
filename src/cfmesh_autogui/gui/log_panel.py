from __future__ import annotations

import threading

from PySide6.QtCore import Q_ARG, QMetaObject, Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QTextEdit

from cfmesh_autogui.gui.design_tokens import (
    ERROR_LIGHT,
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


class LogPanel(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 10))
        self.setPlaceholderText("Log output will appear here...")
        self._log_lock = threading.Lock()
        # Qt trims the oldest blocks once the cap is reached — O(1), unlike
        # the previous cursor-walk prune that made every append during a
        # flood O(cap).
        self.document().setMaximumBlockCount(_MAX_LOG_LINES)
        self._pending = 0  # cross-thread appends queued, not yet rendered
        self._dropped = 0  # lines dropped during a burst

    @Slot(str)
    def append_log(self, text: str):
        """Thread-safe log append with semantic highlighting and burst
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

        with self._log_lock:
            if self._pending > 0:
                self._pending -= 1
            if self._dropped:
                dropped = self._dropped
                self._dropped = 0
                self._insert_line(
                    f"... {dropped} righe soppresse (flusso di log troppo "
                    "intenso) ..."
                )
            self._insert_line(text)

    def _insert_line(self, text: str):
        from html import escape
        html = escape(str(text))
        html = html.replace("[ERROR]", f'<span style="color:{ERROR_LIGHT};font-weight:bold">[ERROR]</span>')
        html = html.replace("[WARN]", f'<span style="color:{WARNING_LIGHT};font-weight:bold">[WARN]</span>')
        html = html.replace("[DONE]", f'<span style="color:{SUCCESS_LIGHT};font-weight:bold">[DONE]</span>')
        html = html.replace("[error]", f'<span style="color:{ERROR_LIGHT}">[error]</span>')
        html = html.replace("[warn]", f'<span style="color:{WARNING_LIGHT}">[warn]</span>')
        html = html.replace("[suggestion]", f'<span style="color:{PRIMARY_500}">[suggestion]</span>')
        html = html.replace("[FIX]", f'<span style="color:{SUCCESS_LIGHT};font-weight:bold">[FIX]</span>')
        self.insertHtml(html + "<br>")
        scrollbar = self.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())

    def clear_log(self):
        self.clear()
        with self._log_lock:
            self._pending = 0
            self._dropped = 0
