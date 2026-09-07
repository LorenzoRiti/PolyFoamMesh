"""Tests for the session persistence module."""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Direct import without triggering cadquery dependency
src = Path(__file__).resolve().parents[1] / "src" / "polyfoammesh" / "core" / "session.py"
code = src.read_text(encoding="utf-8")

_sess_mod = types.ModuleType("session")
_sess_mod.__file__ = str(src)
_sess_mod.__package__ = "polyfoammesh.core"
sys.modules["session"] = _sess_mod

exec(compile(code, str(src), "exec"), _sess_mod.__dict__)

SessionSnapshot = _sess_mod.SessionSnapshot
save_snapshot = _sess_mod.save_snapshot
load_snapshot = _sess_mod.load_snapshot
clear_snapshot = _sess_mod.clear_snapshot
has_snapshot = _sess_mod.has_snapshot


def _with_temp_dir(fn):
    old_dir = _sess_mod._SESSION_DIR
    old_file = _sess_mod._SESSION_FILE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _sess_mod._SESSION_DIR = tmp_path
            _sess_mod._SESSION_FILE = tmp_path / "last_session.json"
            fn(tmp_path)
    finally:
        _sess_mod._SESSION_DIR = old_dir
        _sess_mod._SESSION_FILE = old_file


def test_save_and_load():
    def check(tmp):
        snap = SessionSnapshot(
            version="2.0.1",
            geometry_path="C:\\test\\model.step",
            max_cell=0.05,
            min_cell=0.01,
        )
        save_snapshot(snap)
        assert (tmp / "last_session.json").exists()
        loaded = load_snapshot()
        assert loaded is not None
        assert loaded.version == "2.0.1"
        assert loaded.geometry_path == "C:\\test\\model.step"
        assert loaded.max_cell == 0.05
    _with_temp_dir(check)


def test_no_snapshot():
    def check(tmp):
        assert not has_snapshot()
        loaded = load_snapshot()
        assert loaded is None
    _with_temp_dir(check)


def test_clear_snapshot():
    def check(tmp):
        snap = SessionSnapshot()
        save_snapshot(snap)
        assert has_snapshot()
        clear_snapshot()
        assert not has_snapshot()
    _with_temp_dir(check)


def test_session_defaults():
    snap = SessionSnapshot()
    assert snap.max_cell == 0.05
    assert snap.min_cell == 0.01
    assert snap.detail == "medium"
    assert snap.unit == "m"


if __name__ == "__main__":
    test_session_defaults()
    test_save_and_load()
    test_no_snapshot()
    test_clear_snapshot()
    print("ALL PASS")
