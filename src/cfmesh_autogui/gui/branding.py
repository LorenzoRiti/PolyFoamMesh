"""Branding assets for CFMesh-AutoGUI.

All assets are generated programmatically (QPainter) so the bundle has
zero external image dependencies. Resolution-independent and theme-aware.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize, QPointF
from PySide6.QtGui import QPixmap, QPainter, QColor, QIcon, QFont, QFontMetrics, QPolygonF
from PySide6.QtWidgets import QApplication, QStyle

from cfmesh_autogui.gui.design_tokens import (
    APP_NAME, APP_VERSION, APP_DESCRIPTION, APP_VENDOR, APP_COPYRIGHT, APP_BUILD_YEAR,
    PRIMARY_600, PRIMARY_500, ORANGE_500, FLUENT_DARK, NEUTRAL_50, NEUTRAL_900, NEUTRAL_800,
)


_LOGO_BG = PRIMARY_600
_LOGO_BG_DARK = PRIMARY_500
_LOGO_FG = "#ffffff"


def _draw_logo_on(pix: QPixmap, dark: bool = False) -> None:
    """Draw the app logo into *pix* (which must already be allocated)."""
    size = pix.width()
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    bg = QColor(_LOGO_BG_DARK if dark else _LOGO_BG)
    p.setBrush(bg)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, size * 0.20, size * 0.20)

    # Inner rounded square (background-coloured, so the "C" appears as
    # the difference between this and the outer rounded square)
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    inner_margin = size * 0.30
    p.drawRoundedRect(
        int(inner_margin), int(inner_margin),
        int(size - 2 * inner_margin), int(size - 2 * inner_margin),
        int(size * 0.40), int(size * 0.40),
    )
    p.end()


def make_app_icon(size: int = 256, dark: bool = False) -> QIcon:
    """Return a QIcon with the logo at the requested size (multi-resolution)."""
    icon = QIcon()
    # Provide the same icon at multiple standard sizes — Qt picks the best.
    for s in (16, 24, 32, 48, 64, 128, 256):
        pix = QPixmap(s, s)
        pix.fill(Qt.transparent)
        _draw_logo_on(pix, dark=dark)
        icon.addPixmap(pix)
    return icon


def make_logo_pixmap(size: int = 96, dark: bool = False) -> QPixmap:
    """Single-resolution logo pixmap (for the splash and About dialog)."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    _draw_logo_on(pix, dark=dark)
    return pix


def make_splash_pixmap(width: int = 520, height: int = 300, dark: bool = False) -> QPixmap:
    """Render the splash screen pixmap — ANSYS-grade commercial style."""
    pix = QPixmap(width, height)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    # Background: dark gradient (like ANSYS Fluent)
    if dark:
        bg = QColor("#1a1a2e")
        bottom_bar = QColor(ORANGE_500)
        accent = QColor(ORANGE_500)
    else:
        bg = QColor("#f0f2f5")
        bottom_bar = QColor(PRIMARY_600)
        accent = QColor(PRIMARY_600)
    p.fillRect(0, 0, width, height, bg)

    # Thin top accent line (like ANSYS Workbench orange header)
    p.fillRect(0, 0, width, 3, QColor(ORANGE_500))

    # Logo on the left
    logo_size = 96
    logo = make_logo_pixmap(logo_size, dark=dark)
    p.drawPixmap(24, (height - logo_size) // 2 - 8, logo)

    # App name — bold display
    text_x = 24 + logo_size + 20
    name_color = QColor("#ffffff" if dark else "#1a1a2e")
    name_font = QFont()
    name_font.setPointSize(24)
    name_font.setBold(True)
    p.setFont(name_font)
    p.setPen(name_color)
    p.drawText(text_x, 92, APP_NAME)

    # Tagline
    sub_font = QFont()
    sub_font.setPointSize(10)
    p.setFont(sub_font)
    p.setPen(QColor(accent.name()))
    p.drawText(text_x, 115, APP_DESCRIPTION)

    # Version + copyright
    ver_font = QFont()
    ver_font.setPointSize(8)
    p.setFont(ver_font)
    p.setPen(QColor("#8888b0" if dark else "#888888"))
    p.drawText(text_x, 138, f"Version {APP_VERSION}  •  Build {APP_BUILD_YEAR}")
    p.drawText(text_x, 154, f"{APP_VENDOR}")

    # Bottom accent bar (thick, like ANSYS mechanical)
    bar_h = 4
    p.fillRect(0, height - bar_h, width, bar_h, bottom_bar)

    # Loading indicator dots at bottom-right
    p.setPen(QColor("#555577" if dark else "#aaaaaa"))
    p.drawText(width - 100, height - 14, "Loading...")

    p.end()
    return pix


# ---------------------------------------------------------------------------
# Ribbon toolbar icons (16x16, QPainter-generated, no external assets)
# ---------------------------------------------------------------------------
_RIBBON_ICON_CACHE: dict[str, QIcon] = {}

def _draw_ribbon_icon(name: str, size: int = 20) -> QIcon:
    """Draw a simple vector icon for ribbon toolbar buttons."""
    icon = QIcon()
    for s in (16, 20, 24):
        pix = QPixmap(s, s)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QColor("#e0e4ee")
        brush = QColor("#e0e4ee")
        half = s // 2
        q4 = s // 4
        q3 = s // 3
        if name == "home":
            # Simple house
            p.setPen(Qt.NoPen)
            p.setBrush(brush)
            pts = [(half, 2), (2, half+2), (s-2, half+2)]
            p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
            p.drawRect(q4, half, q4*2, q4*2)
        elif name == "geometry":
            # Cube
            p.setPen(Qt.NoPen)
            p.setBrush(brush)
            p.drawRect(q3, q3+2, s-2*q3, s-2*q3-2)
            p.setBrush(QColor("#555577"))
            p.drawRect(q3+2, q3+4, s-2*q3-4, s-2*q3-6)
        elif name == "mesh":
            # Grid
            p.setPen(QColor("#e0e4ee"))
            for i in range(1, 4):
                x = i * s // 4
                p.drawLine(x, 3, x, s-3)
                y = i * s // 4
                p.drawLine(3, y, s-3, y)
        elif name == "advanced":
            # Gear (circle with teeth)
            p.setPen(Qt.NoPen)
            p.setBrush(brush)
            p.drawEllipse(q3+1, q3+1, s-2*q3-2, s-2*q3-2)
            p.setBrush(QColor("#1a1a2e"))
            p.drawEllipse(half-2, half-2, 4, 4)
        elif name == "quality":
            # Checkmark
            p.setPen(QColor("#27ae60"))
            p.drawLine(3, half, half-2, s-4)
            p.drawLine(half-2, s-4, s-3, 4)
        elif name == "quick":
            # Lightning bolt (orange)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#e37222"))
            pts = [(half, 2), (s-3, half+2), (half+1, half+2), (half-1, s-3), (3, half-1), (half-2, half-1)]
            p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
        p.end()
        icon.addPixmap(pix)
    _RIBBON_ICON_CACHE[name] = icon
    return icon


def get_ribbon_icon(name: str) -> QIcon:
    if name not in _RIBBON_ICON_CACHE:
        _draw_ribbon_icon(name)
    return _RIBBON_ICON_CACHE.get(name, QIcon())


def apply_app_icon(app: QApplication, dark: bool = False) -> None:
    """Set the application-wide icon."""
    app.setWindowIcon(make_app_icon(dark=dark))
