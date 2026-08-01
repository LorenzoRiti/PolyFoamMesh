"""Tetrahedral -> polyhedral conversion by barycentric (median) dual rebuild.

Why this exists
---------------
`terminal_face.py` converts by *merging adjacent tetrahedra*. Three variants of
that idea were measured (mutual agreement, disjoint-pair matching, guarded
leftover sweeps) and all hit the same wall: coverage above ~80% could only be
bought with proportionally worse cell shapes, because "these two tets share a
big face" says nothing about "the merged cell would be well shaped".

This module does not merge tets at all. It builds the **dual complex** of the
tetrahedral mesh:

    one output cell per primal VERTEX
    one internal output face per primal EDGE
    boundary output faces = subdivisions of the original boundary triangles

Every input tet is consumed, so coverage is 100% by construction — there is no
"residual tet" concept at all. Cell count drops by ~6x (a tet mesh has roughly
six tets per vertex), which is the usual and desirable tet->poly reduction.

The boundary problem, and why it is not a problem here
-----------------------------------------------------
`docs/poly_workflow_part2.md` treats "clip the dual cell against the CAD
surface" as the hard, possibly out-of-reach step. That difficulty is specific
to a **Voronoi / circumcentric** dual, whose cells genuinely stick out through
the boundary and have to be cut back against the geometry.

The **median (barycentric)** dual has no such step. Its dual vertices are tet
centroids in the interior, and on the boundary it uses points that already lie
exactly on the primal surface: the primal boundary vertex itself, boundary-edge
midpoints and boundary-triangle centroids. Each primal boundary triangle
(a, b, c) therefore splits into exactly three planar quads

    [a, mid(ab), centroid(abc), mid(ca)]   -> dual cell of a
    [b, mid(bc), centroid(abc), mid(ab)]   -> dual cell of b
    [c, mid(ca), centroid(abc), mid(bc)]   -> dual cell of c

which tile the original triangle exactly. The output boundary is *geometrically
identical* to the input boundary, patch for patch, with no clipping, no CAD
queries and no tolerance tuning. Sharp features survive because the original
surface points are still mesh points.

Topology
--------
Interior primal edge (a, b): the tets around it form a closed ring; the dual
face runs through their centroids (and, with `median_faces`, through the
centroids of the primal faces between them) in rotational order.

Boundary primal edge (a, b): the tets form an open fan terminated by two
boundary triangles, so the dual face closes back through the surface via those
two triangle centroids and the edge midpoint. That is what keeps the boundary
dual cells watertight.

A dual edge (dual to a primal triangle abc) is shared by the three dual faces
of edges ab, bc and ca — but only two of those touch any one dual cell, so
every cell's surface is a closed 2-manifold.

Orientation is geometric and unambiguous (unlike in the merge approach): the
dual face for edge (a, b) separates exactly the dual cells of a and b, so its
normal is required to point from a towards b. Boundary quads inherit the
winding of their parent primal triangle, which gmshToFoam already wrote
pointing out of the domain.

Feature splitting
-----------------
One measured failure mode remains on real CAD: where a primal boundary vertex
sits on a *concave* feature edge, its dual cell wraps around the feature and is
genuinely non-convex, so the cell's own centroid can fall outside one of its
boundary quads — checkMesh reports an inverted face pyramid. Measured on the
valve part, the cells that fail have boundary-face normals spanning 90-134
degrees, against 1.1 degrees for a typical boundary cell, and their volumes are
completely normal — so this is a feature-geometry effect, not a sliver effect.

The fix is to give such a vertex more than one dual cell: its star of tets is
partitioned into groups (one per smooth surface region meeting at the vertex),
and each group becomes its own cell. The extra internal faces this needs are
again exact barycentric corners, this time of the interior primal faces the cut
passes through, so the construction stays watertight by the same argument.

This is applied strictly *defect-driven*: a vertex is only split if its cell
currently fails a check, so the 99.8% of the mesh that is already clean is
never touched, and a round is kept only if the total defect count went down.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import numpy as np

from .terminal_face import TerminalFaceConverter

logger = logging.getLogger(__name__)


@dataclass
class DualPolyResult:
    success: bool = False
    n_tets_before: int = 0
    n_cells_after: int = 0
    n_poly_cells: int = 0
    n_residual_tets: int = 0
    n_internal_faces: int = 0
    n_boundary_faces: int = 0
    n_points_after: int = 0
    min_cell_volume: float = 0.0
    max_closure_error: float = 0.0
    volume_before: float = 0.0
    volume_after: float = 0.0
    split_vertices: int = 0
    residual_defects: int = 0
    defect_breakdown: dict[str, int] = field(default_factory=dict)
    stage_times: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    # Aliases so the GUI's existing poly-conversion result handlers, written
    # against TerminalFaceResult, work with this result type unchanged.
    @property
    def n_polyhedra(self) -> int:
        return self.n_poly_cells

    @property
    def wall_time_s(self) -> float:
        return float(self.stage_times.get("total", 0.0))

    @property
    def poly_fraction(self) -> float:
        if not self.n_cells_after:
            return 0.0
        return self.n_poly_cells / self.n_cells_after


class TetPolyDualConverter:
    """Rebuild a tetrahedral OpenFOAM case as its barycentric dual polyhedral mesh.

    Usage::

        conv = TetPolyDualConverter(case_dir, log=print)
        result = conv.run()
    """

    def __init__(
        self,
        case_dir: Path,
        log: Callable[[str], None] | None = None,
        cancel: Callable[[], bool] | None = None,
        median_faces: bool = False,
        split_rounds: int = 0,
        feature_angle: float = 40.0,
    ):
        """
        median_faces
            False (default): an internal dual face runs through the tet
                  centroids only. True: it also passes through the primal-face
                  centroids between them, which is the exact median-dual
                  surface but doubles the vertices per face.
        split_rounds
            How many defect-driven feature-splitting passes to attempt.
            Default 0 = off. MEASURED on the valve part (824,661 tets): the
            split is implemented and correct, but it does not pay. Round 0
            (no splitting) gives 895 inverted pyramids; splitting the 352
            offending feature vertices gives 1,215, and a second round 1,218.
            Splitting a wrapped cell into wedges trades one non-convex cell
            for several thin ones that invert in their own right. The run
            loop keeps whichever round scored best, so enabling this can
            never make the written mesh worse — it just costs ~20s per extra
            round for nothing. Left in, off, and measured rather than
            deleted: it is the natural first thing to try again, and this is
            the number to beat.
        feature_angle
            Dihedral angle (degrees) above which two boundary triangles meeting
            at a vertex count as belonging to different smooth surface regions.
        """
        self._case_dir = Path(case_dir).resolve()
        self._log_cb = log
        self._cancel_cb = cancel
        self._median_faces = bool(median_faces)
        self._split_rounds = int(split_rounds)
        self._feature_angle = float(feature_angle)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        logger.info(msg)
        if self._log_cb is not None:
            try:
                self._log_cb(msg)
            except Exception:  # pragma: no cover - logging must never break a run
                pass

    def _check_cancel(self) -> None:
        if self._cancel_cb is not None and self._cancel_cb():
            raise RuntimeError("poly dual conversion cancelled")

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def run(self) -> DualPolyResult:
        t_start = time.monotonic()
        res = DualPolyResult()
        try:
            self._run_inner(res)
            res.success = True
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            logger.exception("Dual poly conversion failed")
            res.errors.append(str(exc))
        res.stage_times["total"] = round(time.monotonic() - t_start, 2)
        return res

    # ------------------------------------------------------------------

    def _run_inner(self, res: DualPolyResult) -> None:
        t = time.monotonic()
        P = self._read_primal(res)
        res.stage_times["read"] = round(time.monotonic() - t, 2)
        self._check_cancel()

        splits: dict[int, dict[int, int]] = {}
        best = None
        best_defects: int | None = None
        best_counts: dict[str, int] = {}

        for rnd in range(self._split_rounds + 1):
            self._check_cancel()
            t = time.monotonic()
            M = self._build_dual(P, splits)
            build_s = time.monotonic() - t
            self._log(
                f"[poly 5/9] round {rnd}: {M.n_cells:,} dual cells, {M.n_int:,} "
                f"internal + {len(M.faces) - M.n_int:,} boundary faces, "
                f"{len(M.points):,} points ({build_s:.1f}s)"
            )
            self._check_cancel()

            t = time.monotonic()
            sf, cf = _face_geometry(M.points, M.faces)
            ctr, vol = _cell_centres(sf, cf, M.owner, M.neigh, M.n_int, M.n_cells)
            bad, counts = _detect_defects(
                M.points, M.faces, sf, cf, ctr, M.owner, M.neigh, M.n_int, M.n_cells,
            )
            total = counts["pyramid"] + counts["non_ortho"] + counts["skew"]
            self._log(
                f"[poly 6/9] round {rnd} quality: {counts['pyramid']} inverted face "
                f"pyramids, {counts['non_ortho']} non-orthogonality errors, "
                f"{counts['skew']} skewness errors "
                f"({int(bad.sum()):,} cells, {100.0 * bad.sum() / M.n_cells:.3f}%) "
                f"[{time.monotonic() - t:.1f}s]"
            )
            if best_defects is None or total < best_defects:
                best_defects, best, best_counts = total, M, counts
            if total == 0:
                break
            if rnd == self._split_rounds:
                break

            new = self._plan_splits(P, M, bad, splits)
            if not new:
                self._log(
                    "[poly 6/9] no further feature split available — the remaining "
                    "defects are not at multi-region boundary vertices"
                )
                break
            splits.update(new)
            self._log(
                f"[poly 6/9] splitting the dual cell at {len(new):,} feature "
                f"vertices ({len(splits):,} total) and rebuilding"
            )

        M = best
        res.split_vertices = len(splits)
        res.residual_defects = int(best_defects or 0)
        res.defect_breakdown = dict(best_counts)
        res.n_internal_faces = M.n_int
        res.n_boundary_faces = len(M.faces) - M.n_int

        t = time.monotonic()
        self._validate(res, M, P)
        res.stage_times["validate"] = round(time.monotonic() - t, 2)
        self._check_cancel()

        t = time.monotonic()
        self._write(M.points, M.faces, M.owner, M.neigh, M.patches)
        res.stage_times["write"] = round(time.monotonic() - t, 2)

        res.n_cells_after = M.n_cells
        res.n_poly_cells = M.n_cells
        res.n_residual_tets = 0
        res.n_points_after = len(M.points)
        self._log(
            f"[poly 9/9] wrote {M.n_cells:,} polyhedral cells, {len(M.faces):,} "
            f"faces, {len(M.points):,} points — residual tetrahedra: 0"
        )

    # ------------------------------------------------------------------
    # P1-P4: read the tet mesh and precompute everything the dual needs
    # ------------------------------------------------------------------

    def _read_primal(self, res: DualPolyResult) -> SimpleNamespace:
        poly_dir = self._case_dir / "constant" / "polyMesh"
        points = TerminalFaceConverter._read_points(poly_dir / "points")
        faces_raw = TerminalFaceConverter._read_faces(poly_dir / "faces")
        owner = TerminalFaceConverter._read_label_list(poly_dir / "owner").astype(np.int64)
        neighbour = TerminalFaceConverter._read_label_list(
            poly_dir / "neighbour"
        ).astype(np.int64)
        patches = TerminalFaceConverter._read_boundary(poly_dir / "boundary")

        n_faces = len(faces_raw)
        if n_faces == 0:
            raise RuntimeError(
                "No faces found in polyMesh — re-run gmshToFoam to regenerate "
                "the tet mesh."
            )
        if len(owner) != n_faces:
            raise RuntimeError(f"owner has {len(owner)} entries but faces has {n_faces}")

        widths = np.fromiter((len(f) for f in faces_raw), dtype=np.int64, count=n_faces)
        if not np.all(widths == 3):
            bad = int(np.count_nonzero(widths != 3))
            raise RuntimeError(
                f"Dual conversion requires a pure tetrahedral mesh; {bad}/{n_faces} "
                "faces are not triangles (boundary layers / prisms / an already-"
                "converted polyhedral mesh)."
            )
        tri = np.array([f for f in faces_raw], dtype=np.int64)
        del faces_raw

        neigh = np.full(n_faces, -1, dtype=np.int64)
        neigh[: len(neighbour)] = neighbour
        n_int_primal = int(len(neighbour))
        n_tets = int(max(owner.max(), neigh.max())) + 1
        res.n_tets_before = n_tets

        face_count = np.zeros(n_tets, dtype=np.int64)
        np.add.at(face_count, owner, 1)
        np.add.at(face_count, neigh[neigh >= 0], 1)
        if not np.all(face_count == 4):
            bad = int(np.count_nonzero(face_count != 4))
            raise RuntimeError(
                f"Dual conversion requires a pure tetrahedral volume; {bad}/{n_tets} "
                "cells do not have exactly 4 faces."
            )

        n_pi = len(points)
        bnd_face_ids = np.arange(n_int_primal, n_faces, dtype=np.int64)
        n_bnd = len(bnd_face_ids)
        if n_bnd == 0:
            raise RuntimeError("Mesh has no boundary faces — refusing to convert.")
        self._log(
            f"[poly 1/9] input: {n_tets:,} tetrahedra, {n_faces:,} faces "
            f"({n_bnd:,} boundary), {n_pi:,} points"
        )

        # Each tet vertex lies on 3 of the tet's 4 faces, so summing the
        # coordinates of every face vertex counts each vertex three times:
        # centroid = (sum over the cell's faces of their vertex coords) / 12.
        face_coord_sum = points[tri].sum(axis=1)
        acc = np.zeros((n_tets, 3), dtype=np.float64)
        np.add.at(acc, owner, face_coord_sum)
        has_nb = neigh >= 0
        np.add.at(acc, neigh[has_nb], face_coord_sum[has_nb])
        cell_centroid = acc / 12.0
        face_centroid = face_coord_sum / 3.0
        del acc, face_coord_sum

        used = np.zeros(n_pi, dtype=bool)
        used[np.unique(tri)] = True
        n_used = int(used.sum())
        if n_used != n_pi:
            self._log(f"[poly 2/9] {n_pi - n_used:,} unused input points ignored")

        # unique primal edges, and the faces incident on each
        e_a = np.concatenate([tri[:, 0], tri[:, 1], tri[:, 2]])
        e_b = np.concatenate([tri[:, 1], tri[:, 2], tri[:, 0]])
        e_key = np.minimum(e_a, e_b) * np.int64(n_pi) + np.maximum(e_a, e_b)
        e_face = np.tile(np.arange(n_faces, dtype=np.int64), 3)
        order = np.argsort(e_key, kind="stable")
        e_key_s = e_key[order]
        e_face_s = e_face[order]
        starts = np.flatnonzero(np.r_[True, e_key_s[1:] != e_key_s[:-1]])
        ends = np.r_[starts[1:], len(e_key_s)]
        n_edges = len(starts)
        uniq_key = e_key_s[starts]
        edge_mid = 0.5 * (points[uniq_key // n_pi] + points[uniq_key % n_pi])
        # edge index of each (face, corner) pair: column j is edge (v_j, v_j+1)
        face_edge = np.searchsorted(uniq_key, e_key.reshape(3, n_faces).T)
        self._log(
            f"[poly 3/9] primal edges: {n_edges:,} (one internal dual face each); "
            f"dual cells: {n_used:,} (one per primal vertex)"
        )

        # vertex -> incident primal faces, CSR (used by the feature-split search)
        vo = np.argsort(e_a, kind="stable")  # e_a is tri[:,0]|tri[:,1]|tri[:,2]
        vf_idx = e_face[vo]
        vf_ptr = np.searchsorted(e_a[vo], np.arange(n_pi + 1))

        patch_of_bnd = np.full(n_bnd, -1, dtype=np.int64)
        for pi, p in enumerate(patches):
            s = int(p["startFace"]) - n_int_primal
            k = int(p["nFaces"])
            if k == 0:
                continue
            if s < 0 or s + k > n_bnd:
                raise RuntimeError(
                    f"Patch '{p['name']}' range [{s}, {s + k}) lies outside the "
                    f"{n_bnd} boundary faces."
                )
            patch_of_bnd[s:s + k] = pi
        if np.any(patch_of_bnd < 0):
            raise RuntimeError(
                f"{int((patch_of_bnd < 0).sum())} boundary faces belong to no patch "
                "— refusing to invent a defaultFaces patch."
            )
        self._log(
            f"[poly 4/9] {len(patches)} boundary patch(es), "
            f"{3 * n_bnd:,} exact surface sub-faces to emit"
        )

        return SimpleNamespace(
            points=points, tri=tri, owner=owner, neigh=neigh, patches=patches,
            n_faces=n_faces, n_int_primal=n_int_primal, n_tets=n_tets, n_pi=n_pi,
            cell_centroid=cell_centroid, face_centroid=face_centroid,
            used=used, n_used=n_used,
            bnd_face_ids=bnd_face_ids, n_bnd=n_bnd, bnd_tri=tri[bnd_face_ids],
            e_face_s=e_face_s, starts=starts, ends=ends, n_edges=n_edges,
            uniq_key=uniq_key, edge_mid=edge_mid, face_edge=face_edge,
            vf_ptr=vf_ptr, vf_idx=vf_idx, patch_of_bnd=patch_of_bnd,
        )

    # ------------------------------------------------------------------
    # P5: build the dual complex for a given vertex-star partition
    # ------------------------------------------------------------------

    def _build_dual(self, P, splits: dict[int, dict[int, int]]) -> SimpleNamespace:
        n_pi = P.n_pi
        median = self._median_faces

        # ---- cell numbering: one cell per (vertex, star group) ----------
        n_groups = P.used.astype(np.int64)
        for v, m in splits.items():
            n_groups[v] = max(m.values()) + 1
        base = np.zeros(n_pi + 1, dtype=np.int64)
        np.cumsum(n_groups, out=base[1:])
        n_cells = int(base[n_pi])
        cell_vertex = np.repeat(np.arange(n_pi, dtype=np.int64), n_groups)
        base_l = base[:n_pi].tolist()

        # ---- lazy dual-point allocator ----------------------------------
        # Points are materialised only when a face actually references them,
        # so the output never carries unused points (checkMesh treats those as
        # a topology error).
        max_pts = P.n_tets + P.n_faces + P.n_edges + n_pi
        xyz = np.empty((max_pts, 3), dtype=np.float64)
        xyz[: P.n_tets] = P.cell_centroid
        n_pt = P.n_tets
        face_pt = [-1] * P.n_faces
        if median:
            xyz[n_pt:n_pt + P.n_faces] = P.face_centroid
            face_pt = list(range(n_pt, n_pt + P.n_faces))
            n_pt += P.n_faces
        edge_pt = [-1] * P.n_edges
        vert_pt = [-1] * n_pi
        fc_all = P.face_centroid
        emid = P.edge_mid
        pts_in = P.points

        def pt_face(f: int) -> int:
            nonlocal n_pt
            i = face_pt[f]
            if i < 0:
                i = n_pt
                n_pt += 1
                xyz[i] = fc_all[f]
                face_pt[f] = i
            return i

        def pt_edge(e: int) -> int:
            nonlocal n_pt
            i = edge_pt[e]
            if i < 0:
                i = n_pt
                n_pt += 1
                xyz[i] = emid[e]
                edge_pt[e] = i
            return i

        def pt_vert(v: int) -> int:
            nonlocal n_pt
            i = vert_pt[v]
            if i < 0:
                i = n_pt
                n_pt += 1
                xyz[i] = pts_in[v]
                vert_pt[v] = i
            return i

        owner_l = P.owner.tolist()
        neigh_l = P.neigh.tolist()
        e_face_l = P.e_face_s.tolist()
        starts_l = P.starts.tolist()
        ends_l = P.ends.tolist()
        uniq_l = P.uniq_key.tolist()

        int_faces: list[list[int]] = []
        int_own: list[int] = []
        int_nb: list[int] = []

        # ---- internal dual faces, one run per primal edge ---------------
        heartbeat = max(1, P.n_edges // 8)
        for ei in range(P.n_edges):
            if ei and ei % heartbeat == 0:
                self._check_cancel()
                self._log(f"[poly 5/9] internal dual faces {ei:,}/{P.n_edges:,}")
            s = starts_l[ei]
            e = ends_l[ei]
            key = uniq_l[ei]
            va = key // n_pi
            vb = key % n_pi
            group = e_face_l[s:e]

            cf_map: dict[int, list[int]] = defaultdict(list)
            bfaces: list[int] = []
            for f in group:
                cf_map[owner_l[f]].append(f)
                nb = neigh_l[f]
                if nb >= 0:
                    cf_map[nb].append(f)
                else:
                    bfaces.append(f)

            ring, link, closed = _walk_edge_fan(
                group, bfaces, cf_map, owner_l, neigh_l, va, vb,
            )
            n = len(ring)

            sa = splits.get(va)
            sb = splits.get(vb)
            ba = base_l[va]
            bb = base_l[vb]
            if sa is None and sb is None:
                uniform = True
                pa = pb = None
            else:
                pa = [ba + (sa.get(T, 0) if sa else 0) for T in ring]
                pb = [bb + (sb.get(T, 0) if sb else 0) for T in ring]
                uniform = (min(pa) == max(pa)) and (min(pb) == max(pb))

            if closed and uniform:
                if median:
                    poly = []
                    for k in range(n):
                        poly.append(pt_face(link[k]))
                        poly.append(ring[k])
                else:
                    poly = list(ring)
                _emit_edge_face(
                    poly, ba, bb, xyz, pts_in, va, vb, int_faces, int_own, int_nb,
                )
                continue

            if closed:
                # rotate so a group change lands on index 0, then treat the
                # ring as an open chain terminated by the same primal face
                cut = 0
                for k in range(n):
                    if pa[k] != pa[k - 1] or pb[k] != pb[k - 1]:
                        cut = k
                        break
                ring = ring[cut:] + ring[:cut]
                link = link[cut:] + link[:cut]
                pa = pa[cut:] + pa[:cut]
                pb = pb[cut:] + pb[:cut]
                link = link + [link[0]]
            elif uniform:
                pa = [ba] * n
                pb = [bb] * n

            mid = pt_edge(ei)
            i = 0
            while i < n:
                j = i
                while j + 1 < n and pa[j + 1] == pa[i] and pb[j + 1] == pb[i]:
                    j += 1
                poly = [mid, pt_face(link[i]), ring[i]]
                for k in range(i + 1, j + 1):
                    if median:
                        poly.append(pt_face(link[k]))
                    poly.append(ring[k])
                poly.append(pt_face(link[j + 1]))
                _emit_edge_face(
                    poly, pa[i], pb[i], xyz, pts_in, va, vb,
                    int_faces, int_own, int_nb,
                )
                i = j + 1

        # ---- extra internal faces where a split cuts the vertex star ----
        if splits:
            is_split = np.zeros(n_pi, dtype=bool)
            is_split[np.fromiter(splits.keys(), dtype=np.int64, count=len(splits))] = True
            cand = np.flatnonzero(is_split[P.tri].any(axis=1) & (P.neigh >= 0))
            tri_l = P.tri.tolist()
            fe_l = P.face_edge.tolist()
            cc = P.cell_centroid
            n_cut = 0
            for f in cand.tolist():
                t1 = owner_l[f]
                t2 = neigh_l[f]
                vs = tri_l[f]
                fe = fe_l[f]
                for c in range(3):
                    x = vs[c]
                    m = splits.get(x)
                    if m is None:
                        continue
                    g1 = m.get(t1, 0)
                    g2 = m.get(t2, 0)
                    if g1 == g2:
                        continue
                    # the barycentric corner of primal face f at vertex x
                    quad = [pt_vert(x), pt_edge(fe[c]), pt_face(f), pt_edge(fe[c - 1])]
                    nrm = _newell(xyz, quad)
                    if float(nrm @ (cc[t2] - cc[t1])) < 0.0:
                        quad.reverse()
                    o = base_l[x] + g1
                    nbc = base_l[x] + g2
                    if o > nbc:
                        o, nbc = nbc, o
                        quad.reverse()
                    int_faces.append(quad)
                    int_own.append(o)
                    int_nb.append(nbc)
                    n_cut += 1
            if n_cut:
                self._log(f"[poly 5/9] {n_cut:,} extra faces along the feature cuts")

        # ---- boundary dual faces: exact subdivision of the surface ------
        bnd_by_patch: list[list[list[int]]] = [[] for _ in P.patches]
        bown_by_patch: list[list[int]] = [[] for _ in P.patches]
        bnd_tri_l = P.bnd_tri.tolist()
        pob_l = P.patch_of_bnd.tolist()
        fe_all = P.face_edge
        for bi in range(P.n_bnd):
            f = P.n_int_primal + bi
            t1 = owner_l[f]
            vs = bnd_tri_l[bi]
            fe = fe_all[f]
            fcp = pt_face(f)
            pi = pob_l[bi]
            for c in range(3):
                x = vs[c]
                m = splits.get(x)
                g = m.get(t1, 0) if m is not None else 0
                quad = [pt_vert(x), pt_edge(int(fe[c])), fcp, pt_edge(int(fe[c - 1]))]
                bnd_by_patch[pi].append(quad)
                bown_by_patch[pi].append(base_l[x] + g)

        # ---- assemble in OpenFOAM order --------------------------------
        io = np.array(int_own, dtype=np.int64)
        inb = np.array(int_nb, dtype=np.int64)
        if len(io) and np.any(io == inb):
            raise RuntimeError("Internal dual face with identical owner and neighbour.")
        sort_idx = np.lexsort((inb, io))
        faces = [int_faces[i] for i in sort_idx]
        out_own = io[sort_idx].tolist()
        out_nb = inb[sort_idx].tolist()
        n_int = len(faces)

        new_patches: list[dict] = []
        start = n_int
        for pi, p in enumerate(P.patches):
            k = len(bnd_by_patch[pi])
            faces.extend(bnd_by_patch[pi])
            out_own.extend(bown_by_patch[pi])
            new_patches.append(
                {"name": p["name"], "type": p.get("type", "patch"),
                 "nFaces": k, "startFace": start}
            )
            start += k

        return SimpleNamespace(
            points=xyz[:n_pt], faces=faces,
            owner=np.array(out_own, dtype=np.int64),
            neigh=np.array(out_nb, dtype=np.int64),
            n_int=n_int, patches=new_patches, n_cells=n_cells,
            cell_vertex=cell_vertex,
        )

    # ------------------------------------------------------------------
    # P6: decide which vertex stars to split
    # ------------------------------------------------------------------

    def _plan_splits(self, P, M, bad, splits) -> dict[int, dict[int, int]]:
        verts = np.unique(M.cell_vertex[np.flatnonzero(bad)])
        cos_limit = np.cos(np.deg2rad(self._feature_angle))
        new: dict[int, dict[int, int]] = {}
        for v in verts.tolist():
            if v in splits:
                continue  # already split as far as this criterion can take it
            g = _split_vertex_star(P, v, cos_limit)
            if g:
                new[v] = g
        return new

    # ------------------------------------------------------------------
    # topology + geometry invariants, checked before anything is written
    # ------------------------------------------------------------------

    def _validate(self, res: DualPolyResult, M, P) -> None:
        faces, owner, neigh = M.faces, M.owner, M.neigh
        n_internal, n_cells, points = M.n_int, M.n_cells, M.points
        n_faces = len(faces)
        if len(owner) != n_faces:
            raise RuntimeError("owner/faces length mismatch")
        if owner.min() < 0 or owner.max() >= n_cells:
            raise RuntimeError("owner index out of range")
        if n_internal and (neigh.min() < 0 or neigh.max() >= n_cells):
            raise RuntimeError("neighbour index out of range")
        if n_internal and np.any(owner[:n_internal] >= neigh):
            raise RuntimeError("internal faces are not in upper-triangular order")

        nf_per_cell = np.zeros(n_cells, dtype=np.int64)
        np.add.at(nf_per_cell, owner, 1)
        np.add.at(nf_per_cell, neigh, 1)
        if nf_per_cell.min() < 4:
            bad = int(np.count_nonzero(nf_per_cell < 4))
            raise RuntimeError(f"{bad} output cells have fewer than 4 faces")

        for f in faces:
            if len(f) < 3 or len(set(f)) != len(f):
                raise RuntimeError(f"Degenerate output face: {f}")

        sf, cf = _face_geometry(points, faces)

        closure = np.zeros((n_cells, 3), dtype=np.float64)
        np.add.at(closure, owner, sf)
        np.add.at(closure, neigh, -sf[:n_internal])
        area_scale = np.zeros(n_cells, dtype=np.float64)
        mag = np.linalg.norm(sf, axis=1)
        np.add.at(area_scale, owner, mag)
        np.add.at(area_scale, neigh, mag[:n_internal])
        rel_closure = np.linalg.norm(closure, axis=1) / np.maximum(area_scale, 1e-300)
        res.max_closure_error = float(rel_closure.max())
        if res.max_closure_error > 1e-8:
            worst = int(np.argmax(rel_closure))
            raise RuntimeError(
                f"Open output cell: relative closure error {res.max_closure_error:.3e} "
                f"at cell {worst} — the dual complex is not watertight."
            )

        vol = np.zeros(n_cells, dtype=np.float64)
        contrib = (cf * sf).sum(axis=1) / 3.0
        np.add.at(vol, owner, contrib)
        np.add.at(vol, neigh, -contrib[:n_internal])
        res.min_cell_volume = float(vol.min())
        if res.min_cell_volume <= 0.0:
            bad = int(np.count_nonzero(vol <= 0.0))
            raise RuntimeError(
                f"{bad} output cells have non-positive volume "
                f"(min {res.min_cell_volume:.3e})"
            )
        res.volume_after = float(vol.sum())

        pc = P.face_centroid
        pn = 0.5 * np.cross(
            P.points[P.tri[:, 1]] - P.points[P.tri[:, 0]],
            P.points[P.tri[:, 2]] - P.points[P.tri[:, 0]],
        )
        pv = np.zeros(P.n_tets)
        c = (pc * pn).sum(axis=1) / 3.0
        np.add.at(pv, P.owner, c)
        m = P.neigh >= 0
        np.add.at(pv, P.neigh[m], -c[m])
        res.volume_before = float(pv.sum())

        rel_vol = abs(res.volume_after - res.volume_before) / max(
            abs(res.volume_before), 1e-300
        )
        if rel_vol > 1e-6:
            raise RuntimeError(
                f"Volume not conserved: primal {res.volume_before:.6e} vs dual "
                f"{res.volume_after:.6e} (relative {rel_vol:.3e})"
            )
        self._log(
            f"[poly 8/9] invariants OK — closure {res.max_closure_error:.2e}, "
            f"min cell volume {res.min_cell_volume:.3e}, volume drift {rel_vol:.2e}"
        )

    # ------------------------------------------------------------------
    # writing
    # ------------------------------------------------------------------

    def _write(self, points, faces, owner, neigh, patches) -> None:
        import shutil
        import tempfile

        poly_dir = self._case_dir / "constant" / "polyMesh"
        tmp_dir = Path(tempfile.mkdtemp(prefix="tet_poly_dual_"))
        tmp_poly = tmp_dir / "polyMesh"
        tmp_poly.mkdir(parents=True, exist_ok=True)
        try:
            _write_points(tmp_poly / "points", points)
            _write_faces(tmp_poly / "faces", faces)
            _write_labels(tmp_poly / "owner", owner)
            _write_labels(tmp_poly / "neighbour", neigh)
            _write_boundary(tmp_poly / "boundary", patches)
            for name in ("points", "faces", "owner", "neighbour", "boundary"):
                p = tmp_poly / name
                if not p.exists() or p.stat().st_size == 0:
                    raise RuntimeError(f"Verification failed: {name} is empty or missing")
            if poly_dir.exists():
                shutil.rmtree(poly_dir)
            shutil.move(str(tmp_poly), str(poly_dir))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# dual construction helpers
# ---------------------------------------------------------------------------

def _walk_edge_fan(group, bfaces, cf_map, owner_l, neigh_l, va, vb):
    """Order the tets around a primal edge.

    Returns (ring, link, closed). `ring[i]` is the i-th tet; `link[i]` is the
    primal face between `ring[i-1]` and `ring[i]`. For an open fan `link` has
    one extra entry and its two ends are the terminating boundary triangles.

    In a tet, an edge belongs to exactly two of the four faces, so every cell
    in the group has degree two and the incidence graph is a simple ring
    (interior edge) or a simple path (boundary edge).
    """
    ring: list[int] = []
    walk: list[int] = []
    if bfaces:
        if len(bfaces) != 2:
            raise RuntimeError(
                f"Non-manifold primal edge ({va},{vb}): {len(bfaces)} boundary faces "
                "(expected 2). The input is not a closed tetrahedral volume."
            )
        start_face, end_face = bfaces
        cur_face = start_face
        cur_cell = owner_l[cur_face]
        ring.append(cur_cell)
        while True:
            pair = cf_map[cur_cell]
            if len(pair) != 2:
                raise RuntimeError(
                    f"Malformed edge fan at ({va},{vb}): cell {cur_cell} has "
                    f"{len(pair)} incident faces on this edge (expected 2)."
                )
            nxt = pair[0] if pair[1] == cur_face else pair[1]
            if nxt == end_face:
                break
            o = owner_l[nxt]
            nb = neigh_l[nxt]
            cur_cell = nb if o == cur_cell else o
            cur_face = nxt
            walk.append(nxt)
            ring.append(cur_cell)
            if len(ring) > len(group) + 2:
                raise RuntimeError(f"Edge fan walk did not terminate at ({va},{vb}).")
        return ring, [start_face] + walk + [end_face], False

    start_face = group[0]
    cur_face = start_face
    cur_cell = owner_l[cur_face]
    first_cell = cur_cell
    ring.append(cur_cell)
    while True:
        pair = cf_map[cur_cell]
        if len(pair) != 2:
            raise RuntimeError(
                f"Malformed edge ring at ({va},{vb}): cell {cur_cell} has "
                f"{len(pair)} incident faces on this edge (expected 2)."
            )
        nxt = pair[0] if pair[1] == cur_face else pair[1]
        o = owner_l[nxt]
        nb = neigh_l[nxt]
        nxt_cell = nb if o == cur_cell else o
        if nxt_cell == first_cell:
            break
        cur_cell = nxt_cell
        cur_face = nxt
        walk.append(nxt)
        ring.append(cur_cell)
        if len(ring) > len(group) + 2:
            raise RuntimeError(f"Edge ring walk did not close at ({va},{vb}).")
    if len(ring) != len(group):
        raise RuntimeError(
            f"Edge ring at ({va},{vb}) visited {len(ring)} of {len(group)} incident "
            "tets — non-manifold primal edge."
        )
    return ring, [start_face] + walk, True


def _emit_edge_face(poly, ca, cb, xyz, pts_in, va, vb, faces, own, nb):
    """Append one dual face, wound so its normal points owner -> neighbour."""
    if len(poly) < 3:
        raise RuntimeError(f"Degenerate dual face at edge ({va},{vb}).")
    nrm = _newell(xyz, poly)
    if float(nrm @ (pts_in[vb] - pts_in[va])) < 0.0:
        poly.reverse()
    if ca > cb:
        ca, cb = cb, ca
        poly.reverse()
    faces.append(poly)
    own.append(ca)
    nb.append(cb)


def _split_vertex_star(P, v: int, cos_limit: float) -> dict[int, int] | None:
    """Partition the tets around vertex `v` by smooth surface region.

    The boundary triangles at `v` are clustered by dihedral angle, then that
    labelling is flooded inwards over the tets of the star. Returns
    {tet: group} with at least two groups, or None if `v` is not a
    multi-region boundary vertex.
    """
    inc = P.vf_idx[P.vf_ptr[v]:P.vf_ptr[v + 1]]
    bnd = [int(f) for f in inc if f >= P.n_int_primal]
    if len(bnd) < 2:
        return None

    # cluster boundary triangles at v across the boundary edges through v
    pos = {f: i for i, f in enumerate(bnd)}
    parent = list(range(len(bnd)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    tri = P.tri
    nrm = np.cross(
        P.points[tri[bnd, 1]] - P.points[tri[bnd, 0]],
        P.points[tri[bnd, 2]] - P.points[tri[bnd, 0]],
    )
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1), 1e-300)[:, None]

    edge_tris: dict[tuple[int, int], list[int]] = defaultdict(list)
    for f in bnd:
        a, b, c = (int(x) for x in tri[f])
        for x, y in ((a, b), (b, c), (c, a)):
            if x == v or y == v:
                edge_tris[(min(x, y), max(x, y))].append(f)
    for fl in edge_tris.values():
        if len(fl) != 2:
            continue
        i, j = pos[fl[0]], pos[fl[1]]
        if float(nrm[i] @ nrm[j]) >= cos_limit:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

    roots = {}
    seed_label = {}
    for f in bnd:
        r = find(pos[f])
        if r not in roots:
            roots[r] = len(roots)
        seed_label[f] = roots[r]
    if len(roots) < 2:
        return None

    # flood the surface labelling inwards over the star's tets
    owner_l = P.owner
    neigh_l = P.neigh
    star: set[int] = set()
    for f in inc:
        star.add(int(owner_l[f]))
        nb = int(neigh_l[f])
        if nb >= 0:
            star.add(nb)
    adj: dict[int, list[int]] = defaultdict(list)
    for f in inc:
        nb = int(neigh_l[f])
        if nb >= 0:
            o = int(owner_l[f])
            adj[o].append(nb)
            adj[nb].append(o)

    label: dict[int, int] = {}
    frontier: list[int] = []
    best_area: dict[int, float] = {}
    for f in bnd:
        t = int(owner_l[f])
        a = float(np.linalg.norm(np.cross(
            P.points[tri[f, 1]] - P.points[tri[f, 0]],
            P.points[tri[f, 2]] - P.points[tri[f, 0]],
        )))
        if a > best_area.get(t, -1.0):
            best_area[t] = a
            label[t] = seed_label[f]
    frontier = list(label)
    while frontier:
        nxt = []
        for t in frontier:
            lt = label[t]
            for u in adj.get(t, ()):
                if u not in label:
                    label[u] = lt
                    nxt.append(u)
        frontier = nxt
    for t in star:
        label.setdefault(t, 0)

    used_labels = sorted(set(label.values()))
    if len(used_labels) < 2:
        return None
    remap = {l: i for i, l in enumerate(used_labels)}
    return {t: remap[l] for t, l in label.items()}


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def _newell(points: np.ndarray, verts: list[int]) -> np.ndarray:
    p = points[verts]
    q = np.roll(p, -1, axis=0)
    n = np.empty(3, dtype=np.float64)
    n[0] = float(((p[:, 1] - q[:, 1]) * (p[:, 2] + q[:, 2])).sum())
    n[1] = float(((p[:, 2] - q[:, 2]) * (p[:, 0] + q[:, 0])).sum())
    n[2] = float(((p[:, 0] - q[:, 0]) * (p[:, 1] + q[:, 1])).sum())
    return 0.5 * n


def _face_geometry(points: np.ndarray, faces: list[list[int]]):
    """Area vectors and centroids, using OpenFOAM's centre-point decomposition.

    Faces are bucketed by vertex count so each bucket is one vectorised pass.
    """
    n = len(faces)
    sf = np.zeros((n, 3), dtype=np.float64)
    cf = np.zeros((n, 3), dtype=np.float64)
    sizes = np.fromiter((len(f) for f in faces), dtype=np.int64, count=n)
    for k in np.unique(sizes):
        idx = np.flatnonzero(sizes == k)
        v = np.array([faces[i] for i in idx], dtype=np.int64)
        p = points[v]
        c0 = p.mean(axis=1)
        a = p - c0[:, None, :]
        b = np.roll(p, -1, axis=1) - c0[:, None, :]
        tri_area = 0.5 * np.cross(a, b)
        tri_cent = (c0[:, None, :] + p + np.roll(p, -1, axis=1)) / 3.0
        w = np.linalg.norm(tri_area, axis=2)
        wsum = w.sum(axis=1)
        c = (tri_cent * w[:, :, None]).sum(axis=1) / np.maximum(wsum, 1e-300)[:, None]
        degenerate = wsum <= 0.0
        if np.any(degenerate):
            c[degenerate] = c0[degenerate]
        sf[idx] = tri_area.sum(axis=1)
        cf[idx] = c
    return sf, cf


def _cell_centres(sf, cf, owner, neigh, n_int, n_cells):
    """Cell centres and volumes, as `primitiveMeshCellCentresAndVols` computes them."""
    nfc = np.zeros(n_cells, dtype=np.float64)
    est = np.zeros((n_cells, 3), dtype=np.float64)
    np.add.at(est, owner, cf)
    np.add.at(nfc, owner, 1.0)
    np.add.at(est, neigh, cf[:n_int])
    np.add.at(nfc, neigh, 1.0)
    est /= np.maximum(nfc, 1.0)[:, None]

    pyr_v_own = (sf * (cf - est[owner])).sum(axis=1) / 3.0
    pyr_c_own = 0.75 * cf + 0.25 * est[owner]
    pyr_v_nb = -(sf[:n_int] * (cf[:n_int] - est[neigh])).sum(axis=1) / 3.0
    pyr_c_nb = 0.75 * cf[:n_int] + 0.25 * est[neigh]

    vol = np.zeros(n_cells, dtype=np.float64)
    acc = np.zeros((n_cells, 3), dtype=np.float64)
    np.add.at(vol, owner, pyr_v_own)
    np.add.at(acc, owner, pyr_v_own[:, None] * pyr_c_own)
    np.add.at(vol, neigh, pyr_v_nb)
    np.add.at(acc, neigh, pyr_v_nb[:, None] * pyr_c_nb)
    ctr = acc / np.where(np.abs(vol) > 0, vol, 1.0)[:, None]
    degenerate = np.abs(vol) <= 0
    if np.any(degenerate):
        ctr[degenerate] = est[degenerate]
    return ctr, vol


def _detect_defects(
    points, faces, sf, cf, ctr, owner, neigh, n_int, n_cells,
    non_ortho_limit_deg: float = 70.0, skew_limit: float = 4.0,
):
    """Flag cells failing checkMesh's face-pyramid, non-orthogonality or
    skewness criteria. Returns (bad_cell_mask, counts)."""
    bad = np.zeros(n_cells, dtype=bool)

    pv_own = (sf * (cf - ctr[owner])).sum(axis=1)
    pv_nb = -(sf[:n_int] * (cf[:n_int] - ctr[neigh])).sum(axis=1)
    m_own = pv_own <= 0.0
    m_nb = pv_nb <= 0.0
    n_pyr = int(m_own.sum() + m_nb.sum())
    bad[owner[m_own]] = True
    bad[neigh[m_nb]] = True

    d = ctr[neigh] - ctr[owner[:n_int]]
    dn = np.linalg.norm(d, axis=1)
    sn = np.linalg.norm(sf[:n_int], axis=1)
    cos = (d * sf[:n_int]).sum(axis=1) / np.maximum(dn * sn, 1e-300)
    m_no = cos < np.cos(np.deg2rad(non_ortho_limit_deg))
    n_no = int(m_no.sum())
    bad[owner[:n_int][m_no]] = True
    bad[neigh[m_no]] = True

    cpf = cf[:n_int] - ctr[owner[:n_int]]
    denom = (sf[:n_int] * d).sum(axis=1)
    scale = (sf[:n_int] * cpf).sum(axis=1) / np.where(np.abs(denom) > 0, denom, 1e-300)
    sv = cpf - scale[:, None] * d
    svm = np.linalg.norm(sv, axis=1)
    fd = 0.2 * dn + _face_extent(points, faces, cf, sv, n_int)
    m_sk = svm / np.maximum(fd, 1e-300) > skew_limit
    n_sk = int(m_sk.sum())
    bad[owner[:n_int][m_sk]] = True
    bad[neigh[m_sk]] = True

    return bad, {"pyramid": n_pyr, "non_ortho": n_no, "skew": n_sk}


def _face_extent(points, faces, cf, sv, n_int):
    """max over a face's points of |unit(sv) . (pt - faceCentre)|."""
    hat = sv / np.maximum(np.linalg.norm(sv, axis=1), 1e-300)[:, None]
    out = np.zeros(n_int, dtype=np.float64)
    sizes = np.fromiter((len(faces[i]) for i in range(n_int)), dtype=np.int64, count=n_int)
    for k in np.unique(sizes):
        idx = np.flatnonzero(sizes == k)
        v = np.array([faces[i] for i in idx], dtype=np.int64)
        rel = points[v] - cf[idx][:, None, :]
        out[idx] = np.abs((rel * hat[idx][:, None, :]).sum(axis=2)).max(axis=1)
    return out


