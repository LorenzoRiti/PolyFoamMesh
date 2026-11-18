"""Lines without a [TAG] must not show HTML entities literally."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from polyfoammesh.gui.log_panel import LogPanel  # noqa: E402


def test_plain_line_shows_real_characters():
    _app = QApplication.instance() or QApplication([])
    panel = LogPanel()
    panel._insert_line("Run plan: 1) tet mesh > 2) poly; 'still running' & \"x\"")
    text = panel.toPlainText()
    assert "tet mesh > 2)" in text and "'still running' & \"x\"" in text
    assert "&gt;" not in text and "&#x27;" not in text and "&amp;" not in text
