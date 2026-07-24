"""Tests for the AMR module."""
from __future__ import annotations

import json
import os as _os
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


def test_mark_cells_parses_toposet_now_size(monkeypatch):
    """_mark_cells used to run `refineMesh -dry-run`, a flag that does not
    exist on OpenFOAM's refineMesh (confirmed via `refineMesh -help`) — every
    call silently failed and always returned (0, 0), so the AMR loop reported
    "converged — no cells to refine" regardless of the real mesh. It now
    parses topoSet's own "<name> now size N" log line after creating a real
    cellSet via the fieldToCell source."""
    e = AMREngine()
    e._case_dir = Path(_os.environ.get("TEMP", "/tmp")) / "amr_marktest"
    (e._case_dir / "system").mkdir(parents=True, exist_ok=True)

    import subprocess as _subprocess

    def fake_run(cmd, **kwargs):
        return type("R", (), {
            "returncode": 0,
            "stdout": f"Created cellSet {e._REFINE_SET_NAME}\n"
                      f"    {e._REFINE_SET_NAME} now size 42\n",
            "stderr": "",
        })()

    monkeypatch.setattr(_subprocess, "run", fake_run)
    n_refine, n_unrefine = e._mark_cells("mag(grad(U))")
    assert n_refine == 42
    assert n_unrefine == 0

    topo_dict = (e._case_dir / "system" / "topoSetDict").read_text()
    assert "fieldToCell" in topo_dict
    assert '"mag(grad(U))"' in topo_dict


def test_execute_refinement_writes_word_set_and_uses_overwrite(monkeypatch):
    """refineMeshDict's `set` entry used to be written as an (invalid) dict
    block `set {};` — OpenFOAM's refineMesh reads it as a word naming a
    pre-existing cellSet (`refineDict.get<word>("set")`, confirmed against
    its source). Also confirms -overwrite is passed, without which refineMesh
    writes the refined mesh to a new time directory instead of
    constant/polyMesh."""
    e = AMREngine()
    e._case_dir = Path(_os.environ.get("TEMP", "/tmp")) / "amr_reftest"
    (e._case_dir / "system").mkdir(parents=True, exist_ok=True)

    import subprocess as _subprocess
    captured_cmd = {}

    def fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(_subprocess, "run", fake_run)
    e._execute_refinement()

    ref_dict = (e._case_dir / "system" / "refineMeshDict").read_text()
    assert f"set {e._REFINE_SET_NAME};" in ref_dict
    assert "set {}" not in ref_dict
    assert "-overwrite" in " ".join(captured_cmd["cmd"])


def test_remap_internal_field_uses_cell_map():
    """After refineMesh -overwrite, field files still have the OLD cell
    count — this must remap each new cell to its parent old cell's value
    using refineMesh's own cellMap output, not leave stale data in place."""
    from cfmesh_autogui.commercial.amr import _remap_internal_field

    field_path = Path(_os.environ.get("TEMP", "/tmp")) / "amr_remap_p"
    field_path.write_text(
        "FoamFile { version 2.0; format ascii; class volScalarField; object p; }\n"
        "internalField   nonuniform List<scalar> \n3\n(\n10\n20\n30\n)\n;\n"
        "boundaryField {}\n",
        encoding="ascii",
    )

    # New mesh has 5 cells: 0,1 came from old cell 0; 2 from old cell 1; 3,4 from old cell 2.
    cell_map = [0, 0, 1, 2, 2]
    _remap_internal_field(field_path, cell_map)

    text = field_path.read_text()
    assert "List<scalar>" in text
    m = __import__("re").search(r"List<scalar>\s*\n?\s*(\d+)\s*\n\s*\(([^)]*)\)", text)
    assert m is not None
    assert int(m.group(1)) == 5
    values = [float(v) for v in m.group(2).split()]
    assert values == [10.0, 10.0, 20.0, 30.0, 30.0]
    field_path.unlink(missing_ok=True)


def test_export_report():
    e = AMREngine()
    e._result = AMRResult(success=True, initial_cells=1000, final_cells=5000, iterations=3)
    out = Path(_os.environ.get("TEMP", "/tmp")) / "test_amr_report.json"
    e.export_report(out)
    data = json.loads(out.read_text())
    assert data["success"]
    assert data["initial_cells"] == 1000
    out.unlink(missing_ok=True)


if __name__ == "__main__":
    test_amr_params_defaults()
    test_amr_params_custom()
    test_amr_result_defaults()
    test_amr_engine_init()
    test_amr_engine_set_params()
    test_amr_run_no_case()
    # test_mark_cells_parses_toposet_now_size and
    # test_execute_refinement_writes_word_set_and_uses_overwrite take a
    # pytest `monkeypatch` fixture — run via pytest, not this __main__ block.
    test_remap_internal_field_uses_cell_map()
    test_export_report()
    print("ALL PASS")
