# -*- coding: utf-8 -*-
"""Repair non-star-shaped ("face pyramid") polyhedral cells by MERGING them
with an interior neighbour, instead of cutting them.

Why merge instead of cut (measured, not assumed)
-------------------------------------------------

checkMesh's "face pyramids" check (`bl_poly._pyramid_violations`) tests,
for every cell, whether every face forms a positive-volume pyramid with
the cell centre computed by OpenFOAM's own exact formula
(`bl_poly._cell_centroids`, byte-identical to
`primitiveMeshTools::makeCellCentresAndVols`). That formula is not ours to
change - it IS checkMesh's ground truth, so a cell that fails it really is
invalid, and the fix must change the cell's geometry, not the test.

Splitting the one offending face doesn't help: if the face is planar,
every sub-piece keeps the same orientation relative to the cell centre,
so the pyramid test still fails on each piece. Cutting the CELL along a
new internal plane (the classic literature approach for concave dual
cells, e.g. Lee 2015's "cut-along-concave-edge" method) requires
constructing a valid cutting plane through an irregular 4-9 sided
polyhedron - real, risky, from-scratch computational geometry.

Merging the offending cell with an interior neighbour is measured to work
in most cases (302 of 348 concave cells on the reference valve, 86.8%,
picking the first interior neighbour whose merged cell passes the
pyramid test - see notes/poly_cell_merge_reasoning.md) and is
conceptually the same operation OpenFOAM's own cell agglomeration
(multigrid) already does: remove the shared internal face(s), union the
two cells' remaining faces into one cell. No point moves, no face
geometry changes - only bookkeeping (which cell owns which face, and
renumbering after the removed face(s)).

Measured trade-off, not a free win (2026-09-13)
------------------------------------------------

Merging BEFORE the boundary layer is built measurably worse elsewhere:
on the reference valve it cut wrong-oriented faces 340 -> 61 (real
checkMesh) but nearly TRIPLED the max cell aspect ratio (6,555 -> 19,665)
and increased severely non-orthogonal faces (859 -> 1,320). Merging
AFTER the boundary layer is built (so the merge selection sees the real
prism geometry it sits next to) avoids that regression entirely on the
valve (340 -> 58 wrong-oriented, aspect ratio and non-orthogonality
UNCHANGED or slightly better) - but is NOT a universal win: on the
slot1 reference geometry, the same technique (applied post-BL, gated by
`repair_concave_cells_if_safe` below) still turned a fully "Mesh OK"
input into one with 3 new highly-skewed faces, because this module's
in-process gate cannot see checkMesh's own skewness metric closely
enough to catch that regression before it happens.

**Practical consequence**: this repair is a real, useful, geometry-
dependent tool, not a default. Always apply it AFTER the boundary layer
(not before), always confirm with a real `checkMesh` run for the actual
production case before trusting it - the in-process gate reliably
prevents PYRAMID and volume regressions, but not every quality
regression checkMesh can find.
"""

from __future__ import annotations

import numpy as np


