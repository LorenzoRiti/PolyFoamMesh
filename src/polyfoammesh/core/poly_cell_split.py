"""Split the L-shaped dual cells that sit on a concave feature edge.

Why (measured 2026-11-10 on 26 duals of the comparison corpus): every
residual "face pyramid" error left by the dual is a BOUNDARY face of a cell
with two boundary faces meeting at a concave edge.  The cell wraps the edge,
its centroid falls in front of the smaller wall face and checkMesh reports
the pyramid as inverted.  Moving vertices only halves the count; the surface
must stay pinned, so the wall faces cannot move either.

The fix is topological.  The two wall faces A and B share the concave edge
(p, q).  A path of existing cell edges from q back to p, kept close to the
bisector plane of the two walls, closes a cycle that cuts the cell surface
in two disks: A's side and B's side.  The cycle becomes one new internal
face and B's side becomes a new cell.  No point is added or moved, no
neighbour cell changes shape, the boundary faces are untouched.

Every split is keep-best against the caller's defect score, so the mesh can
never come out with more defects than it went in with.
"""
from __future__ import annotations

import heapq
import time
from collections.abc import Callable

import numpy as np

__all__ = ["relax_boundary_pyramids", "split_concave_boundary_cells"]


def _edges(face) -> list[tuple[int, int]]:
    m = len(face)
    return [
        (int(face[i]), int(face[(i + 1) % m])) for i in range(m)
    ]


def _key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _shared_chain(face_a, face_b) -> list[int] | None:
    """Vertices of the single edge chain shared by two faces, in order.

    None when the faces share no edge or more than one separate chain.
    """
    eb = {_key(a, b) for a, b in _edges(face_b)}
    shared = [(a, b) for a, b in _edges(face_a) if _key(a, b) in eb]
    if not shared:
        return None
    nxt = {a: b for a, b in shared}
    starts = [a for a in nxt if a not in set(nxt.values())]
    if len(starts) != 1:
        return None
    chain = [starts[0]]
    while chain[-1] in nxt:
        chain.append(nxt[chain[-1]])
        if len(chain) > len(shared) + 1:
            return None
    return chain if len(chain) == len(shared) + 1 else None


