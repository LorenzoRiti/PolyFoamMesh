"""estimate_cell_count_geometric: a patch whose own suggested size is
NOT finer than the core cell must not contribute to the near-wall
"shell" — regression for a real collapse-to-floor bug found while
wiring this estimate into the GUI's live slider label.

compute_patch_cell_sizes derives each patch's size from the detail
preset's own defaults, independent of whatever core_cell is actually
in use, so the two can legitimately disagree (a patch "suggested" at
0.25 m while the active core_cell is 0.04 m). Before the fix, such a
patch's shell volume (area * 4 * local_size) could exceed the entire
domain volume and get clamped to all of it, leaving zero core volume
and collapsing the whole estimate to the 100-cell floor — regardless
of how fine core_cell actually was.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import trimesh

from polyfoammesh.core.geometry import (
    estimate_cell_count,
    estimate_cell_count_geometric,
)


def _venturi_mesh() -> trimesh.Trimesh:
    cyl = trimesh.creation.cylinder(radius=0.5, height=2.0, sections=48)
    verts = cyl.vertices.copy()
    z = verts[:, 2]
    r = np.sqrt(np.maximum(verts[:, 0] ** 2 + verts[:, 1] ** 2, 1e-12))
    waist_r = 0.30 + 0.20 * np.clip(1 - np.abs(z) / 1.0, 0, 1)
    scale = np.where(r > 0.001, np.where(z > -1.0, waist_r / r, 1.0), 1.0)
    verts[:, 0] *= scale
    verts[:, 1] *= scale
    m = trimesh.Trimesh(vertices=verts, faces=cyl.faces)
    m.metadata["name"] = "wall"
    return m


def test_coarser_than_core_patch_does_not_collapse_estimate():
    m = _venturi_mesh()
    volume = float(m.volume)
    core_cell = 0.0418451229  # fine core, well below the patch's own size
    patch_sizes = {"wall": 0.25}  # coarser than core_cell -- should be skipped

    lo, nominal, hi = estimate_cell_count_geometric([m], volume, core_cell, patch_sizes)

    # Must reduce to the plain volume/core_cell**3 estimate (no shell
    # contribution at all), NOT collapse to the 100-cell floor.
    expected = int(volume / (core_cell ** 3))
    assert nominal > 1000
    assert abs(nominal - expected) / expected < 0.05
    assert lo <= nominal <= hi


def test_finer_than_core_patch_still_contributes_shell():
    m = _venturi_mesh()
    volume = float(m.volume)
    core_cell = 0.05
    patch_sizes = {"wall": 0.01}  # finer than core -- should refine locally

    lo, nominal, hi = estimate_cell_count_geometric([m], volume, core_cell, patch_sizes)
    blind_lo, blind_nominal, blind_hi = estimate_cell_count(volume, core_cell, core_cell)

    # A genuinely finer patch should raise the estimate above the plain
    # core-only estimate (real local refinement adds real cells), and
    # must not collapse to the floor either.
    assert nominal > blind_nominal
    assert nominal > 1000


def test_no_patch_sizes_falls_back_to_blind_estimate():
    m = _venturi_mesh()
    volume = float(m.volume)
    core_cell = 0.05
    assert estimate_cell_count_geometric([m], volume, core_cell, {}) == \
        estimate_cell_count(volume, core_cell, core_cell)
    assert estimate_cell_count_geometric([m], volume, core_cell, None) == \
        estimate_cell_count(volume, core_cell, core_cell)