def find_merge_candidates(points, faces, owner, neigh, n_int, n_cells):
    """Find, for every cell that fails checkMesh's face-pyramid test, an
    interior neighbour whose merge (see `merge_cell_groups`) would fix it.

    For each concave cell, its interior neighbours are tried in decreasing
    shared-face-area order; the first one whose VIRTUAL merge (computed
    without mutating anything) passes the pyramid test is kept. Measured
    on the reference valve: fixes 302 of 348 concave cells this way (see
    notes/poly_cell_merge_reasoning.md).

    Returns ``(pairs, n_concave, n_unresolved)`` - ``pairs`` is a list of
    ``(cell, neighbour)`` tuples suitable for `_union_find_groups`.
    """
    from polyfoammesh.core.bl_poly import _face_geometry, _pyramid_violations

    _bad, idx = _pyramid_violations(
        points, faces, owner, neigh, n_int, n_cells, return_idx=True,
    )
    concave_cells = sorted(
        set(int(owner[fi]) for fi in idx)
        | set(int(neigh[fi]) for fi in idx if fi < n_int)
    )

    sf, cf = _face_geometry(points, faces)
    # cell_faces is only ever looked up for concave cells and their direct
    # neighbours (a tiny fraction of n_cells on a real mesh - 0.23% on the
    # reference valve). Building it for EVERY cell scales with the WHOLE
    # mesh (O(n_faces) python dict-of-lists) and was the dominant memory
    # cost that exhausted 32GB RAM on a real ~5M-cell run. Restrict it to
    # the needed cells only, found via a vectorised numpy pass instead of
    # a per-face python loop over the full mesh.
    own_arr = np.asarray(owner)
    nb_arr = np.asarray(neigh)
    concave_arr = np.zeros(n_cells, dtype=bool)
    if concave_cells:
        concave_arr[np.asarray(concave_cells, dtype=np.int64)] = True
    touch = concave_arr[own_arr[:n_int]] | concave_arr[nb_arr]
    needed_arr = np.zeros(n_cells, dtype=bool)
    needed_arr[own_arr[:n_int][touch]] = True
    needed_arr[nb_arr[touch]] = True
    needed_arr[concave_arr] = True

    cell_faces: dict[int, list[int]] = {}
    for fi in np.flatnonzero(needed_arr[own_arr]):
        cell_faces.setdefault(int(own_arr[fi]), []).append(int(fi))
    for fi in np.flatnonzero(needed_arr[nb_arr]):
        cell_faces.setdefault(int(nb_arr[fi]), []).append(int(fi))
    for lst in cell_faces.values():
        lst.sort()

    def virtual_merge_ok(cA: int, cB: int, shared_fi: int) -> bool:
        fA = set(cell_faces[cA])
        fB = set(cell_faces[cB])
        merged = sorted((fA | fB) - {shared_fi})
        if not merged:
            return False
        m_sf, m_cf = [], []
        for fi in merged:
            if fi < n_int and (neigh[fi] == cA or neigh[fi] == cB):
                m_sf.append(-sf[fi])
            else:
                m_sf.append(sf[fi])
            m_cf.append(cf[fi])
        m_sf = np.array(m_sf)
        m_cf = np.array(m_cf)
        c_est = m_cf.mean(axis=0)
        w = (m_sf * (m_cf - c_est)).sum(axis=1)
        v3 = w.sum()
        if v3 <= 0:
            return False
        cc = (w[:, None] * (0.75 * m_cf + 0.25 * c_est)).sum(axis=0) / v3
        w2 = (m_sf * (m_cf - cc)).sum(axis=1)
        return bool(np.all(w2 > 0))

    pairs: list[tuple[int, int]] = []
    n_unresolved = 0
    for c0 in concave_cells:
        internal = [fi for fi in cell_faces[c0] if fi < n_int]
        if not internal:
            n_unresolved += 1
            continue
        face_area = np.linalg.norm(sf[internal], axis=1)
        order = np.argsort(-face_area)
        found = False
        for oi in order:
            fi = internal[oi]
            cB = int(neigh[fi]) if int(owner[fi]) == c0 else int(owner[fi])
            if virtual_merge_ok(c0, cB, fi):
                pairs.append((c0, cB))
                found = True
                break
        if not found:
            n_unresolved += 1

    return pairs, len(concave_cells), n_unresolved


