"""Lightweight QSS helpers.

This module is a thin facade over `design_tokens.py` for the few patterns
the existing GUI uses heavily (status pills, metric labels, cell-count
label). New code should prefer `design_tokens` constants directly.
"""
from __future__ import annotations

from cfmesh_autogui.gui.design_tokens import (
    SUCCESS, SUCCESS_LIGHT, WARNING, WARNING_LIGHT,
    ERROR, ERROR_LIGHT, NEUTRAL_500, NEUTRAL_400,
    FONT_SIZE_MD, FONT_SIZE_SM, PRIMARY_600,
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
    " background-color: {bg}; color: white; border-radius: 4px;"
)


def status_pill(bg: str) -> str:
    """Return the QSS string for the colored status banner."""
    return _STATUS_PILL_QSS.format(fs=FS_STATUS, bg=bg)


def metric_label(color: str = COLOR_TEXT_DIM, size: int = FS_METRIC) -> str:
    """Return the QSS string for a small metric label."""
    return f"font-size: {size}px; color: {color};"


# Convenience aliases
PASS, WARN, FAIL = COLOR_PASS, COLOR_WARN, COLOR_FAIL
