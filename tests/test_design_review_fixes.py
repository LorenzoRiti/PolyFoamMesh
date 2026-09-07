"""Tests for the design-review fixes (no Qt/PyVista required)."""
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

from cfmesh_autogui.gui.log_tags import Tag
from cfmesh_autogui.gui.constants import MAX_STEP_FILE_BYTES, MAX_RECENT_STEP_FILES
from cfmesh_autogui.gui.style import (
    COLOR_PASS, COLOR_WARN, status_pill, metric_label,
)
from cfmesh_autogui.gui.quality_panel import _load_thresholds, DEFAULT_THRESHOLDS


def test_log_tags_have_brackets():
    for name in (
        "GEOM", "CASE", "BL", "EXPORT", "MESHING", "SAFEGUARD", "EST",
        "DICT", "ERROR", "WARN", "SUGGESTION", "FIX", "BOUNDARY", "SETUP",
        "DONE", "STALE", "RESET", "CANCELLED", "QUALITY", "CHECKMESH",
        "MANUAL", "PATCH", "SCALE",
    ):
        value = getattr(Tag, name)
        assert value.startswith("["), f"{name} = {value!r} missing leading bracket"
        assert value.endswith("]"), f"{name} = {value!r} missing trailing bracket"
    print("PASS: all log tags wrapped in brackets")


def test_max_step_size_is_500_mb():
    assert MAX_STEP_FILE_BYTES == 500 * 1024 * 1024
    print("PASS: MAX_STEP_FILE_BYTES = 500 MB")


def test_max_recent_files_is_5():
    assert 1 <= MAX_RECENT_STEP_FILES <= 10
    print(f"PASS: MAX_RECENT_STEP_FILES = {MAX_RECENT_STEP_FILES}")


def test_status_pill_includes_color():
    css = status_pill(COLOR_PASS)
    assert COLOR_PASS in css
    assert "border-radius" in css
    print("PASS: status_pill embeds color + border-radius")


def test_metric_label_includes_color_and_size():
    css = metric_label(COLOR_WARN, 11)
    assert COLOR_WARN in css
    assert "11px" in css
    print("PASS: metric_label embeds color + font size")


def test_default_thresholds_have_all_keys():
    needed = {
        "nonortho_warn", "nonortho_fail", "skew_warn", "skew_fail",
        "aspect_warn", "aspect_fail",
    }
    assert needed.issubset(DEFAULT_THRESHOLDS.keys())
    print("PASS: DEFAULT_THRESHOLDS contains all metric keys")


def test_load_thresholds_with_no_settings_returns_defaults():
    """No QSettings → defaults."""
    out = _load_thresholds(s=None)
    for k, v in DEFAULT_THRESHOLDS.items():
        assert out[k] == v, f"{k}: expected {v}, got {out[k]}"
    print("PASS: _load_thresholds returns defaults with no QSettings")