def repair_concave_cells_if_safe(points, faces, owner, neigh, n_int, n_cells, patches):
    """Find and apply the merge repair, but ONLY keep it if it does not
    make the mesh worse on the metrics we can check exactly like
    checkMesh does in-process: face-pyramid violation count, skewness
    violation count, and total volume. Otherwise returns the ORIGINAL
    mesh unchanged.

    History (see docs/residual_risks.md): this gate originally only
    checked pyramid violations and volume. On the slot1 reference
    geometry, a merge that passed that narrower gate still introduced 3
    new highly-skewed faces real checkMesh flagged as a failure the
    un-merged mesh did not have — traced to a real bug in the skewness
    formula itself (`tet_poly_dual._detect_defects` summed two
    normalisation terms instead of taking OpenFOAM's own max of them,
    see notes/skewness_formula_fix_reasoning.md), now fixed and verified
    against real checkMesh (exact match on two independent meshes). The
    gate now also rejects a merge that increases the skew violation
    count. This closes the SPECIFIC gap that let the slot1 regression
    through, but is still not a formal guarantee against every possible
    quality regression checkMesh could find (e.g. aspect ratio is not
    checked here at all) — always confirm with a real `checkMesh` run
    before trusting the result in production.

    Returns ``(faces, owner, neigh, n_int, n_cells, patches, report)``
    where ``report`` is a dict with ``applied`` (bool) and the before/after
    counts, for logging.
    """
    from polyfoammesh.core.bl_poly import _cell_metrics, _pyramid_violations
    from polyfoammesh.core.tet_poly_dual import (
        _cell_centres, _detect_defects, _face_geometry,
    )

    def _skew_count(pts, fcs, own, nbr, nint, ncell):
        sf, cf = _face_geometry(pts, fcs)
        ctr, _ = _cell_centres(sf, cf, own, nbr, nint, ncell)
        _, counts = _detect_defects(pts, fcs, sf, cf, ctr, own, nbr, nint, ncell)
        return counts["skew"]

    bad0 = _pyramid_violations(points, faces, owner, neigh, n_int, n_cells)
    vol0 = float(_cell_metrics(points, faces, owner, neigh, n_cells, n_int)[0].sum())
    # 2026-09-14: now that the skewness formula bug is fixed (see
    # notes/skewness_formula_fix_reasoning.md), the guardian can finally
    # check it too - this is the exact metric that let a real regression
    # through on slot1 before the formula fix.
    skew0 = _skew_count(points, faces, owner, neigh, n_int, n_cells)

    pairs, n_concave, n_unresolved = find_merge_candidates(
        points, faces, owner, neigh, n_int, n_cells,
    )
    report = {
        "applied": False,
        "pyramid_violations_before": bad0,
        "concave_cells": n_concave,
        "candidates_found": len(pairs),
        "candidates_unresolved": n_unresolved,
    }
    if not pairs:
        report["reason"] = "no candidate merges found"
        return faces, owner, neigh, n_int, n_cells, patches, report

    groups = _union_find_groups(pairs)
    faces2, owner2, neigh2, n_int2, n_cells2, patches2 = merge_cell_groups(
        faces, owner, neigh, n_int, n_cells, patches, groups,
    )
    bad1 = _pyramid_violations(points, faces2, owner2, neigh2, n_int2, n_cells2)
    vol1 = float(_cell_metrics(points, faces2, owner2, neigh2, n_cells2, n_int2)[0].sum())
    rel_vol_err = abs(vol1 - vol0) / max(abs(vol0), 1e-30)
    skew1 = _skew_count(points, faces2, owner2, neigh2, n_int2, n_cells2)

    report["pyramid_violations_after"] = bad1
    report["volume_rel_err"] = rel_vol_err
    report["skew_violations_before"] = skew0
    report["skew_violations_after"] = skew1

    if bad1 > bad0:
        report["reason"] = f"rejected: pyramid violations increased ({bad0} -> {bad1})"
        return faces, owner, neigh, n_int, n_cells, patches, report
    if rel_vol_err > 1e-6:
        report["reason"] = f"rejected: volume not conserved (rel err {rel_vol_err:.2e})"
        return faces, owner, neigh, n_int, n_cells, patches, report
    if skew1 > skew0:
        report["reason"] = f"rejected: skewness violations increased ({skew0} -> {skew1})"
        return faces, owner, neigh, n_int, n_cells, patches, report

    report["applied"] = True
    report["reason"] = (
        f"accepted: pyramid violations {bad0} -> {bad1}, skew {skew0} -> {skew1}"
    )
    return faces2, owner2, neigh2, n_int2, n_cells2, patches2, report


