"""fTetWild wrapper (experimental tet mesher, issue #12): pure functions,
MSH 2.2 output contract, and a subprocess end-to-end run.

The unit tests never need pytetwild; the e2e test is skipped when the
optional dependency is absent (pip install pytetwild / polyfoammesh[ftetwild]).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import meshio  # noqa: E402
import numpy as np
import pytest  # noqa: E402
from scipy.spatial import Delaunay  # noqa: E402

from polyfoammesh.core import ftetwild_wrapper as ftw  # noqa: E402

_HAS_PYTETWILD = importlib.util.find_spec("pytetwild") is not None


def _closed_tet_mesh() -> tuple[np.ndarray, np.ndarray]:
    """Delaunay tets of a jittered point grid in [0,1]^3 — closed, convex.

    Jitter is required: a PERFECT grid yields coplanar degenerate tets
    (29 of 191 on the 4^3 lattice) whose faces cancel in the unique-face
    count and make the boundary non-manifold-ish.
    """
    rng = np.random.default_rng(42)
    axis = np.linspace(0.0, 1.0, 4)
    grid = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1)
    pts = grid.reshape(-1, 3) + rng.uniform(-0.03, 0.03, size=(64, 3))
    pts = np.clip(pts, 0.0, 1.0)
    tri = Delaunay(pts)
    return pts, tri.simplices.astype(np.int64)


def test_sanitize_patch_names():
    assert ftw.sanitize_patch_names(["wall", "in let", "a/b"]) == [
        "wall", "in_let", "a_b",
    ]
    # every repeat of an already-seen name gets the next _N suffix
    assert ftw.sanitize_patch_names(["a b", "a_b", "a b"]) == [
        "a_b", "a_b_2", "a_b_3",
    ]
    # nothing usable left -> stable fallback
    assert ftw.sanitize_patch_names(["   ", ""]) == ["patch", "patch_2"]


def test_boundary_faces_closed_mesh():
    V, T = _closed_tet_mesh()
    faces, owner = ftw.boundary_faces(V, T)
    assert len(faces) == len(owner) > 0
    # each boundary face belongs to exactly one tet: re-count over all faces
    allf = np.concatenate([T[:, list(f)] for f in ftw._TET_FACES], axis=0)
    key = np.sort(allf, axis=1)
    _, counts = np.unique(key, axis=0, return_counts=True)
    assert int(np.sum(counts == 1)) == len(faces)
    # every owner actually references its face
    for f, t in zip(faces[:50], owner[:50]):
        tri = np.sort(T[t])
        assert all(v in tri for v in f)


def test_orient_faces_outward():
    V, T = _closed_tet_mesh()
    faces, owner = ftw.boundary_faces(V, T)
    out = ftw.orient_faces_outward(V, T, faces, owner)
    mesh_c = V.mean(axis=0)
    a, b, c = V[out[:, 0]], V[out[:, 1]], V[out[:, 2]]
    n = np.cross(b - a, c - a)
    face_c = (a + b + c) / 3.0
    # convex body centered in the box: outward normal must point away from
    # the mesh centroid for EVERY face
    dots = np.einsum("ij,ij->i", n, face_c - mesh_c)
    assert np.all(dots > 0)


def test_assign_patches_by_proximity():
    V, T = _closed_tet_mesh()
    faces, _owner = ftw.boundary_faces(V, T)
    # synthetic input surface: left half tagged 0, right half tagged 1
    src_tris = np.array(
        [
            [[0.1, 0.1, 0.1], [0.2, 0.1, 0.1], [0.1, 0.2, 0.1]],
            [[0.1, 0.5, 0.5], [0.2, 0.5, 0.5], [0.1, 0.6, 0.5]],
            [[0.8, 0.1, 0.1], [0.9, 0.1, 0.1], [0.8, 0.2, 0.1]],
            [[0.8, 0.5, 0.5], [0.9, 0.5, 0.5], [0.8, 0.6, 0.5]],
        ]
    )
    src_codes = np.array([0, 0, 1, 1])
    codes, stats = ftw.assign_patches(V, faces, src_tris, src_codes)
    centroids = V[faces].mean(axis=1)
    expected = (centroids[:, 0] >= 0.5).astype(int)
    assert np.array_equal(codes, expected)
    assert stats["n_output_boundary_faces"] == len(faces)
    assert stats["assign_dist_max"] < 1.0


def test_write_msh22_roundtrip(tmp_path):
    V, T = _closed_tet_mesh()
    faces, owner = ftw.boundary_faces(V, T)
    faces = ftw.orient_faces_outward(V, T, faces, owner)
    names = ["walls", "inlet"]
    # tag faces by side of the box they sit on
    face_phys = np.where(V[faces].mean(axis=1)[:, 0] < 0.5, 1, 2)
    out = tmp_path / "mesh.msh"
    ftw.write_msh22(out, V, T, faces, face_phys, names)

    m = meshio.read(out)
    counts = {cb.type: len(cb.data) for cb in m.cells}
    assert counts["tetra"] == len(T)
    assert counts["triangle"] == len(faces)
    assert set(m.field_data) == {"walls", "inlet"}
    # meshio's gmsh 2.2 reader stores field_data as [tag, dim]: dim=2, tags 1..N
    assert all(int(v[1]) == 2 for v in m.field_data.values())
    assert sorted(int(v[0]) for v in m.field_data.values()) == [1, 2]
    phys = dict(zip([cb.type for cb in m.cells], m.cell_data["gmsh:physical"]))
    assert set(np.asarray(phys["triangle"]).tolist()) <= {1, 2}
    assert set(np.asarray(phys["tetra"]).tolist()) == {1}
    # gmshToFoam consumes 2.2 with contiguous 1-based node ids
    assert out.read_text(encoding="ascii").startswith("$MeshFormat\n2.2 0 8\n")


def test_cli_unknown_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        ftw._cli_main(["prog", "bogus"])
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["success"] is False


def test_cli_missing_flag_reports_json(capsys):
    # no --step/--msh -> run_volume raises inside the CLI handler, which must
    # report it as JSON on stdout (the worker's contract), not a traceback
    with pytest.raises(SystemExit) as exc:
        ftw._cli_main(["prog", "volume"])
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["success"] is False
    assert "error" in payload


def test_default_target_edge_maps_detail_to_cross_section():
    # cells across the cross-section, straight from the slider — no clamp:
    # density alignment is decimation's job (input -> target direction)
    assert ftw._default_target_edge(0.1, "medium") == pytest.approx(0.1 / 20)
    assert ftw._default_target_edge(0.1, "very_fine") == pytest.approx(0.1 / 48)
    assert ftw._default_target_edge(0.1, "bogus") == pytest.approx(
        0.1 / ftw.DEFAULT_CELLS_ACROSS
    )


def test_decimation_reduction_fires_only_for_overdense_input():
    # input roughly at/below the target density -> no decimation
    assert ftw._decimation_reduction(0.005, 0.005) is None
    assert ftw._decimation_reduction(0.005, 0.004) is None
    # borderline: fires only when the input is >1.3x finer than the target
    assert ftw._decimation_reduction(0.005, 0.0064) is None  # 1.28x finer
    # 4x finer (the measured 3.5 mm vs 12.75 mm real part) -> keep ~1/16
    r = ftw._decimation_reduction(0.0035, 0.01275)
    assert r == pytest.approx((0.0035 / 0.01275) ** 2, rel=1e-6)
    # extreme ratios clamp at the 1% floor
    assert ftw._decimation_reduction(0.001, 0.1) == pytest.approx(0.01)
    # the real stall case: 1.43 mm input vs a 12.75 mm target (Parte4)
    r = ftw._decimation_reduction(0.00143, 0.01275)
    assert r == pytest.approx(0.0126, rel=0.05)
    # degenerate inputs -> no decision, no crash
    assert ftw._decimation_reduction(0.0, 0.01) is None
    assert ftw._decimation_reduction(0.01, 0.0) is None


def test_detail_cells_across_covers_slider_levels():
    assert set(ftw.DETAIL_CELLS_ACROSS) == {
        "very_coarse", "coarse", "medium", "fine", "very_fine",
    }
    # finer slider -> MORE cells across the cross-section
    order = ["very_coarse", "coarse", "medium", "fine", "very_fine"]
    counts = [ftw.DETAIL_CELLS_ACROSS[k] for k in order]
    assert counts == sorted(counts)
    assert ftw.DEFAULT_CELLS_ACROSS == ftw.DETAIL_CELLS_ACROSS["medium"]


def _run_cli(geom: Path, tmp_path: Path) -> tuple[dict, Path]:
    """Run ftetwild_wrapper.py volume as a real subprocess; (payload, msh)."""
    msh = tmp_path / (geom.stem + "_out.msh")
    src_root = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src_root) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(src_root / "polyfoammesh" / "core" / "ftetwild_wrapper.py"),
            "volume",
            f"--step={geom}",
            f"--msh={msh}",
            "--detail=coarse",
        ],
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
        cwd=str(src_root.parent),
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, f"no stdout; stderr tail: {proc.stderr[-1000:]}"
    payload = json.loads(lines[-1])
    assert payload["success"], payload
    assert payload["n_tets"] > 0
    assert payload["names"], "patch names must be reported"

    m = meshio.read(msh)
    tet = next(cb.data for cb in m.cells if cb.type == "tetra")
    assert len(tet) == payload["n_tets"]
    tri = next(cb.data for cb in m.cells if cb.type == "triangle")
    assert len(tri) == payload["tagging"]["n_output_boundary_faces"]
    assert set(m.field_data) == set(payload["names"])
    return payload, msh


@pytest.mark.skipif(not _HAS_PYTETWILD, reason="pytetwild not installed")
def test_e2e_cli_box_stl(tmp_path):
    """STL input: no GMSH — the input IS the surface (one patch per file)."""
    import trimesh

    stl = tmp_path / "box.stl"
    trimesh.creation.box(extents=(0.1, 0.1, 0.1)).export(stl)
    payload, _msh = _run_cli(stl, tmp_path)
    assert len(payload["names"]) == 1


@pytest.mark.skipif(not _HAS_PYTETWILD, reason="pytetwild not installed")
def test_e2e_cli_box_step(tmp_path):
    """STEP input: GMSH surface + tagged export — the primary GUI path."""
    cadquery = pytest.importorskip("cadquery")
    step = tmp_path / "box.step"
    cadquery.exporters.export(
        cadquery.Workplane("XY").box(0.1, 0.1, 0.1), str(step)
    )
    payload, _msh = _run_cli(step, tmp_path)
    # a STEP box has 6 faces -> gmsh entities become surface_N patches
    assert len(payload["names"]) >= 2
    assert all(n.startswith("surface_") for n in payload["names"])
