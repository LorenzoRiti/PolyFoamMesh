"""Design tokens for PolyFoamMesh (commercial-grade).

This module is the single source of truth for all visual values used by the
GUI. It is consumed by:

  - the in-app `theme.py` (palette + font + QSS for the running QApplication)
  - the legacy `style.py` (lightweight helpers: status_pill, metric_label)
  - any new widget that needs brand colors, spacing, or font sizes.

Design contract:
  - Never hardcode a color, spacing, or font size in widget code. Import from here.
  - All values are immutable strings; do not mutate at runtime.
  - For a "rebrand", change values here and reload via `theme.apply(app)`.

Tokens follow the W3C Design Token Community Group format and mirror
`.ui-design/design-system.json` one-to-one. If you add a token, add it to
both places.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# ANSYS-inspired commercial color palette
# ---------------------------------------------------------------------------
# ANSYS Workbench accent orange
ORANGE_500 = "#e37222"
ORANGE_600 = "#c95a1a"
ORANGE_400 = "#f08c4a"
# ANSYS Fluent dark ribbon / header
FLUENT_DARK = "#2d2d2d"
FLUENT_DARKER = "#1a1a1a"
FLUENT_RIBBON = "#3c3c3c"
# Brand accent — Anthropic clay/terracotta (replaces the former ANSYS-blue
# accent everywhere it was used as the app's primary/selected color).
ANTHROPIC_CLAY = "#d97757"
ANTHROPIC_CLAY_LIGHT = "#e2916f"
ANTHROPIC_CLAY_DARK = "#b85c3e"
ANTHROPIC_CLAY_TINT = "#fbede6"  # very light bg tint for hover/selected rows
# Graphics background (dark)
GRAPHICS_BG = "#1e1e1e"
GRAPHICS_BG_LIGHT = "#f4f4f4"
# Workflow cell colors
CELL_HEADER = "#2b579a"
CELL_BODY_LIGHT = "#ffffff"
CELL_BODY_DARK = "#2d2d3a"
CELL_BORDER = "#e0e0e0"
# Ribbon tab colors
RIBBON_BG = "#3c3c3c"
RIBBON_TEXT = "#ffffff"
RIBBON_HOVER = "#505050"
RIBBON_SELECTED = ANTHROPIC_CLAY
# Status indicators (ANSYS Workbench style)
STATUS_READY = "#27ae60"       # green check
STATUS_ATTENTION = "#f39c12"  # yellow triangle
STATUS_ERROR = "#e74c3c"      # red X
STATUS_REFRESH = "#3498db"    # blue arrow
STATUS_PENDING = "#95a5a6"    # gray

# Color: brand palettes
# ---------------------------------------------------------------------------
# Primary (Anthropic clay/terracotta)
PRIMARY_50  = "#fdf3ef"
PRIMARY_100 = "#fbe4da"
PRIMARY_200 = "#f6c7b0"
PRIMARY_300 = "#eea283"
PRIMARY_400 = ANTHROPIC_CLAY_LIGHT   # "#e2916f"
PRIMARY_500 = ANTHROPIC_CLAY         # "#d97757"
PRIMARY_600 = ANTHROPIC_CLAY_DARK    # "#b85c3e"
PRIMARY_700 = "#a34a30"
PRIMARY_800 = "#833a27"
PRIMARY_900 = "#5f2a1e"
PRIMARY_950 = "#3d1a13"

# Secondary (accent violet)
SECONDARY_50  = "#f5f3ff"
SECONDARY_100 = "#ede9fe"
SECONDARY_200 = "#ddd6fe"
SECONDARY_300 = "#c4b5fd"
SECONDARY_400 = "#a78bfa"
SECONDARY_500 = "#8b5cf6"
SECONDARY_600 = "#7c3aed"
SECONDARY_700 = "#6d28d9"
SECONDARY_800 = "#5b21b6"
SECONDARY_900 = "#4c1d95"
SECONDARY_950 = "#2e1065"

# Accent (cyan, for highlights / data viz)
ACCENT_500 = "#06b6d4"
ACCENT_600 = "#0891b2"

# Neutral (true gray, no blue tint — BaramFlow/most CFD tools use flat
# grays rather than a slate/blue-tinted scale for the dark surfaces)
NEUTRAL_50  = "#fafafa"
NEUTRAL_100 = "#f5f5f5"
NEUTRAL_200 = "#e5e5e5"
NEUTRAL_300 = "#d4d4d4"
NEUTRAL_400 = "#a3a3a3"
NEUTRAL_500 = "#737373"
NEUTRAL_600 = "#525252"
NEUTRAL_700 = "#404040"
NEUTRAL_800 = "#262626"
NEUTRAL_900 = "#171717"
NEUTRAL_950 = "#0a0a0a"

# Semantic
SUCCESS       = "#16a34a"
SUCCESS_LIGHT = "#22c55e"
WARNING       = "#f59e0b"
WARNING_LIGHT = "#fbbf24"
ERROR         = "#dc2626"
ERROR_LIGHT   = "#ef4444"
INFO          = "#2563eb"
INFO_LIGHT    = "#60a5fa"

# ---------------------------------------------------------------------------
# Semantic palette: light mode
# ---------------------------------------------------------------------------
LIGHT = {
    "bgBase":      NEUTRAL_50,
    "bgSubtle":    NEUTRAL_100,
    "bgMuted":     NEUTRAL_200,
    "bgElevated":  "#ffffff",
    "fgDefault":   NEUTRAL_900,
    "fgMuted":     NEUTRAL_600,
    "fgSubtle":    NEUTRAL_500,
    "fgOnAccent":  "#ffffff",
    "border":      NEUTRAL_200,
    "borderStrong":NEUTRAL_300,
    "accent":      PRIMARY_600,
    "accentHover": PRIMARY_700,
    "accentFg":    "#ffffff",
    "success":     SUCCESS,
    "warning":     WARNING,
    "error":       ERROR,
    "info":        INFO,
    "selection":   PRIMARY_100,
    "selectionFg": PRIMARY_900,
}

# ---------------------------------------------------------------------------
# Semantic palette: dark mode
# ---------------------------------------------------------------------------
DARK = {
    "bgBase":      NEUTRAL_950,
    "bgSubtle":    NEUTRAL_900,
    "bgMuted":     NEUTRAL_800,
    "bgElevated":  NEUTRAL_800,
    "fgDefault":   NEUTRAL_50,
    "fgMuted":     NEUTRAL_400,
    "fgSubtle":    NEUTRAL_500,
    "fgOnAccent":  "#ffffff",
    "border":      NEUTRAL_800,
    "borderStrong":NEUTRAL_700,
    "accent":      PRIMARY_500,
    "accentHover": PRIMARY_400,
    "accentFg":    "#ffffff",
    "success":     SUCCESS_LIGHT,
    "warning":     WARNING_LIGHT,
    "error":       ERROR_LIGHT,
    "info":        INFO_LIGHT,
    "selection":   PRIMARY_800,
    "selectionFg": PRIMARY_100,
}

# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------
FONT_SANS = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', "
    "'Helvetica Neue', Arial, sans-serif"
)
FONT_MONO = (
    "'Cascadia Code', 'JetBrains Mono', 'Fira Code', "
    "Consolas, 'Courier New', monospace"
)

FONT_SIZE_XS   = 10
FONT_SIZE_SM   = 11
FONT_SIZE_BASE = 12
FONT_SIZE_MD   = 13
FONT_SIZE_LG   = 15
FONT_SIZE_XL   = 17
FONT_SIZE_2XL  = 20
FONT_SIZE_3XL  = 26

FONT_WEIGHT_NORMAL   = 400
FONT_WEIGHT_MEDIUM   = 500
FONT_WEIGHT_SEMIBOLD = 600
FONT_WEIGHT_BOLD     = 700

LINE_HEIGHT_TIGHT   = 1.2
LINE_HEIGHT_NORMAL  = 1.45
LINE_HEIGHT_RELAXED = 1.6

# ---------------------------------------------------------------------------
# Spacing (4px base, Tailwind-compatible)
# ---------------------------------------------------------------------------
SPACE_0  = 0
SPACE_1  = 4
SPACE_2  = 8
SPACE_3  = 12
SPACE_4  = 16
SPACE_5  = 20
SPACE_6  = 24
SPACE_7  = 32
SPACE_8  = 40
SPACE_9  = 48
SPACE_10 = 64

# ---------------------------------------------------------------------------
# Radius
# ---------------------------------------------------------------------------
RADIUS_NONE   = 0
RADIUS_SM     = 3
RADIUS_BASE   = 4
RADIUS_MD     = 6
RADIUS_LG     = 8
RADIUS_XL     = 12
RADIUS_2XL    = 16
RADIUS_PILL   = 9999
RADIUS_CIRCLE = 9999  # effectively circular for square elements

# ---------------------------------------------------------------------------
# Borders
# ---------------------------------------------------------------------------
BORDER_0 = 0
BORDER_1 = 1
BORDER_2 = 2
BORDER_3 = 3

# ---------------------------------------------------------------------------
# Shadows (used in QSS as box-shadow equivalent via border + color overlays)
# Stored as descriptive strings for documentation; QSS uses lighter borders.
# ---------------------------------------------------------------------------
SHADOW_NONE  = "none"
SHADOW_XS    = "0 1px 2px 0 rgba(0,0,0,0.05)"
SHADOW_SM    = "0 1px 3px 0 rgba(0,0,0,0.10), 0 1px 2px 0 rgba(0,0,0,0.06)"
SHADOW_MD    = "0 4px 6px -1px rgba(0,0,0,0.10), 0 2px 4px -1px rgba(0,0,0,0.06)"
SHADOW_LG    = "0 10px 15px -3px rgba(0,0,0,0.10), 0 4px 6px -2px rgba(0,0,0,0.05)"
SHADOW_XL    = "0 20px 25px -5px rgba(0,0,0,0.10), 0 10px 10px -5px rgba(0,0,0,0.04)"

# ---------------------------------------------------------------------------
# Animation (used by QPropertyAnimation in code; documented in design tokens)
# ---------------------------------------------------------------------------
DURATION_INSTANT = 0
DURATION_FAST    = 120
DURATION_NORMAL  = 200
DURATION_SLOW    = 320
DURATION_SLOWER  = 500

EASING_DEFAULT = "cubic-bezier(0.4, 0, 0.2, 1)"
EASING_IN      = "cubic-bezier(0.4, 0, 1, 1)"
EASING_OUT     = "cubic-bezier(0.0, 0, 0.2, 1)"
EASING_IN_OUT  = "cubic-bezier(0.4, 0, 0.2, 1)"
EASING_LINEAR  = "linear"

# ---------------------------------------------------------------------------
# Z-index (kept for documentation; Qt manages stacking implicitly)
# ---------------------------------------------------------------------------
Z_BASE     = 0
Z_RAISED   = 10
Z_DROPDOWN = 100
Z_STICKY   = 200
Z_MODAL    = 1000
Z_TOOLTIP  = 2000
Z_TOAST    = 3000

# ---------------------------------------------------------------------------
# Component sizes
# ---------------------------------------------------------------------------
BUTTON_HEIGHT_SM   = 24
BUTTON_HEIGHT_MD   = 32
BUTTON_HEIGHT_LG   = 40
BUTTON_MIN_WIDTH   = 64
BUTTON_PADDING_X   = 12
BUTTON_PADDING_X_SM = 8

INPUT_HEIGHT     = 32
INPUT_PADDING_X  = 10
INPUT_BORDER     = 1

PANEL_PADDING       = 12
PANEL_HEADER_HEIGHT = 32

TOOLBAR_HEIGHT     = 36
TOOLBAR_PADDING_X  = 8
TOOLBAR_ICON_SIZE  = 16

STATUSBAR_HEIGHT = 24

MENU_ITEM_HEIGHT = 24
MENU_PADDING_X   = 12

DIALOG_MIN_WIDTH = 320
DIALOG_PADDING   = 20
DIALOG_BUTTON_GAP = 8

PROGRESSBAR_HEIGHT       = 6
PROGRESSBAR_BORDER_RADIUS = 3

# ---------------------------------------------------------------------------
# Application metadata (used by the splash screen and About dialog)
# ---------------------------------------------------------------------------
APP_NAME         = "PolyFoamMesh"
# Single source of truth: polyfoammesh._version. Re-exported here because
# about_dialog / branding / main_window / api.server all import APP_VERSION
# from design_tokens. (noqa F401: re-export, not an unused import)
from polyfoammesh._version import __version__ as APP_VERSION  # noqa: F401
APP_DESCRIPTION  = "Commercial-grade cartesian mesh generator for OpenFOAM"
APP_VENDOR       = "PolyFoamMesh Project"
APP_COPYRIGHT    = "© 2026 PolyFoamMesh"
APP_DOMAIN       = "polyfoammesh.local"
APP_WEBSITE      = "https://github.com/polyfoammesh"
APP_LICENSE      = "Proprietary — All Rights Reserved"
APP_BUILD_YEAR   = 2026