def _union_find_groups(pairs: list[tuple[int, int]]) -> list[set[int]]:
    """Collapse a list of (a, b) "merge these" pairs into disjoint groups,
    handling transitive overlaps (a merges with b, b also merges with c ->
    one group {a, b, c})."""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in pairs:
        union(a, b)

    groups: dict[int, set[int]] = {}
    for x in parent:
        groups.setdefault(find(x), set()).add(x)
    return [g for g in groups.values() if len(g) >= 2]


def merge_cell_groups(
    faces: list[list[int]],
    owner: np.ndarray,
    neigh: np.ndarray,
    n_int: int,
    n_cells: int,
    patches: list[dict],
    groups: list[set[int]],
) -> tuple[list[list[int]], np.ndarray, np.ndarray, int, int, list[dict]]:
    """Merge each group of cell ids into ONE cell.

    For every group: internal faces where BOTH sides are inside the SAME
    group are dropped entirely (they become interior to the merged cell,
    no longer a face at all). Internal faces with only one side in a group
    keep existing, just relabelled to the group's new cell id (reversed
    if that flips which side is owner, to keep owner < neighbour and the
    correct face-normal convention). Boundary faces are never added,
    removed, or reordered - only their owner id changes - so every
    patch's `nFaces` is untouched and `startFace` shifts down by exactly
    the number of DROPPED internal faces (all of which precede the
    boundary block).

    Points are untouched: merging never moves a vertex, so downstream
    volume must match the pre-merge mesh to machine precision.

    Cells not mentioned in any group keep their relative order, compacted;
    each group becomes one new cell id, appended after them.

    Returns ``(faces2, owner2, neigh2, n_int2, n_cells2, patches2)``.
    """
    group_of: dict[int, int] = {}
    for gi, g in enumerate(groups):
        for c in g:
            group_of[c] = gi

    new_id: dict[int, int] = {}
    next_id = 0
    for c in range(n_cells):
        if c in group_of:
            continue
        new_id[c] = next_id
        next_id += 1
    group_new_id = {gi: next_id + gi for gi in range(len(groups))}
    next_id += len(groups)
    new_n_cells = next_id

    def map_cell(c: int) -> int:
        gi = group_of.get(c)
        return group_new_id[gi] if gi is not None else new_id[c]

    new_internal: list[tuple[list[int], int, int]] = []
    n_dropped = 0
    for fi in range(n_int):
        o, n = int(owner[fi]), int(neigh[fi])
        go, gn = group_of.get(o), group_of.get(n)
        if go is not None and go == gn:
            n_dropped += 1
            continue
        no, nn = map_cell(o), map_cell(n)
        if no == nn:
            # both sides collapsed to the same new cell via a different
            # path (shouldn't happen with disjoint groups, but stay safe)
            n_dropped += 1
            continue
        f = faces[fi]
        if no > nn:
            no, nn = nn, no
            f = list(reversed(f))
        new_internal.append((f, no, nn))

    new_boundary: list[tuple[list[int], int]] = []
    for fi in range(n_int, len(faces)):
        new_boundary.append((faces[fi], map_cell(int(owner[fi]))))

    # OpenFOAM requires internal faces in "upper triangular order": owner
    # non-decreasing, and for equal owner, neighbour non-decreasing.
    # Renumbering cells (compacting ungrouped cells, appending merged
    # groups) does not preserve the ORIGINAL faces' relative order against
    # this rule, even though each individual face still has owner <
    # neighbour - checkMesh checks the GLOBAL ordering separately
    # ("Faces not in upper triangular order"), so it must be re-sorted
    # here, not left to the caller.
    new_internal.sort(key=lambda t: (t[1], t[2]))

    new_n_int = len(new_internal)
    faces2 = [f for f, _o, _n in new_internal] + [f for f, _o in new_boundary]
    owner2 = np.array(
        [o for _f, o, _n in new_internal] + [o for _f, o in new_boundary],
        dtype=np.int64,
    )
    neigh2 = np.array([n for _f, _o, n in new_internal], dtype=np.int64)

    patches2 = []
    for p in patches:
        p2 = dict(p)
        p2["startFace"] = p["startFace"] - n_dropped
        patches2.append(p2)

    return faces2, owner2, neigh2, new_n_int, new_n_cells, patches2
