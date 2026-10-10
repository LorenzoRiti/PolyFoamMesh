"""Tests for the Phase-0 performance instrumentation.

Covers the POLYFOAMMESH_PROFILE environment switch, the cProfile wrapper
(no-op when unset, never crashes the run when profiling fails), and the
``[perf] stage times:`` trace formatter.  These are pure-Python helpers; no
mesh or GUI is involved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from polyfoammesh.core import openfoam_runner as runner


@pytest.fixture(autouse=True)
def _clear_profile_env(monkeypatch):
    monkeypatch.delenv(runner.PROFILE_ENV_VAR, raising=False)
    # Isolate the process-wide trace between tests.
    runner._PERF_TRACE.clear()
    runner._PERF_META.clear()
    yield
    runner._PERF_TRACE.clear()
    runner._PERF_META.clear()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ("0", False),
        ("false", False),
        ("off", False),
        ("no", False),
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("Yes", True),
        (" on ", True),
    ],
)
def test_profile_enabled_parses_env(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(runner.PROFILE_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(runner.PROFILE_ENV_VAR, value)
    assert runner._profile_enabled() is expected


def test_run_with_profile_is_noop_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv(runner.PROFILE_ENV_VAR, raising=False)
    log = []
    calls = []

    def fn():
        calls.append(1)
        return "result"

    out = runner._run_with_profile(tmp_path, "dual", fn, log.append)

    assert out == "result"
    assert len(calls) == 1
    assert log == []
    assert list(Path(tmp_path).iterdir()) == []


def test_run_with_profile_dumps_and_logs_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv(runner.PROFILE_ENV_VAR, "1")
    log = []
    calls = []

    def fn():
        calls.append(1)
        # A little work so there is at least one non-trivial frame.
        return sum(i * i for i in range(20_000))

    out = runner._run_with_profile(tmp_path, "dual", fn, log.append)

    assert out == sum(i * i for i in range(20_000))
    assert len(calls) == 1
    assert (Path(tmp_path) / "profile_dual.prof").is_file()
    assert any("profile saved" in line for line in log)
    assert any("hot:" in line and "cumtime=" in line for line in log)
    # Never more than the caps the spec asks for.
    assert len(log) <= 1 + 20


def test_run_with_profile_swallows_dump_failure(tmp_path, monkeypatch):
    """A dump failure must not propagate; fn still runs exactly once."""
    monkeypatch.setenv(runner.PROFILE_ENV_VAR, "1")
    log = []
    calls = []
    # A regular file used as the "case dir" makes dump_stats fail.
    bad_dir = tmp_path / "not_a_dir"
    bad_dir.write_text("x")

    def fn():
        calls.append(1)
        return 7

    out = runner._run_with_profile(bad_dir, "dual", fn, log.append)

    assert out == 7
    assert len(calls) == 1
    assert any("failed" in line for line in log)


def test_perf_format_orders_and_labels(tmp_path):
    runner._perf_record(tmp_path, "gmsh", 12.3)
    runner._perf_note(tmp_path, "gmsh_mode", "robust")
    runner._perf_record(tmp_path, "convert", 4.0)
    runner._perf_record(tmp_path, "dual", 9.1)
    runner._perf_record(tmp_path, "bl_pre", 21.2)
    runner._perf_record(tmp_path, "total", 55.5)

    line = runner._perf_format(tmp_path)

    assert line is not None
    assert line.startswith("[perf] stage times: ")
    assert "GMSH 12.3s (robust)" in line
    assert "convert 4.0s" in line
    assert "dual 9.1s" in line
    assert "BL precompute 21.2s" in line
    assert "total 55.5s" in line
    # Documented stage order is preserved.
    assert line.index("GMSH") < line.index("convert") < line.index("dual")


def test_perf_format_empty_is_none(tmp_path):
    assert runner._perf_format(tmp_path) is None


def test_perf_log_emits_only_when_trace_exists(tmp_path):
    log = []
    runner._perf_log(tmp_path, log.append)
    assert log == []
    runner._perf_record(tmp_path, "dual", 1.0)
    runner._perf_log(tmp_path, log.append)
    assert log == ["[perf] stage times: dual 1.0s"]


def test_perf_reset_clears_case(tmp_path):
    runner._perf_record(tmp_path, "dual", 1.0)
    runner._perf_note(tmp_path, "gmsh_mode", "normal")
    runner._perf_reset(tmp_path)
    assert runner._perf_format(tmp_path) is None
