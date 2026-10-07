"""Lightweight QSS helpers.

This module is a thin facade over `design_tokens.py` for the few patterns
the existing GUI uses heavily (status pills, metric labels, cell-count
label). New code should prefer `design_tokens` constants directly.
"""
from __future__ import annotations

from polyfoammesh.gui.design_tokens import (
    SUCCESS,
    SUCCESS_LIGHT,
    SUCCESS_TEXT_LIGHT,
    WARNING,
    ERROR,
    NEUTRAL_500,
    NEUTRAL_400,
    FONT_SIZE_MD,
    FONT_SIZE_SM,
    PRIMARY_600,
)

# Legacy aliases (kept for backward compat — prefer design_tokens.*)
COLOR_TEXT_DIM = NEUTRAL_500
COLOR_TEXT_DISABLED = NEUTRAL_400
COLOR_ACCENT = PRIMARY_600  # brand accent — was a hardcoded blue that had
                            # drifted out of sync with design_tokens.PRIMARY_600
COLOR_DANGER = "#dc2626"  # semantic error
COLOR_BG_DIM = NEUTRAL_500  # legacy alias used in quality_panel.py

COLOR_PASS = SUCCESS
COLOR_WARN = WARNING
COLOR_FAIL = ERROR

FS_METRIC = FONT_SIZE_MD
FS_BTN_TINY = FONT_SIZE_SM
FS_STATUS = FONT_SIZE_MD

_STATUS_PILL_QSS = (
    "font-weight: bold; font-size: {fs}px; padding: 2px 8px;"
    " background-color: {bg}; color: {fg}; border-radius: 4px;"
)


def _relative_luminance(color: str) -> float:
    """WCAG relative luminance for a ``#rgb``/``#rrggbb`` color string."""
    s = color.lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    r, g, b = (int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _contrast_ratio(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def readable_fg(bg: str) -> str:
    """Return near-black or white, whichever contrasts better on *bg*.

    Status pills used to hardcode ``color: white`` regardless of the fill;
    on light fills (warning amber, success green) that was unreadable.
    """
    dark, light = "#1a1a1a", "#ffffff"
    try:
        return dark if _contrast_ratio(bg, dark) >= _contrast_ratio(bg, light) else light
    except (ValueError, IndexError):
        return light


def status_pill(bg: str) -> str:
    """Return the QSS string for the colored status banner."""
    return _STATUS_PILL_QSS.format(fs=FS_STATUS, bg=bg, fg=readable_fg(bg))


def muted_text_color() -> str:
    """Theme-aware secondary/disabled text color (follows light/dark)."""
    from polyfoammesh.gui.design_tokens import DARK, LIGHT
    from polyfoammesh.gui.theme import current_mode
    return (LIGHT if current_mode() == "light" else DARK)["fgMuted"]


def success_text_color() -> str:
    """Theme-aware success *text* color (readable on light and dark surfaces)."""
    from polyfoammesh.gui.theme import current_mode
    return SUCCESS_TEXT_LIGHT if current_mode() == "light" else SUCCESS_LIGHT


def metric_label(color: str | None = None, size: int = FS_METRIC) -> str:
    """Return the QSS string for a small metric label.

    ``color`` defaults to the theme-aware muted text color; pass an explicit
    color (e.g. a semantic status color) to override it.
    """
    if color is None:
        color = muted_text_color()
    return f"font-size: {size}px; color: {color};"


# Convenience aliases
PASS, WARN, FAIL = COLOR_PASS, COLOR_WARN, COLOR_FAIL
