"""Tests for the AMR module."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("amr")
AMRParams = _mod.AMRParams
AMRResult = _mod.AMRResult
AMREngine = _mod.AMREngine


def test_amr_params_defaults():
    p = AMRParams()
    assert p.field == "U"
    assert p.gradient_threshold == 0.1
    assert p.max_cells == 2_000_000
    assert p.max_iterations == 5


def test_amr_params_custom():
    p = AMRParams(field="p", gradient_threshold=0.05, max_iterations=10)
    assert p.field == "p"
    assert p.gradient_threshold == 0.05
    assert p.max_iterations == 10


def test_amr_result_defaults():
    r = AMRResult()
    assert not r.success
    assert r.initial_cells == 0
    assert r.final_cells == 0
    assert r.iterations == 0
    assert r.errors == []


def test_amr_engine_init():
    e = AMREngine()
    assert e._params.max_iterations == 5


def test_amr_engine_set_params():
    e = AMREngine()
    p = AMRParams(field="nut", gradient_threshold=0.2)
    e.set_params(p)
    assert e._params.field == "nut"
    assert e._params.gradient_threshold == 0.2


def test_amr_run_no_case():
    e = AMREngine()
    r = e.run()
    assert not r.success
    assert len(r.errors) > 0


def test_parse_refine_count():
    from cfmesh_autogui.commercial.amr import _parse_refine_count
    assert _parse_refine_count("500 cells to refine", "cells to refine") == 500
    assert _parse_refine_count("No matches here", "cells to refine") == 0


def test_export_report():
    import os, json
    e = AMREngine()
    e._result = AMRResult(success=True, initial_cells=1000, final_cells=5000, iterations=3)
    out = Path(os.environ.get("TEMP", "/tmp")) / "test_amr_report.json"
    e.export_report(out)
    data = json.loads(out.read_text())
    assert data["success"]
    assert data["initial_cells"] == 1000
    out.unlink(missing_ok=True)


if __name__ == "__main__":
    import os, json
    test_amr_params_defaults()
    test_amr_params_custom()
    test_amr_result_defaults()
    test_amr_engine_init()
    test_amr_engine_set_params()
    test_amr_run_no_case()
    test_parse_refine_count()
    test_export_report()
    print("ALL PASS")
