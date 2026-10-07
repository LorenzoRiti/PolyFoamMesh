"""Platform-specific app-data path resolution (_paths).

Windows keeps ``%APPDATA%\\polyfoammesh`` exactly; POSIX follows the XDG
Base Directory spec. Tests patch ``sys.platform`` so both branches run on
either OS.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh import _paths  # noqa: E402


def test_app_data_dir_windows_uses_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(_paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert _paths.app_data_dir() == tmp_path / "polyfoammesh"


def test_app_data_dir_posix_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.setattr(_paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert _paths.app_data_dir() == tmp_path / "polyfoammesh"


def test_app_data_dir_posix_default_fallback(monkeypatch):
    monkeypatch.setattr(_paths.sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    expected = (
        Path(os.path.expanduser("~")) / ".local" / "share" / "polyfoammesh"
    )
    assert _paths.app_data_dir() == expected


def test_migrate_legacy_app_data_windows(monkeypatch, tmp_path):
    """Legacy %APPDATA%\\cfmesh-autogui is copied into the new dir."""
    monkeypatch.setattr(_paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    legacy = tmp_path / "cfmesh-autogui"
    legacy.mkdir()
    (legacy / "marker.txt").write_text("x", encoding="utf-8")
    new = _paths.migrate_legacy_app_data()
    assert new == tmp_path / "polyfoammesh"
    assert (new / "marker.txt").read_text(encoding="utf-8") == "x"
