"""Theme manager: applies the design system to a running QApplication.

Three modes:
  - "light":  force light QSS
  - "dark":   force dark QSS
  - "system": follow OS preference (recommended default)

Usage:
    from PySide6.QtWidgets import QApplication
    from cfmesh_autogui.gui.theme import apply_theme, set_theme_mode
    app = QApplication([])
    set_theme_mode("system")   # or "light" / "dark"
    apply_theme(app)

    # later, in a settings dialog:
    set_theme_mode("dark")
    apply_theme(app)

The QSS strings are embedded in this module (not loaded from disk) so the
bundled EXE has no external file dependencies.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot, Qt
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication

from cfmesh_autogui.gui.design_tokens import (
    APP_NAME, APP_VERSION, FONT_SANS,
    LIGHT, DARK, NEUTRAL_50, NEUTRAL_900,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# QSS bodies (loaded from .ui-design/qss/ if available, else embedded fallback)
# ---------------------------------------------------------------------------
_QSS_DIR = Path(__file__).resolve().parents[3] / ".ui-design" / "qss"
_EMBEDDED_LIGHT = """\
/* ANSYS Workbench-inspired light theme */
QWidget { background:#f0f2f5; color:#1a1a2e; font-size:12px; font-family:'Segoe UI','Helvetica Neue',Arial,sans-serif; }
QMainWindow { background:#e8ebf0; }
QMenuBar { background:#ffffff; color:#1a1a2e; border-bottom:1px solid #d4d8dd; padding:2px; }
QMenuBar::item:selected { background:#e8f0fe; color:#0078d7; }
QMenu { background:#ffffff; border:1px solid #c8ccd4; border-radius:4px; padding:4px; }
QMenu::item { padding:6px 24px; border-radius:3px; }
QMenu::item:selected { background:#e8f0fe; color:#0078d7; }
QPushButton { background:#ffffff; color:#1a1a2e; border:1px solid #c8ccd4;
  border-radius:4px; padding:5px 14px; min-height:22px; font-weight:500; }
QPushButton:hover { background:#f0f2f5; border-color:#0078d7; }
QPushButton:pressed { background:#e0e4ea; }
QPushButton:default { background:#0078d7; color:#ffffff; border-color:#0078d7; }
QPushButton:default:hover { background:#106ebe; }
QGroupBox { background:#ffffff; border:1px solid #d4d8dd; border-radius:6px;
  margin-top:12px; padding:14px 10px 10px 10px; font-weight:500; }
QGroupBox::title { subcontrol-origin:margin; subcontrol-position:top left;
  padding:1px 8px; color:#0078d7; background:#ffffff; left:8px; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background:#ffffff; color:#1a1a2e;
  border:1px solid #c8ccd4; border-radius:4px; padding:3px 8px; min-height:22px; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color:#0078d7; }
QComboBox::drop-down { border:none; padding-right:8px; }
QTextEdit, QPlainTextEdit { background:#f8f9fb; color:#1a1a2e;
  border:1px solid #d4d8dd; border-radius:4px; padding:4px;
  font-family:'Cascadia Code','JetBrains Mono',Consolas,monospace; font-size:11px; }
QStatusBar { background:#e8ebf0; color:#4b5565; border-top:1px solid #d4d8dd; font-size:11px; }
QProgressBar { background:#e0e3e8; border:none; border-radius:3px; min-height:6px; max-height:6px; }
QProgressBar::chunk { background:#0078d7; border-radius:3px; }
QScrollBar:vertical { background:transparent; width:8px; }
QScrollBar::handle:vertical { background:#c8ccd4; min-height:30px; border-radius:4px; }
QScrollBar::handle:vertical:hover { background:#a0a5b0; }
QScrollBar:horizontal { background:transparent; height:8px; }
QScrollBar::handle:horizontal { background:#c8ccd4; min-width:30px; border-radius:4px; }
QSplitter::handle { background:#d4d8dd; width:2px; height:2px; }
QSplitter::handle:hover { background:#0078d7; }
QToolTip { background:#1a1a2e; color:#e0e4ee; border:1px solid #3a3a5c; border-radius:4px; }
QToolBar { background:#ffffff; border-bottom:1px solid #d4d8dd; spacing:2px; padding:2px; }
QToolBar QToolButton { border:1px solid transparent; border-radius:4px; padding:6px 10px; }
QToolBar QToolButton:hover { background:#e8f0fe; border-color:#d4d8dd; }
QToolBar QToolButton:checked { background:#0078d7; color:#ffffff; border-color:#0078d7; }
QTreeWidget { background:#ffffff; border:none; font-size:12px; }
QTreeWidget::item { padding:6px 4px; border-bottom:1px solid #f0f2f5; }
QTreeWidget::item:selected { background:#e8f0fe; color:#0078d7; }
QTreeWidget::item:hover { background:#f5f7fa; }
QHeaderView::section { background:#f0f2f5; border:none; padding:4px; font-weight:600; }
QTabWidget::pane { border:1px solid #d4d8dd; border-radius:4px; background:#ffffff; }
QTabBar::tab { background:#f0f2f5; border:1px solid #d4d8dd; padding:6px 14px; margin-right:2px; border-radius:3px 3px 0 0; }
QTabBar::tab:selected { background:#ffffff; border-bottom-color:#ffffff; color:#0078d7; font-weight:600; }
QTabBar::tab:hover { background:#e8f0fe; }
QLabel[role="status-pass"]   { background:#27ae60; color:#ffffff; padding:2px 10px; border-radius:4px; font-weight:700; font-size:10px; }
QLabel[role="status-warn"]   { background:#f39c12; color:#ffffff; padding:2px 10px; border-radius:4px; font-weight:700; font-size:10px; }
QLabel[role="status-fail"]   { background:#e74c3c; color:#ffffff; padding:2px 10px; border-radius:4px; font-weight:700; font-size:10px; }
QLabel[role="status-neutral"]{ background:#95a5a6; color:#ffffff; padding:2px 10px; border-radius:4px; font-weight:700; font-size:10px; }
"""

_EMBEDDED_DARK = """\
/* ANSYS Fluent-inspired dark theme */
QWidget { background:#1a1a2e; color:#e0e4ee; font-size:12px; font-family:'Segoe UI','Helvetica Neue',Arial,sans-serif; }
QMainWindow { background:#16172b; }
QMenuBar { background:#2d2d3a; color:#e0e4ee; border-bottom:1px solid #3a3a4a; padding:2px; }
QMenuBar::item:selected { background:#3a3a5c; color:#ffffff; }
QMenu { background:#2d2d3a; border:1px solid #3a3a5c; border-radius:4px; padding:4px; color:#e0e4ee; }
QMenu::item { padding:6px 24px; border-radius:3px; }
QMenu::item:selected { background:#0078d7; color:#ffffff; }
QMenu::separator { height:1px; background:#3a3a5c; margin:4px 8px; }
QPushButton { background:#2d2d3a; color:#e0e4ee; border:1px solid #3a3a5c;
  border-radius:4px; padding:5px 14px; min-height:22px; font-weight:500; }
QPushButton:hover { background:#3a3a5c; border-color:#0078d7; }
QPushButton:pressed { background:#1e1e30; }
QPushButton:default { background:#0078d7; color:#ffffff; border-color:#0078d7; }
QPushButton:default:hover { background:#4a9bdb; }
QGroupBox { background:#1e1e30; border:1px solid #3a3a5c; border-radius:6px;
  margin-top:12px; padding:14px 10px 10px 10px; }
QGroupBox::title { subcontrol-origin:margin; subcontrol-position:top left;
  padding:1px 8px; color:#4a9bdb; background:#1e1e30; left:8px; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background:#1e1e30; color:#e0e4ee;
  border:1px solid #3a3a5c; border-radius:4px; padding:3px 8px; min-height:22px; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color:#0078d7; }
QComboBox::drop-down { border:none; padding-right:8px; }
QComboBox QAbstractItemView { background:#2d2d3a; border:1px solid #3a3a5c; color:#e0e4ee; selection-background-color:#0078d7; }
QTextEdit, QPlainTextEdit { background:#14142a; color:#c8cce0;
  border:1px solid #3a3a5c; border-radius:4px; padding:4px;
  font-family:'Cascadia Code','JetBrains Mono',Consolas,monospace; font-size:11px; }
QStatusBar { background:#16172b; color:#8888b0; border-top:1px solid #3a3a5c; font-size:11px; }
QProgressBar { background:#2d2e4a; border:none; border-radius:3px; min-height:6px; max-height:6px; }
QProgressBar::chunk { background:#0078d7; border-radius:3px; }
QScrollBar:vertical { background:transparent; width:8px; }
QScrollBar::handle:vertical { background:#3a3a5c; min-height:30px; border-radius:4px; }
QScrollBar::handle:vertical:hover { background:#5a5a7a; }
QScrollBar:horizontal { background:transparent; height:8px; }
QScrollBar::handle:horizontal { background:#3a3a5c; min-width:30px; border-radius:4px; }
QSplitter::handle { background:#3a3a5c; width:2px; height:2px; }
QSplitter::handle:hover { background:#0078d7; }
QToolTip { background:#e0e4ee; color:#1a1a2e; border:1px solid #a0a5c0; border-radius:4px; }
QToolBar { background:#2d2d3a; border-bottom:1px solid #3a3a5c; spacing:2px; padding:2px; }
QToolBar QToolButton { border:1px solid transparent; border-radius:4px; padding:6px 10px; color:#e0e4ee; }
QToolBar QToolButton:hover { background:#3a3a5c; border-color:#50507a; }
QToolBar QToolButton:checked { background:#0078d7; color:#ffffff; border-color:#0078d7; }
QTreeWidget { background:#1e1e30; border:none; font-size:12px; color:#e0e4ee; }
QTreeWidget::item { padding:6px 4px; border-bottom:1px solid #2a2a40; }
QTreeWidget::item:selected { background:#0078d7; color:#ffffff; }
QTreeWidget::item:hover { background:#2a2a40; }
QHeaderView::section { background:#16172b; border:none; padding:4px; font-weight:600; color:#e0e4ee; }
QTabWidget::pane { border:1px solid #3a3a5c; border-radius:4px; background:#1e1e30; }
QTabBar::tab { background:#2d2d3a; border:1px solid #3a3a5c; padding:6px 14px; margin-right:2px; border-radius:3px 3px 0 0; color:#e0e4ee; }
QTabBar::tab:selected { background:#1e1e30; border-bottom-color:#1e1e30; color:#4a9bdb; font-weight:600; }
QTabBar::tab:hover { background:#3a3a5c; }
QTableWidget { background:#1e1e30; border:1px solid #3a3a5c; gridline-color:#2a2a40; color:#e0e4ee; }
QTableWidget::item:selected { background:#0078d7; }
QLabel[role="status-pass"]   { background:#27ae60; color:#ffffff; padding:2px 10px; border-radius:4px; font-weight:700; font-size:10px; }
QLabel[role="status-warn"]   { background:#f39c12; color:#ffffff; padding:2px 10px; border-radius:4px; font-weight:700; font-size:10px; }
QLabel[role="status-fail"]   { background:#e74c3c; color:#ffffff; padding:2px 10px; border-radius:4px; font-weight:700; font-size:10px; }
QLabel[role="status-neutral"]{ background:#95a5a6; color:#ffffff; padding:2px 10px; border-radius:4px; font-weight:700; font-size:10px; }
QLabel[role="workflow-title"] { font-weight:700; color:#e37222; font-size:11px; padding:4px 8px; }
QLabel[role="section-header"] { font-weight:600; color:#4a9bdb; font-size:10px; padding:2px 0; }
"""


def _load_qss(mode: str) -> str:
    """Load QSS from disk if present, else use embedded fallback."""
    path = _QSS_DIR / f"{mode}.qss"
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read %s: %s — using embedded", path, exc)
    return _EMBEDDED_LIGHT if mode == "light" else _EMBEDDED_DARK


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
class ThemeManager(QObject):
    """Singleton-style theme manager. Tracks current mode and broadcasts changes."""

    themeChanged = Signal(str)  # emits the resolved ("light" | "dark") mode

    _instance: "ThemeManager | None" = None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._requested_mode: str = "system"  # what the user asked for
        self._resolved_mode: str = "light"    # what is actually applied

    @classmethod
    def instance(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = ThemeManager()
        return cls._instance

    @property
    def requested_mode(self) -> str:
        return self._requested_mode

    @property
    def resolved_mode(self) -> str:
        return self._resolved_mode

    def request(self, mode: str) -> None:
        """Set the user's preferred mode ('light', 'dark', or 'system')."""
        if mode not in ("light", "dark", "system"):
            raise ValueError(f"Unknown theme mode: {mode!r}")
        self._requested_mode = mode
        if mode != "system":
            self._resolved_mode = mode
        else:
            self._resolved_mode = _detect_system_mode()
        self.themeChanged.emit(self._resolved_mode)

    def toggle(self) -> str:
        """Flip between light and dark; returns the new resolved mode."""
        new = "dark" if self._resolved_mode == "light" else "light"
        self.request(new)
        return self._resolved_mode


def _detect_system_mode() -> str:
    """Best-effort detection of the OS dark-mode preference."""
    # Qt 6.5+ exposes a hint via QStyleHints; fall back to palette lightness.
    app = QApplication.instance()
    if app is not None:
        try:
            hints = app.styleHints()
            if hasattr(hints, "colorScheme"):
                # Qt 6.5+
                scheme = hints.colorScheme()
                if scheme == Qt.ColorScheme.Dark:
                    return "dark"
                if scheme == Qt.ColorScheme.Light:
                    return "light"
        except Exception:
            pass
    # Fallback: if the default window background is dark, assume dark mode
    if app is not None:
        bg = app.palette().color(QPalette.Window)
        if bg.lightness() < 128:
            return "dark"
    return "light"


def apply_theme(app: QApplication | None = None) -> str:
    """Apply the current theme to *app*. Returns the resolved mode.

    Idempotent: safe to call multiple times. Listens to system theme changes
    when the requested mode is 'system'.
    """
    app = app or QApplication.instance()
    if app is None:
        raise RuntimeError("apply_theme() called with no QApplication instance")

    mgr = ThemeManager.instance()
    if mgr._requested_mode == "system":
        mgr._resolved_mode = _detect_system_mode()
        # Re-check on next system change
        try:
            hints = app.styleHints()
            if hasattr(hints, "colorSchemeChanged"):
                hints.colorSchemeChanged.connect(_on_system_scheme_changed)
        except Exception:
            pass

    qss = _load_qss(mgr._resolved_mode)
    app.setStyleSheet(qss)
    _apply_qpalette(app, mgr._resolved_mode)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    # Default font
    font = app.font()
    font.setFamily(FONT_SANS.split(",")[0].strip().strip('"').strip("'"))
    font.setPointSize(10)
    app.setFont(font)
    logger.info("Theme applied: %s (requested=%s)", mgr._resolved_mode, mgr._requested_mode)
    return mgr._resolved_mode


@Slot()
def _on_system_scheme_changed() -> None:
    """Slot: re-apply when the OS color scheme changes (only if mode='system')."""
    mgr = ThemeManager.instance()
    if mgr._requested_mode == "system":
        new = _detect_system_mode()
        if new != mgr._resolved_mode:
            mgr._resolved_mode = new
            apply_theme()


def set_theme_mode(mode: str) -> None:
    """Convenience: set the requested mode without applying it yet."""
    ThemeManager.instance().request(mode)


def current_mode() -> str:
    """Return the currently resolved mode ('light' or 'dark')."""
    return ThemeManager.instance()._resolved_mode


def current_request() -> str:
    """Return the user's requested mode ('light', 'dark', or 'system')."""
    return ThemeManager.instance()._requested_mode


def _apply_qpalette(app: QApplication, mode: str) -> None:
    """Apply a QPalette consistent with the QSS for native-rendered widgets."""
    p = app.palette()
    tokens = LIGHT if mode == "light" else DARK
    p.setColor(QPalette.Window,          QColor(tokens["bgBase"]))
    p.setColor(QPalette.WindowText,      QColor(tokens["fgDefault"]))
    p.setColor(QPalette.Base,            QColor(tokens["bgElevated"]))
    p.setColor(QPalette.AlternateBase,   QColor(tokens["bgSubtle"]))
    p.setColor(QPalette.Text,            QColor(tokens["fgDefault"]))
    p.setColor(QPalette.Button,          QColor(tokens["bgElevated"]))
    p.setColor(QPalette.ButtonText,      QColor(tokens["fgDefault"]))
    p.setColor(QPalette.Highlight,       QColor(tokens["accent"]))
    p.setColor(QPalette.HighlightedText, QColor(tokens["accentFg"]))
    p.setColor(QPalette.ToolTipBase,     QColor("#0f172a" if mode == "light" else "#f8fafc"))
    p.setColor(QPalette.ToolTipText,     QColor("#f8fafc" if mode == "light" else "#0f172a"))
    p.setColor(QPalette.PlaceholderText, QColor(tokens["fgSubtle"]))
    app.setPalette(p)
