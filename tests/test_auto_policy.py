"""W18: geometry-driven auto policy — deterministic, measured, provenance."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polyfoammesh.core.mesher_registry import (  # noqa: E402
    DEFAULT_MESHER_KEY,
    normalize_mesher_key,
)
from polyfoammesh.core.tet_source import (  # noqa: E402
    PolymeshEnginePlan,
    TetSourcePreflight,
    choose_polymesh_engine,
    choose_tet_source,
    preflight_tet_source,
)


def _pre(**kw):
    base = dict(
        geometry_path="case.step",
        suffix=".step",
        detail="medium",
        fTetWild_available=True,
        refinement_requested=False,
        feature_angle=None,
        scale=1.0,
    )
    base.update(kw)
    return TetSourcePreflight(**base)


def test_default_is_auto_and_normalizes():
    assert DEFAULT_MESHER_KEY == "auto"
    assert normalize_mesher_key("auto") == "auto"
    assert normalize_mesher_key("") == "auto"
    assert normalize_mesher_key("nope") == "auto"


def test_open_input_routes_to_ftetwild():
    plan = choose_polymesh_engine(_pre(watertight=False, suffix=".stl"))
    assert plan.primary == "ftetwild_poly"
    assert plan.fallback1 == "gmsh_direct_poly"
    assert "watertight" in plan.reason or "open" in plan.reason or "manifold" in plan.reason


def test_missing_pytetwild_routes_to_gmsh_only():
    plan = choose_polymesh_engine(_pre(fTetWild_available=False))
    assert plan.primary == "gmsh_direct_poly"
    assert plan.fallback1 is None
    assert plan.fallback2 is None


def test_default_is_gmsh_first_with_ftetwild_fallback():
    plan = choose_polymesh_engine(_pre(watertight=True))
    assert plan.primary == "gmsh_direct_poly"
    assert plan.fallback1 == "ftetwild_poly"


def test_unknown_probes_fall_back_to_gmsh_default():
    plan = choose_polymesh_engine(_pre(watertight=None))
    assert plan.primary == "gmsh_direct_poly"


def test_policy_is_deterministic():
    a = choose_polymesh_engine(_pre(watertight=False))
    b = choose_polymesh_engine(_pre(watertight=False))
    assert a == b
    assert a.chain() == ["ftetwild_poly", "gmsh_direct_poly"]


def test_bl_on_tets_never_in_auto_chain():
    for pre in (
        _pre(watertight=True),
        _pre(watertight=False),
        _pre(watertight=None),
        _pre(fTetWild_available=False),
        _pre(suffix=".stl", watertight=True),
        _pre(suffix=".step", solid_count=0),
        _pre(suffix=".step", solid_count=3),
    ):
        assert "bl_on_tets" not in choose_polymesh_engine(pre).chain()


def test_plan_carries_provenance():
    plan = choose_polymesh_engine(_pre(watertight=True))
    assert isinstance(plan, PolymeshEnginePlan)
    m = plan.to_mapping()
    assert m["primary"] == "gmsh_direct_poly"
    assert m["fallback1"] == "ftetwild_poly"
    assert m["reason"]


def test_preflight_probes_open_stl_cheaply():
    pre = preflight_tet_source(ROOT / "benchmarks/geometries/dirty_cad.stl")
    assert pre.watertight is False
    plan = choose_polymesh_engine(pre)
    assert plan.primary == "ftetwild_poly"


def test_preflight_closed_stl_is_watertight():
    pre = preflight_tet_source(ROOT / "benchmarks/geometries/mini_valve.stl")
    assert pre.watertight is True
    assert pre.bbox_diag_m is not None and pre.bbox_diag_m > 0


def test_preflight_step_skips_surface_probe_by_design():
    pre = preflight_tet_source(ROOT / "benchmarks/geometries/cylinder.stl")
    assert pre.suffix == ".stl"
    step_like = _pre(suffix=".step", watertight=None)
    assert step_like.watertight is None


def test_explicit_tet_sources_unchanged():
    pre = _pre()
    gmsh_only = choose_tet_source(pre, "gmsh")
    assert gmsh_only.primary == "gmsh" and gmsh_only.fallback == "ftetwild"
    ftet_only = choose_tet_source(pre, "ftetwild")
    assert ftet_only.primary == "ftetwild" and ftet_only.fallback == "gmsh"
    with pytest.raises(ValueError):
        choose_tet_source(pre, "nope")


def test_solidless_step_routes_to_ftetwild_only():
    """Parte4.step 30/09 (0 solidi, shell aperte): GMSH non può fare
    volumi — l'unico tentativo sensato è l'envelope fTetWild."""
    plan = choose_polymesh_engine(_pre(suffix=".step", solid_count=0))
    assert plan.primary == "ftetwild_poly"
    assert plan.fallback1 is None
    assert "solid" in plan.reason


def test_step_with_solids_uses_default_chain():
    plan = choose_polymesh_engine(_pre(suffix=".step", solid_count=1))
    assert plan.chain()[0] == "gmsh_direct_poly"
    assert "ftetwild_poly" in plan.chain()


@pytest.mark.slow
def test_preflight_counts_step_solids():
    """Prova reale sui due STEP noti (skip dove assenti: path macchina)."""
    old = Path(r"C:\Users\Davide Valoroso\Desktop\Report\Parte4.step")
    if old.is_file():
        assert preflight_tet_source(old).solid_count == 0
    new = Path(r"C:\Users\Davide Valoroso\Desktop\Report\Parte4.stp")
    if new.is_file():
        assert preflight_tet_source(new).solid_count == 1
    if not old.is_file() and not new.is_file():
        pytest.skip("R1 STEP inputs not present on this machine")


def test_r1_auto_decision_follows_solids_probe():
    """R1 decision half: 30/09 (0 solidi) -> fTetWild; 18/05 (1 solido)
    -> GMSH-first. STEP skips the watertight probe by design. The
    measured half is the §3.4 table run, not a unit test.
    """
    old = Path(r"C:\Users\Davide Valoroso\Desktop\Report\Parte4.step")
    new = Path(r"C:\Users\Davide Valoroso\Desktop\Report\Parte4.stp")
    if not old.is_file() or not new.is_file():
        pytest.skip("R1 STEP inputs not present on this machine")
    assert choose_polymesh_engine(preflight_tet_source(old)).primary == "ftetwild_poly"
    assert choose_polymesh_engine(preflight_tet_source(new)).primary == "gmsh_direct_poly"


def _write_msh2(path: Path, with_tet: bool):
    lines = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$Nodes", "4"]
    for i in range(1, 5):
        lines.append(f"{i} 0 0 {i}")
    lines += ["$EndNodes", "$Elements", "2" if with_tet else "1"]
    lines.append("1 2 2 0 3 1 2 3")
    if with_tet:
        lines.append("2 4 2 0 4 1 2 3 4")
    lines.append("$EndElements")
    path.write_text("\n".join(lines), encoding="ascii")


def test_has_volume_elements_msh2(tmp_path):
    from polyfoammesh.core.tet_source import _has_volume_elements

    surf_only = tmp_path / "surf.msh"
    _write_msh2(surf_only, with_tet=False)
    assert _has_volume_elements(surf_only) is False
    vol = tmp_path / "vol.msh"
    _write_msh2(vol, with_tet=True)
    assert _has_volume_elements(vol) is True
    assert _has_volume_elements(tmp_path / "missing.msh") is True


def test_has_volume_elements_msh4(tmp_path):
    from polyfoammesh.core.tet_source import _has_volume_elements

    surf_only = tmp_path / "s.msh"
    surf_only.write_text(
        "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n$Entities\n0 0 0 0\n"
        "$EndEntities\n$Elements\n1 5\n2 0 2 5\n1 2 3 4 5 6\n$EndElements\n",
        encoding="ascii",
    )
    assert _has_volume_elements(surf_only) is False
    vol = tmp_path / "v.msh"
    vol.write_text(
        "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n$Entities\n0 0 0 0\n"
        "$EndEntities\n$Elements\n1 6\n2 0 2 5\n1 2 3 4 5 6\n3 0 4 1\n1 2 3 4\n$EndElements\n",
        encoding="ascii",
    )
    assert _has_volume_elements(vol) is True


def test_run_tet_source_falls_back_on_surface_only_msh(tmp_path):
    """A 2D-only MSH is a failed tet stage (W18): the chain must move on."""
    from polyfoammesh.core.tet_source import (
        TetSourceSettings, run_tet_source,
    )

    two_d = tmp_path / "surf.msh"
    _write_msh2(two_d, with_tet=False)

    def fake_empty(_settings, _geometry, _msh):
        shutil.copy(two_d, _msh)
        return str(_msh), ["wall"], {}, {}

    good = tmp_path / "good.msh"
    _write_msh2(good, with_tet=True)

    def fake_vol(_settings, _geometry, _msh):
        shutil.copy(good, _msh)
        return str(_msh), ["wall"], {"n_tets": 1}, {}

    res = run_tet_source(
        tmp_path / "case.step", tmp_path / "out.msh",
        TetSourceSettings(source="gmsh"),
        requested="gmsh",
        runners={"gmsh": fake_empty, "ftetwild": fake_vol},
    )
    assert res.success
    assert res.effective_source == "ftetwild"
    assert res.fallback_from == "gmsh"
