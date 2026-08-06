"""Native mesher — cut-cell hex-dominant mesh from a tessellated surface.

Phase 3 of the in-house polyhedral mesher.  This module generates a hex
(-dominant) mesh DIRECTLY from a closed tessellated surface (trimesh, from
the app's own CAD tessellation) — no GMSH, no external mesher:

    1. background uniform Cartesian grid over the surface bounding box
    2. classify each grid point inside/outside the fluid via trimesh
       containment (ray casting)
    3. cell status: all-8-corners-inside -> hex cell; all outside -> skipped;
       mixed -> CUT cell
    4. cut cell: kept region = the part of the box inside the fluid, rebuilt
       as the convex hull of {inside corners} U {surface crossings on the 12
       box edges (bisection on signed distance)}
    5. faces of the kept region, exactly partitioned:
       - box-face parts: 2D convex hull of {inside corners of the face} U
         {crossings on the face's edges} -> canonical polygon, so two
         adjacent cells compute the IDENTICAL shared face (a neighbour across
         a face whose edges are crossed is itself a cut cell, because the
         crossed edge has one outside endpoint among its corners);
       - cap (surface-side) facets: the 3D hull facets not lying on any box
         plane -> boundary triangles on the surface;
    6. global point/face dedup (crossings on a shared box edge are computed
       independently by both cells from the same surface -> identical
       coordinates), owner/neighbour, boundary patches per input mesh.

The output is a valid, watertight hex+poly mesh whose boundary approximates
the surface with chord facets (snappy-style castellated phase, no snapping).
Boundary conformity is within one cell size on curvature, which the Phase 3
gate measures against cfMesh cartesianMesh on the same case.  The mesh feeds
straight into `hex_poly_dual` for the 100%-poly pass.

Known limitations (measured, not hidden):
- single-sheet cells only: a cell whose fluid region is thin or disjoint
  (thin wall < one cell) falls back to corner-majority with a warning;
- uniform grid only (no octree refinement yet), so small features need a
  small global cell size.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import trimesh
from scipy.spatial import ConvexHull

from cfmesh_autogui.core import foam_mesh_io as fio
from cfmesh_autogui.core.tet_poly_dual import _cell_centres, _detect_defects, _face_geometry

logger = logging.getLogger(__name__)

# local corner offsets of a hex cell (OpenFOAM hex model order)
_CORNERS = np.array([
    [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
    [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
], dtype=np.int64)
# 12 local edges of a hex cell (pairs of corner indices)
_EDGES = np.array([
    [0, 1], [1, 2], [2, 3], [3, 0],
    [4, 5], [5, 6], [6, 7], [7, 4],
    [0, 4], [1, 5], [2, 6], [3, 7],
], dtype=np.int64)
# 6 local faces of a hex cell (corner index lists, outward cyclic order)
_FACES = [
    [0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4],
    [2, 3, 7, 6], [0, 4, 7, 3], [1, 2, 6, 5],
]
# face -> its 4 bounding local edges (edge indices 0..11)
#  0:(0,1) 1:(1,2) 2:(2,3) 3:(3,0) 4:(4,5) 5:(5,6) 6:(6,7) 7:(7,4)
#  8:(0,4) 9:(1,5) 10:(2,6) 11:(3,7)
_FACE_EDGES = [
    [3, 2, 1, 0], [4, 5, 6, 7], [0, 9, 4, 8],
    [2, 11, 6, 10], [8, 7, 11, 3], [1, 10, 5, 9],
]
_PLANE_TOL = 1e-8


def _newell(points, verts) -> np.ndarray:
    p = np.array([points[v] for v in verts], dtype=np.float64)
    n = np.zeros(3)
    k = len(p)
    for i in range(k):
        j = (i + 1) % k
        n[0] += (p[i, 1] - p[j, 1]) * (p[i, 2] + p[j, 2])
        n[1] += (p[i, 2] - p[j, 2]) * (p[i, 0] + p[j, 0])
        n[2] += (p[i, 0] - p[j, 0]) * (p[i, 1] + p[j, 1])
    return 0.5 * n


def _dedup_poly(ids):
    """Drop consecutive and wrap-around duplicate point ids; None if degenerate."""
    out = []
    for i in ids:
        if not out or out[-1] != i:
            out.append(i)
    while len(out) > 1 and out[0] == out[-1]:
        out.pop()
    if len(out) < 3 or len(set(out)) != len(out):
        return None
    return out


def _clip_poly_plane(poly, axis: int, val: float, keep_le: bool):
    """Sutherland–Hodgman: clip a 3D polygon by the plane p[axis] = val."""
    out = []
    n = len(poly)
    for i in range(n):
        p = poly[i]
        q = poly[(i + 1) % n]
        p_in = (p[axis] <= val) if keep_le else (p[axis] >= val)
        q_in = (q[axis] <= val) if keep_le else (q[axis] >= val)
        if p_in:
            out.append(p)
        if p_in != q_in:
            denom = q[axis] - p[axis]
            if abs(denom) < 1e-300:
                continue  # edge parallel to the plane — cannot cross it
            t = (val - p[axis]) / denom
            r = p + t * (q - p)
            r[axis] = val  # snap to the plane (float hygiene)
            out.append(r)
    return out


def _clip_tri_box(tri: np.ndarray, lo: np.ndarray, hi: np.ndarray):
    """Clip a triangle against an axis-aligned box -> polygon or []."""
    poly = [np.array(v, dtype=np.float64) for v in tri]
    for axis in range(3):
        poly = _clip_poly_plane(poly, axis, hi[axis], keep_le=True)
        if len(poly) < 3:
            return []
        poly = _clip_poly_plane(poly, axis, lo[axis], keep_le=False)
        if len(poly) < 3:
            return []
    return poly


@dataclass
class NativeMeshResult:
    """Outcome of a native cut-cell meshing run."""
    success: bool = False
    n_cells: int = 0
    n_hex_cells: int = 0
    n_cut_cells: int = 0
    n_skipped_thin: int = 0
    n_internal_faces: int = 0
    n_boundary_faces: int = 0
    n_points: int = 0
    n_surface_tris: int = 0
    grid: tuple = (0, 0, 0)
    cell_size: float = 0.0
    volume: float = 0.0
    min_cell_volume: float = 0.0
    max_closure_error: float = 0.0
    defects: dict[str, int] = field(default_factory=dict)
    stage_times: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    out_dir: str = ""


def _ray_hits_triangles(o, d, tris):
    """Möller–Trumbore: t (ray parameter) of each triangle hit by the ray.

    ``o`` is the origin (3,), ``d`` the direction (3,), ``tris`` (n,3,3).
    Returns the positive ``t`` for hits with a strictly interior barycentric
    point; grazing (edge/vertex) hits yield both adjacent triangles at the
    same t — their double count keeps the parity unchanged, which is exactly
    what the scanline classification wants.
    """
    v0 = tris[:, 0]
    e1 = tris[:, 1] - v0
    e2 = tris[:, 2] - v0
    n = np.cross(e1, e2)
    denom = (d[None, :] * n).sum(axis=1)
    ok = np.abs(denom) > 1e-300
    t = ((v0 - o[None, :]) * n).sum(axis=1) / np.where(
        np.abs(denom) > 0, denom, 1.0)
    p = o[None, :] + t[:, None] * d[None, :]
    v0p = p - v0
    d00 = (e1 * e1).sum(axis=1)
    d01 = (e1 * e2).sum(axis=1)
    d11 = (e2 * e2).sum(axis=1)
    d20 = (v0p * e1).sum(axis=1)
    d21 = (v0p * e2).sum(axis=1)
    den = np.maximum(d00 * d11 - d01 * d01, 1e-300)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    u = 1.0 - v - w
    hit = ok & (t > 1e-9) & (u >= -1e-10) & (v >= -1e-10) & (w >= -1e-10)
    return t[hit]


def _drop_contained_coplanar(surface, res=None):
    """Remove triangles contained in a coplanar triangle of the same
    orientation (a common CAD-export artifact: one coarse triangle covering
    several finer overlapping ones).  Overlapping coplanar triangles
    double-cover the cut-cell caps (breaking the cell closure) and corrupt
    the ray-cast parity (double crossing).  Triangles sharing only an edge
    are untouched (their far vertex is outside the covering triangle).
    Returns ``(Trimesh, keep_mask)`` — the caller must reindex patch arrays
    with the mask.
    """
    tris = surface.triangles
    n = len(tris)
    if n < 2:
        return surface, np.ones(n, dtype=bool)
    verts = surface.vertices
    areas = np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0],
                                    tris[:, 2] - tris[:, 0]), axis=1) / 2.0
    normals = surface.face_normals
    keep = np.ones(n, dtype=bool)

    # group by (rounded normal dir, plane offset)
    groups: dict[tuple, list[int]] = {}
    for i in range(n):
        if areas[i] <= 1e-30:
            keep[i] = False
            continue
        nn = normals[i] / np.linalg.norm(normals[i])
        key = (tuple(np.round(nn, 6)),
               round(float(nn @ verts[surface.faces[i][0]]), 6))
        groups.setdefault(key, []).append(i)

    dropped = 0
    for key, idx in groups.items():
        if len(idx) < 2:
            continue
        idx.sort(key=lambda i: -areas[i])
        kept_here: list[int] = []
        # projection plane: drop the dominant normal axis
        axis = int(np.argmax(np.abs(key[0])))
        for i in idx:
            contained = False
            for j in kept_here:
                if _tri_contained_2d(verts, tris, i, j, axis):
                    contained = True
                    break
            if contained:
                keep[i] = False
                dropped += 1
            else:
                kept_here.append(i)
    if not dropped:
        return surface, keep
    if res is not None:
        res.n_surface_tris_dropped = dropped
    return (trimesh.Trimesh(
        vertices=verts, faces=surface.faces[keep], process=False), keep)


def _tri_contained_2d(verts, tris, i, j, axis):
    """True if triangle i is fully inside triangle j in the 2D projection
    (drop axis).  Uses strict-ish containment: all three vertices inside."""
    # project by dropping 'axis' (0/1/2) -> pick the two remaining coords
    cols = [c for c in range(3) if c != axis]
    a = tris[j][:, cols]
    b = tris[i][:, cols]
    # barycentric containment of each vertex of i in j
    v0, v1, v2 = a[0], a[1] - a[0], a[2] - a[0]
    d00 = float(v1 @ v1); d01 = float(v1 @ v2); d11 = float(v2 @ v2)
    den = d00 * d11 - d01 * d01
    if den <= 1e-300:
        return False
    for p in b:
        d20 = float((p - v0) @ v1); d21 = float((p - v0) @ v2)
        v = (d11 * d20 - d01 * d21) / den
        w = (d00 * d21 - d01 * d20) / den
        u = 1.0 - v - w
        tol = 1e-9
        if u < -tol or v < -tol or w < -tol:
            return False
    return True


class NativeMesher:
    """Cut-cell hex-dominant mesher from a closed trimesh surface."""

    def __init__(
        self,
        surface_meshes: list[trimesh.Trimesh],
        cell_size: float,
        log: callable | None = None,
        cancel: callable | None = None,
        margin: float = 0.05,
        fallback_majority: bool = True,
        clean_cells: bool = False,
        margin_growth: float = 1.0,
    ):
        self._surfaces = surface_meshes
        self._cell_size = float(cell_size)
        self._log_cb = log
        self._cancel_cb = cancel
        self._margin = float(margin)
        self._fallback_majority = bool(fallback_majority)
        # Fase 4b (scoping in docs/poly_mesher_STATO.md 6.1): graded
        # background grid in the margin/pad band around the surface's tight
        # bounding box. Cell size stays exactly `cell_size` (uniform) inside
        # the tight bbox -- surface capture is byte-identical to the
        # ungraded path -- and grows geometrically by this ratio per cell
        # outward through the pad band. Default 1.0 reproduces the prior
        # uniform grid exactly (same `_make_grid` code path, same floats).
        # This solves the far-field-domain cost case (large `margin` for
        # external flow); it does NOT solve local refinement near a small
        # internal feature away from the domain boundary -- that needs a
        # true octree with hanging-node face handling, not attempted here
        # (see poly_mesher_STATO.md 6.1 for why).
        self._margin_growth = float(margin_growth)
        # strict-clean mode drops cut cells whose boundary has a non-manifold
        # edge (an edge used once — e.g. the wedge cells of walls nearly
        # parallel to the grid): the median-dual engine requires clean
        # polyhedra.  OFF by default: the castellated mesh keeps the exact
        # volume (the wedge cells are real geometry; snapping would recover
        # them — Phase 3 follow-up).
        self._clean_cells = bool(clean_cells)

    def _log(self, msg: str) -> None:
        logger.info(msg)
        if self._log_cb is not None:
            try:
                self._log_cb(msg)
            except Exception:  # pragma: no cover
                pass

    def _check_cancel(self) -> None:
        if self._cancel_cb is not None and self._cancel_cb():
            raise RuntimeError("native meshing cancelled")

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def run(self, case_dir: Path | str) -> NativeMeshResult:
        t_start = time.monotonic()
        res = NativeMeshResult()
        try:
            merged, patch_of_tri = self._prepare_surface(res)
            t = time.monotonic()
            grid = self._make_grid(merged, res)
            res.stage_times["grid"] = round(time.monotonic() - t, 2)
            self._check_cancel()
            t = time.monotonic()
            M = self._build(merged, patch_of_tri, grid, res)
            res.stage_times["build"] = round(time.monotonic() - t, 2)
            self._check_cancel()
            t = time.monotonic()
            self._validate(res, M)
            res.stage_times["validate"] = round(time.monotonic() - t, 2)
            self._check_cancel()
            t = time.monotonic()
            self._write(case_dir, M, res)
            res.stage_times["write"] = round(time.monotonic() - t, 2)
            res.success = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Native meshing failed")
            res.errors.append(str(exc))
        res.stage_times["total"] = round(time.monotonic() - t_start, 2)
        return res

    # ------------------------------------------------------------------
    # surface prep + grid
    # ------------------------------------------------------------------

    def _prepare_surface(self, res: NativeMeshResult):
        if not self._surfaces:
            raise RuntimeError("No surface meshes given.")
        verts: list[np.ndarray] = []
        faces: list[np.ndarray] = []
        patch_of_tri: list[int] = []
        offset = 0
        for mi, m in enumerate(self._surfaces):
            if len(m.faces) == 0:
                continue
            verts.append(np.asarray(m.vertices, dtype=np.float64))
            faces.append(np.asarray(m.faces, dtype=np.int64) + offset)
            offset += len(verts[-1])
            patch_of_tri.extend([mi] * len(faces[-1]))
        if not faces:
            raise RuntimeError("All surface meshes are empty.")
        merged = trimesh.Trimesh(
            vertices=np.vstack(verts), faces=np.vstack(faces), process=False,
        )
        if not merged.is_watertight:
            res.warnings.append(
                f"Surface is NOT watertight (euler {merged.euler_number}) — "
                "containment and crossings may be wrong."
            )
            self._log(
                f"[native] WARNING: surface not watertight "
                f"(euler {merged.euler_number}); results may be wrong."
            )
        res.n_surface_tris = len(merged.faces)
        n_before = len(merged.faces)
        merged, keep_mask = _drop_contained_coplanar(merged, res)
        patch_of_tri = np.asarray(patch_of_tri, dtype=np.int64)[keep_mask]
        if len(merged.faces) < n_before:
            res.warnings.append(
                f"Removed {n_before - len(merged.faces):,} triangles contained "
                "in a coplanar triangle (overlapping CAD tessellation)."
            )
            self._log(
                f"[native] cleaned overlapping coplanar triangles: "
                f"{n_before - len(merged.faces):,} removed "
                f"({len(merged.faces):,} left)"
            )
        self._log(
            f"[native] surface: {len(merged.faces):,} triangles, "
            f"{len(merged.vertices):,} points, watertight={merged.is_watertight}, "
            f"bbox {merged.bounds[0]} .. {merged.bounds[1]}"
        )
        return merged, np.array(patch_of_tri, dtype=np.int64)

    def _uniform_axis(self, lo: float, hi: float) -> np.ndarray:
        """Prior single-block uniform grid line coordinates (unchanged)."""
        cell = self._cell_size
        n = int(max(1, np.ceil((hi - lo) / cell)))
        return lo + np.arange(n + 1) * cell

    def _graded_axis(self, core_lo: float, core_hi: float, pad: float) -> np.ndarray:
        """1D grid line coordinates for one axis, graded pad band.

        Uniform ``cell_size`` spacing inside ``[core_lo, core_hi]`` (the
        surface's tight bounding box on this axis), growing geometrically
        by ``margin_growth`` per step through the pad band on each side.
        Only used when ``margin_growth > 1.0`` -- the default path keeps
        calling ``_uniform_axis`` on the pre-padded bounds unchanged, so it
        is byte-identical to the pre-Fase-4b grid.
        """
        cell = self._cell_size
        n_core = int(max(1, np.ceil((core_hi - core_lo) / cell)))
        core = core_lo + np.arange(n_core + 1) * cell

        def pad_steps(length: float) -> np.ndarray:
            steps = []
            total, step = 0.0, cell
            while total < length:
                total += step
                steps.append(total)
                step *= self._margin_growth
            return np.array(steps)

        lo_ext = core[0] - pad_steps(pad)[::-1]
        hi_ext = core[-1] + pad_steps(pad)
        return np.concatenate([lo_ext, core, hi_ext])

    def _make_grid(self, surface: trimesh.Trimesh, res: NativeMeshResult):
        core_lo = surface.bounds[0].astype(np.float64)
        core_hi = surface.bounds[1].astype(np.float64)
        pad = self._margin * self._cell_size
        if self._margin_growth > 1.0:
            xs = self._graded_axis(core_lo[0], core_hi[0], pad)
            ys = self._graded_axis(core_lo[1], core_hi[1], pad)
            zs = self._graded_axis(core_lo[2], core_hi[2], pad)
        else:
            # exact prior behaviour: one uniform block over [bounds-pad, bounds+pad]
            xs = self._uniform_axis(core_lo[0] - pad, core_hi[0] + pad)
            ys = self._uniform_axis(core_lo[1] - pad, core_hi[1] + pad)
            zs = self._uniform_axis(core_lo[2] - pad, core_hi[2] + pad)
        lo = np.array([xs[0], ys[0], zs[0]])
        hi = np.array([xs[-1], ys[-1], zs[-1]])
        n = np.array([len(xs) - 1, len(ys) - 1, len(zs) - 1], dtype=np.int64)
        res.grid = tuple(int(x) for x in n)
        res.cell_size = self._cell_size
        self._log(
            f"[native] grid {n[0]}x{n[1]}x{n[2]} cells "
            f"(~{int(n[0] * n[1] * n[2]):,} candidates), "
            f"cell_size {self._cell_size:.6g}"
            + (f", margin_growth {self._margin_growth:g}"
               if self._margin_growth > 1.0 else "")
        )
        return SimpleNamespace(lo=lo, hi=hi, n=n, cell=self._cell_size,
                                xs=xs, ys=ys, zs=zs)

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------

    def _build(self, surface, patch_of_tri, grid, res):
        nx, ny, nz = (int(x) for x in grid.n)
        cell = grid.cell

        # ---- corner lattice + containment --------------------------------
        # i-fastest flattening (index = i + (nx+1)*(j + (ny+1)*k)) — MUST
        # match vid() below.  (meshgrid().reshape is k-fastest and would
        # silently transpose the lattice.)
        xs, ys, zs = grid.xs, grid.ys, grid.zs
        corners = np.array(
            [[x, y, z] for z in zs for y in ys for x in xs], dtype=np.float64,
        )
        t0 = time.monotonic()
        # Scanline containment: one vertical (jittered) ray per grid column,
        # crossing only the triangles that locally overlap the column (RTree).
        # Memory-safe (no O(rays x tris) dense arrays — trimesh's all-at-once
        # `contains` blew up to ~6 GiB on a 38k-tri STL) and robust for
        # corners lying exactly on the surface (the jitter avoids exact
        # grazing; an edge hit returns both triangles => parity unchanged).
        jit = np.array([1e-6, 1e-6, 1.0])
        inside = np.zeros(len(corners), dtype=bool)
        tree = surface.triangles_tree
        tris_arr = surface.triangles
        n_cross_tot = 0
        for j in range(ny + 1):
            y = ys[j]
            for i in range(nx + 1):
                x = xs[i]
                # thin box with a small x/y thickness — a zero-width query
                # box makes rtree return nothing (degenerate overlap).
                e = max(cell * 1e-4, 1e-9)
                cand = list(tree.intersection(
                    (float(x - e), float(y - e), float(grid.lo[2]),
                     float(x + e), float(y + e), float(grid.hi[2]))))
                if not cand:
                    continue
                t = _ray_hits_triangles(
                    np.array([x, y, grid.lo[2]], dtype=np.float64), jit,
                    tris_arr[np.array(cand, dtype=np.int64)])
                if len(t) == 0:
                    continue
                n_cross_tot += len(t)
                t.sort()
                # parity: inside below the first crossing, alternating.
                # corner k sits at zs[k]; crossings strictly below it count.
                z_off = zs - grid.lo[2]
                n_below = np.searchsorted(t, z_off, side="left")
                inside[i + (nx + 1) * (j + (ny + 1) * np.arange(nz + 1))] = \
                    (n_below % 2) == 1
        res.stage_times["contains"] = round(time.monotonic() - t0, 2)
        self._log(
            f"[native] containment: {int(inside.sum()):,}/{len(corners):,} "
            f"grid points inside, {n_cross_tot:,} ray-triangle crossings "
            f"[{res.stage_times['contains']:.1f}s]"
        )

        def vid(i, j, k):
            return i + (nx + 1) * (j + (ny + 1) * k)

        # ---- global point registry ---------------------------------------
        pts_global: list[np.ndarray] = []
        pt_id: dict[tuple, int] = {}

        def get_pt(p: np.ndarray) -> int:
            key = tuple(np.round(p, 12))
            i = pt_id.get(key)
            if i is None:
                i = len(pts_global)
                pt_id[key] = i
                pts_global.append(np.array(p, dtype=np.float64))  # COPY (not view!)
            return i

        corner_id = np.full(len(corners), -1, dtype=np.int64)

        def get_corner(i, j, k) -> int:
            c = vid(i, j, k)
            v = corner_id[c]
            if v < 0:
                v = get_pt(corners[c])
                corner_id[c] = v
            return v

        def cross_on_edge(ci, cj, ck, ca, cb):
            """Global point id where the surface crosses the cell edge
            between corner indices ca and cb (0..7)."""
            a = _CORNERS[ca]
            b = _CORNERS[cb]
            pa = corners[vid(ci + a[0], cj + a[1], ck + a[2])]
            pb = corners[vid(ci + b[0], cj + b[1], ck + b[2])]
            sa = bool(inside[vid(ci + a[0], cj + a[1], ck + a[2])])
            sb = bool(inside[vid(ci + b[0], cj + b[1], ck + b[2])])
            if sa == sb:
                return None
            t0, t1 = 0.0, 1.0
            d0 = float(trimesh.proximity.signed_distance(surface, pa[None, :])[0])
            for _ in range(40):
                tm = 0.5 * (t0 + t1)
                dm = float(trimesh.proximity.signed_distance(
                    surface, (pa + tm * (pb - pa))[None, :])[0])
                if (d0 > 0.0) == (dm > 0.0):
                    t0, d0 = tm, dm
                else:
                    t1 = tm
            p = pa + 0.5 * (t0 + t1) * (pb - pa)
            return get_pt(p)

        # ---- classify cells ----------------------------------------------
        n_cells = nx * ny * nz
        n_in_cell = np.zeros(n_cells, dtype=np.int64)
        for (di, dj, dk) in _CORNERS:
            ii = np.arange(nx)[:, None, None] + di
            jj = np.arange(ny)[None, :, None] + dj
            kk = np.arange(nz)[None, None, :] + dk
            # NOTE: G is (nx,ny,nz) with axis 0 = ci (SLOWEST in C-flatten),
            # but the cell index is i-FASTEST (ci + nx*(cj + ny*ck)):
            # transpose to (nz,ny,nx) before flattening.  A plain reshape(-1)
            # silently transposes the whole classification!
            n_in_cell += inside[ii + (nx + 1) * (jj + (ny + 1) * kk)].T.reshape(-1)
        hex_mask = n_in_cell == 8
        out_mask = n_in_cell == 0
        cut_mask = ~(hex_mask | out_mask)
        res.n_hex_cells = int(hex_mask.sum())
        res.n_cut_cells = int(cut_mask.sum())
        self._log(
            f"[native] cells: {res.n_hex_cells:,} hex, {res.n_cut_cells:,} cut, "
            f"{int(out_mask.sum()):,} outside"
        )

        # ---- per-cell face construction ----------------------------------
        # cell_faces[cell] = list of (poly, patch) ; patch -1 = internal
        cell_faces: dict[int, list[tuple[list[int], int]]] = {}

        def cut_cell_faces(ci, cj, ck):
            """Kept-region faces of a cut cell via clipped-surface construction.

            The surface triangles intersecting the cell box are clipped
            (Sutherland–Hodgman) against the box: the clipped polygons ARE
            the true surface cap (one boundary face each), and their vertices
            lying on a box face feed the 2D box-face rule, so adjacent cells
            compute identical shared faces.  No edge bisection needed.
            """
            inside_local = np.array([
                bool(inside[vid(ci + _CORNERS[v][0], cj + _CORNERS[v][1],
                                ck + _CORNERS[v][2])])
                for v in range(8)
            ])
            n_in = int(inside_local.sum())
            if n_in == 0 or n_in == 8:
                return None
            lo = np.array([xs[ci], ys[cj], zs[ck]])
            hi = np.array([xs[ci + 1], ys[cj + 1], zs[ck + 1]])

            tris_in = list(surface.triangles_tree.intersection(
                (float(lo[0]), float(lo[1]), float(lo[2]),
                 float(hi[0]), float(hi[1]), float(hi[2]))))
            if not tris_in:
                res.n_skipped_thin += 1
                return None

            tri_pts = surface.triangles  # (n,3,3)
            faces: list[tuple[list[int], int]] = []
            # the 6 box-face planes of THIS cell, in _FACES order
            # (_FACES = z-, z+, y-, y+, x-, x+ per the OpenFOAM hex model)
            face_axis = [2, 2, 1, 1, 0, 0]
            face_plane = [lo[2], hi[2], lo[1], hi[1], lo[0], hi[0]]
            on_face_pts: list[list[int]] = [[] for _ in range(6)]
            cap_any = False
            for ti in tris_in:
                poly = _clip_tri_box(tri_pts[int(ti)], lo, hi)
                if len(poly) < 3:
                    continue
                ids = _dedup_poly([get_pt(p) for p in poly])
                if ids is None:
                    continue
                cap_any = True
                # FLAT-ON-A-BOX-FACE: if every vertex of the clip lies on ONE
                # box-face plane, the surface coincides with the grid plane
                # there (e.g. a duct wall exactly on a grid plane).  It must
                # NOT be a separate cap — the box-face 2D-hull face covers
                # that region (its vertices feed the hull below); emitting
                # both double-covers the face and breaks the closure.
                flat_fi = -1
                for pid in ids:
                    v = pts_global[pid]
                    fi_hit = -1
                    for fi in range(6):
                        if abs(v[face_axis[fi]] - face_plane[fi]) < _PLANE_TOL:
                            fi_hit = fi
                            break
                    if fi_hit < 0:
                        flat_fi = -2
                        break
                    if flat_fi == -1:
                        flat_fi = fi_hit
                    elif flat_fi != fi_hit:
                        flat_fi = -2
                        break
                if flat_fi >= 0:
                    # all verts on plane flat_fi: only feed that hull
                    on_face_pts[flat_fi].extend(ids)
                    continue
                # reject zero-area clipped polygons (a surface triangle
                # grazing the cell along a line yields a degenerate polygon
                # with distinct-but-collinear vertices).
                if len(ids) >= 3:
                    p = np.asarray(pts_global)[ids]
                    n = np.zeros(3)
                    for k in range(len(ids)):
                        j = (k + 1) % len(ids)
                        n += np.cross(p[k], p[j])
                    if np.linalg.norm(n) < 1e-12:
                        continue
                faces.append((ids, int(patch_of_tri[int(ti)])))
                # vertices lying on a box face plane (a vertex on a box EDGE
                # lies on TWO planes and must feed both adjacent faces).
                # Iterate the DEDUPED ids (zip(poly, ids) would drop the
                # trailing vertices after duplicate removal!).
                for pid in ids:
                    v = pts_global[pid]
                    for fi in range(6):
                        axis = face_axis[fi]
                        if abs(v[axis] - face_plane[fi]) < _PLANE_TOL:
                            on_face_pts[fi].append(pid)
            if not cap_any:
                res.n_skipped_thin += 1
                return None

            # ---- box-face parts (2D hull rule, now with all crossing pts)
            dbg22 = False
            for fi in range(6):
                axis = face_axis[fi]
                pts_on_face = list(on_face_pts[fi])
                for v in _FACES[fi]:
                    if inside_local[v]:
                        pts_on_face.append(get_corner(
                            ci + _CORNERS[v][0], cj + _CORNERS[v][1],
                            ck + _CORNERS[v][2]))
                if dbg22:
                    print(
                        f"  DBG22 fi={fi} axis={axis} npts={len(pts_on_face)} "
                        f"onface={[list(np.round(pts_global[v], 6)) for v in on_face_pts[fi]]} "
                        f"corners={[list(np.round(pts_global[v], 6)) for v in pts_on_face[len(on_face_pts[fi]):]]}"
                    )
                if len(pts_on_face) >= 3:
                    proj = np.array([pts_global[v] for v in pts_on_face])
                    u = np.delete(proj, axis, axis=1)
                    try:
                        h = ConvexHull(u)
                    except Exception:
                        continue
                    poly = _dedup_poly([pts_on_face[i] for i in h.vertices])
                    if poly is None:
                        continue
                    if len(poly) >= 3:
                        p = np.asarray(pts_global)[poly]
                        n = np.zeros(3)
                        for k in range(len(poly)):
                            j = (k + 1) % len(poly)
                            n += np.cross(p[k], p[j])
                        if np.linalg.norm(n) < 1e-12:
                            continue
                    faces.append((poly, -1))
            # ---- drop sliver / non-manifold cells -------------------------
            # A cell whose kept region is a degenerate-thin sliver (surface
            # grazing a corner/edge) produces faces with extreme skewness and
            # non-manifold edges (an edge used by 1 face — the median dual
            # requires exactly 2).  Standard cut-cell practice (snappy removes
            # tiny cells): drop the cell — its neighbours close up, the mesh
            # stays watertight, the volume error is the sliver.
            if faces:
                all_ids = [v for poly, _ in faces for v in poly]
                ctr = np.mean([pts_global[v] for v in all_ids], axis=0)
                vol = 0.0
                closure = np.zeros(3)
                sum_abs = 0.0
                ok_edges = True
                edge_use: dict[tuple, int] = {}
                for poly, _ in faces:
                    sfv = _newell(pts_global, poly)
                    fc = np.mean([pts_global[v] for v in poly], axis=0)
                    # faces are not yet oriented: flip so each contributes
                    # positively to the volume (outward from the centre)
                    if (fc - ctr) @ sfv < 0.0:
                        sfv = -sfv
                    vol += float((fc - ctr) @ sfv) / 3.0
                    closure += sfv
                    sum_abs += float(np.linalg.norm(sfv))
                    for k in range(len(poly)):
                        a, b = poly[k], poly[(k + 1) % len(poly)]
                        key = (a, b) if a < b else (b, a)
                        edge_use[key] = edge_use.get(key, 0) + 1
                if any(u != 2 for u in edge_use.values()):
                    ok_edges = False
                rel_closure = float(np.linalg.norm(closure)) / max(sum_abs, 1e-300)
                # drop: non-manifold edges (needed by the dual), degenerate
                # volume (thin slivers), or a genuinely open cell (the 2D-hull
                # box-face rule over-approximates concave kept regions — an
                # obstacle crossing a box face — leaving the cell open).
                box_vol = float((hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2]))
                if (self._clean_cells and not ok_edges) or \
                        vol < 5e-5 * box_vol or \
                        rel_closure > 1e-6:
                    res.n_skipped_thin += 1
                    return None
            return faces

        for ci in range(nx):
            for cj in range(ny):
                for ck in range(nz):
                    cell_idx = ci + nx * (cj + ny * ck)
                    if hex_mask[cell_idx]:
                        fcs = []
                        for f in _FACES:
                            fcs.append((
                                [get_corner(ci + _CORNERS[v][0],
                                            cj + _CORNERS[v][1],
                                            ck + _CORNERS[v][2]) for v in f],
                                -1,
                            ))
                        cell_faces[cell_idx] = fcs
                    elif cut_mask[cell_idx]:
                        self._check_cancel()
                        fcs = cut_cell_faces(ci, cj, ck)
                        if fcs is not None:
                            cell_faces[cell_idx] = fcs

        # (n_hex/n_cut final counts are set in _validate from the assembly)

        # ---- compact cell ids (grid indices are sparse) -------------------
        # skipped/outside cells leave gaps in cell_idx; OpenFOAM needs
        # contiguous cells 0..n_cells-1.
        cell_faces = {
            new_id: fcs
            for new_id, (_, fcs) in enumerate(sorted(cell_faces.items()))
        }

        # ---- global face assembly ----------------------------------------
        face_map: dict[tuple, dict] = {}
        for cell_idx, fcs in cell_faces.items():
            for poly, patch in fcs:
                key = tuple(sorted(poly))
                entry = face_map.get(key)
                if entry is None:
                    face_map[key] = {"poly": poly, "owners": [cell_idx],
                                     "patch": patch}
                else:
                    entry["owners"].append(cell_idx)

        centers = {}
        for cell_idx, fcs in cell_faces.items():
            all_ids = [v for poly, _ in fcs for v in poly]
            centers[cell_idx] = np.mean([pts_global[v] for v in all_ids], axis=0)

        def orient_outward(poly: list[int], centre: np.ndarray) -> list[int]:
            n = _newell(pts_global, poly)
            d = np.mean([pts_global[v] for v in poly], axis=0) - centre
            if float(n @ d) < 0.0:
                return list(reversed(poly))
            return list(poly)

        faces_out: list[list[int]] = []
        owner_out: list[int] = []
        neigh_out: list[int] = []
        bnd_faces: list[list[int]] = []  # boundary polygons (already oriented)
        bnd_owners: list[int] = []
        for entry in face_map.values():
            poly, owners, patch = entry["poly"], entry["owners"], entry["patch"]
            if len(owners) == 1:
                c = owners[0]
                bnd_faces.append(orient_outward(poly, centers[c]))
                bnd_owners.append(c)
            elif len(owners) == 2:
                c0, c1 = owners
                op = orient_outward(poly, centers[c0])
                n = _newell(pts_global, op)
                d = centers[c1] - centers[c0]
                if float(n @ d) < 0.0:
                    op = list(reversed(op))
                if c0 > c1:
                    c0, c1 = c1, c0
                    op = list(reversed(op))
                faces_out.append(op)
                owner_out.append(c0)
                neigh_out.append(c1)
            else:
                print(
                    f"DBG: face with {len(owners)} owners: poly={poly} "
                    f"coords={[list(np.round(pts_global[v], 4)) for v in poly]} "
                    f"cells={owners[:6]}"
                )
                for c in sorted(set(owners)):
                    print(
                        f"  cell {c}: "
                        + "; ".join(
                            f"{[list(np.round(pts_global[v], 4)) for v in p]}"
                            for p, _ in cell_faces.get(c, [])
                        )
                    )
                raise RuntimeError(
                    f"Face shared by {len(owners)} cells — non-manifold."
                )

        # internal faces sorted (owner, neighbour) for OpenFOAM
        io = np.array(owner_out, dtype=np.int64)
        inb = np.array(neigh_out, dtype=np.int64)
        sort_idx = np.lexsort((inb, io))
        faces_sorted = [faces_out[i] for i in sort_idx]
        owner_sorted = [owner_out[i] for i in sort_idx]
        neigh_sorted = [neigh_out[i] for i in sort_idx]
        n_int = len(faces_sorted)

        # boundary faces grouped per patch.  A box-face part whose neighbour
        # was skipped (outside) becomes a boundary face with patch=-1: it is
        # a genuine domain boundary (castellation of a feature thinner than
        # the cell) and must NOT be dropped — assign it to the first patch.
        patches: list[dict] = []
        start = n_int
        for mi, m in enumerate(self._surfaces):
            name = str(m.metadata.get("name", f"patch_{mi}")) \
                if getattr(m, "metadata", None) else f"patch_{mi}"
            patches.append({"name": name, "type": "patch",
                            "nFaces": 0, "startFace": start})
        final_faces = list(faces_sorted)
        final_own = list(owner_sorted)
        final_nb = list(neigh_sorted)
        for pi, p in enumerate(patches):
            k = 0
            for poly, owner in zip(bnd_faces, bnd_owners):
                key = tuple(sorted(poly))
                patch = face_map[key]["patch"]
                if patch == pi or (patch < 0 and pi == 0):
                    final_faces.append(poly)
                    final_own.append(owner)
                    k += 1
            p["nFaces"] = k
            p["startFace"] = start
            start += k
        # remove empty trailing patch entries
        patches = [p for p in patches if p["nFaces"] > 0]

        n_hex = int(hex_mask.sum())
        return SimpleNamespace(
            points=np.array(pts_global, dtype=np.float64),
            faces=final_faces,
            owner=np.array(final_own, dtype=np.int64),
            neigh=np.array(final_nb, dtype=np.int64),
            patches=patches, n_int=n_int, n_cells=len(cell_faces),
            n_hex=n_hex, n_cut=len(cell_faces) - n_hex,
            res=res,
        )

    # ------------------------------------------------------------------
    # validation + write
    # ------------------------------------------------------------------

    def _validate(self, res: NativeMeshResult, M) -> None:
        n_cells = M.n_cells
        if n_cells == 0:
            raise RuntimeError("No cells generated.")
        if M.n_int and np.any(M.owner[:M.n_int] >= M.neigh):
            raise RuntimeError("internal faces not upper-triangular")
        for f in M.faces:
            if len(f) < 3 or len(set(f)) != len(f):
                raise RuntimeError(f"Degenerate output face: {f}")
        sf, cf = _face_geometry(M.points, M.faces)
        closure = np.zeros((n_cells, 3))
        np.add.at(closure, M.owner, sf)
        np.add.at(closure, M.neigh, -sf[:M.n_int])
        scale = np.zeros(n_cells)
        mag = np.linalg.norm(sf, axis=1)
        np.add.at(scale, M.owner, mag)
        np.add.at(scale, M.neigh, mag[:M.n_int])
        rel = np.linalg.norm(closure, axis=1) / np.maximum(scale, 1e-300)
        res.max_closure_error = float(rel.max())
        if res.max_closure_error > 1e-6:
            raise RuntimeError(
                f"Open cell: closure error {res.max_closure_error:.3e}."
            )
        vol = np.zeros(n_cells)
        contrib = (cf * sf).sum(axis=1) / 3.0
        np.add.at(vol, M.owner, contrib)
        np.add.at(vol, M.neigh, -contrib[:M.n_int])
        res.min_cell_volume = float(vol.min())
        if res.min_cell_volume <= 0.0:
            raise RuntimeError("Non-positive cell volume in native mesh.")
        res.volume = float(vol.sum())
        ctr, _ = _cell_centres(sf, cf, M.owner, M.neigh, M.n_int, n_cells)
        _, counts = _detect_defects(
            M.points, M.faces, sf, cf, ctr, M.owner, M.neigh, M.n_int, n_cells,
        )
        res.defects = counts
        res.n_cells = n_cells
        res.n_hex_cells = M.n_hex
        res.n_cut_cells = M.n_cut
        res.n_internal_faces = M.n_int
        res.n_boundary_faces = len(M.faces) - M.n_int
        res.n_points = len(M.points)
        self._log(
            f"[native] mesh: {n_cells:,} cells ({res.n_hex_cells:,} hex + "
            f"{res.n_cut_cells:,} cut), {res.n_internal_faces:,} internal + "
            f"{res.n_boundary_faces:,} boundary faces, {len(M.points):,} points, "
            f"volume {res.volume:.6g}, defects {counts}"
        )

    def _write(self, case_dir, M, res: NativeMeshResult) -> None:
        out_dir = Path(case_dir).resolve() / "constant" / "polyMesh"
        if out_dir.exists():
            import shutil

            shutil.rmtree(out_dir)
        fio.write_polymesh(out_dir, M.points, M.faces, M.owner, M.neigh, M.patches)
        res.out_dir = str(out_dir)
        self._log(f"[native] wrote -> {out_dir}")