# ---------------------------------------------------------------------------
# OpenFOAM writers (bulk, not line-at-a-time)
# ---------------------------------------------------------------------------

def _header(cls: str, obj: str) -> str:
    return (
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        f"    class       {cls};\n    location    \"constant/polyMesh\";\n"
        f"    object      {obj};\n}}\n"
    )


def _write_points(path: Path, points: np.ndarray) -> None:
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(_header("vectorField", "points"))
        f.write(f"{len(points)}\n(\n")
        for i in range(0, len(points), 65536):
            f.write("".join(
                f"({x:.12e} {y:.12e} {z:.12e})\n" for x, y, z in points[i:i + 65536]
            ))
        f.write(")\n")


def _write_faces(path: Path, faces: list[list[int]]) -> None:
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(_header("faceList", "faces"))
        f.write(f"{len(faces)}\n(\n")
        for i in range(0, len(faces), 65536):
            f.write("".join(
                f"{len(v)}({' '.join(map(str, v))})\n" for v in faces[i:i + 65536]
            ))
        f.write(")\n")


def _write_labels(path: Path, data: np.ndarray) -> None:
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(_header("labelList", path.name))
        f.write(f"{len(data)}\n(\n")
        for i in range(0, len(data), 262144):
            f.write("\n".join(map(str, data[i:i + 262144].tolist())))
            f.write("\n")
        f.write(")\n")


def _write_boundary(path: Path, patches: list[dict]) -> None:
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(_header("polyBoundaryMesh", "boundary"))
        f.write(f"{len(patches)}\n(\n")
        for p in patches:
            f.write(f"    {p['name']}\n    {{\n")
            f.write(f"        type            {p.get('type', 'patch')};\n")
            f.write(f"        nFaces          {p['nFaces']};\n")
            f.write(f"        startFace       {p['startFace']};\n    }}\n")
        f.write(")\n")
