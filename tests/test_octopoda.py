"""Tests for the local Octopoda runtime."""
from __future__ import annotations

import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfmesh_autogui import octopoda_local
from cfmesh_autogui.octopoda_local import OctopodaRuntime


def test_remember_and_recall():
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = octopoda_local._OCTO_DIR
        try:
            octopoda_local._OCTO_DIR = Path(tmp)
            octo = OctopodaRuntime(app="test_app")
            octo.remember("test_key", {"value": 42})
            result = octo.recall("test_key")
            assert result is not None, "Expected result from recall"
            assert result["value"] == 42, f"Expected 42, got {result['value']}"
        finally:
            octopoda_local._OCTO_DIR = old_dir


def test_recall_nonexistent():
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = octopoda_local._OCTO_DIR
        try:
            octopoda_local._OCTO_DIR = Path(tmp)
            octo = OctopodaRuntime(app="test_app")
            result = octo.recall("nonexistent_key")
            assert result is None, "Expected None for nonexistent key"
        finally:
            octopoda_local._OCTO_DIR = old_dir


def test_log_event():
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = octopoda_local._OCTO_DIR
        try:
            octopoda_local._OCTO_DIR = Path(tmp)
            octo = OctopodaRuntime(app="test_app")
            octo.log_event("test_agent", "test_step", "test details")
            log_file = Path(tmp) / "events.jsonl"
            assert log_file.exists(), "Log file should exist"
            content = log_file.read_text().strip()
            assert len(content) > 0, "Log file should not be empty"
            event = json.loads(content)
            assert event["agent"] == "test_agent"
            assert event["step"] == "test_step"
            assert event["details"] == "test details"
        finally:
            octopoda_local._OCTO_DIR = old_dir


def test_detect_loop_no_events():
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = octopoda_local._OCTO_DIR
        try:
            octopoda_local._OCTO_DIR = Path(tmp)
            octo = OctopodaRuntime(app="test_app")
            assert not octo.detect_loop("agent", "step"), "Expected no loop with empty log"
        finally:
            octopoda_local._OCTO_DIR = old_dir


def test_detect_loop_under_threshold():
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = octopoda_local._OCTO_DIR
        try:
            octopoda_local._OCTO_DIR = Path(tmp)
            octo = OctopodaRuntime(app="test_app")
            octo.log_event("agent", "step", "1")
            octo.log_event("agent", "step", "2")
            assert not octo.detect_loop("agent", "step", max_repeat=3), "Expected no loop below threshold"
        finally:
            octopoda_local._OCTO_DIR = old_dir


def test_detect_loop_above_threshold():
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = octopoda_local._OCTO_DIR
        try:
            octopoda_local._OCTO_DIR = Path(tmp)
            octo = OctopodaRuntime(app="test_app")
            for i in range(4):
                octo.log_event("agent", "step", str(i))
            assert octo.detect_loop("agent", "step", max_repeat=3), "Expected loop detected above threshold"
        finally:
            octopoda_local._OCTO_DIR = old_dir


if __name__ == "__main__":
    test_remember_and_recall()
    test_recall_nonexistent()
    test_log_event()
    test_detect_loop_no_events()
    test_detect_loop_under_threshold()
    test_detect_loop_above_threshold()
    print("ALL PASS")
