from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks import compare_snappy as cs  # noqa: E402
from benchmarks.run_comparison import make_record  # noqa: E402


def _rec(geom, runner, *, non_ortho=20.0, skew=1.0, aspect=10.0, neg=0,
         cells=1000, t=5.0, timed=False, forced_fail=False) -> dict:
    if forced_fail:
        non_ortho = 99.0
    r = make_record(
        geom, runner, success=not forced_fail, wall_time_s=t,
        metrics={"cell_count": cells, "max_non_ortho": non_ortho, "avg_non_ortho": 5.0,
                 "max_skewness": skew, "max_aspect_ratio": aspect, "neg_cells": neg,
                 "min_volume": 1e-5},
        errors=["hard timeout 1500s"] if timed else [],
        warnings=[], timed_out=timed,
    )
    return r


def _sample() -> list[dict]:
    return [
        _rec("cylinder", "hex", non_ortho=30.0),
        _rec("cylinder", "poly", non_ortho=25.0),
        _rec("cylinder", "snappy_base", non_ortho=45.0),
        _rec("cylinder", "snappy_expert", non_ortho=35.0),
        _rec("slot1", "hex", forced_fail=True),
        _rec("slot1", "snappy_base", non_ortho=55.0),
        _rec("slot1", "poly", timed=True),
    ]


def test_index_records_shape() -> None:
    idx = cs.index_records(_sample())
    assert set(idx) == {"cylinder", "slot1"}
    assert set(idx["cylinder"]) == {"hex", "poly", "snappy_base", "snappy_expert"}


def test_pick_winner_prefers_lowest_defect_among_viable() -> None:
    idx = cs.index_records(_sample())
    assert cs.pick_winner(idx["cylinder"]) == "poly"


def test_pick_winner_none_when_nobody_passes_thresholds() -> None:
    records = [
        _rec("x", "hex", forced_fail=True),
        _rec("x", "snappy_base", forced_fail=True),
    ]
    assert cs.pick_winner(cs.index_records(records)["x"]) is None


def test_aggregate_pass_rates_and_exclusive_pass() -> None:
    agg = cs.aggregate(_sample())
    assert agg["pass_rate"]["hex"] == 0.5          # cylinder pass, slot1 fail
    assert agg["pass_rate"]["snappy_base"] == 1.0  # both pass thresholds
    # terrain C: hex fails slot1 while snappy_base passes -> exclusive to snappy
    assert "slot1" in agg["exclusive_pass"]["snappy_base"]


def test_write_leaderboard_emits_markdown(tmp_path) -> None:
    out = tmp_path / "LEADERBOARD.md"
    cs.write_leaderboard(_sample(), out)
    text = out.read_text(encoding="utf-8")
    assert "# PolyFoamMesh vs snappyHexMesh" in text
    assert "cylinder" in text and "slot1" in text
    assert "poly" in text and "snappy_expert" in text
    assert "Pass rate" in text


def test_cli_reads_results_json(tmp_path) -> None:
    p = tmp_path / "comparison_x.json"
    p.write_text(json.dumps(_sample()), encoding="utf-8")
    out = tmp_path / "L.md"
    rc_ = cs.main(["--results", str(p), "--out", str(out)])
    assert rc_ == 0
    assert out.exists()
