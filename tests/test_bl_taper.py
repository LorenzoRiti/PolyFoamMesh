"""W18.1 taper: opt-in layer descent on total BL refusal (keep-best).

Contract under test (all on real meshes, no stubs):
- off (default): historical behaviour, byte-identical outcomes;
- healthy case + taper on: validates at requested N, no taper keys
  (single-level path, keep-best = most layers first);
- hopeless case: SAME failure string with/without taper, taper
  provenance recorded, n_layers restored to requested;
- descent is bounded (taper_max_steps) and validated (0..5).
A real rescue (fail-at-N, validate-at-fewer) is NOT claimed: no
measured corpus case needs it (total refusals here are defect-driven,
which fewer layers cannot fix — declared, not hidden).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polyfoammesh.core import ftetwild_wrapper as ftw  # noqa: E402
from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine  # noqa: E402
from polyfoammesh.core.case_setup import write_control_dict  # noqa: E402
from polyfoammesh.core.mesh_converter import msh_to_of_polymesh  # noqa: E402
from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402


def _box_dual_case(dest: Path) -> Path:
    """Tiny closed-box tet -> dual case (seconds to build)."""
    V, T, _ = ftw.tetrahedralize(
        ROOT / "benchmarks/geometries/box_obstacle.stl", lr=0.1, opt_iter=2)
    V, T = np.asarray(V), np.asarray(T)
    faces, owner = ftw.boundary_faces(V, T)
    faces = ftw.orient_faces_outward(V, T, faces, owner)
    msh = dest / "box.msh"
    ftw.write_msh22(msh, V, T, np.asarray(faces),
                    np.ones(len(faces), dtype=np.int64), ["wall"])
    case = dest / "case"
    if case.exists():
        shutil.rmtree(case)
    msh_to_of_polymesh(msh, case)
    write_control_dict(case)
    dres = TetPolyDualConverter(case, log=lambda m: None).run()
    assert dres.success, dres.errors
    return case


@pytest.fixture(scope="module")
def box_case(tmp_path_factory):
    dest = tmp_path_factory.mktemp("taper_box")
    return _box_dual_case(dest)


def _fresh_copy(box_case: Path, tmp_path: Path) -> Path:
    case = tmp_path / "case"
    if case.exists():
        shutil.rmtree(case)
    shutil.copytree(box_case, case)
    return case


def test_taper_off_by_default_is_historical(box_case, tmp_path):
    """Healthy case, N=5: taper on == taper off (no taper keys)."""
    case = _fresh_copy(box_case, tmp_path)
    base = PolyBoundaryLayerEngine(case, log=lambda m: None).run(
        n_layers=5, first_height=0.01, growth_rate=1.2)
    assert base.success, base.errors
    case2 = _fresh_copy(box_case, tmp_path / "t2")
    tapered = PolyBoundaryLayerEngine(case2, log=lambda m: None).run(
        n_layers=5, first_height=0.01, growth_rate=1.2, taper_layers=True)
    assert tapered.success, tapered.errors
    assert tapered.n_prism_cells == base.n_prism_cells
    assert not any(k.startswith("taper") for k in (tapered.stats or {}))


def test_taper_exhausts_with_same_error_and_provenance(box_case, tmp_path):
    """Hopeless case (10 m layers on a ~1 m box): identical failure
    string, taper trail recorded, n_layers restored to requested."""
    kw_off = dict(n_layers=5, first_height=10.0, growth_rate=1.2)
    r_off = PolyBoundaryLayerEngine(
        _fresh_copy(box_case, tmp_path / "off"), log=lambda m: None).run(**kw_off)
    assert not r_off.success
    r_on = PolyBoundaryLayerEngine(
        _fresh_copy(box_case, tmp_path / "on"), log=lambda m: None).run(
        **kw_off, taper_layers=True, taper_max_steps=2)
    assert not r_on.success
    assert r_on.errors == r_off.errors
    assert r_on.stats.get("taper_requested_layers") == 5
    assert r_on.stats.get("taper_layers") == 3  # bounded: tried 5,4,3
    assert r_on.n_layers == 5  # restored, not left at last attempt
    assert any("taper" in w for w in r_on.warnings)


def test_taper_max_steps_validated(box_case, tmp_path):
    case = _fresh_copy(box_case, tmp_path)
    r = PolyBoundaryLayerEngine(case, log=lambda m: None).run(
        n_layers=5, first_height=0.01, growth_rate=1.2,
        taper_layers=True, taper_max_steps=99)
    assert any("taper_max_steps" in e for e in r.errors)
    r2 = PolyBoundaryLayerEngine(
        _fresh_copy(box_case, tmp_path / "b"), log=lambda m: None).run(
        n_layers=5, first_height=0.01, growth_rate=1.2,
        taper_layers=True, taper_max_steps=-1)
    assert any("taper_max_steps" in e for e in r2.errors)


def test_taper_ignored_under_local_termination(box_case, tmp_path):
    """Taper + local_termination: the local path wins outright and says so
    (no silent combination)."""
    kw = dict(n_layers=3, first_height=0.01, growth_rate=1.2,
              local_termination="decoupled")
    plain = PolyBoundaryLayerEngine(
        _fresh_copy(box_case, tmp_path / "plain"), log=lambda m: None).run(**kw)
    combo = PolyBoundaryLayerEngine(
        _fresh_copy(box_case, tmp_path / "combo"), log=lambda m: None).run(
        **kw, taper_layers=True)
    assert any("taper_layers ignored" in w for w in combo.warnings)
    assert combo.success == plain.success
    assert combo.n_prism_cells == plain.n_prism_cells
    assert "taper_requested_layers" not in (combo.stats or {})
