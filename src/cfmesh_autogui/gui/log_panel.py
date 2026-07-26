from __future__ import annotations

import threading

from PySide6.QtWidgets import QTextEdit, QScrollBar
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtCore import Slot, Qt, QMetaObject, Q_ARG

from cfmesh_autogui.gui.design_tokens import ERROR_LIGHT, WARNING_LIGHT, SUCCESS_LIGHT, PRIMARY_500


class LogPanel(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 10))
        self.setPlaceholderText("Log output will appear here...")
        self._log_lock = threading.Lock()

    @Slot(str)
    def append_log(self, text: str):
        """Thread-safe log append with semantic highlighting.

        Can be safely called from any thread.  When invoked from a
        non-main thread the actual text insertion is dispatched to the
        GUI thread via invokeMethod with a QueuedConnection, preventing
        the Qt heap corruption (ntdll!RtlReportHeapFailure) that
        occurred when insertHtml/scrollbar were called directly from
        a QThread worker.
        """
        if threading.current_thread() is not threading.main_thread():
            QMetaObject.invokeMethod(
                self, "append_log", Qt.QueuedConnection,
                Q_ARG(str, text),
            )
            return

        with self._log_lock:
            html = str(text)
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
