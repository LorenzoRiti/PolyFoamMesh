"""Tests for the journal/macro recorder module."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("journal")
JournalEntry = _mod.JournalEntry
Journal = _mod.Journal
JournalPlayer = _mod.JournalPlayer


def test_journal_entry_defaults():
    e = JournalEntry()
    assert e.action == ""
    assert e.params == {}
    assert e.duration_s == 0.0


def test_journal_entry_with_data():
    e = JournalEntry(action="set_cell_sizes", params={"max_cell": 0.05}, duration_s=1.2)
    assert e.action == "set_cell_sizes"
    assert e.params["max_cell"] == 0.05
    assert e.duration_s == 1.2


def test_journal_entry_to_dict():
    e = JournalEntry(action="test", params={"key": "val"})
    d = e.to_dict()
    assert d["action"] == "test"
    assert d["params"]["key"] == "val"


def test_journal_init():
    j = Journal()
    assert j.entry_count == 0
    assert not j.is_recording


def test_journal_record():
    j = Journal()
    e = j.record("test_action", param1=42)
    assert e.action == "test_action"
    assert e.params["param1"] == 42
    assert j.entry_count == 0, "Not recording yet — should not add to entries"


def test_journal_record_while_recording():
    j = Journal()
    j.start_recording()
    j.record("action1", value=100)
    j.record("action2", value=200)
    assert j.entry_count == 2
    assert j._entries[0].action == "action1"
    assert j._entries[1].params["value"] == 200


def test_journal_start_stop():
    j = Journal()
    j.start_recording()
    assert j.is_recording
    j.record("a")
    entries = j.stop_recording()
    assert len(entries) == 1
    assert not j.is_recording


def test_journal_context_manager():
    j = Journal()
    with j:
        assert j.is_recording
        j.record("ctx_action")
    assert not j.is_recording
    assert j.entry_count == 1


def test_journal_save_load():
    import os as _os
    j = Journal()
    j.start_recording()
    j.record("set_cell_sizes", max_cell=0.05, min_cell=0.01)
    j.record("run_mesh")
    j.stop_recording()

    tmp = Path(_os.environ.get("TEMP", "/tmp")) / "test_journal.json"
    j.save(tmp)
    assert tmp.exists()

    loaded = Journal.load(tmp)
    assert loaded.entry_count == 2
    assert loaded._entries[0].action == "set_cell_sizes"
    assert loaded._entries[0].params["max_cell"] == 0.05
    tmp.unlink(missing_ok=True)


def test_journal_to_dict():
    j = Journal()
    j.start_recording()
    j.record("a")
    d = j.to_dict()
    assert d["version"] == "1.0"
    assert d["entry_count"] == 1


def test_to_python_script():
    j = Journal()
    j.start_recording()
    j.record("set_cell_sizes", max_cell=0.05, min_cell=0.01)
    j.record("set_boundary_layers", n_layers=5)
    j.record("run_mesh")
    j.stop_recording()

    script = j.to_python_script(target_geometry="model.step")
    assert "WatertightWorkflow" in script
    assert "model.step" in script
    assert "set_cell_sizes" in script
    assert "wf.run()" in script


def test_to_python_script_no_geometry():
    j = Journal()
    j.start_recording()
    j.record("set_cell_sizes", max_cell=0.02)
    j.stop_recording()

    script = j.to_python_script()
    assert "set_cell_sizes" in script
    # Should still work without target geometry


def test_journal_player_init():
    j = Journal()
    player = JournalPlayer(j)
    assert player._journal is j


def test_journal_player_play_no_geometry():
    j = Journal()
    j.start_recording()
    j.record("set_cell_sizes", max_cell=0.05)
    j.record("run_mesh")
    j.stop_recording()

    player = JournalPlayer(j)
    result = player.play("Z:\\nonexistent.step")
    assert result is not None
    assert not result.success  # Should fail gracefully


if __name__ == "__main__":
    test_journal_entry_defaults()
    test_journal_entry_with_data()
    test_journal_entry_to_dict()
    test_journal_init()
    test_journal_record()
    test_journal_record_while_recording()
    test_journal_start_stop()
    test_journal_context_manager()
    test_journal_save_load()
    test_journal_to_dict()
    test_to_python_script()
    test_to_python_script_no_geometry()
    test_journal_player_init()
    test_journal_player_play_no_geometry()
    print("ALL PASS")
