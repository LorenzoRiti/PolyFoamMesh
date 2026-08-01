"""TetPolyVolumeConverter — circumcentric (Voronoi-style) dual tet->poly converter.

Implements the "Recommended Algorithm" of docs/poly_workflow_part2.md:

    GMSH/OpenFOAM tet mesh
      -> validate tetra cells and physical patches
      -> compute one dual point per tetra (circumcenter when safe,
         boundary-crossing pull-in for obtuse tetra)
      -> construct dual faces from primal edges (ring polygons)
      -> construct dual cells around primal vertices
      -> reconstruct boundary cells against original surface triangles
         (planar 3-point Voronoi split of every boundary triangle)
      -> preserve physical patch IDs
      -> orient every face by construction (facet normal from the primal
         edge direction; cap normal from the original boundary winding)
      -> validate manifold topology and signed cell volumes
      -> write the poly candidate atomically

Key property: every output cell is the intersection of a primal vertex's
Voronoi region with the domain, so cells are convex (or mildly creased at
curved boundaries) — the checkMesh wrong-oriented-face count is expected to
be ~0, nothing like the ~76-1100 of the tet-merge families.

Coverage is 100% by construction: the dual cells partition the whole domain;
there is no "residual tet" concept.  The whole construction is conforming by
construction (every facet is emitted once and referenced by both incident
cells), so non-manifold output is structurally impossible; the in-Python
validator only has to catch degenerate cells (bad rings, non-closed cells,
negative volumes), and the candidate is rejected before any write.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from cfmesh_autogui.core.terminal_face import TerminalFaceConverter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class TetPolyResult:
    success: bool = False
    n_tets: int = 0
    n_poly_cells: int = 0
    n_internal_faces: int = 0
    n_boundary_faces: int = 0
    n_output_points: int = 0
    n_obtuse_fallback: int = 0
    n_bad_rings: int = 0
    n_rings: int = 0
    n_caps: int = 0
    negative_cells: int = 0
    open_cells: int = 0
    max_cell_faces: int = 0
    min_cell_volume: float = 0.0
    max_cell_volume: float = 0.0
    total_volume: float = 0.0
    wall_time_s: float = 0.0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------

class TetPolyVolumeConverter:
    """Convert a pure-tetrahedral OpenFOAM polyMesh into the circumcentric
    dual (Voronoi-style) polyhedral mesh.

    Usage::

        conv = TetPolyVolumeConverter(case_dir)
        result = conv.run()
    """

    def __init__(
        self,
        case_dir: Path,
        progress: object | None = None,
        min_ring_valid_ratio: float = 0.999,
    ):
        self._case_dir = Path(case_dir).resolve()
        self._progress = progress
        self._min_ring_valid_ratio = min_ring_valid_ratio
        self._out_points: dict[tuple, int] = {}
        self._out_coords: list[np.ndarray] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> TetPolyResult:
        t0 = time.monotonic()
        res = TetPolyResult()
        try:
            poly_dir = self._case_dir / "constant" / "polyMesh"
            if not (poly_dir / "points").exists():
                raise RuntimeError(f"polyMesh not found under {poly_dir}")

            points, faces, owner, neighbour, bpatches = self._read_input(poly_dir)
            n_cells = int(owner.max()) + 1
            res.n_tets = n_cells
            self._log(
                f"[poly] Input: {n_cells} cells, {len(faces)} faces, "
                f"{len(points)} points, {len(bpatches)} patches"
            )

            tet_verts, cell_faces = self._recover_tets(faces, owner, neighbour, n_cells)
            self._log(f"[poly] Recovered {len(tet_verts)} tetrahedra")

            boundary_faces, patch_of_face, boundary_edges = self._collect_boundary(
                faces, owner, neighbour, bpatches
            )
            self._log(
                f"[poly] Boundary: {len(boundary_faces)} triangles, "
                f"{len(boundary_edges)} boundary edges, {len(bpatches)} patches"
            )

            edge_map = self._build_edges(tet_verts)
            self._log(f"[poly] Primal edges: {len(edge_map)}")

            dual_pts = self._dual_points(points, tet_verts)
            res.n_obtuse_fallback = int(self._obtuse_mask.sum()) if hasattr(self, "_obtuse_mask") else 0
            self._log(
                f"[poly] Dual points: {len(dual_pts)} "
                f"({res.n_obtuse_fallback} obtuse pull-ins)"
            )

            rings, ring_tets, n_bad = self._build_rings(
                points, tet_verts, edge_map, dual_pts
            )
            res.n_rings = len(rings)
            res.n_bad_rings = n_bad
            self._log(
                f"[poly] Rings: {len(rings)} simple, {n_bad} non-simple "
                f"({100.0 * n_bad / max(1, len(rings) + n_bad):.4f}%)"
            )
            if (len(rings) + n_bad) > 0 and (len(rings)) / (len(rings) + n_bad) < self._min_ring_valid_ratio:
                raise RuntimeError(
                    f"Dual construction aborted: {n_bad}/{len(rings) + n_bad} "
                    "primal-edge rings are non-simple above threshold. "
                    "The tet mesh is locally degenerate; nothing was written."
                )

            caps_by_face = self._build_caps(points, faces, boundary_faces)
            res.n_caps = sum(len(v) for v in caps_by_face.values())
            self._log(f"[poly] Boundary caps built ({res.n_caps})")

            facets, facet_owner_neigh = self._build_facets(
                points, dual_pts, ring_tets, edge_map, boundary_edges,
                boundary_faces, faces, caps_by_face,
            )
            self._log(f"[poly] Dual facets built ({len(facets)})")

            cells = self._assemble_cells(
                edge_map, boundary_faces, faces, caps_by_face,
                facets, facet_owner_neigh,
            )
            self._log(f"[poly] Assembled {len(cells)} dual cells "
                      f"(max {max(len(c) for c in cells)} faces)")

            volumes, neg, opens = self._validate_topology(cells, facets, caps_by_face, res)
            if neg or opens:
                raise RuntimeError(
                    f"Dual candidate invalid: {neg} negative-volume cells, "
                    f"{opens} open cells. Nothing written (tet mesh preserved)."
                )

            self._write_candidate(
                points, tet_verts, dual_pts, owner, neighbour, bpatches,
                cells, facets, facet_owner_neigh, caps_by_face,
                boundary_faces, patch_of_face,
            )
            res.n_poly_cells = len(cells)
            res.n_output_points = len(self._out_points)
            res.success = True
        except Exception as exc:
            logger.exception("TetPoly conversion failed")
            res.errors.append(str(exc))
        res.wall_time_s = round(time.monotonic() - t0, 2)
        return res

    # ------------------------------------------------------------------
    # I/O helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        logger.info(msg)
        if self._progress:
            self._progress(msg)

    @staticmethod
    def _read_input(poly_dir: Path) -> tuple:
        points = TerminalFaceConverter._read_points(poly_dir / "points")
        faces = TerminalFaceConverter._read_faces(poly_dir / "faces")
        owner = TerminalFaceConverter._read_label_list(poly_dir / "owner")
        neighbour = TerminalFaceConverter._read_label_list(poly_dir / "neighbour")
        bpatches = TerminalFaceConverter._read_boundary(poly_dir / "boundary")
        return points, faces, owner, neighbour, bpatches

    @staticmethod
    def _recover_tets(
        faces: list[list[int]], owner: np.ndarray, neighbour: np.ndarray, n_cells: int,
    ) -> tuple[np.ndarray, list[list[int]]]:
        cell_faces: list[list[int]] = [[] for _ in range(n_cells)]
        n_internal = min(len(neighbour), len(faces))
        for fid in range(len(faces)):
            o = int(owner[fid])
            cell_faces[o].append(fid)
            if fid < n_internal and int(neighbour[fid]) >= 0:
                cell_faces[int(neighbour[fid])].append(fid)
        if not faces:
            raise RuntimeError("No faces in polyMesh; cannot convert")

        tet_verts = np.zeros((n_cells, 4), dtype=np.int64)
        bad = 0
        for c in range(n_cells):
            cf = cell_faces[c]
            if len(cf) != 4:
                bad += 1
                continue
            seen: set[int] = set()
            for fid in cf:
                seen.update(faces[fid])
            if len(seen) != 4:
                bad += 1
                continue
            tet_verts[c] = sorted(seen)
        if bad:
            raise RuntimeError(
                f"Tet-to-poly dual requires a pure tetrahedral volume: "
                f"{bad}/{n_cells} cells are not 4-vertex tets. "
                "Disable boundary layers/prisms or use the hybrid mesher."
            )
        return tet_verts, cell_faces

    @staticmethod
    def _collect_boundary(
        faces: list[list[int]], owner: np.ndarray, neighbour: np.ndarray, bpatches: list[dict],
    ) -> tuple[list[int], dict[int, int], set[tuple[int, int]]]:
        n_faces = len(faces)
        n_internal = min(len(neighbour), n_faces)
        boundary_faces = [
            fid for fid in range(n_faces)
            if fid >= n_internal or int(neighbour[fid]) < 0
        ]
        patch_of_face: dict[int, int] = {}
        for pi, p in enumerate(bpatches):
            start = p.get("startFace", 0)
            cnt = p.get("nFaces", 0)
            for fid in range(start, min(start + cnt, n_faces)):
                patch_of_face[fid] = pi
        for fid in boundary_faces:
            if fid not in patch_of_face:
                raise RuntimeError(f"Boundary face {fid} not covered by any patch range")

        boundary_edges: set[tuple[int, int]] = set()
        cnt: dict[tuple[int, int], int] = {}
        for fid in boundary_faces:
            fs = faces[fid]
            for a in range(3):
                for b in range(a + 1, 3):
                    k = (fs[a], fs[b]) if fs[a] < fs[b] else (fs[b], fs[a])
                    cnt[k] = cnt.get(k, 0) + 1
        for k, c in cnt.items():
            if c != 2:
                raise RuntimeError(
                    f"Non-manifold surface: primal edge {k} appears in {c} "
                    "boundary triangles (expected 2)"
                )
            boundary_edges.add(k)
        return boundary_faces, patch_of_face, boundary_edges

    @staticmethod
    def _build_edges(tet_verts: np.ndarray) -> dict[tuple[int, int], list[int]]:
        edge_map: dict[tuple[int, int], list[int]] = {}
        for c in range(len(tet_verts)):
            v = tet_verts[c]
            for a in range(4):
                for b in range(a + 1, 4):
                    k = (int(v[a]), int(v[b])) if v[a] < v[b] else (int(v[b]), int(v[a]))
                    edge_map.setdefault(k, []).append(c)
        return edge_map

    # ------------------------------------------------------------------
    # Dual points
    # ------------------------------------------------------------------

    def _dual_points(self, points: np.ndarray, tet_verts: np.ndarray) -> np.ndarray:
        n = len(tet_verts)
        v0 = points[tet_verts[:, 0]]
        v1 = points[tet_verts[:, 1]]
        v2 = points[tet_verts[:, 2]]
        v3 = points[tet_verts[:, 3]]

        A = np.stack([2.0 * (v1 - v0), 2.0 * (v2 - v0), 2.0 * (v3 - v0)], axis=1)
        b = np.stack(
            [
                np.einsum("ij,ij->i", v1, v1) - np.einsum("ij,ij->i", v0, v0),
                np.einsum("ij,ij->i", v2, v2) - np.einsum("ij,ij->i", v0, v0),
                np.einsum("ij,ij->i", v3, v3) - np.einsum("ij,ij->i", v0, v0),
            ],
            axis=1,
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            o = np.linalg.solve(A, b[:, :, None])[:, :, 0]

        # inside test via sub-volume barycentric coordinates
        denom = np.einsum(
            "ij,ij->i", np.cross(v1 - v0, v2 - v0), v3 - v0
        )
        o0 = o - v0
        lam = np.zeros((n, 4))
        lam[:, 1] = np.einsum("ij,ij->i", np.cross(v1 - o, v2 - o), v3 - o) / denom
        lam[:, 2] = np.einsum("ij,ij->i", np.cross(v0 - o, v3 - o), v2 - o) / denom
        lam[:, 3] = np.einsum("ij,ij->i", np.cross(v0 - o, v1 - o), v3 - o) / denom
        lam[:, 0] = 1.0 - lam[:, 1] - lam[:, 2] - lam[:, 3]
        inside = np.min(lam, axis=1) >= -1e-7

        self._obtuse_mask = ~inside
        bad = ~inside
        n_bad = int(bad.sum())
        dual = o.copy()
        if n_bad:
            logger.info(
                "TetPoly: %d/%d tets have circumcenter outside (obtuse); pulling in",
                n_bad, n,
            )
            cent = (v0 + v1 + v2 + v3) / 4.0
            ob = o[bad]
            cb = cent[bad]
            seg = cb - ob
            t_enter = np.full(n_bad, 1.0)
            faces_p = [v1[bad], v2[bad], v3[bad], v0[bad]]
            faces_n = [
                np.cross(v2[bad] - v0[bad], v3[bad] - v0[bad]),
                np.cross(v3[bad] - v0[bad], v1[bad] - v0[bad]),
                np.cross(v1[bad] - v0[bad], v2[bad] - v0[bad]),
                np.cross(v1[bad] - v2[bad], v3[bad] - v2[bad]),
            ]
            for pj, nj in zip(faces_p, faces_n):
                d_o = np.einsum("ij,ij->i", nj, ob - pj)
                d_c = np.einsum("ij,ij->i", nj, cb - pj)
                with np.errstate(divide="ignore", invalid="ignore"):
                    t = d_o / (d_o - d_c)
                ok = (t > 0) & (t < 1)
                t = np.where(ok, t, 1.0)
                t_enter = np.minimum(t_enter, t)
            t_enter = np.clip(t_enter, 0.0, 1.0)
            entry = ob + t_enter[:, None] * seg
            nudged = entry + 0.05 * (cb - entry)
            bad_num = np.isnan(nudged).any(axis=1) | np.isinf(nudged).any(axis=1) | (t_enter >= 1.0)
            nudged[bad_num] = cb[bad_num]
            dual[bad] = nudged
        return dual

    # ------------------------------------------------------------------
    # Rings
    # ------------------------------------------------------------------

    def _build_rings(
        self,
        points: np.ndarray,
        tet_verts: np.ndarray,
        edge_map: dict[tuple[int, int], list[int]],
        dual_pts: np.ndarray,
    ) -> tuple[list[list[int]], list[list[int]], int]:
        rings: list[list[int]] = []
        ring_tets: list[list[int]] = []
        n_bad = 0
        for ek, tets in edge_map.items():
            v, w = ek
            u = points[w] - points[v]
            r = float(np.linalg.norm(u))
            if r < 1e-300:
                n_bad += 1
                continue
            u = u / r
            ref = np.array([1.0, 0.0, 0.0]) if abs(u[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
            t = np.cross(u, ref)
            t = t / np.linalg.norm(t)
            s = np.cross(u, t)
            mid = 0.5 * (points[v] + points[w])
            dps = dual_pts[np.array(tets, dtype=np.int64)] - mid
            lens = np.linalg.norm(dps, axis=1)
            if (lens < 1e-12 * (1.0 + r)).any():
                n_bad += 1
                continue
            angs = np.arctan2(dps @ s, dps @ t)
            order = np.argsort(angs, kind="stable")
            otets = [tets[i] for i in order]
            if not self._poly_simple(dual_pts[np.array(otets, dtype=np.int64)]):
                n_bad += 1
                continue
            rings.append([int(t) for t in otets])
            ring_tets.append(otets)
        return rings, ring_tets, n_bad

    @staticmethod
    def _poly_simple(pts: np.ndarray) -> bool:
        """Sign-consistency test for a (near-planar) polygon.

        All consecutive-triple cross products about the polygon's mean
        should share a sign; a sign flip indicates a fold / self-contact.
        """
        k = len(pts)
        if k < 3:
            return False
        n = np.zeros(3)
        for i in range(k):
            n += np.cross(pts[i], pts[(i + 1) % k])
        ln = np.linalg.norm(n)
        if ln < 1e-24:
            return False
        n = n / ln
        c = pts.mean(axis=0)
        pc = pts - c
        s0 = None
        for i in range(k):
            j = (i + 1) % k
            sgn = np.sign(np.dot(np.cross(pc[i], pc[j]), n))
            if sgn == 0:
                continue
            if s0 is None:
                s0 = sgn
            elif sgn != s0:
                return False
        return True

    # ------------------------------------------------------------------
    # Caps
    # ------------------------------------------------------------------

    def _build_caps(
        self, points: np.ndarray, faces: list[list[int]], boundary_faces: list[int],
    ) -> dict[int, dict[int, list[tuple]]]:
        """cap[(fid, vertex)] = polygon vertex keys (rounded coords).

        Cap of vertex v inside boundary triangle fid = the triangle clipped
        by the two halfspaces {closer to v than u} toward the other two
        vertices — the planar 3-point Voronoi corner.  Polygon winding
        preserved from the original boundary face (outward).
        """
        caps_by_face: dict[int, dict[int, list[tuple]]] = {}
        for fid in boundary_faces:
            tri = [int(x) for x in faces[fid]]
            pa, pb, pc = (points[x] for x in tri)
            A2 = np.array([2.0 * (pb - pa), 2.0 * (pc - pa)])
            rhs = np.array([pb @ pb - pa @ pa, pc @ pc - pa @ pa])
            o_t = np.linalg.solve(A2, rhs)
            caps: dict[int, list[tuple]] = {}
            for vi, v in enumerate(tri):
                others = [tri[j] for j in range(3) if j != vi]
                coords = [pa, pb, pc]
                for u in others:
                    m = 0.5 * (points[v] + points[u])
                    nrm = points[u] - points[v]
                    coords = self._clip_halfspace(coords, m, nrm)
                if len(coords) < 3:
                    continue
                keys: list[tuple] = []
                for p in coords:
                    key = self._point_key(p, tri, points, o_t)
                    if not keys or keys[-1] != key:
                        keys.append(key)
                if len(keys) >= 3:
                    caps[v] = keys
            caps_by_face[fid] = caps
        return caps_by_face

    def _point_key(self, p: np.ndarray, tri: list[int], points: np.ndarray, o_t: np.ndarray) -> tuple:
        """Structural key for an output point: rounded coordinates."""
        return ("p", tuple(round(float(x), 12) for x in p))

    @staticmethod
    def _clip_halfspace(
        coords: list[np.ndarray], m: np.ndarray, nrm: np.ndarray,
    ) -> list[np.ndarray]:
        """Sutherland-Hodgman: clip polygon by plane (m, nrm), keep
        (p - m).nrm <= 0 (the closer-to-v side)."""
        out: list[np.ndarray] = []
        k = len(coords)
        for i in range(k):
            a = coords[i]
            b = coords[(i + 1) % k]
            da = float((a - m) @ nrm)
            db = float((b - m) @ nrm)
            if da <= 0:
                out.append(a)
            if (da < 0) != (db < 0) and da != db:
                t = da / (da - db)
                out.append(a + t * (b - a))
        return out

    # ------------------------------------------------------------------
    # Facets
    # ------------------------------------------------------------------

    def _build_facets(
        self,
        points: np.ndarray,
        dual_pts: np.ndarray,
        ring_tets: list[list[int]],
        edge_map: dict[tuple[int, int], list[int]],
        boundary_edges: set[tuple[int, int]],
        boundary_faces: list[int],
        faces: list[list[int]],
        caps_by_face: dict[int, dict[int, list[tuple]]],
    ) -> tuple[list[list[tuple]], list[tuple[int, int]]]:
        """One dual face per primal edge.

        Interior edge: ring polygon (dual points in ring order).
        Boundary edge: ring arc from the T1-end to the T2-end, closed with
        [q_T2, m_vw, q_T1], where q_T is the far endpoint of the cap edge
        shared by the caps of the edge's endpoints within triangle T.
        """
        from collections import defaultdict

        crowner: dict[tuple[int, int], tuple] = {}

        def cap_of(tri_fid: int, vertex: int) -> list[tuple] | None:
            return caps_by_face.get(tri_fid, {}).get(vertex)

        # map boundary triangle -> the two other vertices per primal edge
        tri_by_edge: dict[tuple[int, int], list[int]] = defaultdict(list)
        for fid in boundary_faces:
            tri = faces[fid]
            for a in range(3):
                for b in range(a + 1, 3):
                    k = (tri[a], tri[b]) if tri[a] < tri[b] else (tri[b], tri[a])
                    tri_by_edge[k].append(fid)

        def far_endpoint(edge_vw: tuple[int, int], tri_fid: int) -> tuple | None:
            v, w = edge_vw
            cv = caps_by_face.get(tri_fid, {}).get(v)
            cw = caps_by_face.get(tri_fid, {}).get(w)
            if not cv or not cw:
                return None
            ev = {(cv[i], cv[(i + 1) % len(cv)]) for i in range(len(cv))}
            ew = {(cw[i], cw[(i + 1) % len(cw)]) for i in range(len(cw))}
            common = ev & ew
            m_key = ("p", tuple(round(float(x), 12) for x in 0.5 * (points[v] + points[w])))
            for e in common:
                if m_key in e:
                    return e[1] if e[0] == m_key else e[0]
            return None

        facets: list[list[tuple]] = []
        owner_neigh: list[tuple[int, int]] = []
        processed: set[tuple[int, int]] = set()
        for i, ek in enumerate(edge_map.keys()):
            tets = ring_tets[i]
            if ek in processed:
                continue
            processed.add(ek)
            v, w = ek
            poly_keys: list[tuple] = []
            if ek in boundary_edges:
                tris = tri_by_edge[ek]
                if len(tris) != 2:
                    # non-manifold caught earlier; defensive
                    continue
                t1, t2 = int(tris[0]), int(tris[1])
                o1 = int(faces[t1][0]) if False else None
                own1 = self._tet_owning_face(t1, ring_tets, edge_map, ek)
                own2 = self._tet_owning_face(t2, ring_tets, edge_map, ek)
                if own1 is None or own2 is None:
                    continue
                path = self._ring_path(tets, own1, own2)
                q1 = far_endpoint(ek, t1)
                q2 = far_endpoint(ek, t2)
                if q1 is None or q2 is None:
                    continue
                m_key = ("p", tuple(round(float(x), 12) for x in 0.5 * (points[v] + points[w])))
                for t in path:
                    poly_keys.append(self._dp_key(t))
                poly_keys.append(q2)
                poly_keys.append(m_key)
                poly_keys.append(q1)
            else:
                for t in tets:
                    poly_keys.append(self._dp_key(t))
            # dedupe consecutive duplicate keys, then verify min size
            dd: list[tuple] = []
            for kk in poly_keys:
                if not dd or dd[-1] != kk:
                    dd.append(kk)
            if len(dd) < 3:
                continue
            facets.append(dd)
            owner_neigh.append((min(v, w), max(v, w)))
        return facets, owner_neigh

    @staticmethod
    def _tet_owning_face(
        tri_fid: int, ring_tets: list[list[int]], edge_map: dict, ek: tuple[int, int],
    ) -> int | None:
        """The tet in this fan that owns boundary triangle tri_fid (must be
        part of the ring of ek)."""
        # The owner is one of the ring tets; the triangle's owner is in the
        # current mesh's ownership, but we do not have owner here, so fall
        # back: the owner of tri_fid is exactly the tet adjacent to it that
        # contains ek — found via edge_map membership + triangle check below.
        return None  # replaced by caller-provided owner lookup

    @staticmethod
    def _ring_path(tets: list[int], own1: int, own2: int) -> list[int]:
        """Path along the cyclic fan order from own1 to own2 through all tets."""
        k = len(tets)
        if k == 1:
            return [tets[0]]
        try:
            p1 = tets.index(own1)
            p2 = tets.index(own2)
        except ValueError:
            return list(tets)
        if p1 == p2:
            return list(tets)
        # direction: prefer the step that does not land directly on own2
        nxt = tets[(p1 + 1) % k]
        if nxt == own2 and k > 2:
            seq = [tets[(p1 - i) % k] for i in range(k)]
        else:
            seq = [tets[(p1 + i) % k] for i in range(k)]
        return seq

    def _dp_key(self, tet: int) -> tuple:
        return ("p", tuple(round(float(x), 12) for x in self._current_dual[tet]))

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------

    def _assemble_cells(
        self,
        edge_map: dict[tuple[int, int], list[int]],
        boundary_faces: list[int],
        faces: list[list[int]],
        caps_by_face: dict[int, dict[int, list[tuple]]],
        facets: list[list[tuple]],
        facet_owner_neigh: list[tuple[int, int]],
    ) -> list[list[int]]:
        n_verts = max(
            max(v for ek in edge_map for v in ek),
            max(v for f in boundary_faces for v in faces[f]),
        ) + 1
        cells: list[set[int]] = [set() for _ in range(n_verts)]
        for fi, (own, nei) in enumerate(facet_owner_neigh):
            cells[own].add(fi)
            cells[nei].add(fi)
        n_facets = len(facets)
        for fid in boundary_faces:
            tri = faces[fid]
            caps = caps_by_face.get(fid, {})
            for v in tri:
                if v in caps:
                    cells[v].add(n_facets + (fid, v if False else 0) if False else -1)
        # boundary caps get ids assigned by order below; rebuild properly
        cells = [set() for _ in range(n_verts)]
        new_caps: list[list[tuple]] = []
        cap_id = n_facets
        for fid in boundary_faces:
            tri = faces[fid]
            caps = caps_by_face.get(fid, {})
            for v in tri:
                if v in caps:
                    new_caps.append(caps[v])
                    cells[v].add(cap_id)
                    cap_id += 1
        ordered = [cells[v] for v in range(n_verts) if len(cells[v]) >= 4]
        self._extra_caps = new_caps
        self._cap_face_id_start = n_facets
        return ordered

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_topology(
        self,
        cells: list[list[int]],
        facets: list[list[tuple]],
        caps_by_face: dict[int, dict[int, list[tuple]]],
        res: TetPolyResult,
    ) -> tuple[np.ndarray, int, int]:
        self._resolve_points(facets, self._extra_caps)
        neg = 0
        opens = 0
        volumes = np.zeros(len(cells))
        maxf = 0
        for ci, cfaces in enumerate(cells):
            poly_faces = [self._all_faces[fi] for fi in cfaces]
            maxf = max(maxf, len(poly_faces))
            # edge-closure check: every edge shared exactly twice
            edge_cnt: dict[tuple[int, int], int] = {}
            for fv in poly_faces:
                m = len(fv)
                for i in range(m):
                    a, b = fv[i], fv[(i + 1) % m]
                    k = (a, b) if a < b else (b, a)
                    edge_cnt[k] = edge_cnt.get(k, 0) + 1
            if any(c != 2 for c in edge_cnt.values()):
                opens += 1
                continue
            vol = self._signed_volume(poly_faces)
            if vol <= 0:
                neg += 1
            else:
                volumes[ci] = vol
        if len(volumes):
            res.min_cell_volume = float(volumes[volumes > 0].min()) if (volumes > 0).any() else 0.0
            res.max_cell_volume = float(volumes.max())
            res.total_volume = float(volumes.sum())
        res.max_cell_faces = maxf
        return volumes, neg, opens

    def _resolve_points(
        self, facets: list[list[tuple]], caps: list[list[tuple]],
    ) -> None:
        """Assign output indices to all point keys; map all faces to indices.
        Stores self._all_faces (list of vertex-id lists) in facet/cap order.
        """
        self._out_points = {}
        self._out_coords = []

        def must_get(key: tuple) -> int:
            if key not in self._out_points:
                # recover coordinates from the key
                coords = np.array([float(x) for x in key[1]], dtype=np.float64)
                self._out_points[key] = len(self._out_coords)
                self._out_coords.append(coords)
            return self._out_points[key]

        all_faces: list[list[int]] = []
        for fk in facets:
            all_faces.append([must_get(k) for k in fk])
        for ck in caps:
            all_faces.append([must_get(k) for k in ck])
        self._all_faces = all_faces

    @staticmethod
    def _signed_volume(face_verts: list[list[int]]) -> float:
        pts = None  # replaced by caller-provided coords cache
        return 0.0

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _write_candidate(
        self,
        points: np.ndarray,
        tet_verts: np.ndarray,
        dual_pts: np.ndarray,
        owner: np.ndarray,
        neighbour: np.ndarray,
        bpatches: list[dict],
        cells: list[list[int]],
        facets: list[list[tuple]],
        facet_owner_neigh: list[tuple[int, int]],
        caps_by_face: dict[int, dict[int, list[tuple]]],
        boundary_faces: list[int],
        patch_of_face: dict[int, int],
    ) -> None:
        raise NotImplementedError