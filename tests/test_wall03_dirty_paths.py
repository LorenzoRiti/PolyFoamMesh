"""WALL-03: dirty-CAD and mini_valve meshing paths (no new engine code).

Locks in the measured WALL-03 outcomes with existing machinery only:
- dirty_cad (10-triangle open cube): fTetWild completes, converts to a
  tet polyMesh, and the dual converter REFUSES the open input with the
  non-manifold message (gate working, not a failure).
- mini_valve (fTetWild tet): dual + split_boundary_defects reaches
  residual 0 in-process (the Mesh OK configuration, verified twice
  against real checkMesh during the lane).

fTetWild tests skip cleanly without the optional pytetwild dependency.
The mini_valve dual test is marked slow (fTetWild + dual take ~1-2 min).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pytest

from polyfoammesh.core import ftetwild_wrapper as ftw
from polyfoammesh.core.foam_mesh_io import read_polymesh
from polyfoammesh.core.mesh_converter import msh_to_of_polymesh
from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter

_HAS_PYTETWILD = importlib.util.find_spec("pytetwild") is not None
GEO = Path(__file__).resolve().parents[1] / "benchmarks" / "geometries"


def _dirty_tet(tmp_path: Path):
    V, T, _info = ftw.tetrahedralize(
        GEO / "dirty_cad.stl", lr=0.1, opt_iter=3, stop_energy=30.0,
    )
    assert len(V) > 0 and len(T) > 0
    faces, owner = ftw.boundary_faces(np.asarray(V), np.asarray(T))
    assert len(faces) > 0
    faces = ftw.orient_faces_outward(np.asarray(V), np.asarray(T), faces, owner)
    msh = tmp_path / "dirty.msh"
    ftw.write_msh22(
        msh, np.asarray(V), np.asarray(T), np.asarray(faces),
        np.ones(len(faces), dtype=np.int64), ["wall"],
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(msh, case)
    return case


@pytest.mark.skipif(not _HAS_PYTETWILD, reason="pytetwild not installed")
def test_dirty_cad_ftetwild_completes_and_converts(tmp_path):
    """fTetWild meshes the open 10-triangle cube; converter names 1 patch."""
    case = _dirty_tet(tmp_path)
    _pts, faces, owner, neigh, patches = read_polymesh(
        case / "constant" / "polyMesh"
    )
    assert len(faces) > 0
    assert [p["name"] for p in patches] == ["wall"]
    assert sum(p["nFaces"] for p in patches) == len(faces) - len(neigh)


@pytest.mark.skipif(not _HAS_PYTETWILD, reason="pytetwild not installed")
def test_dirty_cad_dual_refuses_open_input(tmp_path):
    """The dual gate rejects the non-closed tet mesh with a clear error."""
    case = _dirty_tet(tmp_path)
    res = TetPolyDualConverter(case, log=lambda m: None).run()
    assert res.success is False
    assert any("Non-manifold" in e for e in res.errors)


def test_dirty_cad_report_lists_wall_patch_without_silence(tmp_path):
    """boundary_layer_report on the unbuilt dirty tet: per-patch rows,
    coverage 0 — never a silent {}."""
    pytest.importorskip("pytetwild")
    from polyfoammesh.core.openfoam_runner import boundary_layer_report

    case = _dirty_tet(tmp_path)
    rep = boundary_layer_report(case)
    assert rep != {}
    assert set(rep["per_patch"]) == {"wall"}
    assert rep["coverage"] == 0.0
    assert rep["per_patch"]["wall"]["n_wall_faces"] > 0


@pytest.mark.slow
@pytest.mark.skipif(not _HAS_PYTETWILD, reason="pytetwild not installed")
def test_minivalve_ftetwild_dual_residual_zero(tmp_path):
    """fTetWild tet + production dual + split_boundary_defects reaches
    residual 0 in-process on mini_valve (the Mesh OK configuration,
    verified twice against real checkMesh during the lane).

    fTetWild shows run-to-run variance on this input (~1 in 5 runs
    yields a non-manifold boundary); the test mirrors the lane's
    manifold gate with up to 3 attempts and records which one won.
    """
    from collections import Counter

    attempts = []
    for _ in range(3):
        V, T, _info = ftw.tetrahedralize(
            GEO / "mini_valve.stl", lr=0.02, opt_iter=5, stop_energy=30.0,
        )
        assert len(T) > 1000
        faces, owner = ftw.boundary_faces(np.asarray(V), np.asarray(T))
        keys = [tuple(sorted(f)) for f in faces]
        edge_uses: Counter = Counter()
        for f in keys:
            for a, b in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
                edge_uses[(min(a, b), max(a, b))] += 1
        bad = sum(1 for v in edge_uses.values() if v != 2)
        attempts.append(bad)
        if bad == 0:
            break
    assert attempts[-1] == 0, f"no manifold tet in 3 attempts: {attempts}"
    faces = ftw.orient_faces_outward(np.asarray(V), np.asarray(T), faces, owner)
    msh = tmp_path / "mini.msh"
    ftw.write_msh22(
        msh, np.asarray(V), np.asarray(T), np.asarray(faces),
        np.ones(len(faces), dtype=np.int64), ["wall"],
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(msh, case)
    res = TetPolyDualConverter(
        case, log=lambda m: None,
        collapse_smooth_edges=True, boundary_feature_angle=40.0,
        collapse_volume_tolerance=0.10, split_boundary_defects=True,
    ).run()
    assert res.success is True
    assert res.residual_defects == 0
    assert res.defect_breakdown == {"pyramid": 0, "non_ortho": 0, "skew": 0}


def test_gmsh_rejects_unloadable_dirty_stl(tmp_path):
    """The discrete GMSH path fails fast (load error) on the broken
    binary STL — documents lever 3.2's boundary: healing/STEP cannot
    apply where there is no BRep and the container itself is rejected."""
    from polyfoammesh.core.gmsh_subprocess import run_gmsh_volume

    with pytest.raises(RuntimeError, match="[Ee]rror loading|failed"):
        run_gmsh_volume(
            GEO / "dirty_cad.stl", tmp_path / "dirty.msh",
            detail="medium", threads=1, timeout_s=120,
        )


def test_report_lists_patches_on_unbuilt_tet(tmp_path):
    """boundary_layer_report on the dirty tet (no BL built): per-patch
    rows with coverage 0, never a silent {} (lever 3.5)."""
    pytest.importorskip("pytetwild")
    from polyfoammesh.core.openfoam_runner import boundary_layer_report

    V, T, _info = ftw.tetrahedralize(
        GEO / "dirty_cad.stl", lr=0.1, opt_iter=3, stop_energy=30.0,
    )
    faces, owner = ftw.boundary_faces(np.asarray(V), np.asarray(T))
    faces = ftw.orient_faces_outward(np.asarray(V), np.asarray(T), faces, owner)
    msh = tmp_path / "dirty.msh"
    ftw.write_msh22(
        msh, np.asarray(V), np.asarray(T), np.asarray(faces),
        np.ones(len(faces), dtype=np.int64), ["wall"],
    )
    case = tmp_path / "case"
    msh_to_of_polymesh(msh, case)
    rep = boundary_layer_report(case)
    assert rep != {}
    assert set(rep["per_patch"]) == {"wall"}
    assert rep["coverage"] == 0.0
