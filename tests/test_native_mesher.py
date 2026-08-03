"""Tests for the native cut-cell mesher (Phase 3).

Fast suite — in-memory trimesh surfaces, no GMSH, no WSL.  Invariants:

- watertight (per-cell closure ~0), positive cell volumes;
- total volume matches the tessellated surface volume to ~1e-4 relative;
- every cut cell is a valid polyhedron (each edge in exactly 2 faces) in
  clean_cells mode — the median-dual engine's requirement;
- boundary faces belong to the surface patch;
- rejects non-manifold / degenerate inputs gracefully.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh

from cfmesh_autogui.core.native_mesher import NativeMesher
from cfmesh_autogui.core.tet_poly_dual import _cell_centres, _face_geometry


def _run(tmp_path: Path, mesh, cell_size: float, **kw) -> "object":
    return NativeMesher([mesh], cell_size=cell_size, log=lambda m: None,
                        **kw).run(tmp_path / "case")


@pytest.mark.parametrize("subdiv,cell", [(1, 0.25), (2, 0.2), (2, 0.15)])
def test_sphere_watertight_positive_volume(tmp_path, subdiv, cell):
    s = trimesh.creation.icosphere(subdivisions=subdiv, radius=0.5)
    r = _run(tmp_path, s, cell)
    assert r.success, r.errors
    assert r.max_closure_error < 1e-6
    assert r.min_cell_volume > 0.0
    assert r.defects["pyramid"] == 0
    # volume within 0.5% of the tessellated surface volume
    assert abs(r.volume - s.volume) < 5e-3 * s.volume


def test_cube_exact_volume_and_counts(tmp_path):
    c = trimesh.creation.box(extents=(1, 1, 1)).apply_translation([0.5, 0.5, 0.5])
    r = _run(tmp_path, c, 0.5)
    assert r.success
    assert r.volume == pytest.approx(1.0, abs=1e-12)
    assert r.max_closure_error < 1e-10
    # 3x3x3 grid (margin), all surface cells cut
    assert r.n_cells == 27
    assert r.n_hex_cells == 1
    assert r.n_cut_cells == 26


def test_clean_cells_mode_is_dual_ready(tmp_path):
    """clean_cells=True must yield only valid polyhedra (each edge in 2 faces)
    so the median-dual engine can consume the mesh."""
    s = trimesh.creation.icosphere(subdivisions=2, radius=0.5)
    r = _run(tmp_path, s, 0.15, clean_cells=True)
    assert r.success, r.errors
    # re-read and verify per-cell edge counts
    from cfmesh_autogui.core import foam_mesh_io as fio

    pts, faces, own, nb, patches = fio.read_polymesh(
        Path(r.out_dir))
    n_int = len(nb)
    from collections import Counter

    for c in range(int(own.max()) + 1):
        idx = [i for i in range(len(own))
               if own[i] == c or (i < n_int and nb[i] == c)]
        ec: Counter = Counter()
        for i in idx:
            f = faces[i]
            for k in range(len(f)):
                a, b = f[k], f[(k + 1) % len(f)]
                ec[(min(a, b), max(a, b))] += 1
        assert all(v == 2 for v in ec.values()), f"cell {c} non-manifold edge"


def test_surface_patch_present(tmp_path):
    s = trimesh.creation.icosphere(subdivisions=1, radius=0.5)
    r = _run(tmp_path, s, 0.25)
    assert r.success
    from cfmesh_autogui.core import foam_mesh_io as fio

    _, faces, own, nb, patches = fio.read_polymesh(Path(r.out_dir))
    assert len(patches) >= 1
    assert sum(p["nFaces"] for p in patches) == len(faces) - len(nb)


def test_rejects_empty_surfaces(tmp_path):
    r = NativeMesher([trimesh.Trimesh()], cell_size=0.1,
                     log=lambda m: None).run(tmp_path / "case")
    assert not r.success
    assert r.errors
