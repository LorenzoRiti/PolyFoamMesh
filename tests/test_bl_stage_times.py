"""Stage-timing instrumentation for the poly BL engine (`core/bl_poly.py`).

Zero-behaviour-change feature: `PolyBoundaryLayerEngine.run` must stash the
precompute / build / total wall times in `result.stats` and log exactly one
`[bl] stage times:` line.  These tests pin that contract without touching any
mesh golden.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from polyfoammesh.core.bl_poly import (  # noqa: E402
    PolyBoundaryLayerEngine,
    PolyBoundaryLayerResult,
)
from _test_helpers import space_free_tmp_root  # noqa: E402

WORK_ROOT = space_free_tmp_root() / "cfmesh_bench" / "bl_stage_times_test"


def _cleanup() -> None:
    shutil.rmtree(WORK_ROOT, ignore_errors=True)


def test_finalize_stage_times_stores_and_logs():
    logs: list[str] = []
    eng = PolyBoundaryLayerEngine(WORK_ROOT / "unused", log=logs.append)
    eng._stage_pre_s = 1.3
    eng._stage_build_s = 2.6
    res = PolyBoundaryLayerResult()
    eng._finalize_stage_times(res, time.perf_counter())
    assert res.stats["stage_precompute_s"] == 1.3
    assert res.stats["stage_build_s"] == 2.6
    assert res.stats["stage_total_s"] >= 0.0
    lines = [m for m in logs if "[bl] stage times:" in m]
    assert len(lines) == 1, logs
    assert "precompute 1.3s" in lines[0]
    assert "build 2.6s" in lines[0]
    assert "total" in lines[0]


def test_run_populates_stage_times_on_real_dual():
    from test_bl_poly import _build_dual_case  # tiny in-memory cube dual

    _cleanup()
    try:
        case = _build_dual_case(WORK_ROOT / "cube")
        logs: list[str] = []
        eng = PolyBoundaryLayerEngine(case, log=logs.append)
        r = eng.run(n_layers=3, first_height=0.02, growth_rate=1.2,
                    apply_to_all=True)
        assert r.success, r.errors
        for key in ("stage_precompute_s", "stage_build_s", "stage_total_s"):
            assert key in r.stats, r.stats
            assert r.stats[key] >= 0.0
        assert any("[bl] stage times:" in m for m in logs), logs[-5:]
    finally:
        _cleanup()
