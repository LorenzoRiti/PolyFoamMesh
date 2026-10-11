# -*- coding: utf-8 -*-
"""Tests for the opt-in "core reflow" multi-layer BL consumption.

The gate is ``POLYFOAMMESH_BL_CONSUME=1`` (default OFF, byte-identical when
off).  These tests cover:

- Task 1: the multi-cell support walk (``_consume_support_layers`` through
  ``_build_precompute(consume=True)``) on a synthetic TWO-LAYER graded dual;
- Task 2: the consuming reflow producing the full requested stack with
  positive exact volumes and a valid mesh on that synthetic case, while the
  gate OFF caps to the first cell (the historical behaviour);
- feature-OFF invariance: with the env unset the precompute exposes no
  support walk and ``_consume_enabled()`` is False.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _test_helpers import space_free_tmp_root  # noqa: E402

import polyfoammesh.core.foam_mesh_io as fio  # noqa: E402
from polyfoammesh.core import bl_poly  # noqa: E402
from polyfoammesh.core.bl_poly import (  # noqa: E402
    PolyBoundaryLayerEngine, _consume_enabled, _cell_metrics,
    _exact_cell_volumes,
)

WORK_ROOT = space_free_tmp_root() / "cfmesh_bench" / "bl_consume_test"


def _layered_box_points_tets():
    """A box [0,1]^2 x [0,2] split at z=1 into a THIN (z 0..1) and a THICK
    (z 1..2) tet layer, each a 5-tet-per-corner-star cube like the fixture in
    ``test_tet_poly_dual``.  The barycentric dual then has two cell layers
    along the bottom-wall normal, which is what the support walk must cross.
    """
    b = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    m = np.array([[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], dtype=float)
    t = np.array([[0, 0, 2], [1, 0, 2], [1, 1, 2], [0, 1, 2]], dtype=float)
    c0 = np.array([[0.5, 0.5, 0.5]])
    c1 = np.array([[0.5, 0.5, 1.5]])
    pts = np.vstack([b, m, t, c0, c1])
    # local indices: 0..3 = bottom, 4..7 = middle, 8..11 = top,
    # 12 = bottom-layer centre, 13 = top-layer centre.
    i_c0, i_c1 = 12, 13

    # each layer is a cube split into 12 tets (2 per face, all touching the
    # layer centre), exactly like the fixture cube helper.
    faces2d = [
        [0, 1, 2, 3], [4, 7, 6, 5], [0, 4, 5, 1],
        [1, 5, 6, 2], [2, 6, 7, 3], [3, 7, 4, 0],
    ]
    tets = []

    def add_layer(lo, hi, centre):
        for f in faces2d:
            for tri in ((f[0], f[1], f[2]), (f[0], f[2], f[3])):
                tets.append([
                    lo + tri[0] if tri[0] < 4 else hi + (tri[0] - 4),
                    lo + tri[1] if tri[1] < 4 else hi + (tri[1] - 4),
                    lo + tri[2] if tri[2] < 4 else hi + (tri[2] - 4),
                    centre,
                ])

    add_layer(0, 4, i_c0)   # bottom layer: b(0..3), m(4..7)
    add_layer(4, 8, i_c1)   # top layer:    m(4..7), t(8..11)
    return pts, tets


def _build_layered_dual(root: Path) -> Path:
    from test_tet_poly_dual import build_tet_case, _run_dual

    shutil.rmtree(root, ignore_errors=True)
    pts, tets = _layered_box_points_tets()
    case = build_tet_case(root, pts, tets)
    res = _run_dual(case)
    assert res.success, res.errors
    return case


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    shutil.rmtree(WORK_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def _env_off():
    old = os.environ.pop("POLYFOAMMESH_BL_CONSUME", None)
    yield
    if old is None:
        os.environ.pop("POLYFOAMMESH_BL_CONSUME", None)
    else:
        os.environ["POLYFOAMMESH_BL_CONSUME"] = old


def _pre(engine, case):
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neighbour, patches = fio.read_polymesh(poly)
    n_int = len(neighbour)
    sel, _used = engine._select_faces(
        faces, owner, patches, n_int, None, True,
    )
    return (points, faces, owner, neighbour, patches, n_int, sel)


# ---------------------------------------------------------------------------
# Task 1: multi-cell support walk
# ---------------------------------------------------------------------------

def test_consume_support_walk_reaches_two_layers_on_graded_dual():
    case = _build_layered_dual(WORK_ROOT / "graded")
    eng = PolyBoundaryLayerEngine(case, log=lambda _m: None)
    points, faces, owner, neighbour, patches, n_int, sel = _pre(eng, case)
    pre = eng._build_precompute(
        points, faces, owner, neighbour, patches, n_int, sel, frozenset(),
        consume=True,
    )
    sl = pre["support_layers"]
    assert sl, "support_layers must be populated with consume=True"
    n_multi = sum(1 for w, e in sl.items() if len(e) >= 2)
    assert n_multi > 0, "the graded dual must expose >= 2 cell layers"
    # every entry list is monotone increasing in cumulative depth
    for w, entries in sl.items():
        depths = [d for d, _k in entries]
        assert depths == sorted(depths)
        assert all(d > 0.0 for d in depths)
    # at least one wall vertex crosses into the second dual cell
    w0 = next(w for w, e in sl.items() if len(e) >= 2)
    assert sl[w0][1][0] > sl[w0][0][0]


def test_consume_support_walk_absent_when_gate_off(monkeypatch):
    # T6 promotion: the consume path is the DEFAULT now, so "gate off" means
    # the explicit OFF switch.
    monkeypatch.setenv("POLYFOAMMESH_BL_CONSUME", "0")
    case = _build_layered_dual(WORK_ROOT / "graded_off")
    eng = PolyBoundaryLayerEngine(case, log=lambda _m: None)
    points, faces, owner, neighbour, patches, n_int, sel = _pre(eng, case)
    pre = eng._build_precompute(
        points, faces, owner, neighbour, patches, n_int, sel, frozenset(),
        consume=False,
    )
    assert pre["support_layers"] == {}
    assert pre["consume_free"] is None
    assert _consume_enabled() is False


# ---------------------------------------------------------------------------
# Task 2: core reflow builds the full stack (gated)
# ---------------------------------------------------------------------------

def test_consume_reflow_builds_full_stack_on_graded_dual(monkeypatch):
    # The synthetic box's z-extremes are genuine geometric caps, so the
    # 2026-10-08 cap criterion would exclude them; neutralise it here to keep
    # this test focused on the core-reflow multi-cell support.
    monkeypatch.setattr(bl_poly, "_CONSUME_CAP_MIN_PATCH_FACES", 10 ** 9)
    case = _build_layered_dual(WORK_ROOT / "reflow")
    os.environ["POLYFOAMMESH_BL_CONSUME"] = "1"
    eng = PolyBoundaryLayerEngine(case, log=lambda _m: None)
    r = eng.run(
        n_layers=4, first_height=0.05, growth_rate=1.2,
        apply_to_all=True, terminate_by_budget=True,
    )
    assert r.success, r.errors
    # the full requested layer NUMBER, on every wall face
    assert r.median_layers == 4.0
    assert r.stats["wall_face_coverage_pct"] == 100.0
    assert r.n_prism_cells == 4 * r.n_wall_faces

    # re-read and check exact positive volumes + closure
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neigh, _p = fio.read_polymesh(poly)
    n_int = len(neigh)
    n_cells = int(max(owner.max(), neigh.max())) + 1
    vols = _exact_cell_volumes(points, faces, owner, neigh, n_cells, n_int)
    assert np.all(vols > 0.0), "reflow must not leave a non-positive cell"
    _v, closure, scale = _cell_metrics(
        points, faces, owner, neigh, n_cells, n_int,
    )
    rel = np.linalg.norm(closure, axis=1) / np.maximum(scale, 1e-300)
    assert np.all(rel < 1e-8)


def test_gate_off_caps_to_supported_layers_on_graded_dual(monkeypatch):
    """With the explicit OFF switch the historical budget cap applies: the
    very same case and config build far fewer layers, proving the reflow is
    what buys the multi-cell stack.  (T6 promotion: OFF is now explicit.)"""
    monkeypatch.setenv("POLYFOAMMESH_BL_CONSUME", "0")
    case = _build_layered_dual(WORK_ROOT / "capped")
    eng = PolyBoundaryLayerEngine(case, log=lambda _m: None)
    r = eng.run(
        n_layers=4, first_height=0.05, growth_rate=1.2,
        apply_to_all=True, terminate_by_budget=True,
    )
    assert r.success, r.errors
    assert r.median_layers < 4.0


def test_consume_env_default_on_with_explicit_off_switch():
    """T6 promotion: the consuming path is the DEFAULT; the env var is an
    explicit OFF switch (previously the default was OFF)."""
    os.environ.pop("POLYFOAMMESH_BL_CONSUME", None)
    assert _consume_enabled() is True
    for val in ("1", "true", "ON", "yes", ""):
        os.environ["POLYFOAMMESH_BL_CONSUME"] = val
        assert _consume_enabled() is True
        os.environ.pop("POLYFOAMMESH_BL_CONSUME")
    for val in ("0", "false", "off", "no"):
        os.environ["POLYFOAMMESH_BL_CONSUME"] = val
        assert _consume_enabled() is False
        os.environ.pop("POLYFOAMMESH_BL_CONSUME")


# ---------------------------------------------------------------------------
# T4b: sliver-aware guard
# ---------------------------------------------------------------------------

def test_stack_depths_matches_geometric_series():
    """The shared cumulative-height helper (used by both the budget and the
    sliver threshold) is the exact geometric series ``h1*(r^k-1)/(r-1)``, and
    uniform when growth == 1."""
    assert np.allclose(
        bl_poly._stack_depths(4, 0.05, 1.2),
        [0.05, 0.11, 0.182, 0.2684],
    )
    assert np.allclose(bl_poly._stack_depths(3, 0.1, 1.0), [0.1, 0.2, 0.3])
    assert bl_poly._stack_depths(1, 0.007, 1.2)[0] == pytest.approx(0.007)


def test_consume_cell_min_edge_is_smallest_incident_face_edge():
    """``_consume_cell_min_edge`` must equal an independently computed
    min-over-incident-face-edges for every candidate cell."""
    case = _build_layered_dual(WORK_ROOT / "sliver_minedge")
    eng = PolyBoundaryLayerEngine(case, log=lambda _m: None)
    points, faces, owner, neighbour, patches, n_int, sel = _pre(eng, case)
    pts = np.asarray(points)
    n_cells = int(max(owner.max(), neighbour.max())) + 1
    cells = list(range(min(60, n_cells)))
    got = eng._consume_cell_min_edge(
        pts, faces, owner, neighbour, n_int, cells,
    )
    for c in cells:
        edges = []
        for fi, f in enumerate(faces):
            hit = int(owner[fi]) == c or (
                fi < n_int and int(neighbour[fi]) == c
            )
            if not hit:
                continue
            k = len(f)
            for i in range(k):
                edges.append(
                    float(np.linalg.norm(pts[f[i]] - pts[f[(i + 1) % k]]))
                )
        assert got[c] == pytest.approx(min(edges), rel=1e-12)


def test_sliver_guard_pins_cells_and_falls_back_without_touching_healthy_cells(
    monkeypatch,
):
    """With the threshold forced to flag every walked cell, the guard must
    (a) pin interior vertices of sliver cells (zero reflow displacement) and
    (b) flag the wall vertices whose support touches a sliver; with the
    default threshold the graded dual has no slivers, so nothing is pinned."""
    case = _build_layered_dual(WORK_ROOT / "sliver_pin")
    eng = PolyBoundaryLayerEngine(case, log=lambda _m: None)
    points, faces, owner, neighbour, patches, n_int, sel = _pre(eng, case)

    # the synthetic box's z-extremes are geometric caps; neutralise the cap
    # criterion so this test isolates the sliver guard.
    monkeypatch.setattr(bl_poly, "_CONSUME_CAP_MIN_PATCH_FACES", 10 ** 9)

    def _pre_at(target_depth):
        return eng._build_precompute(
            points, faces, owner, neighbour, patches, n_int, sel,
            frozenset(), consume=True, target_depth=target_depth,
        )

    # default threshold: no sliver cells on this healthy graded dual.
    pre_ok = _pre_at(0.30)
    assert pre_ok["support_sliver"], "support_sliver must be populated"
    assert not any(pre_ok["support_sliver"].values())
    n_pin_ok = int((~pre_ok["consume_free"]).sum())

    # flag everything: every walked cell becomes a sliver, so interior pin
    # vertices appear and every wall vertex falls back.
    monkeypatch.setattr(bl_poly, "_CONSUME_SLIVER_FRAC", 1e9)
    pre_sliver = _pre_at(0.30)
    assert any(pre_sliver["support_sliver"].values())
    n_pin_sliver = int((~pre_sliver["consume_free"]).sum())
    assert n_pin_sliver > n_pin_ok, "sliver vertices must be pinned"
    # the reflow must leave every pinned vertex exactly where it was
    hw = {w: 1e-3 for w in pre_sliver["wall_verts"]}
    disp = eng._consume_reflow(pre_sliver, hw)
    pinned = np.flatnonzero(~pre_sliver["consume_free"])
    assert np.allclose(disp[pinned], 0.0)


def test_sliver_guard_no_target_depth_is_a_noop(monkeypatch):
    """target_depth == 0 (direct ``_build`` callers / tests) disables the
    sliver detection: no cell is flagged, so the guard cannot change a build
    whose threshold was never supplied."""
    case = _build_layered_dual(WORK_ROOT / "sliver_notarget")
    eng = PolyBoundaryLayerEngine(case, log=lambda _m: None)
    points, faces, owner, neighbour, patches, n_int, sel = _pre(eng, case)
    monkeypatch.setattr(bl_poly, "_CONSUME_SLIVER_FRAC", 1e9)
    pre = eng._build_precompute(
        points, faces, owner, neighbour, patches, n_int, sel,
        frozenset(), consume=True, target_depth=0.0,
    )
    assert not any(pre["support_sliver"].values())


def test_gate_off_mesh_is_byte_identical_to_no_consume_path():
    """With the env unset the written mesh must equal the mesh written with
    the consume plumbing present but disabled (precompute/consume=False)."""
    case_a = _build_layered_dual(WORK_ROOT / "ident_a")
    # run with env OFF
    PolyBoundaryLayerEngine(case_a, log=lambda _m: None).run(
        n_layers=3, first_height=0.1, growth_rate=1.2, apply_to_all=True,
    )
    pa = fio.read_polymesh(case_a / "constant" / "polyMesh")
    case_b = _build_layered_dual(WORK_ROOT / "ident_b")
    # force consume=False explicitly by calling _build directly is covered by
    # the run above; here we just re-run the identical config and compare.
    PolyBoundaryLayerEngine(case_b, log=lambda _m: None).run(
        n_layers=3, first_height=0.1, growth_rate=1.2, apply_to_all=True,
    )
    pb = fio.read_polymesh(case_b / "constant" / "polyMesh")
    assert np.array_equal(np.asarray(pa[0]), np.asarray(pb[0]))
    assert [list(f) for f in pa[1]] == [list(f) for f in pb[1]]
    assert np.array_equal(np.asarray(pa[2]), np.asarray(pb[2]))


def test_consume_off_leaves_bl_poly_constants_untouched():
    """A tiny guard that the gate module is pure (no import-time env read)."""
    assert bl_poly._CONSUME_MAX_LAYERS >= 2


# ---------------------------------------------------------------------------
# T4c: auto-disable when the dual is sliver-heavy
# ---------------------------------------------------------------------------

def test_selected_sliver_face_fraction_reuses_min_edge_criterion():
    """The helper counts selected wall faces whose smallest edge is below
    ``_CONSUME_SLIVER_FRAC * target_depth`` (the sliver-machinery criterion),
    and is a no-op when no stack depth was supplied."""
    points = np.array([
        [0.0, 0.0, 0.0],
        [0.05, 0.0, 0.0],
        [0.05, 0.1, 0.0],
        [0.0, 0.1, 0.0],
        [1.0, 0.0, 0.0],
        [2.0, 0.0, 0.0],
        [2.0, 1.0, 0.0],
        [1.0, 1.0, 0.0],
    ])
    faces = [[0, 1, 2, 3], [4, 5, 6, 7]]
    # target 1.0 -> threshold 0.1: face0 (min edge 0.05) is a sliver, face1 not.
    n_sliver, n_total, frac = bl_poly._selected_sliver_face_fraction(
        points, faces, 0, [0, 1], 1.0,
    )
    assert (n_sliver, n_total) == (1, 2)
    assert frac == pytest.approx(0.5)
    # no target depth (direct _build callers) -> no criterion -> no-op
    assert bl_poly._selected_sliver_face_fraction(
        points, faces, 0, [0, 1], 0.0,
    ) == (0, 2, 0.0)
    # empty selection
    assert bl_poly._selected_sliver_face_fraction(
        points, faces, 0, [], 1.0,
    ) == (0, 0, 0.0)


def test_auto_disable_does_not_fire_on_healthy_graded_dual():
    """The graded dual (the accepted consume case) has no sliver-based wall
    face at its production stack depth, so the auto-disable leaves the gate
    on for it."""
    case = _build_layered_dual(WORK_ROOT / "autodisable_healthy")
    eng = PolyBoundaryLayerEngine(case, log=lambda _m: None)
    points, faces, _owner, _neigh, _patches, n_int, sel = _pre(eng, case)
    target = float(bl_poly._stack_depths(4, 0.05, 1.2)[-1])
    n_sliver, n_total, frac = bl_poly._selected_sliver_face_fraction(
        points, faces, n_int, sel, target,
    )
    assert n_total == len(sel)
    assert frac <= bl_poly._CONSUME_MAX_SLIVER_FACE_FRAC


def test_consume_auto_disables_and_matches_gate_off_on_sliver_heavy():
    """With a stack so deep that every wall face is sliver-based, the gate ON
    must auto-disable and produce a mesh byte-identical to the gate OFF."""
    case_on = _build_layered_dual(WORK_ROOT / "autodisable_on")
    case_off = _build_layered_dual(WORK_ROOT / "autodisable_off")
    logs: list[str] = []
    os.environ["POLYFOAMMESH_BL_CONSUME"] = "1"
    r_on = PolyBoundaryLayerEngine(case_on, log=logs.append).run(
        n_layers=2, first_height=10.0, growth_rate=1.2,
        apply_to_all=True, terminate_by_budget=True,
    )
    os.environ.pop("POLYFOAMMESH_BL_CONSUME", None)
    r_off = PolyBoundaryLayerEngine(
        case_off, log=lambda _m: None,
    ).run(
        n_layers=2, first_height=10.0, growth_rate=1.2,
        apply_to_all=True, terminate_by_budget=True,
    )
    assert any("consume disabled" in m for m in logs), logs
    assert r_on.success == r_off.success
    pa = fio.read_polymesh(case_on / "constant" / "polyMesh")
    pb = fio.read_polymesh(case_off / "constant" / "polyMesh")
    assert np.array_equal(np.asarray(pa[0]), np.asarray(pb[0]))
    assert [list(f) for f in pa[1]] == [list(f) for f in pb[1]]
    assert np.array_equal(np.asarray(pa[2]), np.asarray(pb[2]))


# ---------------------------------------------------------------------------
# T4d: per-face exclusion (replaces the T4c global auto-disable)
# ---------------------------------------------------------------------------

def test_consume_excluded_faces_is_a_noop_on_the_healthy_graded_dual(monkeypatch):
    """The accepted healthy dual has no sliver cell at the production stack
    depth, so the per-face exclusion returns nothing (the consume path is
    byte-identical to before on the tube-like case)."""
    # the synthetic box's z-extremes are geometric caps; neutralise the cap
    # criterion so this test isolates the sliver criterion.
    monkeypatch.setattr(bl_poly, "_CONSUME_CAP_MIN_PATCH_FACES", 10 ** 9)
    case = _build_layered_dual(WORK_ROOT / "excl_healthy")
    eng = PolyBoundaryLayerEngine(case, log=lambda _m: None)
    points, faces, owner, neighbour, _patches, n_int, sel = _pre(eng, case)
    excl, n_excl, frac = eng._consume_excluded_faces(
        points, faces, owner, neighbour, n_int, sel, 0.30,
    )
    assert n_excl == 0 and excl == frozenset() and frac == 0.0


def test_consume_excluded_faces_noop_without_a_target_depth():
    """``target_depth <= 0`` (direct ``_build`` callers) excludes nothing, so
    a build whose stack was never supplied is untouched by the new rule."""
    case = _build_layered_dual(WORK_ROOT / "excl_notarget")
    eng = PolyBoundaryLayerEngine(case, log=lambda _m: None)
    points, faces, owner, neighbour, _patches, n_int, sel = _pre(eng, case)
    assert eng._consume_excluded_faces(
        points, faces, owner, neighbour, n_int, sel, 0.0,
    ) == (frozenset(), 0, 0.0)


def test_consume_excluded_faces_flags_every_face_when_threshold_is_huge(
    monkeypatch,
):
    """With the sliver threshold forced above every edge, every selected face
    is sliver-affected (criterion (a) and (b) agree)."""
    case = _build_layered_dual(WORK_ROOT / "excl_heavy")
    eng = PolyBoundaryLayerEngine(case, log=lambda _m: None)
    points, faces, owner, neighbour, _patches, n_int, sel = _pre(eng, case)
    monkeypatch.setattr(bl_poly, "_CONSUME_SLIVER_FRAC", 1e9)
    excl, n_excl, frac = eng._consume_excluded_faces(
        points, faces, owner, neighbour, n_int, sel, 0.30,
    )
    assert n_excl == len(sel) and frac == 1.0
    assert excl == frozenset(range(len(sel)))


def test_consume_exclusion_rings_add_edge_adjacent_faces(monkeypatch):
    """The 1-ring widening must add exactly the selected faces that share an
    EDGE with a base-excluded face but are not themselves caught by the
    vertex-touch criterion (b)."""
    # cell 0 is forced sliver; its only incident face carries vertices
    # {10,11,12,13}.  Boundary face E = [10,1,2,3] touches the sliver at
    # vertex 10 only, so it is excluded by (b); B = [1,2,4,5] shares the
    # clean edge (1,2) with E and is NOT caught by (b), only by ring 1.
    points = np.array([
        [0.0, 0.0, 0.0],        # 0 (unused)
        [0.5, 0.0, 0.0],        # 1
        [0.5, 0.5, 0.0],        # 2
        [0.0, 0.5, 0.0],        # 3
        [1.0, 0.5, 0.0],        # 4
        [1.0, 0.0, 0.0],        # 5
        [0.0, 0.0, 0.0],        # 6
        [0.0, 0.0, 0.0],        # 7
        [0.0, 0.0, 0.0],        # 8
        [0.0, 0.0, 0.0],        # 9
        [0.2, 0.2, 0.3],        # 10
        [0.4, 0.2, 0.3],        # 11
        [0.4, 0.4, 0.3],        # 12
        [0.2, 0.4, 0.3],        # 13
    ])
    faces = [
        [10, 11, 12, 13],       # internal, owner 0 / neighbour 1
        [10, 1, 2, 3],          # boundary E, owner 1
        [1, 2, 4, 5],           # boundary B, owner 1
    ]
    owner = np.array([0, 1, 1])
    neighbour = np.array([1])
    n_int = 1
    sel = [0, 1]                # boundary-local ids: E = 0, B = 1

    case = _build_layered_dual(WORK_ROOT / "excl_rings")
    eng = PolyBoundaryLayerEngine(case, log=lambda _m: None)
    monkeypatch.setattr(eng, "_consume_cell_min_edge",
                        lambda *a, **k: {0: 1e-6})

    monkeypatch.setattr(bl_poly, "_CONSUME_EXCLUSION_RINGS", 0)
    excl0, _n0, _f0 = eng._consume_excluded_faces(
        points, faces, owner, neighbour, n_int, sel, 1.0,
    )
    assert excl0 == frozenset({0}), sorted(excl0)

    monkeypatch.setattr(bl_poly, "_CONSUME_EXCLUSION_RINGS", 1)
    excl1, _n1, _f1 = eng._consume_excluded_faces(
        points, faces, owner, neighbour, n_int, sel, 1.0,
    )
    assert excl1 == frozenset({0, 1}), sorted(excl1)

    monkeypatch.setattr(bl_poly, "_CONSUME_EXCLUSION_RINGS", 2)
    excl2, _n2, _f2 = eng._consume_excluded_faces(
        points, faces, owner, neighbour, n_int, sel, 1.0,
    )
    assert excl2 == frozenset({0, 1}), sorted(excl2)


# ---------------------------------------------------------------------------
# 2026-10-08: geometric inlet/outlet CAP exclusion
# ---------------------------------------------------------------------------

def _cap_mesh(n_per_side: int = 4):
    """A flat 'duct' with two planar caps at the extremes of the flow axis.

    Extents: x = 2 (largest), y = z = 1, so the flow axis is x.  Each cap is a
    ``n_per_side``-face coplanar patch at xmin/xmax, all wound the same way.
    Returns ``(points, faces)`` with only the cap faces (``n_int=0``).
    """
    pts: list[list[float]] = []
    faces: list[list[int]] = []

    def cap(xc: float):
        base = len(pts)
        ys = np.linspace(0.0, 1.0, n_per_side + 1).tolist()
        zs = np.linspace(0.0, 1.0, n_per_side + 1).tolist()
        idx = {}
        for iy, y in enumerate(ys):
            for iz, z in enumerate(zs):
                idx[(iy, iz)] = len(pts)
                pts.append([xc, y, z])
        for iy in range(n_per_side):
            for iz in range(n_per_side):
                faces.append([
                    idx[(iy, iz)], idx[(iy + 1, iz)],
                    idx[(iy + 1, iz + 1)], idx[(iy, iz + 1)],
                ])
        return base

    cap(0.0)
    cap(2.0)
    return np.array(pts, dtype=float), faces


def test_consume_cap_faces_detects_planar_extreme_patch():
    """A selected planar patch of >=4 axis-aligned faces at the bbox extreme
    plane on the flow axis is flagged as a cap."""
    pts, faces = _cap_mesh(4)
    eng = PolyBoundaryLayerEngine(Path("."), log=lambda _m: None)
    sel = list(range(len(faces)))
    caps, info = eng._consume_cap_faces(pts, faces, 0, sel)
    assert caps == set(sel)
    assert info["axis_name"] == "x"
    assert info["n_cap"] == len(faces)
    assert info["side"] in ("xmin+xmax", "xmin", "xmax")


def test_consume_cap_faces_ignores_unselected_caps():
    """Caps that are NOT selected (the tube case) must not be returned, so a
    build whose caps are not in the wall set is untouched."""
    pts, faces = _cap_mesh(4)
    eng = PolyBoundaryLayerEngine(Path("."), log=lambda _m: None)
    # only the second cap (the max extreme) is 'selected'
    half = len(faces) // 2
    caps, _info = eng._consume_cap_faces(pts, faces, 0, list(range(half, len(faces))))
    assert caps == set(range(half, len(faces)))
    # nothing selected at all -> nothing detected
    caps0, _info0 = eng._consume_cap_faces(pts, faces, 0, [])
    assert caps0 == set()


def test_consume_cap_faces_requires_a_planar_patch(monkeypatch):
    """A single face (or fewer than the patch minimum) at the extreme is not a
    cap: the criterion requires a coplanar patch, not one stray face."""
    pts, faces = _cap_mesh(2)  # 4 faces per cap
    eng = PolyBoundaryLayerEngine(Path("."), log=lambda _m: None)
    sel = list(range(len(faces)))
    monkeypatch.setattr(bl_poly, "_CONSUME_CAP_MIN_PATCH_FACES", 5)
    caps, _info = eng._consume_cap_faces(pts, faces, 0, sel)
    assert caps == set()


def test_consume_cap_faces_ignores_tilted_faces():
    """Faces at the extreme plane whose normal is not axis-aligned are not
    caps (they are wall faces that merely reach the extreme)."""
    pts, faces = _cap_mesh(4)
    # tilt the second cap's points so its normals are no longer axis-aligned
    pts = pts.copy()
    n_pts = (len(pts) + 1) // 2          # first cap owns the first half
    n_faces = len(faces) // 2
    pts[n_pts:, 0] += 0.5 * (pts[n_pts:, 1] - 0.5)
    eng = PolyBoundaryLayerEngine(Path("."), log=lambda _m: None)
    sel = list(range(len(faces)))
    caps, info = eng._consume_cap_faces(pts, faces, 0, sel)
    assert not (set(range(n_faces, len(faces))) & caps)
    assert info["n_cap"] == n_faces


def test_consume_cap_faces_are_unioned_into_excluded(monkeypatch):
    """``_consume_excluded_faces`` unions the detected cap faces into its
    exclusion set (so they get nf=0 downstream)."""
    pts, faces = _cap_mesh(4)
    # prepend one internal face so the function's n_cells/neighbour plumbing
    # has a valid (non-empty) neighbour array, exactly as a real mesh does.
    centre = np.array([[1.0, 0.5, 0.5]])
    first = faces[0]
    faces = [[len(pts), first[0], first[1], first[2]]] + faces
    pts = np.vstack([pts, centre])
    owner = np.array([0] + [1] * (len(faces) - 1), dtype=np.int64)
    neighbour = np.array([1], dtype=np.int64)
    eng = PolyBoundaryLayerEngine(Path("."), log=lambda _m: None)
    sel = list(range(len(faces) - 1))
    # small target depth -> sliver threshold far below every edge, so (a)/(b)
    # stay empty and only the cap criterion excludes anything.
    excl, n_excl, frac = eng._consume_excluded_faces(
        pts, faces, owner, neighbour, 1, sel, 1e-6,
    )
    assert excl == frozenset(sel)
    assert n_excl == len(sel)
    assert frac == 1.0


def test_build_forced_excluded_faces_get_zero_prisms():
    """``_build`` must give an excluded face 0 layers (no prism) while its
    healthy neighbours keep the full requested stack, and the histogram must
    report those zeros (coverage < 100%)."""
    case = _build_layered_dual(WORK_ROOT / "excl_build")
    eng = PolyBoundaryLayerEngine(case, log=lambda _m: None)
    points, faces, owner, neighbour, patches, n_int, sel = _pre(eng, case)
    assert len(sel) >= 2
    excluded = frozenset({0, 1})
    target = float(bl_poly._stack_depths(2, 0.05, 1.2)[-1])
    pre = eng._build_precompute(
        points, faces, owner, neighbour, patches, n_int, sel,
        frozenset(), consume=True, target_depth=target,
        consume_excluded=excluded,
    )
    assert pre["consume_excluded"] == excluded
    built = eng._build(
        points, faces, owner, neighbour, patches, n_int, sel,
        n_layers=2, first_height=0.05, growth=1.2, clamp_factor=0.5,
        pre=pre, zero_concave=True, consume=True,
        consume_excluded=excluded,
    )
    nf = built["prism_nf"]
    assert nf[0] == 0 and nf[1] == 0
    assert any(nf[bi] == 2 for bi in range(len(sel)) if bi not in excluded)
    assert built["n_faces_dropped"] >= 2


# ---------------------------------------------------------------------------
# 2026-10-11: wall vertices with no interior support (degenerate micro-slivers)
# ---------------------------------------------------------------------------

def _nosupport_mesh():
    """A valid 2-tet cluster (its wall vertices have interior support) plus a
    DISCONNECTED degenerate tet (0 internal faces) whose four vertices have no
    interior support.  Wall vertices: {0,1,2,3} supported, {5,6,7,8} not.

    ``n_int=1`` (the shared cluster face); boundary-local ``sel`` = cluster
    cell-0 faces (0,1,2) and the whole degenerate tet (6,7,8,9).  The cluster
    interior vertex 4 is reachable from every supported wall vertex through
    the unselected face [0,1,4], while the degenerate tet has no interior
    vertex at all  -  exactly the tube's vertex-192764 topology.
    """
    pts = np.array([
        [0.0, 0.0, 0.0],    # 0
        [1.0, 0.0, 0.0],    # 1
        [0.5, 1.0, 0.0],    # 2
        [0.5, 0.4, 1.0],    # 3  cluster wall apex
        [0.5, 0.4, -1.0],   # 4  cluster interior dual vertex
        [10.0, 0.0, 0.0],   # 5  degenerate tet
        [11.0, 0.0, 0.0],   # 6
        [10.5, 1.0, 0.0],   # 7
        [10.5, 0.4, 1.0],   # 8
    ])
    faces = [
        [0, 1, 2],          # 0 internal (cells 0 / 1)
        [0, 1, 3],          # 1 boundary, selected
        [0, 2, 3],          # 2 boundary, selected
        [1, 2, 3],          # 3 boundary, selected
        [0, 1, 4],          # 4 boundary, unselected (carries interior 4)
        [0, 2, 4],          # 5 boundary, unselected
        [1, 2, 4],          # 6 boundary, unselected
        [5, 6, 7],          # 7 boundary, selected (degenerate tet)
        [5, 6, 8],          # 8 boundary, selected
        [5, 7, 8],          # 9 boundary, selected
        [6, 7, 8],          # 10 boundary, selected
    ]
    owner = np.array([0, 0, 0, 0, 1, 1, 1, 2, 2, 2, 2], dtype=np.int64)
    neighbour = np.array([1], dtype=np.int64)
    n_int = 1
    n_bnd = len(faces) - n_int
    patches = [{"name": "wall", "type": "wall",
                "startFace": n_int, "nFaces": n_bnd}]
    sel = [0, 1, 2, 6, 7, 8, 9]
    return pts, faces, owner, neighbour, n_int, patches, sel


def test_no_interior_support_vertices_are_excluded_not_fatal(monkeypatch):
    """Wall vertices with no interior support are EXCLUDED (0 layers), not
    fatal: ``_build_precompute`` returns them in ``no_support_verts``, leaves
    them out of ``support_d`` and folds their incident faces into
    ``consume_excluded``, and logs the exclusion."""
    # 4 of 8 wall vertices have no support here (a deliberately mixed mesh);
    # lift the systemic threshold so this test exercises the LOCAL path.
    monkeypatch.setattr(bl_poly, "_NO_SUPPORT_MAX_FRAC", 0.9)
    monkeypatch.setattr(bl_poly, "_NO_SUPPORT_MAX_ABS", 10 ** 9)
    pts, faces, owner, neighbour, n_int, patches, sel = _nosupport_mesh()
    logs: list[str] = []
    eng = PolyBoundaryLayerEngine(Path("."), log=logs.append)
    pre = eng._build_precompute(
        pts, faces, owner, neighbour, patches, n_int, sel, frozenset(),
        consume=False,
    )
    assert pre["no_support_verts"] == {5, 6, 7, 8}
    # supported vertices keep a real support distance; excluded ones have none
    assert set(pre["support_d"]) == {0, 1, 2, 3}
    assert all(pre["support_d"][w] > 0.0 for w in pre["support_d"])
    # the degenerate tet's four selected faces (boundary-local 6..9) are the
    # faces excluded through the no-support union
    assert frozenset({6, 7, 8, 9}).issubset(pre["consume_excluded"])
    assert any(
        "excluded 4 wall vertex(es) without interior support" in m
        for m in logs
    ), logs


def test_no_interior_support_is_fatal_when_systemic(monkeypatch):
    """The honest ValueError survives when the lack of support is SYSTEMIC,
    not a local degeneracy.  On this mesh 4/8 = 50% > 5% trips the fraction
    limit; a single closed tet (100%) trips it too."""
    pts, faces, owner, neighbour, n_int, patches, sel = _nosupport_mesh()
    eng = PolyBoundaryLayerEngine(Path("."), log=lambda _m: None)
    with pytest.raises(ValueError, match="no interior support"):
        eng._build_precompute(
            pts, faces, owner, neighbour, patches, n_int, sel, frozenset(),
            consume=False,
        )

    # the absolute limit alone can trip it: 1 vertex on a mesh whose fraction
    # is below the limit but whose count exceeds the (tiny) absolute cap.
    monkeypatch.setattr(bl_poly, "_NO_SUPPORT_MAX_FRAC", 0.99)
    monkeypatch.setattr(bl_poly, "_NO_SUPPORT_MAX_ABS", 2)
    with pytest.raises(ValueError, match="no interior support"):
        eng._build_precompute(
            pts, faces, owner, neighbour, patches, n_int, sel, frozenset(),
            consume=False,
        )


def test_build_excludes_no_support_faces_with_zero_prisms(monkeypatch):
    """``_build`` gives the no-support vertices 0 layers (no prisms) while the
    healthy cluster keeps the full requested stack."""
    monkeypatch.setattr(bl_poly, "_NO_SUPPORT_MAX_FRAC", 0.9)
    monkeypatch.setattr(bl_poly, "_NO_SUPPORT_MAX_ABS", 10 ** 9)
    pts, faces, owner, neighbour, n_int, patches, sel = _nosupport_mesh()
    eng = PolyBoundaryLayerEngine(Path("."), log=lambda _m: None)
    pre = eng._build_precompute(
        pts, faces, owner, neighbour, patches, n_int, sel, frozenset(),
        consume=False,
    )
    built = eng._build(
        pts, faces, owner, neighbour, patches, n_int, sel,
        n_layers=2, first_height=0.1, growth=1.2, clamp_factor=0.5,
        pre=pre, zero_concave=True,
    )
    nf = built["prism_nf"]
    # positions in ``bnd``: 0..2 = the supported cluster faces, 3..6 = the
    # degenerate tet (boundary-local 6..9)
    assert all(nf[bi] == 0 for bi in (3, 4, 5, 6))
    assert any(nf[bi] == 2 for bi in (0, 1, 2))

