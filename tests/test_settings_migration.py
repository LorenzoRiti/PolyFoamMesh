"""Tests for the settings migration module."""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import _load_module_from_file, _stub_pkg

# Stub the gui and core packages (saved and restored so later test modules
# can still import the real polyfoammesh.gui / .core packages instead of
# an empty stub).
_saved_gui = sys.modules.get("polyfoammesh.gui")
_saved_core = sys.modules.get("polyfoammesh.core")
_saved_tokens = sys.modules.get("polyfoammesh.gui.design_tokens")
_stub_pkg("polyfoammesh.gui")
tokens = types.ModuleType("polyfoammesh.gui.design_tokens")
tokens.APP_NAME = "PolyFoamMesh"
tokens.APP_VERSION = "2.0.1"
sys.modules["polyfoammesh.gui.design_tokens"] = tokens

# Also stub core (so validation import doesn't trigger core/__init__)
_stub_pkg("polyfoammesh.core")
try:
    val_mod = _load_module_from_file(
        "polyfoammesh.core.validation",
        Path(__file__).resolve().parents[1] / "src" / "polyfoammesh" / "core" / "validation.py",
        "polyfoammesh.core",
    )

    # Load settings_migration module directly
    src = Path(__file__).resolve().parents[1] / "src" / "polyfoammesh" / "gui" / "settings_migration.py"
    _mod = _load_module_from_file("polyfoammesh.gui.settings_migration", src, "polyfoammesh.gui")
finally:
    if _saved_gui is not None:
        sys.modules["polyfoammesh.gui"] = _saved_gui
    else:
        sys.modules.pop("polyfoammesh.gui", None)
    if _saved_core is not None:
        sys.modules["polyfoammesh.core"] = _saved_core
    else:
        sys.modules.pop("polyfoammesh.core", None)
    # Restore the real design_tokens module — the stub above only carries
    # APP_NAME/APP_VERSION, so leaving it cached breaks every later test
    # module that imports real tokens (e.g. ORANGE_400).
    if _saved_tokens is not None:
        sys.modules["polyfoammesh.gui.design_tokens"] = _saved_tokens
    else:
        sys.modules.pop("polyfoammesh.gui.design_tokens", None)

# Simple in-memory QSettings replacement
class _FakeSettings:
    def __init__(self, *a, **kw):
        self._data = {}
    def value(self, key, default=None):
        return self._data.get(key, default)
    def setValue(self, key, val):
        self._data[key] = val
    def contains(self, key):
        return key in self._data
    def remove(self, key):
        self._data.pop(key, None)
    def allKeys(self):
        return list(self._data.keys())
    def sync(self):
        pass

_mod.QSettings = _FakeSettings
_mod.QByteArray = str

AppSettings = _mod.AppSettings
_parse_version = _mod._parse_version


def test_parse_version():
    assert _parse_version("2.0.1") == (2, 0, 1)
    assert _parse_version("1.0") == (1, 0)
    assert _parse_version("0.0.0") == (0, 0, 0)
    assert _parse_version("invalid") == (0,)


def test_app_settings_rejects_invalid_theme():
    s = AppSettings()
    s.set_value("ui/theme_mode", "neon")
    stored = s.get_value("ui/theme_mode", "system")
    assert stored == "system"


def test_app_settings_accepts_valid_theme():
    s = AppSettings()
    s.set_value("ui/theme_mode", "dark")
    stored = s.get_value("ui/theme_mode", "system")
    assert stored == "dark"


def test_app_settings_rejects_bad_mesh_params():
    s = AppSettings()
    s.set_value("params/detail_slider", 99)
    stored = s.get_value("params/detail_slider", 10)
    assert stored == 10 or stored != 99


def test_app_settings_accepts_valid_mesh_params():
    s = AppSettings()
    s.set_value("params/detail_slider", 10)
    stored = s.get_value("params/detail_slider", 10)
    assert stored == 10


def test_app_settings_accepts_valid_unit():
    s = AppSettings()
    s.set_value("params/unit", "mm")
    stored = s.get_value("params/unit", "m")
    assert stored == "mm"


def test_app_settings_rejects_invalid_unit():
    s = AppSettings()
    s.set_value("params/unit", "km")
    stored = s.get_value("params/unit", "m")
    assert stored == "m"


def test_purge_stale_keys():
    s = AppSettings()
    s.set_value("params/detail_slider", 10)
    s.set_value("stale_key", "should_be_removed")
    purged = s.purge_stale_keys()
    assert purged >= 1
    assert not s.contains("stale_key")


if __name__ == "__main__":
    test_parse_version()
    test_app_settings_rejects_invalid_theme()
    test_app_settings_accepts_valid_theme()
    test_app_settings_rejects_bad_mesh_params()
    test_app_settings_accepts_valid_mesh_params()
    test_app_settings_accepts_valid_unit()
    test_app_settings_rejects_invalid_unit()
    test_purge_stale_keys()
    print("ALL PASS")