def _plan_cut(points, faces, cell_faces, fa: int, fb: int, n_int: int,
              wide: bool = False):
    """Cycle of cell vertices separating wall face ``fa`` from ``fb``.

    Returns ``(loop, cuts)``: the vertex loop (shared chain followed by the
    return path) and the internal faces the loop crosses along a diagonal,
    as ``(face, u, w)``.  None when the cell has no usable path.

    ``wide`` also allows a diagonal on every other internal face of the
    cell (at most one per face): more faces get split, but the cut can stay
    much closer to the bisector plane.
    """
    chain = _shared_chain(faces[fa], faces[fb])
    if chain is None:
        return None
    p, q = chain[0], chain[-1]
    wall_verts = set(int(v) for v in faces[fa]) | set(int(v) for v in faces[fb])
    wall_edges = {_key(a, b) for f in (fa, fb) for a, b in _edges(faces[f])}

    def unit_normal(fi):
        pts = points[list(faces[fi])]
        c = pts.mean(axis=0)
        n = np.zeros(3)
        for i in range(len(pts)):
            n += np.cross(pts[i] - c, pts[(i + 1) % len(pts)] - c)
        nn = np.linalg.norm(n)
        return n / nn if nn > 0 else n

    inward = -(unit_normal(fa) + unit_normal(fb))
    axis = points[q] - points[p]
    plane_n = np.cross(axis, inward)
    nn = float(np.linalg.norm(plane_n))
    if nn <= 0.0:
        return None
    plane_n /= nn
    scale = float(np.linalg.norm(axis)) or 1.0

    adj: dict[int, set[int]] = {}
    real: dict[int, set[int]] = {}
    # Diagonals of the internal faces at the two chain ends.  At p and q
    # every cell edge already belongs to A or B (the dual vertex has three
    # edges there), so the cut has to cross the internal face next to it.
    diagonals: dict[tuple[int, int], int] = {}
    for fi in cell_faces:
        if fi in (fa, fb):
            continue
        face = [int(v) for v in faces[fi]]
        for a, b in _edges(face):
            if _key(a, b) in wall_edges:
                continue
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
            real.setdefault(a, set()).add(b)
            real.setdefault(b, set()).add(a)
        if fi >= n_int:
            continue
        m = len(face)
        ends = [v for v in face if not (v in wall_verts and v not in (p, q))] \
            if wide else [v for v in (p, q) if v in face]
        for end in ends:
            i = face.index(end)
            for j in range(m):
                r = face[j]
                if j in (i, (i + 1) % m, (i - 1) % m):
                    continue
                if r in wall_verts and r not in (p, q):
                    continue
                if {end, r} == {p, q} or _key(end, r) in diagonals:
                    continue
                diagonals[_key(end, r)] = fi
                adj.setdefault(end, set()).add(r)
                adj.setdefault(r, set()).add(end)

    def off_plane(v: int) -> float:
        return abs(float((points[v] - points[p]) @ plane_n))

    best = {q: 0.0}
    prev: dict[int, int] = {}
    heap = [(0.0, q)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == p:
            break
        if d > best.get(u, np.inf):
            continue
        for w in adj.get(u, ()):
            # the cycle may touch the two wall faces only along the chain
            if w != p and w in wall_verts:
                continue
            length = float(np.linalg.norm(points[w] - points[u]))
            cost = d + length * (
                1.0 + 4.0 * 0.5 * (off_plane(u) + off_plane(w)) / scale
            )
            if cost < best.get(w, np.inf):
                best[w] = cost
                prev[w] = u
                heapq.heappush(heap, (cost, w))
    if p not in prev:
        return None
    path = [p]
    while path[-1] != q:
        path.append(prev[path[-1]])
    path.reverse()                      # q ... p
    loop = chain + path[1:-1]           # p..q then back towards p
    if len(loop) < 3 or len(set(loop)) != len(loop):
        return None
    cuts = []
    for u, w in zip(path, path[1:]):
        fi = diagonals.get(_key(u, w))
        # a real cell edge wins over a diagonal with the same end points
        if fi is not None and w not in real.get(u, ()):
            cuts.append((fi, u, w))
    if len({c[0] for c in cuts}) != len(cuts):
        return None
    return loop, cuts


def _closed(face_loops) -> bool:
    """True when the faces bound one closed cell: every edge is used once
    in each direction (loops given with their outward orientation)."""
    seen: set[tuple[int, int]] = set()
    for loop in face_loops:
        for a, b in _edges(loop):
            if a == b or (a, b) in seen:
                return False
            seen.add((a, b))
    return bool(seen) and all((b, a) in seen for a, b in seen)


def _cell_loops(faces, owner, neigh, n_int: int, cell: int, ids) -> list:
    """Faces of ``cell`` among ``ids``, each oriented outwards."""
    out = []
    for fi in ids:
        if int(owner[fi]) == cell:
            out.append(list(faces[fi]))
        elif fi < n_int and int(neigh[fi]) == cell:
            out.append(list(reversed(faces[fi])))
    return out


def _side_of(faces, cell_faces, loop, start: int) -> set[int]:
    """Cell faces reachable from ``start`` without crossing the loop."""
    cut = {_key(a, b) for a, b in _edges(loop)}
    by_edge: dict[tuple[int, int], list[int]] = {}
    for fi in cell_faces:
        for a, b in _edges(faces[fi]):
            by_edge.setdefault(_key(a, b), []).append(fi)
    seen = {start}
    stack = [start]
    while stack:
        fi = stack.pop()
        for a, b in _edges(faces[fi]):
            k = _key(a, b)
            if k in cut:
                continue
            for fj in by_edge.get(k, ()):
                if fj not in seen:
                    seen.add(fj)
                    stack.append(fj)
    return seen


def split_concave_boundary_cells(
    points: np.ndarray,
    faces: list,
    owner: np.ndarray,
    neigh: np.ndarray,
    n_int: int,
    n_cells: int,
    patches: list[dict],
    score: Callable,
    max_rounds: int = 3,
    max_splits: int = 400,
    log: Callable[[str], None] | None = None,
):
    """Split concave-edge cells whose wall face fails the pyramid test.

    ``score(points, faces, owner, neigh, n_int, n_cells)`` must return
    ``(total_defects, min_cell_volume, bad_boundary_faces, sf, cf, ctr,
    vol)``: the third item lists the wall face ids to repair (pyramid
    failures, optionally boundary-skew failures: the same cell shape seen
    by the other criterion), the rest is the geometry the count was
    measured on.

    Returns ``(faces, owner, neigh, n_int, n_cells, patches, report)``.  The
    inputs are not modified; when nothing is accepted the same objects come
    back and ``report["splits"]`` is 0.
    """
    say = log or (lambda _m: None)
    faces = [list(f) for f in faces]
    owner = np.array(owner, dtype=np.int64)
    neigh = np.array(neigh, dtype=np.int64)
    patches = [dict(p) for p in patches]
    total, _vol, bad = score(points, faces, owner, neigh, n_int, n_cells)[:3]
    report = {"defects_before": int(total), "splits": 0, "tried": 0,
              "refused": 0, "no_path": 0}
    refused: set[tuple[int, ...]] = set()

    for _round in range(max_rounds):
        accepted_round = 0
        for fa_face in [list(faces[int(f)]) for f in bad]:
            if report["splits"] >= max_splits:
                break
            # the face list shifts by one at every accepted split
            fa = next(
                (fi for fi in range(n_int, len(faces)) if faces[fi] == fa_face),
                None,
            )
            if fa is None or tuple(fa_face) in refused:
                continue
            c = int(owner[fa])
            cell_faces = (
                [int(i) for i in np.where(owner == c)[0]]
                + [int(i) for i in np.where(neigh == c)[0]]
            )
            mates = [fi for fi in cell_faces if fi >= n_int and fi != fa]
            mates.sort(key=lambda fi: -len(set(faces[fi]) & set(faces[fa])))
            plans = []
            for wide in (False, True):
                for cand in mates:
                    plan = _plan_cut(
                        points, faces, cell_faces, fa, cand, n_int, wide=wide,
                    )
                    if plan is not None and (cand, plan) not in plans:
                        plans.append((cand, plan))
            if not plans:
                report["no_path"] += 1
                refused.add(tuple(fa_face))
                continue
            report["tried"] += 1
            done = False
            for fb, plan in plans:
                if done:
                    break
                loop, cuts = plan
                new_faces = list(faces)
                new_owner = owner.copy()
                new_neigh = neigh.copy()
                ni = n_int
                # split the crossed internal faces along their diagonal: both
                # halves keep the owner/neighbour of the parent for now
                for fx, u, w in cuts:
                    face = [int(v) for v in new_faces[fx]]
                    i, j = sorted((face.index(u), face.index(w)))
                    new_faces[fx] = face[i:j + 1]
                    new_faces.insert(ni, face[j:] + face[:i + 1])
                    new_owner = np.insert(new_owner, ni, new_owner[fx])
                    new_neigh = np.insert(new_neigh, ni, new_neigh[fx])
                    ni += 1
                shift = ni - n_int
                fa_c, fb_c = fa + shift, fb + shift
                cell_c = (
                    [int(i) for i in np.where(new_owner == c)[0]]
                    + [int(i) for i in np.where(new_neigh == c)[0]]
                )
                side_a = _side_of(new_faces, cell_c, loop, fa_c)
                if fb_c in side_a or len(side_a) < 3 or len(cell_c) - len(side_a) < 3:
                    continue
                new_cell = n_cells
                for fi in cell_c:
                    if fi in side_a:
                        continue
                    if new_owner[fi] == c:
                        if fi < ni:
                            # keep owner < neighbour: the other cell owns it now
                            new_owner[fi] = new_neigh[fi]
                            new_neigh[fi] = new_cell
                            new_faces[fi] = list(reversed(new_faces[fi]))
                        else:
                            new_owner[fi] = new_cell
                    else:
                        new_neigh[fi] = new_cell
                # new internal face, owned by the old cell, pointing at the new one
                a_centre = np.mean(
                    [points[list(new_faces[fi])].mean(axis=0) for fi in side_a],
                    axis=0,
                )
                lp = points[loop]
                lc = lp.mean(axis=0)
                nrm = np.zeros(3)
                for i in range(len(lp)):
                    nrm += np.cross(lp[i] - lc, lp[(i + 1) % len(lp)] - lc)
                if float(nrm @ (lc - a_centre)) < 0.0:
                    loop = list(reversed(loop))
                new_faces.insert(ni, [int(v) for v in loop])
                new_owner = np.insert(new_owner, ni, c)
                new_neigh = np.insert(new_neigh, ni, new_cell)
                ni += 1
                # both halves must be closed cells, whatever the detector
                # says: a cut through a pinched cell surface leaves a hole
                touched = [fi + 1 if fi >= ni - 1 else fi for fi in cell_c]
                touched.append(ni - 1)
                if not all(
                    _closed(_cell_loops(
                        new_faces, new_owner, new_neigh, ni, cell, touched,
                    ))
                    for cell in (c, new_cell)
                ):
                    report["open_cut"] = report.get("open_cut", 0) + 1
                    continue

                t2, vmin = score(
                    points, new_faces, new_owner, new_neigh, ni, n_cells + 1,
                )[:2]
                if vmin > 0.0 and t2 < total:
                    faces, owner, neigh = new_faces, new_owner, new_neigh
                    for pch in patches:
                        pch["startFace"] = int(pch["startFace"]) + ni - n_int
                    n_int = ni
                    n_cells += 1
                    total = t2
                    report["splits"] += 1
                    accepted_round += 1
                    done = True
            if not done:
                report["refused"] += 1
                refused.add(tuple(fa_face))
        if not accepted_round:
            break
        total, _vol, bad = score(points, faces, owner, neigh, n_int, n_cells)[:3]
        if not len(bad):
            break

    if report["splits"]:
        # OpenFOAM wants the internal faces in upper-triangular order
        order = np.lexsort((neigh[:n_int], owner[:n_int]))
        faces = [faces[int(i)] for i in order] + faces[n_int:]
        owner = np.concatenate([owner[:n_int][order], owner[n_int:]])
        neigh = neigh[:n_int][order]
        say(
            f"[poly 7/9] concave-edge cell split: {report['splits']} cell(s) "
            f"split, defects {report['defects_before']} -> {total}"
        )
    report["defects_after"] = int(total)
    return faces, owner, neigh, n_int, n_cells, patches, report


def _face_area_vector(points, face):
    """Area vector of one polygon (centre-point decomposition)."""
    p = points[list(face)]
    c0 = p.mean(axis=0)
    a = p - c0
    return 0.5 * np.cross(a, np.roll(a, -1, axis=0)).sum(axis=0)


def relax_boundary_pyramids(
    points: np.ndarray,
    faces: list,
    owner: np.ndarray,
    neigh: np.ndarray,
    n_int: int,
    n_cells: int,
    score: Callable,
    max_rounds: int = 3,
    max_seconds: float = 120.0,
    log: Callable[[str], None] | None = None,
    incremental: Callable | None = None,
):
    """Clear wall-face pyramid failures by moving INTERIOR vertices only.

    The wall stays where the surface snap put it: boundary vertices are
    never moved.  For each failing wall face the interior vertices of its
    cell are pushed away from the face plane, which carries the cell
    centroid back behind it.  Three moves are tried, cheapest first: a
    rigid shift along the face normal, a shift along the bisector of the
    cell's wall faces, and a hill-climb that also drags the neighbouring
    cells' vertices at half weight so they are not crushed.

    Keep-best on the caller's defect score (same contract as
    :func:`split_concave_boundary_cells`).  Returns ``(points, report)``;
    ``points`` is the input array when nothing was accepted.

    ``incremental`` (optional): factory ``(points) -> scorer`` with
    ``trial(pts, moved) -> (total, vmin)``, ``face_pv(fi, cell)`` and
    ``accept()``, counting the same defects as ``score``.  Each candidate
    move is then evaluated on its own neighbourhood instead of the whole
    mesh (DN1000: 945 full re-detections were 146 s of a 188 s dual).
    """
    say = log or (lambda _m: None)
    t_start = time.monotonic()
    owner = np.asarray(owner)
    neigh = np.asarray(neigh)
    on_wall = np.zeros(len(points), dtype=bool)
    for f in faces[n_int:]:
        on_wall[list(f)] = True
    cell_faces: dict[int, list[int]] = {}

    def faces_of(c: int) -> list[int]:
        if c not in cell_faces:
            cell_faces[c] = (
                [int(i) for i in np.where(owner == c)[0]]
                + [int(i) for i in np.where(neigh == c)[0]]
            )
        return cell_faces[c]

    def verts_of(c: int) -> set[int]:
        return {int(v) for fj in faces_of(c) for v in faces[fj]}

    def evaluate(pts):
        return score(pts, faces, owner, neigh, n_int, n_cells)

    best, _vmin, bad, sf, cf, ctr, vol = evaluate(points)
    report = {"defects_before": int(best), "cleared": 0, "stuck": 0}
    pts = points
    inc = incremental(points) if incremental is not None else None
    if inc is not None:
        # live views: accept() updates these arrays in place
        sf, cf, ctr, vol = inc.sf, inc.cf, inc.ctr, inc.vol

    def try_move(cand, moved):
        """(total, vmin, pv(fi, c) or None, full-state or None)"""
        if inc is not None:
            tot, vmin = inc.trial(cand, moved)
            return tot, vmin, None
        tot, vmin, _b, sf1, cf1, ctr1, vol1 = evaluate(cand)
        return tot, vmin, (sf1, cf1, ctr1, vol1)

    def pv_of(full, fi, c):
        if full is None:
            return inc.face_pv(fi, c)
        sf1, cf1, ctr1, _v = full
        return float(sf1[fi] @ (cf1[fi] - ctr1[c]))
    for _round in range(max_rounds):
        progressed = False
        for fi in [int(f) for f in bad]:
            if time.monotonic() - t_start > max_seconds:
                break
            c = int(owner[fi])
            size = float(vol[c]) ** (1.0 / 3.0)
            area = float(np.linalg.norm(sf[fi]))
            if size <= 0.0 or area <= 0.0:
                continue
            n = sf[fi] / area
            pyramid_ok = float(sf[fi] @ (cf[fi] - ctr[c])) > 0.0
            core = verts_of(c)
            inner = np.array(
                sorted(v for v in core if not on_wall[v]), dtype=np.int64,
            )
            if not len(inner):
                report["stuck"] += 1
                continue
            mates = [fj for fj in faces_of(c) if fj >= n_int and fj != fi]
            diag = n + sum(
                (sf[fj] / max(float(np.linalg.norm(sf[fj])), 1e-300)
                 for fj in mates), np.zeros(3),
            )
            dn = float(np.linalg.norm(diag))
            moves = [n] + ([diag / dn] if mates and dn > 1e-9 else [])
            if pyramid_ok:
                # skew face: bring the cell centre sideways under the face
                off = cf[fi] - ctr[c]
                off = off - n * float(off @ n)
                on = float(np.linalg.norm(off))
                if on <= 1e-12 * size:
                    continue
                moves = [-off / on]
            done = False
            for direction in moves:
                for t in (0.05, 0.1, 0.2, 0.35):
                    cand = pts.copy()
                    cand[inner] -= direction * (t * size)
                    tot, vmin, full = try_move(cand, inner)
                    if vmin > 0.0 and tot < best:
                        pts, best = cand, tot
                        if full is None:
                            inc.accept()
                        else:
                            sf, cf, ctr, vol = full
                        done = True
                        break
                if done:
                    break
            if not done and not pyramid_ok:
                # hill-climb: neighbours follow at half weight
                ring: set[int] = set()
                for fj in faces_of(c):
                    if fj < n_int:
                        other = int(neigh[fj]) if int(owner[fj]) == c else int(owner[fj])
                        ring |= verts_of(other)
                w = np.zeros(len(pts))
                w[list(ring)] = 0.5
                w[list(core)] = 1.0
                w[on_wall] = 0.0
                moved = np.nonzero(w)[0]
                state = (pts, best, (sf, cf, ctr, vol) if inc is None else None)
                cur_pv = float(sf[fi] @ (cf[fi] - ctr[c])) if inc is None                     else inc.face_pv(fi, c)
                for _step in range(10):
                    s_pts, s_tot = state[0], state[1]
                    s_sf = _face_area_vector(s_pts, faces[fi])
                    nn = s_sf / max(float(np.linalg.norm(s_sf)), 1e-300)
                    stepped = False
                    for t in (0.08, 0.04, 0.02):
                        cand = s_pts - np.outer(w, nn) * (t * size)
                        tot, vmin, full = try_move(cand, moved)
                        pv1 = pv_of(full, fi, c)
                        if vmin > 0.0 and tot <= s_tot and pv1 > cur_pv:
                            if full is None:
                                inc.accept()
                            state = (cand, tot, full)
                            cur_pv = pv1
                            stepped = True
                            break
                    if not stepped:
                        break
                    if state[1] < best:
                        pts, best = state[0], state[1]
                        if state[2] is not None:
                            sf, cf, ctr, vol = state[2]
                        done = True
                        break
                if not done and inc is not None and state[0] is not pts:
                    # climb accepted steps that did not pay off: roll back
                    inc = incremental(pts)
                    sf, cf, ctr, vol = inc.sf, inc.cf, inc.ctr, inc.vol
            if done:
                report["cleared"] += 1
                progressed = True
            else:
                report["stuck"] += 1
        if not progressed or time.monotonic() - t_start > max_seconds:
            break
        best, _vmin, bad, sf, cf, ctr, vol = evaluate(pts)
        if inc is not None:
            inc = incremental(pts)
            sf, cf, ctr, vol = inc.sf, inc.cf, inc.ctr, inc.vol
        if not len(bad):
            break
    report["defects_after"] = int(best)
    if report["cleared"]:
        say(
            f"[poly 7/9] interior relaxation at wall pyramids: defects "
            f"{report['defects_before']} -> {best}"
        )
    return pts, report
