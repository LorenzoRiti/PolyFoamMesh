"""W10.2: one TetSource contract, opt-in auto policy and measured handoff."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core import mesh_cache  # noqa: E402
from polyfoammesh.core import tet_source as ts  # noqa: E402
from polyfoammesh.core.ftetwild_wrapper import (  # noqa: E402
    assign_patches,
    surface_refinement_stats,
)


def _geom(tmp_path: Path, suffix: str = ".step") -> Path:
    p = tmp_path / f"part{suffix}"
    p.write_text("dummy", encoding="utf-8")
    return p


def test_preflight_and_auto_choices_are_deterministic(tmp_path):
    step = _geom(tmp_path, ".step")
    pre = ts.preflight_tet_source(
        step, detail="fine", refinement_zones=({"cell_size": 0.1},),
        feature_angle=40.0, fTetWild_available=True,
    )
    plan = ts.choose_tet_source(pre)
    assert plan.primary == "ftetwild"
    assert plan.fallback == "gmsh"
    assert "GMSH surface" in plan.reason
    assert ts.choose_tet_source(pre) == plan


def test_auto_stl_chooses_ftetwild_and_absent_ftet_chooses_gmsh(tmp_path):
    pre_stl = ts.preflight_tet_source(
        _geom(tmp_path, ".stl"), fTetWild_available=True,
    )
    assert ts.choose_tet_source(pre_stl).primary == "ftetwild"
    pre_step = ts.preflight_tet_source(
        _geom(tmp_path, ".step"), fTetWild_available=False,
    )
    plan = ts.choose_tet_source(pre_step)
    assert plan.primary == "gmsh"
    assert plan.fallback is None


def test_auto_falls_back_on_primary_failure(tmp_path):
    geometry = _geom(tmp_path, ".step")
    msh = tmp_path / "mesh.msh"
    calls = []

    def fail(settings, geom, out):
        calls.append("ftetwild")
        raise RuntimeError("subdivide_tets")

    def good(settings, geom, out):
        calls.append("gmsh")
        out.write_text("gmsh mesh", encoding="utf-8")
        return str(out), ["wall", "inlet"], {"n_tets": 10}, {"total": 0.1}

    result = ts.run_tet_source(
        geometry, msh, ts.TetSourceSettings(source="auto"),
        runners={"ftetwild": fail, "gmsh": good},
    )
    assert result.success
    assert result.effective_source == "gmsh"
    assert result.fallback_from == "ftetwild"
    assert calls == ["ftetwild", "gmsh"]
    assert result.names == ["wall", "inlet"]
    assert result.attempts[0].source == "ftetwild"
    assert "subdivide_tets" in result.attempts[0].error


def test_empty_primary_cannot_beat_fallback(tmp_path):
    geometry = _geom(tmp_path, ".step")
    msh = tmp_path / "mesh.msh"

    def empty(settings, geom, out):
        out.write_text("", encoding="utf-8")
        return str(out), ["wall"], {}, {}

    def good(settings, geom, out):
        out.write_text("valid", encoding="utf-8")
        return str(out), ["walls"], {}, {}

    result = ts.run_tet_source(
        geometry, msh, ts.TetSourceSettings(source="auto"),
        runners={"ftetwild": empty, "gmsh": good},
    )
    assert result.success
    assert result.effective_source == "gmsh"
    assert "empty MSH" in result.attempts[0].error


def test_both_failures_are_declared_without_a_mesh(tmp_path):
    geometry = _geom(tmp_path, ".step")
    msh = tmp_path / "mesh.msh"

    def bad(settings, geom, out):
        raise RuntimeError("no mesh")

    result = ts.run_tet_source(
        geometry, msh, ts.TetSourceSettings(source="auto"),
        runners={"ftetwild": bad, "gmsh": bad},
    )
    assert not result.success
    assert result.path is None
    assert "no mesh" in result.failure_reason
    assert len(result.attempts) == 2


def test_result_mapping_has_provenance_settings_and_fingerprint(tmp_path):
    geometry = _geom(tmp_path, ".step")
    msh = tmp_path / "mesh.msh"

    def good(settings, geom, out):
        out.write_text("mesh", encoding="utf-8")
        return str(out), ["wall"], {"n_tets": 1}, {"total": 0.2}

    settings = ts.TetSourceSettings(
        source="gmsh", detail="fine", surface_resolution="fine",
        refinement_zones=({"cell_size": 0.2},),
    )
    result = ts.run_tet_source(
        geometry, msh, settings, runners={"gmsh": good},
    )
    data = result.to_mapping()
    assert data["success"] is True
    assert data["requested_source"] == "gmsh"
    assert data["effective_source"] == "gmsh"
    assert data["settings"]["detail"] == "fine"
    assert data["settings"]["refinement_zones"]
    assert data["fingerprint"]
    assert data["attempts"][0]["source"] == "gmsh"
    # same inputs -> same fingerprint
    assert ts._fingerprint(settings, geometry) == result.fingerprint


def test_explicit_source_is_not_auto_substituted(tmp_path):
    geometry = _geom(tmp_path, ".step")
    msh = tmp_path / "mesh.msh"

    def good(settings, geom, out):
        out.write_text("mesh", encoding="utf-8")
        return str(out), ["wall"], {}, {}

    result = ts.run_tet_source(
        geometry, msh, ts.TetSourceSettings(source="gmsh"),
        runners={"gmsh": good},
    )
    assert result.success
    assert result.effective_source == "gmsh"
    assert result.fallback_from == ""


def test_ftetwild_cli_forwards_surface_refinement(monkeypatch, tmp_path, capsys):
    import polyfoammesh.core.ftetwild_wrapper as ftw

    captured = {}

    def fake_run_volume(**kwargs):
        captured.update(kwargs)
        return {"success": True, "path": str(tmp_path / "mesh.msh"),
                "names": ["wall"], "total_time_s": 0.0}

    monkeypatch.setattr(ftw, "run_volume", fake_run_volume)
    ftw._cli_main([
        "prog", "volume", f"--step={tmp_path / 'part.step'}",
        f"--msh={tmp_path / 'mesh.msh'}", "--surface-resolution=fine",
        '--refinement-zones=[{"cell_size":0.1,"xmin":0,"xmax":1,'
        '"ymin":0,"ymax":1,"zmin":0,"zmax":1}]',
        "--feature-angle=40",
    ])
    assert captured["surface_resolution"] == "fine"
    assert captured["refinement_zones"][0]["cell_size"] == 0.1
    assert captured["feature_angle"] == 40.0


def test_union_guard_is_explicitly_rejected():
    assert ts.union_is_conforming() is False


def test_surface_refinement_stats_measure_inside_vs_outside():
    tris = np.array([
        [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.0, 0.1, 0.0]],
        [[0.0, 0.0, 0.0], [0.01, 0.0, 0.0], [0.0, 0.01, 0.0]],
        [[1.0, 1.0, 0.0], [1.2, 1.0, 0.0], [1.0, 1.2, 0.0]],
    ])
    stats = surface_refinement_stats(
        tris, [{"xmin": -0.1, "xmax": 0.2, "ymin": -0.1, "ymax": 0.2,
                "zmin": -0.1, "zmax": 0.1}],
    )
    assert stats["zone_tri_fraction"] == pytest.approx(2 / 3)
    assert stats["median_edge_inside"] < stats["median_edge_outside"]


def test_patch_normal_filter_avoids_opposite_sheet():
    # Output face normal +x.  The nearest source centroid has normal -x
    # (opposite sheet); the slightly farther one has +x and must win.
    V = np.array([
        [0.0, -0.1, -0.1], [0.0, 0.1, -0.1], [0.0, 0.0, 0.1],
    ])
    faces = np.array([[0, 1, 2]])
    # Same y/z footprint, opposite winding (normal -x) at the nearer sheet.
    wrong = np.array([
        [[-0.001, -0.1, -0.1], [-0.001, 0.0, 0.1], [-0.001, 0.1, -0.1]],
    ])
    right = np.array([
        [[0.002, -0.1, -0.1], [0.002, 0.1, -0.1], [0.002, 0.0, 0.1]],
    ])
    src = np.concatenate([wrong, right])
    codes, stats = assign_patches(
        V, faces, src, np.array([0, 1]), normal_cos_min=0.5,
    )
    assert codes.tolist() == [1]
    assert stats["normal_filtered_faces"] == 1
    assert stats["normal_cos_min"] == pytest.approx(0.5)


def test_ftw_settings_are_in_the_cache_key():
    base = {
        "geometry_path": "part.step", "mesher": "ftetwild_poly",
        "detail": "medium", "ftw_eps_frac": 0.15, "ftw_simplify": False,
        "ftw_adaptive": True,
    }
    changed = dict(base, ftw_eps_frac=0.03)
    assert mesh_cache.mesh_fingerprint(base) != mesh_cache.mesh_fingerprint(
        changed
    )
    assert "ftw_eps_frac" in mesh_cache.CANONICAL_KEYS
    assert mesh_cache.CACHE_VERSION == 3
