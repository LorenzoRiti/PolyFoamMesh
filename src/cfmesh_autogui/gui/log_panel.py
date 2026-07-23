from __future__ import annotations

from PySide6.QtWidgets import QTextEdit, QScrollBar
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtCore import Slot

from cfmesh_autogui.gui.design_tokens import ERROR_LIGHT, WARNING_LIGHT, SUCCESS_LIGHT, PRIMARY_500


class LogPanel(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 10))
        self.setPlaceholderText("Log output will appear here...")

    @Slot(str)
    def append_log(self, text: str):
        """Thread-safe log append with semantic highlighting."""
        # ✅ F-020: semantic color highlighting
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
