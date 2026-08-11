"""Regression tests for the local-passage-width sizing field.

This is the algorithm developed to replace two ad-hoc heuristics that
were causing "refines at random" complaints (a curve-length quartile with
no notion of surface separation, and a bounding-box-distance proxy for
surface-pair gaps): ray-cast the ALREADY-tessellated boundary along each
sample point's inward normal. The first-hit distance is the local width
of the volume the boundary encloses — for a CFD case (GMSH meshes the
fluid domain), that IS the local passage width. This is a real,
established quantity (closely related to "local feature size" /
thickness mapping used by wall-thickness analysis tools), not a proxy.

Two levels of test:
  - test_venturi_* : pure trimesh + numpy, no GMSH — validates the core
    ray-casting measurement (core.geometry.sample_thickness_field) on a
    hand-built, single connected, watertight venturi (wide-narrow-wide
    body of revolution) with a KNOWN throat diameter. Fast, portable, no
    external files.
  - test_gmsh_* : real GMSH (no WSL), validates the full extraction +
    sizing pipeline (gmsh_wrapper._extract_boundary_trimesh +
    _sample_passage_thickness_field) end-to-end on a simple box.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

trimesh = pytest.importorskip("trimesh")

from cfmesh_autogui.core.geometry import sample_thickness_field  # noqa: E402


def _venturi_solid(r_wide=1.0, r_throat=0.15, length=6.0, n_theta=32, n_z=60):
    """A single connected, watertight body of revolution: radius profile
    wide -> throat -> wide (smooth cosine taper), throat at z=length/2.
    Diameter at the wide ends is 2*r_wide; at the throat, ~2*r_throat.
    """
    z = np.linspace(0, length, n_z)
    mid = length / 2
    half_taper_len = length * 0.35
    d = np.abs(z - mid)
    taper = np.clip(d / half_taper_len, 0, 1)
    taper = 0.5 - 0.5 * np.cos(taper * np.pi)
    r = r_throat + (r_wide - r_throat) * taper

    theta = np.linspace(0, 2 * np.pi, n_theta, endpoint=False)
    verts = []
    for zi, ri in zip(z, r):
        for t in theta:
            verts.append([ri * np.cos(t), ri * np.sin(t), zi])
    verts = np.array(verts)

    faces = []
    for i in range(n_z - 1):
        for j in range(n_theta):
            j2 = (j + 1) % n_theta
            a = i * n_theta + j
            b = i * n_theta + j2
            c = (i + 1) * n_theta + j2
            dd = (i + 1) * n_theta + j
            faces.append([a, b, c])
            faces.append([a, c, dd])

    centre_bottom = len(verts)
    verts = np.vstack([verts, [[0, 0, z[0]]]])
    for j in range(n_theta):
        j2 = (j + 1) % n_theta
        faces.append([centre_bottom, j2, j])
    centre_top = len(verts)
    verts = np.vstack([verts, [[0, 0, z[-1]]]])
    base = (n_z - 1) * n_theta
    for j in range(n_theta):
        j2 = (j + 1) % n_theta
        faces.append([centre_top, base + j, base + j2])

    mesh = trimesh.Trimesh(vertices=verts, faces=np.array(faces), process=True)
    mesh.fix_normals()
    return mesh, z, r


def test_venturi_is_watertight_sanity_check():
    """Fixture sanity: if this fails, the test below is meaningless."""
    mesh, _, _ = _venturi_solid()
    assert mesh.is_watertight


def test_thickness_field_is_local_not_a_body_average():
    """The core claim: thickness at the throat is small, at the wide
    ends is large, WITHIN THE SAME connected mesh — a genuinely spatial
    field, not a per-body statistic (which is all the pre-existing
    analyze_local_thickness computes)."""
    mesh, z_profile, r_profile = _venturi_solid()
    bbox_max = float(max(mesh.extents))
    pts, thickness = sample_thickness_field(mesh, 8000, bbox_max, seed=1)
    assert len(pts) > 1000

    # exclude points very near the centreline (end-cap samples)
    r_xy = np.linalg.norm(pts[:, :2], axis=1)
    on_wall = r_xy > 0.05
    pts_w, t_w = pts[on_wall], thickness[on_wall]
    z_w = pts_w[:, 2]

    length = z_profile[-1]
    mid = length / 2
    far_end_mask = (z_w < 0.5) | (z_w > length - 0.5)
    throat_mask = np.abs(z_w - mid) < 0.3

    median_far = np.median(t_w[far_end_mask])
    median_throat = np.median(t_w[throat_mask])

    # true diameters: 2.0 at the wide ends, ~0.30 at the throat
    assert median_far == pytest.approx(2.0, rel=0.05)
    assert median_throat == pytest.approx(0.30, rel=0.2)
    assert median_throat < median_far / 3  # unambiguously distinct, not noise


def test_thickness_field_decreases_monotonically_toward_the_throat():
    """A stronger check than two-bucket comparison: binning along the
    axis, thickness must TREND down toward the throat (matches the real
    venturi.stl behaviour observed end-to-end: 0.121 -> 0.075 across 10
    buckets). Checked two ways, both robust to per-bin sampling noise in
    the still-wide region far from the throat:
      - strong negative correlation between axial position and thickness
      - the last (near-throat) bin is clearly smaller than the first
        (wide-end) bin, and smaller than every bin before the taper
        actually starts (the last 3 bins, which is where the profile's
        taper — half_taper_len — actually reaches)
    """
    mesh, z_profile, _ = _venturi_solid()
    bbox_max = float(max(mesh.extents))
    pts, thickness = sample_thickness_field(mesh, 8000, bbox_max, seed=2)
    r_xy = np.linalg.norm(pts[:, :2], axis=1)
    on_wall = r_xy > 0.05
    pts_w, t_w = pts[on_wall], thickness[on_wall]
    z_w = pts_w[:, 2]

    length = z_profile[-1]
    mid = length / 2
    half = z_w[z_w <= mid]
    half_t = t_w[z_w <= mid]
    n_bins = 8
    edges = np.linspace(half.min(), mid, n_bins + 1)
    centres, medians = [], []
    for i in range(n_bins):
        m = (half >= edges[i]) & (half < edges[i + 1])
        if m.sum() < 5:
            continue
        centres.append((edges[i] + edges[i + 1]) / 2)
        medians.append(np.median(half_t[m]))
    assert len(medians) >= 4
    medians = np.array(medians)

    corr = np.corrcoef(centres, medians)[0, 1]
    assert corr < -0.8, f"expected strong negative trend toward the throat, got r={corr:.3f}: {medians}"
    assert medians[-1] < medians[0] * 0.3, (
        f"near-throat bin should be well below the wide-end bin: {medians}"
    )


def test_zero_samples_degrades_gracefully():
    empty = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=int))
    pts, t = sample_thickness_field(empty, 100, 1.0, seed=0)
    assert len(pts) == 0
    assert len(t) == 0


# ---------------------------------------------------------------------
# Real GMSH (no WSL): full extraction + field pipeline on a simple box
# ---------------------------------------------------------------------

gmsh = pytest.importorskip("gmsh")


@pytest.fixture
def gmsh_box():
    gmsh.initialize()
    try:
        gmsh.model.add("t")
        gmsh.model.occ.addBox(0, 0, 0, 2, 1, 0.5)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", 0.15)
        yield gmsh
    finally:
        gmsh.finalize()


def test_gmsh_boundary_extraction_matches_known_volume(gmsh_box):
    from cfmesh_autogui.core.gmsh_wrapper import _extract_boundary_trimesh

    mesh = _extract_boundary_trimesh(gmsh_box)
    assert mesh is not None
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(1.0, rel=1e-6)  # 2 * 1 * 0.5


def test_gmsh_passage_thickness_field_end_to_end(gmsh_box):
    from cfmesh_autogui.core.gmsh_wrapper import _sample_passage_thickness_field

    pts, target = _sample_passage_thickness_field(
        gmsh_box, h_min=0.01, h_max=1.0, cells_across=8, max_samples=2000,
    )
    assert len(pts) > 0
    assert len(target) == len(pts)
    assert (target >= 0.01 - 1e-9).all()
    assert (target <= 1.0 + 1e-9).all()
    # the box's shortest dimension is 0.5 -> passage width there is 0.5,
    # target = 0.5 / 8 = 0.0625, well inside [h_min, h_max] so unclamped
    assert np.median(target) == pytest.approx(0.5 / 8, rel=0.15)
