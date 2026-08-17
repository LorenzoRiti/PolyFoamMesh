"""Native hex(-dominant) -> polyhedral dual engine (production, Phase 2).

Promoted from the Phase-1 proof-of-concept ``hex_dual_spike`` (gate PASS on
the venturi A/B vs polyDualMesh).  This is the second native-poly building
block of the in-house polyhedral mesher: it takes an EXISTING hex(-dominant)
/ cut-cell OpenFOAM mesh and rebuilds it as its barycentric (median) dual,
exactly like OpenFOAM's ``polyDualMesh`` but 100% in Python/numpy:

    one output cell        per primal VERTEX
    one internal face      per primal EDGE
    boundary output faces  = exact subdivision of the primal boundary faces

Why this is the right construction for a NATIVE polyhedral mesher (measured
in the tet counterpart ``core/tet_poly_dual.py``):

- the median-dual boundary needs NO clipping against the CAD: the dual
  boundary points (primal boundary vertices, boundary-edge midpoints,
  boundary-face centroids) already lie exactly on the primal surface, and
  each primal boundary face tiles into one quad per corner;
- every primal cell is consumed -> 100% poly by construction, no "residual"
  concept;
- orientation is geometric and unambiguous (face normal points from the dual
  cell of vertex a toward the dual cell of vertex b).

The construction does NOT depend on the primal cells being tetrahedra: the
edge fan walk only needs each cell to touch an edge through exactly two of
its faces (true for every valid polyhedral cell — a closed 2-manifold cell
boundary).  So this same engine also dualises cut-cell / hex-dominant meshes,
which is the Phase 3 input of the native mesher.

Everything around the core is reused, not rewritten:
- ``foam_mesh_io`` (single polyMesh writer/parser),
- ``_face_geometry`` / ``_cell_centres`` / ``_detect_defects`` from
  ``tet_poly_dual`` (OpenFOAM's centre-point decomposition and the in-process
  checkMesh replica that matches checkMesh exactly on the valve fixture).

This engine writes to ``constant/<out_rel>`` (default ``polyMesh_dual``) and
never touches the input mesh, so an A/B against the ``polyDualMesh`` oracle
on the SAME hex mesh is a pure quality comparison (``tools/bench_hex_dual_ab.py``).

Run::

    python -m cfmesh_autogui.core.hex_poly_dual C:\\polybench\\valve1 [--out polyMesh_dual]
"""

from __future__ import annotations

import argparse
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from cfmesh_autogui.core import foam_mesh_io
from cfmesh_autogui.core.tet_poly_dual import (
    _cell_centres,
    _detect_defects,
    _face_geometry,
    _newell,
    _newell_dot,
)

logger = logging.getLogger(__name__)


@dataclass
class HexPolyDualResult:
    """Outcome of the hex -> poly dual run."""
    success: bool = False
    n_cells_before: int = 0
    n_faces_before: int = 0
    n_points_before: int = 0
    n_edges: int = 0
    n_cells_after: int = 0
    n_poly_cells: int = 0
    n_internal_faces: int = 0
    n_boundary_faces: int = 0
    n_points_after: int = 0
    n_boundary_quads: int = 0
    min_cell_volume: float = 0.0
    max_closure_error: float = 0.0
    volume_before: float = 0.0
    volume_after: float = 0.0
    defects: dict[str, int] = field(default_factory=dict)
    stage_times: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    out_dir: str = ""


