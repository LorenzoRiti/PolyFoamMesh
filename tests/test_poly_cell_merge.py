"""Tests for `core/poly_cell_merge.py` — repairing non-star-shaped
("face pyramid") polyhedral cells by MERGING them with an interior
neighbour instead of cutting them (see
notes/poly_cell_merge_reasoning.md for why: the checkMesh centroid
formula is fixed ground truth, splitting the offending face doesn't
help, and merging is the low-risk option already conceptually used by
OpenFOAM's own cell agglomeration).

Uses the real barycentric-dual cube fixture (same one `test_bl_poly.py`
builds) so the merge logic is exercised on genuine dual-mesh cells, not
hand-crafted toy geometry.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _test_helpers import space_free_tmp_root  # noqa: E402
from test_bl_poly import _build_dual_case  # noqa: E402

import polyfoammesh.core.foam_mesh_io as fio
from polyfoammesh.core.bl_poly import _cell_metrics, _pyramid_violations
from polyfoammesh.core.poly_cell_merge import (
    _union_find_groups,
    find_merge_candidates,
    merge_cell_groups,
    repair_concave_cells_if_safe,
)

WORK_ROOT = space_free_tmp_root() / "cfmesh_bench" / "poly_cell_merge_test"
FIXDIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    shutil.rmtree(WORK_ROOT, ignore_errors=True)


def _assert_upper_triangular_order(owner, n_int):
    """OpenFOAM's own structural requirement (checked separately by
    checkMesh from plain owner < neighbour): internal faces must be
    ordered so owner is non-decreasing. A real regression was found here
    - merging renumbers cells, which can silently break this global
    ordering even though every individual face still has owner <
    neighbour - checkMesh reported it as 'Faces not in upper triangular
    order' on a real mesh where this wasn't caught by the simpler
    per-face check."""
    o = owner[:n_int]
    assert np.all(o[1:] >= o[:-1]), "internal faces not owner-sorted (upper triangular order)"


def _mesh_invariants(points, faces, owner, neigh, n_int, n_cells):
    """Same invariants `test_bl_poly._mesh_ok` checks: closure, positive
    volume, owner < neighbour, no unused points. Returns total volume."""
    assert np.all(owner[:n_int] < neigh[:n_int])
    _assert_upper_triangular_order(owner, n_int)
    assert owner.min() >= 0 and (n_int == 0 or neigh.min() >= 0)
    assert owner.max() < n_cells and (n_int == 0 or neigh.max() < n_cells)
    used = set()
    for f in faces:
        used.update(f)
    assert len(used) == len(points), (
        f"{len(points) - len(used)} unused points after merge"
    )
    vols, closure, scale = _cell_metrics(points, faces, owner, neigh, n_cells, n_int)
    rel = np.linalg.norm(closure, axis=1) / np.maximum(scale, 1e-300)
    assert np.all(rel < 1e-8), f"cells not closed after merge: {int((rel >= 1e-8).sum())}"
    assert np.all(vols > 0.0), "non-positive cell volume after merge"
    return float(vols.sum())


def test_union_find_groups_collapses_transitive_pairs():
    groups = _union_find_groups([(1, 2), (2, 3), (10, 11)])
    groups_as_sets = sorted((frozenset(g) for g in groups), key=lambda s: min(s))
    assert groups_as_sets == [frozenset({1, 2, 3}), frozenset({10, 11})]


def test_union_find_groups_ignores_singletons():
    # a pair that never appears stays out; unpaired cells produce no group
    assert _union_find_groups([]) == []


def test_merge_two_adjacent_cells_conserves_volume_and_stays_valid():
    case = _build_dual_case(WORK_ROOT / "cube_merge_pair")
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    faces = [list(f) for f in faces]
    n_int = len(neigh)
    n_cells = int(max(owner.max(), neigh.max())) + 1
    vol0 = float(_cell_metrics(points, faces, owner, neigh, n_cells, n_int)[0].sum())
    n_bnd_faces_before = len(faces) - n_int

    assert n_int > 0, "fixture must have at least one internal face to merge across"
    cA, cB = int(owner[0]), int(neigh[0])
    groups = _union_find_groups([(cA, cB)])

    faces2, owner2, neigh2, n_int2, n_cells2, patches2 = merge_cell_groups(
        faces, owner, neigh, n_int, n_cells, patches, groups,
    )

    assert n_cells2 == n_cells - 1
    assert n_int2 <= n_int - 1  # at least the shared face itself was dropped
    # no boundary face was added, removed or reordered
    assert len(faces2) - n_int2 == n_bnd_faces_before
    for p, p2 in zip(patches, patches2):
        assert p2["nFaces"] == p["nFaces"]
        assert p2["startFace"] == p["startFace"] - (n_int - n_int2)

    vol1 = _mesh_invariants(points, faces2, owner2, neigh2, n_int2, n_cells2)
    assert abs(vol1 - vol0) < 1e-9 * max(vol0, 1e-30), (
        f"volume not conserved: {vol0} -> {vol1}"
    )


def test_merge_is_a_mechanical_primitive_not_a_geometric_guarantee():
    """`merge_cell_groups` is bookkeeping ONLY (union faces, drop the
    shared one, renumber) — it does NOT guarantee the merged cell passes
    the pyramid test. Measured on the reference valve: merging the right
    (checked) neighbour fixes 86.8% of concave cells, but an ARBITRARY
    merge can just as easily turn a healthy pair into a non-star-shaped
    one (this is exactly why a real repair pass must try candidates and
    keep only the merges verified, before/after, to help — see
    notes/poly_cell_merge_reasoning.md). This test locks in that
    contract: the mesh stays fully VALID (closed, positive volume,
    volume-conserving) after an arbitrary merge, but pyramid-violation
    COUNT is not asserted either way, because it genuinely isn't
    guaranteed."""
    case = _build_dual_case(WORK_ROOT / "cube_merge_untouched")
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    faces = [list(f) for f in faces]
    n_int = len(neigh)
    n_cells = int(max(owner.max(), neigh.max())) + 1

    cA, cB = int(owner[0]), int(neigh[0])
    groups = _union_find_groups([(cA, cB)])
    faces2, owner2, neigh2, n_int2, n_cells2, patches2 = merge_cell_groups(
        faces, owner, neigh, n_int, n_cells, patches, groups,
    )
    # mechanical correctness must hold regardless of whether THIS
    # particular merge happens to help or hurt the pyramid test
    _mesh_invariants(points, faces2, owner2, neigh2, n_int2, n_cells2)


def test_repair_concave_cells_if_safe_is_noop_on_healthy_geometry():
    """A healthy mesh (no pyramid violations) has no merge candidates -
    the guarded repair must return the input completely unchanged, not
    just "equivalent" (no risk taken where there's nothing to fix)."""
    case = _build_dual_case(WORK_ROOT / "cube_repair_noop")
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    faces = [list(f) for f in faces]
    n_int = len(neigh)
    n_cells = int(max(owner.max(), neigh.max())) + 1
    assert _pyramid_violations(points, faces, owner, neigh, n_int, n_cells) == 0

    faces2, owner2, neigh2, n_int2, n_cells2, patches2, report = repair_concave_cells_if_safe(
        points, faces, owner, neigh, n_int, n_cells, patches,
    )
    assert report["applied"] is False
    assert n_cells2 == n_cells
    assert n_int2 == n_int
    assert faces2 == faces
    assert np.array_equal(owner2, owner)
    assert np.array_equal(neigh2, neigh)


def test_repair_concave_cells_if_safe_rejects_when_skew_would_worsen():
    """On the real (pure, un-collapsed) valve dual fixture, the merge
    would cut pyramid violations dramatically (1045 -> 161) but also
    increases the skew violation count (6 -> 8) - a real, small quality
    trade-off, not a bug. Since the skew formula fix (2026-09-14, see
    notes/skewness_formula_fix_reasoning.md) the guardian can finally SEE
    this trade-off and correctly REJECTS the merge rather than silently
    accepting a mesh that trades one defect for another. This locks in
    that the gate is doing its job (this test used to assert acceptance,
    before the guardian could see skewness at all)."""
    npz = FIXDIR / "valve_dual.npz"
    if not npz.exists():
        pytest.skip("valve_dual.npz fixture not present")
    data = np.load(npz, allow_pickle=True)
    points = data["points"]
    offsets = data["face_offsets"]
    verts = data["face_verts"]
    owner = data["owner"]
    neigh = data["neighbour"]
    n_int = len(neigh)
    n_faces = len(offsets) - 1
    faces = [verts[offsets[i]:offsets[i + 1]].tolist() for i in range(n_faces)]
    n_cells = int(max(owner.max(), neigh.max())) + 1
    patches = [{"name": "wall", "type": "wall",
                "nFaces": n_faces - n_int, "startFace": n_int}]

    bad0 = _pyramid_violations(points, faces, owner, neigh, n_int, n_cells)
    assert bad0 > 0, "fixture expected to have real pyramid violations to repair"
    vol0 = float(_cell_metrics(points, faces, owner, neigh, n_cells, n_int)[0].sum())
    # this fixture stores points as float32 (max closure residual ~5.6e-6,
    # not the ~1e-12 a float64 production mesh gets) - so closure/volume
    # are compared BEFORE vs AFTER the merge, not against an absolute
    # tolerance tuned for float64.
    _vols0, closure0, scale0 = _cell_metrics(points, faces, owner, neigh, n_cells, n_int)
    rel0 = np.linalg.norm(closure0, axis=1) / np.maximum(scale0, 1e-300)
    n_not_closed0 = int((rel0 >= 1e-8).sum())

    faces2, owner2, neigh2, n_int2, n_cells2, patches2, report = repair_concave_cells_if_safe(
        points, faces, owner, neigh, n_int, n_cells, patches,
    )
    assert report["applied"] is False, report
    assert "skew" in report["reason"], report
    assert report["skew_violations_after"] > report["skew_violations_before"], report
    # rejected -> the mesh returned must be BYTE-IDENTICAL to the input
    assert faces2 == faces
    assert np.array_equal(owner2, owner)
    assert np.array_equal(neigh2, neigh)
    bad1 = _pyramid_violations(points, faces2, owner2, neigh2, n_int2, n_cells2)
    assert bad1 == bad0, "rejected merge must leave pyramid count unchanged too"
    _assert_upper_triangular_order(owner2, n_int2)

    vols2, closure2, scale2 = _cell_metrics(points, faces2, owner2, neigh2, n_cells2, n_int2)
    rel2 = np.linalg.norm(closure2, axis=1) / np.maximum(scale2, 1e-300)
    n_not_closed2 = int((rel2 >= 1e-8).sum())
    assert n_not_closed2 <= n_not_closed0, (
        f"merge made closure worse: {n_not_closed0} -> {n_not_closed2}"
    )
    assert np.all(vols2 > 0.0), "non-positive cell volume after merge"
    used = set()
    for f in faces2:
        used.update(f)
    assert len(used) == len(points), f"{len(points) - len(used)} unused points after merge"

    vol1 = float(vols2.sum())
    assert abs(vol1 - vol0) < 1e-9 * max(vol0, 1e-30)


def test_find_merge_candidates_returns_pairs_usable_by_union_find():
    """`find_merge_candidates`'s output must be directly consumable by
    `_union_find_groups` (matching tuple shape) - a thin integration
    check between the two functions the guarded repair composes."""
    npz = FIXDIR / "valve_dual.npz"
    if not npz.exists():
        pytest.skip("valve_dual.npz fixture not present")
    data = np.load(npz, allow_pickle=True)
    points = data["points"]
    offsets = data["face_offsets"]
    verts = data["face_verts"]
    owner = data["owner"]
    neigh = data["neighbour"]
    n_int = len(neigh)
    n_faces = len(offsets) - 1
    faces = [verts[offsets[i]:offsets[i + 1]].tolist() for i in range(n_faces)]
    n_cells = int(max(owner.max(), neigh.max())) + 1

    pairs, n_concave, n_unresolved = find_merge_candidates(
        points, faces, owner, neigh, n_int, n_cells,
    )
    assert n_concave > 0
    assert len(pairs) + n_unresolved == n_concave
    groups = _union_find_groups(pairs)
    assert all(len(g) >= 2 for g in groups)
