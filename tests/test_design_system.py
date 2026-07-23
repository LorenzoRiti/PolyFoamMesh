"""Tests for the CFMesh-AutoGUI design system.

Covers: token module integrity, theme manager, branding assets (logo/splash),
QSS file presence, hardcoded-color audit in widget code.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import os as _os
try:
    import OCP as _ocp
    _d = _os.path.dirname(_ocp.__file__)
    if _d not in _os.environ.get("PATH", ""):
        _os.environ["PATH"] = _d + _os.pathsep + _os.environ.get("PATH", "")
except Exception:
    pass

# IMPORTANT: build a QApplication BEFORE importing any widget code, so
# QPainter / QPixmap can be constructed in tests.
from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from cfmesh_autogui.gui import design_tokens
from cfmesh_autogui.gui import style as legacy_style
from cfmesh_autogui.gui import theme
from cfmesh_autogui.gui import branding
from cfmesh_autogui.gui import about_dialog


# ---------------------------------------------------------------------------
# Token module integrity
# ---------------------------------------------------------------------------
def test_primary_brand_color_is_engineer_blue():
    assert design_tokens.PRIMARY_600 == "#2563eb"
    print("PASS: primary brand is engineer blue #2563eb")


def test_secondary_is_violet():
    assert design_tokens.SECONDARY_600 == "#7c3aed"
    print("PASS: secondary is violet #7c3aed")


def test_semantic_palette_complete():
    for c in (
        design_tokens.SUCCESS, design_tokens.WARNING,
        design_tokens.ERROR, design_tokens.INFO,
    ):
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", c)
    print("PASS: semantic palette all valid hex")


def test_font_stack_present():
    assert "Segoe UI" in design_tokens.FONT_SANS
    assert "Cascadia Code" in design_tokens.FONT_MONO
    print("PASS: system font stack present")


def test_spacing_grid_4px_base():
    for s in (
        design_tokens.SPACE_1, design_tokens.SPACE_2, design_tokens.SPACE_3,
        design_tokens.SPACE_4, design_tokens.SPACE_5, design_tokens.SPACE_6,
    ):
        assert s % 4 == 0, f"spacing token {s} is not a multiple of 4"
    print("PASS: spacing grid is 4px-based")


def test_light_and_dark_palettes_have_same_keys():
    assert set(design_tokens.LIGHT.keys()) == set(design_tokens.DARK.keys())
    print("PASS: light and dark palettes have identical key sets")


def test_app_metadata_present():
    assert design_tokens.APP_NAME == "CFMesh-AutoGUI"
    assert design_tokens.APP_VERSION  # non-empty
    assert design_tokens.APP_LICENSE
    print("PASS: app metadata complete")


# ---------------------------------------------------------------------------
# Legacy style.py facade
# ---------------------------------------------------------------------------
def test_legacy_style_delegates_to_design_tokens():
    assert legacy_style.COLOR_PASS == design_tokens.SUCCESS
    assert legacy_style.COLOR_FAIL == design_tokens.ERROR
    assert legacy_style.COLOR_ACCENT == design_tokens.PRIMARY_600
    print("PASS: legacy style.py delegates to design_tokens")


def test_status_pill_qss():
    css = legacy_style.status_pill("#16a34a")
    assert "#16a34a" in css
    assert "border-radius" in css
    assert "font-weight" in css
    print("PASS: status_pill() QSS template intact")


def test_metric_label_qss():
    css = legacy_style.metric_label("#888", 12)
    assert "#888" in css
    assert "12px" in css
    print("PASS: metric_label() QSS template intact")


# ---------------------------------------------------------------------------
# Theme manager
# ---------------------------------------------------------------------------
def test_theme_manager_rejects_bad_mode():
    mgr = theme.ThemeManager()
    try:
        mgr.request("neon")
        assert False, "Should have raised"
    except ValueError:
        pass
    print("PASS: ThemeManager rejects unknown modes")


def test_theme_manager_accepts_valid_modes():
    mgr = theme.ThemeManager()
    mgr.request("light")
    assert mgr._requested_mode == "light"
    mgr.request("dark")
    assert mgr._requested_mode == "dark"
    mgr.request("system")
    assert mgr._requested_mode == "system"
    print("PASS: ThemeManager accepts light/dark/system")


# ---------------------------------------------------------------------------
# Branding assets
# ---------------------------------------------------------------------------
def test_logo_pixmap_size():
    pix = branding.make_logo_pixmap(96)
    assert pix.width() == 96
    assert pix.height() == 96
    assert not pix.isNull()
    print("PASS: make_logo_pixmap(96) returns 96x96 non-null pixmap")


def test_logo_pixmap_variants():
    for size in (16, 32, 64, 128, 256):
        pix = branding.make_logo_pixmap(size)
        assert pix.width() == size
    print("PASS: logo pixmap works for all standard sizes")


def test_splash_pixmap():
    pix = branding.make_splash_pixmap(480, 240)
    assert pix.width() == 480
    assert pix.height() == 240
    assert not pix.isNull()
    print("PASS: make_splash_pixmap returns 480x240 non-null pixmap")


def test_splash_pixmap_dark_variant():
    pix_light = branding.make_splash_pixmap(480, 240, dark=False)
    pix_dark = branding.make_splash_pixmap(480, 240, dark=True)
    # They should differ (different backgrounds). Compare pixel by pixel
    # at the center to make sure the dark variant is genuinely different.
    from PySide6.QtGui import QImage
    img_light = pix_light.toImage()
    img_dark = pix_dark.toImage()
    px_light = img_light.pixelColor(240, 120)
    px_dark = img_dark.pixelColor(240, 120)
    assert px_light != px_dark, "Light and dark splash should differ"
    print("PASS: light and dark splash variants are visually different")


def test_app_icon():
    icon = branding.make_app_icon()
    sizes = [s.width() for s in icon.availableSizes()]
    assert 16 in sizes and 32 in sizes and 256 in sizes
    print(f"PASS: app icon has multi-resolution: {sizes}")


# ---------------------------------------------------------------------------
# About dialog
# ---------------------------------------------------------------------------
def test_about_dialog_logo_pixmap():
    pix = about_dialog._make_logo(96)
    assert pix.width() == 96
    assert not pix.isNull()
    print("PASS: About dialog logo generator works")


# ---------------------------------------------------------------------------
# QSS files exist and are non-empty
# ---------------------------------------------------------------------------
def test_qss_files_present():
    qss_dir = Path(design_tokens.__file__).resolve().parents[3] / ".ui-design" / "qss"
    light = qss_dir / "light.qss"
    dark = qss_dir / "dark.qss"
    assert light.is_file(), f"Missing {light}"
    assert dark.is_file(), f"Missing {dark}"
    assert light.stat().st_size > 1000
    assert dark.stat().st_size > 1000
    print(f"PASS: QSS files present (light={light.stat().st_size} B, dark={dark.stat().st_size} B)")


def test_qss_files_cover_common_widgets():
    qss_dir = Path(design_tokens.__file__).resolve().parents[3] / ".ui-design" / "qss"
    for mode in ("light", "dark"):
        text = (qss_dir / f"{mode}.qss").read_text(encoding="utf-8")
        for w in ("QPushButton", "QLineEdit", "QSpinBox", "QComboBox",
                  "QCheckBox", "QGroupBox", "QListWidget", "QMenuBar",
                  "QStatusBar", "QProgressBar", "QToolTip", "QScrollBar",
                  "QSplitter"):
            assert w in text, f"{mode}.qss missing selector for {w}"
    print("PASS: both QSS files cover all common widget selectors")


def test_qss_status_pills_match_design_tokens():
    """The status role colors in QSS must match the Python semantic tokens."""
    qss_dir = Path(design_tokens.__file__).resolve().parents[3] / ".ui-design" / "qss"
    light = (qss_dir / "light.qss").read_text(encoding="utf-8")
    dark = (qss_dir / "dark.qss").read_text(encoding="utf-8")
    assert design_tokens.SUCCESS in light or design_tokens.SUCCESS.lower() in light
    assert design_tokens.WARNING in light or design_tokens.WARNING.lower() in light
    assert design_tokens.ERROR in light or design_tokens.ERROR.lower() in light
    assert design_tokens.SUCCESS_LIGHT in dark or design_tokens.SUCCESS_LIGHT.lower() in dark
    print("PASS: QSS status-pill colors match design_tokens semantic palette")


# ---------------------------------------------------------------------------
# design-system.json integrity
# ---------------------------------------------------------------------------
def test_design_system_json_valid():
    json_path = (
        Path(design_tokens.__file__).resolve().parents[3]
        / ".ui-design" / "design-system.json"
    )
    assert json_path.is_file()
    with json_path.open(encoding="utf-8") as f:
        ds = json.load(f)
    assert ds["name"] == "CFMesh-AutoGUI Design System"
    assert ds["version"] == "1.0.0"
    assert "light" in ds["semantic"] and "dark" in ds["semantic"]
    assert "primary" in ds["tokens"]["color"]
    assert "spacing" in ds["tokens"]
    assert "component" in ds["tokens"]
    print("PASS: design-system.json structure is valid")


def test_design_system_json_primary_matches_tokens():
    json_path = (
        Path(design_tokens.__file__).resolve().parents[3]
        / ".ui-design" / "design-system.json"
    )
    with json_path.open(encoding="utf-8") as f:
        ds = json.load(f)
    assert ds["tokens"]["color"]["primary"]["600"] == design_tokens.PRIMARY_600
    assert ds["tokens"]["color"]["primary"]["500"] == design_tokens.PRIMARY_500
    print("PASS: design-system.json primary palette matches design_tokens")


# ---------------------------------------------------------------------------
# Hardcoded color audit
# ---------------------------------------------------------------------------
def test_no_hardcoded_hex_in_main_window_widgets():
    """Widget code under gui/ must not contain raw hex colors — use design_tokens."""
    gui_dir = Path(design_tokens.__file__).resolve().parent
    offenders: list[str] = []
    hex_re = re.compile(r"#[0-9a-fA-F]{3,6}\b")
    # Skip files that legitimately contain hex (icons, palettes, design tokens,
    # branding, theme, about_dialog, constants, log_tags).
    ALLOWLIST = {
        "design_tokens.py",
        "theme.py",
        "branding.py",
        "about_dialog.py",
        "style.py",
            "constants.py",
            "log_tags.py",
            "pdf_report.py",  # reportlab PDF colors, not Qt widget display
        }
    for py in gui_dir.glob("*.py"):
        if py.name in ALLOWLIST:
            continue
        text = py.read_text(encoding="utf-8")
        for m in hex_re.finditer(text):
            # Allow #fff/#000 in QSS attribute values? No — those are still
            # tokens, should be moved to design_tokens.
            offenders.append(f"{py.name}: '{m.group(0)}'")
    assert not offenders, (
        f"Hardcoded hex colors found in widget code (move to design_tokens.py): "
        + "\n".join(offenders[:10])
    )
    print("PASS: no hardcoded hex colors in widget code")


# ---------------------------------------------------------------------------
# End-to-end: tokens reach theme
# ---------------------------------------------------------------------------
def test_light_dark_palettes_non_empty():
    assert design_tokens.LIGHT["accent"] == design_tokens.PRIMARY_600
    assert design_tokens.DARK["accent"] == design_tokens.PRIMARY_500
    print("PASS: light and dark accent tokens differ as expected")


def test_embedded_qss_fallbacks_present():
    """The theme module's embedded QSS contains the role pills (fallback)."""
    src = Path(theme.__file__).read_text(encoding="utf-8")
    for role in ("status-pass", "status-warn", "status-fail", "status-neutral"):
        assert role in src
    print("PASS: embedded QSS fallback contains all 4 status roles")

