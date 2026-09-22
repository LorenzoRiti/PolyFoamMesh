"""Parent orchestration: timeout record, exit-code gate — subprocess mocked."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks import run_comparison as rc  # noqa: E402


def _good_record(geometry: str, runner: str) -> dict:
    return rc.make_record(
        geometry, runner, success=True, wall_time_s=1.0,
        metrics={"cell_count": 10, "max_non_ortho": 10.0, "avg_non_ortho": 5.0,
                 "max_skewness": 0.5, "max_aspect_ratio": 2.0, "neg_cells": 0,
                 "min_volume": 1e-4},
        errors=[], warnings=[],
    )


def _bad_record(geometry: str, runner: str) -> dict:
    return rc.make_record(
        geometry, runner, success=True, wall_time_s=1.0,
        metrics={"cell_count": 10, "max_non_ortho": 99.0, "avg_non_ortho": 5.0,
                 "max_skewness": 0.5, "max_aspect_ratio": 2.0, "neg_cells": 0,
                 "min_volume": 1e-4},
        errors=[], warnings=[],
    )


def test_parent_timeout_produces_timed_out_record(tmp_path, monkeypatch) -> None:
    import subprocess as sp

    def raising_run(cmd, timeout=None, **kw):
        raise sp.TimeoutExpired(cmd=cmd, timeout=float(timeout or 0))

    monkeypatch.setattr(rc.subprocess, "run", raising_run)
    monkeypatch.setattr(rc, "_kill_tree", lambda pid: None)

    records, exit_code = rc.run_parent(
        tier="fast", runners=["hex"], geometries=["cylinder"],
        _child_out_dir=tmp_path, _write_results=False,
    )
    assert len(records) == 1
    rec = records[0]
    assert rec["timed_out"] is True
    assert rec["thresholds_pass"] is False
    assert exit_code == 1


def test_exit_code_is_zero_when_our_runners_all_pass(tmp_path, monkeypatch) -> None:
    def fake_run(cmd, timeout=None, **kw):
        out = Path(cmd[cmd.index("--out") + 1])
        geom = cmd[cmd.index("--geometry") + 1]
        runner = cmd[cmd.index("--runner") + 1]
        out.write_text(json.dumps(_good_record(geom, runner)), encoding="utf-8")

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    records, exit_code = rc.run_parent(
        tier="fast", runners=["hex", "poly"], geometries=["cylinder"],
        _child_out_dir=tmp_path, _write_results=False,
    )
    assert len(records) == 2
    assert exit_code == 0


def test_exit_code_is_one_when_an_our_runner_fails_thresholds(tmp_path, monkeypatch) -> None:
    def fake_run(cmd, timeout=None, **kw):
        out = Path(cmd[cmd.index("--out") + 1])
        geom = cmd[cmd.index("--geometry") + 1]
        runner = cmd[cmd.index("--runner") + 1]
        bad = runner == "poly"
        rec = _bad_record(geom, runner) if bad else _good_record(geom, runner)
        out.write_text(json.dumps(rec), encoding="utf-8")

        class R:
            returncode = 1 if bad else 0
        return R()

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    _, exit_code = rc.run_parent(
        tier="fast", runners=["hex", "poly"], geometries=["cylinder"],
        _child_out_dir=tmp_path, _write_results=False,
    )
    assert exit_code == 1


def test_snappy_runner_is_available_in_cli() -> None:
    opts = rc._build_parser().parse_args(
        ["--tier", "full", "--runner", "snappy_base", "--geometry", "slot1"]
    )
    assert opts.tier == "full"
    assert opts.runner == ["snappy_base"]
    assert opts.geometry == ["slot1"]


def test_child_mode_accepts_snappy_runners() -> None:
    opts = rc._build_parser().parse_args(
        ["--single", "--runner", "snappy_expert", "--geometry", "venturi", "--out", "x.json"]
    )
    assert opts.runner == ["snappy_expert"]