class HexPolyDualConverter:
    """Rebuild a hex(-dominant) / cut-cell OpenFOAM case as its barycentric dual.

    Usage::

        conv = HexPolyDualConverter(case_dir, log=print)
        result = conv.run()
    """

    def __init__(
        self,
        case_dir: Path | str,
        log: callable | None = None,
        cancel: callable | None = None,
        out_rel: str = "polyMesh_dual",
        feature_angle: float = 90.0,
        collapse_smooth_edges: bool = True,
        smooth: bool = True,
        smooth_iters: int = 3,
        smooth_relax: float = 0.5,
    ):
        self._case_dir = Path(case_dir).resolve()
        self._log_cb = log
        self._cancel_cb = cancel
        self._out_rel = out_rel
        # polyDualMesh-style smooth-edge seam collapse (the Phase-2 quality
        # fix, closes the skewness gap vs the oracle).  Off keeps the exact
        # median-dual tiling (volume-conserving, but higher skew).
        self._feature_angle = float(feature_angle)
        self._collapse = bool(collapse_smooth_edges)
        # Quality-driven Laplacian smoothing of interior dual vertices,
        # same keep-best contract as tet_poly_dual (Fase 4a): a pass is
        # accepted only if total defects do not increase and no cell volume
        # turns non-positive, so this can never write a worse mesh.  Cut
        # cells from the native mesher are the case this closes — their
        # irregular shape carries more skew into the dual than clean hex
        # primal cells do.
        self._smooth = bool(smooth)
        self._smooth_iters = int(smooth_iters)
        self._smooth_relax = float(smooth_relax)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        logger.info(msg)
        if self._log_cb is not None:
            try:
                self._log_cb(msg)
            except Exception:  # pragma: no cover - logging must never break a run
                # a broken log callback (e.g. a destroyed Qt widget) must never
                # abort the conversion — record it and keep meshing
                logger.debug("hex_poly_dual: log callback failed", exc_info=True)

    def _check_cancel(self) -> None:
        if self._cancel_cb is not None and self._cancel_cb():
            raise RuntimeError("hex dual conversion cancelled")

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def run(self) -> HexPolyDualResult:
        t_start = time.monotonic()
        res = HexPolyDualResult()
        try:
            P = self._read_primal(res)
            res.stage_times["read"] = round(time.monotonic() - t_start, 2)
            self._check_cancel()
            t = time.monotonic()
            M = self._build_dual(P)
            res.stage_times["build"] = round(time.monotonic() - t, 2)
            self._log(
                f"[hexdual] {M.n_cells:,} dual cells (1 per primal vertex), "
                f"{M.n_int:,} internal + {len(M.faces) - M.n_int:,} boundary "
                f"faces, {len(M.points):,} points "
                f"[{res.stage_times['build']:.1f}s]"
            )
            self._check_cancel()
            t = time.monotonic()
            self._validate(res, M, P)
            res.stage_times["validate"] = round(time.monotonic() - t, 2)
            self._check_cancel()
            t = time.monotonic()
            self._write(M, P, res)
            res.stage_times["write"] = round(time.monotonic() - t, 2)
            res.success = True
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            logger.exception("Hex dual conversion failed")
            res.errors.append(str(exc))
        res.stage_times["total"] = round(time.monotonic() - t_start, 2)
        return res

    # ------------------------------------------------------------------
    # P1: read the primal mesh and precompute everything the dual needs
    # ------------------------------------------------------------------

    def _read_primal(self, res: HexPolyDualResult) -> SimpleNamespace:
        poly_dir = self._case_dir / "constant" / "polyMesh"
        points, faces_raw, owner, neighbour, patches = foam_mesh_io.read_polymesh(
            poly_dir
        )
        n_faces = len(faces_raw)
        if n_faces == 0:
            raise RuntimeError(
                "No faces found in polyMesh — the primal mesh is empty."
            )
        if len(owner) != n_faces:
            raise RuntimeError(
                f"owner has {len(owner)} entries but faces has {n_faces}"
            )
        n_int_primal = int(len(neighbour))
        neigh_full = np.full(n_faces, -1, dtype=np.int64)
        neigh_full[:n_int_primal] = neighbour
        n_cells = int(max(owner.max(), neigh_full.max())) + 1
        res.n_cells_before = n_cells
        res.n_faces_before = n_faces
        res.n_points_before = len(points)

        # ---- primal sanity: valid closed polyhedral volume -----------------
        widths = np.fromiter((len(f) for f in faces_raw), dtype=np.int64, count=n_faces)
        if widths.min() < 3:
            raise RuntimeError(
                f"{int(np.count_nonzero(widths < 3))} faces have fewer than 3 "
                "vertices — degenerate input."
            )
        nf_per_cell = np.zeros(n_cells, dtype=np.int64)
        np.add.at(nf_per_cell, owner, 1)
        np.add.at(nf_per_cell, neigh_full, 1)
        if nf_per_cell.min() < 4:
            raise RuntimeError(
                f"{int(np.count_nonzero(nf_per_cell < 4))} primal cells have "
                "fewer than 4 faces."
            )
        if n_int_primal and np.any(owner[:n_int_primal] >= neigh_full[:n_int_primal]):
            raise RuntimeError(
                "primal internal faces are not in upper-triangular order."
            )
        sf_p, cf_p = _face_geometry(points, faces_raw)
        ctr_p, vol_p = _cell_centres(
            sf_p, cf_p, owner, neigh_full[:n_int_primal], n_int_primal, n_cells,
        )
        if np.any(vol_p <= 0.0):
            raise RuntimeError(
                f"{int(np.count_nonzero(vol_p <= 0.0))} primal cells have "
                "non-positive volume — input is not a valid closed volume."
            )
        res.volume_before = float(vol_p.sum())

        # ---- unique primal edges (one internal dual face each) -------------
        n_pi = len(points)
        e_a_list: list[int] = []
        e_b_list: list[int] = []
        e_face_list: list[int] = []
        for fi, f in enumerate(faces_raw):
            k = len(f)
            for c in range(k):
                a = f[c]
                b = f[(c + 1) % k]
                e_a_list.append(a)
                e_b_list.append(b)
                e_face_list.append(fi)
        e_a = np.array(e_a_list, dtype=np.int64)
        e_b = np.array(e_b_list, dtype=np.int64)
        e_face = np.array(e_face_list, dtype=np.int64)
        e_key = np.minimum(e_a, e_b) * np.int64(n_pi) + np.maximum(e_a, e_b)
        order = np.argsort(e_key, kind="stable")
        e_key_s = e_key[order]
        e_face_s = e_face[order]
        starts = np.flatnonzero(np.r_[True, e_key_s[1:] != e_key_s[:-1]])
        ends = np.r_[starts[1:], len(e_key_s)]
        n_edges = len(starts)
        uniq_key = e_key_s[starts]
        edge_mid = 0.5 * (points[uniq_key // n_pi] + points[uniq_key % n_pi])

        # face -> corner edge index (column c is edge (v_c, v_{c+1})).
        # e_key is FACE-major with variable-width rows (faces have 3..k
        # vertices), so the mapping is built per face via cumulative offsets.
        face_edge: list[np.ndarray] = []
        pos = 0
        for fi in range(n_faces):
            k = int(widths[fi])
            face_edge.append(np.searchsorted(uniq_key, e_key[pos:pos + k]))
            pos += k

        # ---- boundary bookkeeping ------------------------------------------
        bnd_face_ids = np.arange(n_int_primal, n_faces, dtype=np.int64)
        n_bnd = len(bnd_face_ids)
        if n_bnd == 0:
            raise RuntimeError("Mesh has no boundary faces — refusing to convert.")
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
                f"{int((patch_of_bnd < 0).sum())} boundary faces belong to no "
                "patch — refusing to invent a defaultFaces patch."
            )
        n_bnd_quads = int(widths[n_int_primal:].sum())

        # ---- feature edges / points (polyDualMesh semantics) ---------------
        # A boundary edge is a FEATURE edge when it is non-manifold, joins
        # two different patches, or the dihedral angle between its two
        # boundary faces is >= feature_angle.  Feature edges keep a midpoint
        # dual point and their seam face; smooth edges are collapsed.
        # A feature POINT is a boundary vertex with > 2 incident feature
        # edges (exactly polyDualMesh's calcFeatures rule).
        bnd_e_faces: dict[int, list[int]] = defaultdict(list)
        for bi in range(n_bnd):
            f = n_int_primal + bi
            vs = faces_raw[f]
            for c in range(len(vs)):
                a, b = vs[c], vs[(c + 1) % len(vs)]
                bnd_e_faces[min(a, b) * np.int64(n_pi) + max(a, b)].append(bi)
        bnd_edge_index: dict[int, int] = {}
        feat_edge = np.zeros(n_edges, dtype=bool)
        cos_limit = np.cos(np.deg2rad(self._feature_angle))
        for key, bis in bnd_e_faces.items():
            ei = int(np.searchsorted(uniq_key, key))
            bnd_edge_index[key] = ei
            if len(bis) != 2:
                feat_edge[ei] = True  # non-manifold surface edge
                continue
            f1, f2 = bis
            if patch_of_bnd[f1] != patch_of_bnd[f2]:
                feat_edge[ei] = True  # patch seam
                continue
            n1 = _newell(points, faces_raw[n_int_primal + f1])
            n2 = _newell(points, faces_raw[n_int_primal + f2])
            m1 = float(np.linalg.norm(n1))
            m2 = float(np.linalg.norm(n2))
            if m1 < 1e-300 or m2 < 1e-300:
                feat_edge[ei] = True
                continue
            if float(n1 @ n2) / (m1 * m2) < cos_limit:
                feat_edge[ei] = True  # sharp feature (>= feature_angle)
        feat_vertex = np.zeros(n_pi, dtype=bool)
        feat_vertex_count = np.zeros(n_pi, dtype=np.int64)
        for key, ei in bnd_edge_index.items():
            if feat_edge[ei]:
                a, b = int(key // n_pi), int(key % n_pi)
                feat_vertex_count[a] += 1
                feat_vertex_count[b] += 1
        feat_vertex[feat_vertex_count > 2] = True
        n_feat_edges = int(feat_edge.sum())
        n_feat_verts = int(feat_vertex.sum())
        if self._collapse:
            self._log(
                f"[hexdual] features: {n_feat_edges:,} feature edges, "
                f"{n_feat_verts:,} feature points "
                f"(featureAngle={self._feature_angle:.0f} deg, collapse smooth "
                f"edges)"
            )

        # used primal vertices (the dual cells) — drop unused input points
        used = np.zeros(n_pi, dtype=bool)
        used[np.unique(np.concatenate([e_a, e_b]))] = True
        n_used = int(used.sum())
        if n_used != n_pi:
            self._log(f"[hexdual] {n_pi - n_used:,} unused input points ignored")

        self._log(
            f"[hexdual] input: {n_cells:,} hex/poly cells, {n_faces:,} faces "
            f"({n_bnd:,} boundary, {n_bnd_quads:,} exact surface sub-faces), "
            f"{n_pi:,} points; primal edges: {n_edges:,}"
        )

        return SimpleNamespace(
            points=points, faces=faces_raw, owner=owner, neighbour=neigh_full,
            patches=patches, n_faces=n_faces, n_int_primal=n_int_primal,
            n_cells=n_cells, n_pi=n_pi,
            cell_centroid=ctr_p, face_centroid=cf_p,
            used=used, n_used=n_used,
            bnd_face_ids=bnd_face_ids, n_bnd=n_bnd, patch_of_bnd=patch_of_bnd,
            n_bnd_quads=n_bnd_quads,
            e_face_s=e_face_s, starts=starts, ends=ends, n_edges=n_edges,
            uniq_key=uniq_key, edge_mid=edge_mid, face_edge=face_edge,
            bnd_e_faces=bnd_e_faces, bnd_edge_index=bnd_edge_index,
            feat_edge=feat_edge, feat_vertex=feat_vertex,
            n_feat_edges=n_feat_edges, n_feat_verts=n_feat_verts,
        )

    # ------------------------------------------------------------------
    # P2: build the dual complex
    # ------------------------------------------------------------------

    def _build_dual(self, P) -> SimpleNamespace:
        n_pi = P.n_pi
        n_cells = P.n_cells  # primal cells == dual-cell points 0..n_cells-1

        # ---- lazy dual-point allocator -----------------------------------
        # Points are materialised only when a face actually references them,
        # so the output never carries unused points (checkMesh treats those
        # as a topology error).
        max_pts = P.n_cells + P.n_faces + P.n_edges + n_pi
        xyz = np.empty((max_pts, 3), dtype=np.float64)
        xyz[: n_cells] = P.cell_centroid
        n_pt = n_cells
        face_pt = [-1] * P.n_faces
        edge_pt = [-1] * P.n_edges
        vert_pt = [-1] * n_pi

        def pt_face(f: int) -> int:
            nonlocal n_pt
            i = face_pt[f]
            if i < 0:
                i = n_pt
                n_pt += 1
                xyz[i] = P.face_centroid[f]
                face_pt[f] = i
            return i

        def pt_edge(e: int) -> int:
            nonlocal n_pt
            i = edge_pt[e]
            if i < 0:
                i = n_pt
                n_pt += 1
                xyz[i] = P.edge_mid[e]
                edge_pt[e] = i
            return i

        def pt_vert(v: int) -> int:
            nonlocal n_pt
            i = vert_pt[v]
            if i < 0:
                i = n_pt
                n_pt += 1
                xyz[i] = P.points[v]
                vert_pt[v] = i
            return i

        owner_l = P.owner.tolist()
        neigh_l = P.neighbour.tolist()
        e_face_l = P.e_face_s.tolist()
        starts_l = P.starts.tolist()
        ends_l = P.ends.tolist()
        uniq_l = P.uniq_key.tolist()

        # dual CELLS are numbered compactly 0..n_used-1 (one per used primal
        # vertex); a primal mesh may carry unused points (e.g. a bore mesh
        # whose removed cells left orphan grid points), which must not become
        # cells with zero faces (checkMesh topology error).
        vid_to_cell = np.full(n_pi, -1, dtype=np.int64)
        vid_to_cell[np.flatnonzero(P.used)] = np.arange(P.n_used, dtype=np.int64)

        int_faces: list[list[int]] = []
        int_own: list[int] = []
        int_nb: list[int] = []

        # ---- internal dual faces, one run per primal edge -----------------
        def _edge_face(ei: int) -> None:
            """Build and emit the single dual face of primal edge ei."""
            s = starts_l[ei]
            e = ends_l[ei]
            key = uniq_l[ei]
            va = key // n_pi
            vb = key % n_pi
            ca = int(vid_to_cell[va])
            cb = int(vid_to_cell[vb])

            cf_map: dict[int, list[int]] = defaultdict(list)
            bfaces: list[int] = []
            for f in e_face_l[s:e]:
                cf_map[owner_l[f]].append(f)
                nb = neigh_l[f]
                if nb >= 0:
                    cf_map[nb].append(f)
                else:
                    bfaces.append(f)

            ring, link, closed = _walk_edge_fan(
                e_face_l[s:e], bfaces, cf_map, owner_l, neigh_l, va, vb,
            )
            n = len(ring)
            if closed:
                # closed ring: polygon through the incident cell centroids,
                # in the rotational order the walk found
                poly = list(ring)
            else:
                # open fan (boundary edge), polyDualMesh construction:
                # [fc(startFace), c1, ..., cn, fc(endFace)] — the seam face
                # is kept, only the edge-midpoint vertex is dropped on smooth
                # edges (feature edges keep it).
                mid_pt = pt_edge(ei) if (not self._collapse or P.feat_edge[ei]) else None
                poly = [pt_face(link[0]), ring[0]]
                for k in range(1, n):
                    poly.append(ring[k])
                poly.append(pt_face(link[n]))
                if mid_pt is not None:
                    poly.insert(0, mid_pt)
            _emit_edge_face(poly, ca, cb, xyz, P.points, va, vb,
                            int_faces, int_own, int_nb)

        heartbeat = max(1, P.n_edges // 8) if P.n_edges >= 64 else 0
        for ei in range(P.n_edges):
            if heartbeat and ei and ei % heartbeat == 0:
                self._check_cancel()
                self._log(f"[hexdual] internal dual faces {ei:,}/{P.n_edges:,}")
            _edge_face(ei)

        # ---- boundary dual faces --------------------------------------------
        bnd_by_patch: list[list[list[int]]] = [[] for _ in P.patches]
        bown_by_patch: list[list[int]] = [[] for _ in P.patches]
        if self._collapse:
            self._build_boundary_collapsed(
                P, xyz, pt_face, pt_edge, pt_vert, vid_to_cell,
                bnd_by_patch, bown_by_patch,
            )
        else:
            faces_l = P.faces
            fe_all = P.face_edge
            pob_l = P.patch_of_bnd.tolist()
            for bi in range(P.n_bnd):
                f = P.n_int_primal + bi
                vs = faces_l[f]
                fe = fe_all[f]
                fcp = pt_face(f)
                pi = pob_l[bi]
                k = len(vs)
                for c in range(k):
                    x = vs[c]
                    # quad at corner c: [vertex, mid(edge c), face centroid,
                    # mid(edge c-1)] — winding follows the parent face's corner
                    # order, so it inherits the (outward) boundary winding
                    quad = [pt_vert(x), pt_edge(int(fe[c])), fcp,
                            pt_edge(int(fe[c - 1]))]
                    bnd_by_patch[pi].append(quad)
                    bown_by_patch[pi].append(int(vid_to_cell[x]))  # dual cell

        # ---- assemble in OpenFOAM order ----------------------------------
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
            n_int=n_int, patches=new_patches, n_cells=P.n_used,
            n_boundary_quads=start - n_int,
        )

    # ------------------------------------------------------------------
    # P2b: boundary dual faces, polyDualMesh-style (seam collapse)
    # ------------------------------------------------------------------

    def _build_boundary_collapsed(
        self, P, xyz, pt_face, pt_edge, pt_vert, vid_to_cell,
        bnd_by_patch, bown_by_patch,
    ) -> None:
        """One dual boundary face per primal boundary vertex, through the
        incident boundary-face centroids in rotational walk order, with
        feature-edge midpoints inserted where the walk crosses them.

        Faithful port of polyDualMesh's ``dualPatch`` / ``collectPatchInternal
        Face`` / ``splitFace`` (verified against the OpenFOAM 2512 source):

        - smooth edges (dihedral < featureAngle, same patch, manifold) are
          collapsed: no midpoint, no seam face, the endpoint cells connect
          through the boundary;
        - feature edges keep their midpoint in the polygon; if a vertex
          crosses >= 2 feature edges it is split: fans from the vertex point
          when it is a feature point (> 2 feature edges), arcs between
          consecutive feature midpoints otherwise.
        """
        faces_l = P.faces
        fe_all = P.face_edge
        pob_l = P.patch_of_bnd.tolist()
        uniq_l = P.uniq_key.tolist()
        n_pi = P.n_pi
        bnd_e_faces = P.bnd_e_faces

        # per boundary vertex: incident boundary edges (global edge indices)
        vert_edges: dict[int, list[int]] = defaultdict(list)
        for key, ei in P.bnd_edge_index.items():
            a, b = int(key // n_pi), int(key % n_pi)
            vert_edges[a].append(ei)
            vert_edges[b].append(ei)
        # per boundary vertex: outward-ish reference = sum of incident
        # boundary-face Newell normals (primal winding is outward)
        vert_out: dict[int, np.ndarray] = {}
        for bi in range(P.n_bnd):
            f = P.n_int_primal + bi
            vs = faces_l[f]
            for v in vs:
                vert_out.setdefault(v, np.zeros(3))
                vert_out[v] += _newell(P.points, vs)

        # every boundary vertex (both endpoints of every boundary edge)
        bnd_verts: set[int] = set()
        for key in bnd_e_faces.keys():
            bnd_verts.add(int(key // n_pi))
            bnd_verts.add(int(key % n_pi))

        done_v: set[int] = set()
        for v in sorted(bnd_verts):
            if v in done_v:
                continue
            eis = vert_edges.get(v, [])
            if not eis:
                continue
            # ---- rotational walk around v --------------------------------
            e0 = eis[0]
            key0 = uniq_l[e0]
            bis0 = bnd_e_faces.get(key0)
            if bis0 is None or len(bis0) != 2:
                raise RuntimeError(
                    f"Non-manifold boundary edge at vertex {v} — the input "
                    "surface is not a closed 2-manifold."
                )
            pi = pob_l[bis0[0]]  # patch of the walk's start face
            cur_bi = bis0[0]
            cur_edge = e0
            dual_face: list[int] = []
            feat_idx: list[int] = []
            while True:
                f = P.n_int_primal + cur_bi
                vs = faces_l[f]
                dual_face.append(pt_face(f))
                # the two edges of face f at vertex v
                c = vs.index(v)
                e_next = int(fe_all[f][c])
                e_prev = int(fe_all[f][c - 1])
                if cur_edge not in (e_next, e_prev):
                    raise RuntimeError(
                        f"Boundary walk desync at vertex {v} (face {cur_bi})"
                    )
                new_edge = e_next if cur_edge == e_prev else e_prev
                if P.feat_edge[new_edge]:
                    dual_face.append(pt_edge(new_edge))
                    feat_idx.append(len(dual_face) - 1)
                if new_edge == e0:
                    break
                bis = bnd_e_faces.get(uniq_l[new_edge])
                if bis is None or len(bis) != 2:
                    raise RuntimeError(
                        f"Non-manifold boundary edge ({uniq_l[new_edge] // n_pi},"
                        f"{uniq_l[new_edge] % n_pi}) — the input surface is not "
                        "a closed 2-manifold."
                    )
                cur_bi = bis[0] if bis[1] == cur_bi else bis[1]
                cur_edge = new_edge
                if len(dual_face) > 4 * len(bnd_e_faces) + 4:
                    raise RuntimeError(
                        f"Boundary walk around vertex {v} did not terminate."
                    )
            done_v.add(v)

            # ---- orient outward (away from the dual cell of v) -----------
            ref = vert_out.get(v)
            if ref is None or float(np.linalg.norm(ref)) < 1e-300:
                raise RuntimeError(
                    f"Boundary vertex {v} has no outward reference normal."
                )
            if _newell_dot(xyz, dual_face, ref) < 0.0:
                dual_face.reverse()
                feat_idx = [len(dual_face) - 1 - i for i in reversed(feat_idx)]

            # ---- split at feature edges (polyDualMesh splitFace) ---------
            nf = len(feat_idx)
            if nf < 2:
                bnd_by_patch[pi].append(dual_face)
                bown_by_patch[pi].append(int(vid_to_cell[v]))
                continue
            if P.feat_vertex[v]:
                # feature point: face-centre decomposition, fans from the
                # primal vertex dual point (the "feature point becomes face
                # centre" of polyDualMesh)
                vp = pt_vert(v)
                for i in range(nf):
                    start = feat_idx[i]
                    end = feat_idx[(i + 1) % nf]
                    sub = [vp]
                    k = start
                    while True:
                        sub.append(dual_face[k])
                        if k == end:
                            break
                        k = (k + 1) % len(dual_face)
                    bnd_by_patch[pi].append(sub)
                    bown_by_patch[pi].append(int(vid_to_cell[v]))
            else:
                # arcs between consecutive feature midpoints
                for i in range(nf):
                    start = feat_idx[i]
                    end = feat_idx[(i + 1) % nf]
                    sub: list[int] = []
                    k = start
                    while True:
                        sub.append(dual_face[k])
                        if k == end:
                            break
                        k = (k + 1) % len(dual_face)
                    if len(sub) >= 3:
                        bnd_by_patch[pi].append(sub)
                        bown_by_patch[pi].append(int(vid_to_cell[v]))

    # ------------------------------------------------------------------
    # P3: topology + geometry invariants, checked before anything is written
    # ------------------------------------------------------------------

    def _validate(self, res: HexPolyDualResult, M, P) -> None:
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
        if not self._collapse:
            # the median-dual tiling is an exact partition of the primal
            # domain: total volume must be conserved to machine precision.
            if abs(res.volume_after - res.volume_before) > 1e-6 * max(
                abs(res.volume_before), 1e-30
            ):
                raise RuntimeError(
                    f"Volume not conserved: {res.volume_after:.6g} vs "
                    f"{res.volume_before:.6g} (primal)"
                )
        else:
            # collapse mode: the boundary passes through face centroids, so
            # the dual volume differs from the primal by the surface offset
            # (exactly like polyDualMesh).  Invariant = positive, watertight.
            self._log(
                f"[hexdual] collapse mode: dual volume {res.volume_after:.6g} "
                f"vs primal {res.volume_before:.6g} "
                f"({100.0 * (res.volume_after - res.volume_before) / max(res.volume_before, 1e-30):+.3f}% "
                f"surface-offset, same as polyDualMesh)"
            )

        used = set()
        for f in faces:
            used.update(f)
        if len(used) != len(points):
            unused = len(points) - len(used)
            raise RuntimeError(f"{unused} unused points in the dual output")

        # quality numbers via the in-process checkMesh replica
        ctr, _ = _cell_centres(sf, cf, owner, neigh, n_internal, n_cells)
        _, counts = _detect_defects(
            points, faces, sf, cf, ctr, owner, neigh, n_internal, n_cells,
        )
        res.defects = counts

        # Fase 4a: quality-driven smoothing of interior vertices, guided by
        # the same in-process defect detector (keep-best — never regresses).
        total_defects = counts["pyramid"] + counts["non_ortho"] + counts["skew"]
        if self._smooth and total_defects > 0:
            self._check_cancel()
            t = time.monotonic()
            from cfmesh_autogui.core.poly_smoother import smooth_dual_mesh

            new_points, new_counts = smooth_dual_mesh(
                points, faces, owner, neigh, n_internal, n_cells,
                _detect_defects, _face_geometry, _cell_centres,
                iterations=self._smooth_iters,
                relaxation=self._smooth_relax,
                log=self._log,
            )
            if new_points is not points:
                points = new_points
                M.points = points
                counts = dict(new_counts)
                res.defects = counts
                self._log(
                    f"[hexdual] smoothing applied: defects now "
                    f"{counts['pyramid']}/{counts['non_ortho']}/{counts['skew']} "
                    f"[{time.monotonic() - t:.1f}s]"
                )

        res.n_cells_after = n_cells
        res.n_poly_cells = n_cells
        res.n_internal_faces = n_internal
        res.n_boundary_faces = n_faces - n_internal
        res.n_boundary_quads = M.n_boundary_quads
        res.n_points_after = len(points)
        res.n_edges = P.n_edges

    # ------------------------------------------------------------------
    # P4: write (non-destructive: constant/<out_rel>)
    # ------------------------------------------------------------------

    def _write(self, M, P, res: HexPolyDualResult) -> None:
        out_dir = self._case_dir / "constant" / self._out_rel
        if out_dir.exists():
            import shutil

            shutil.rmtree(out_dir)
        foam_mesh_io.write_polymesh(
            out_dir, M.points, M.faces, M.owner, M.neigh, M.patches,
        )
        res.out_dir = str(out_dir)
        self._log(
            f"[hexdual] wrote {M.n_cells:,} polyhedral cells, {len(M.faces):,} "
            f"faces, {len(M.points):,} points -> {out_dir}"
        )


# ---------------------------------------------------------------------------
# Edge-fan walk: order the cells around a primal edge (ported verbatim from
# tet_poly_dual.py — the construction is cell-shape-agnostic).
# ---------------------------------------------------------------------------

def _walk_edge_fan(group, bfaces, cf_map, owner_l, neigh_l, va, vb):
    """Order the cells around a primal edge.

    Returns (ring, link, closed). `ring[i]` is the i-th cell; `link[i]` is the
    primal face between `ring[i-1]` and `ring[i]`. For an open fan `link` has
    one extra entry and its two ends are the terminating boundary faces.

    In any valid polyhedral cell, an edge belongs to exactly two of the cell's
    faces (a closed 2-manifold cell boundary), so every cell in the group has
    degree two and the incidence graph is a simple ring (interior edge) or a
    simple path (boundary edge).
    """
    ring: list[int] = []
    walk: list[int] = []
    if bfaces:
        if len(bfaces) != 2:
            raise RuntimeError(
                f"Non-manifold primal edge ({va},{vb}): {len(bfaces)} boundary "
                "faces (expected 2). The input is not a closed volume."
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
        # NOTE: an open fan has len(ring) == k cells but len(group) == k+1
        # faces (adjacent cells share their edge faces), so no equality check
        # here — the tet original guards only the closed-ring case below.
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
            f"Edge ring at ({va},{vb}) visited {len(ring)} of {len(group)} "
            "incident cells — non-manifold primal edge."
        )
    return ring, [start_face] + walk, True


def _emit_edge_face(poly, ca, cb, xyz, pts_in, va, vb, faces, own, nb):
    """Append one dual face, wound so its normal points owner -> neighbour."""
    if len(poly) < 3:
        raise RuntimeError(f"Degenerate dual face at edge ({va},{vb}).")
    # Orientation sign only: Newell normal dotted with (vb - va).
    if _newell_dot(xyz, poly, pts_in[vb] - pts_in[va]) < 0.0:
        poly.reverse()
    if ca > cb:
        ca, cb = cb, ca
        poly.reverse()
    faces.append(poly)
    own.append(ca)
    nb.append(cb)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Hex(-dominant)/cut-cell -> polyhedral dual (native poly).",
    )
    ap.add_argument("case_dir", help="OpenFOAM case directory")
    ap.add_argument("--out", default="polyMesh_dual",
                    help="output mesh dir under constant/ (default: polyMesh_dual)")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    conv = HexPolyDualConverter(Path(args.case_dir), log=None, out_rel=args.out)
    res = conv.run()
    if not res.success:
        print(f"FAILED: {'; '.join(res.errors)}")
        return 1
    print(
        f"OK: {res.n_cells_before:,} hex/poly -> {res.n_cells_after:,} poly cells "
        f"({res.n_internal_faces:,} internal + {res.n_boundary_faces:,} boundary "
        f"faces, {res.n_points_after:,} points) in {res.stage_times['total']:.1f}s"
    )
    print(
        f"defects (in-process checkMesh replica): "
        f"{res.defects.get('pyramid', 0)} inverted pyramids, "
        f"{res.defects.get('non_ortho', 0)} non-ortho, "
        f"{res.defects.get('skew', 0)} skew — max closure "
        f"{res.max_closure_error:.2e}, min volume {res.min_cell_volume:.3e}"
    )
    print(f"wrote -> {res.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
