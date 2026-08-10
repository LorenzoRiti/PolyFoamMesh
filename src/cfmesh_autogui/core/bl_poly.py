"""Boundary layers for the barycentric-dual polyhedral mesh.

Adds an anisotropic prism layer stack to the wall of an EXISTING polyhedral
mesh produced by the tet->poly dual converter (`core/tet_poly_dual.py`) —
no external tool, pure Python + numpy, headless.

Why this is the right construction (measured, not assumed)
----------------------------------------------------------

The median-dual wall faces are planar quads ``[a, mid(ab), cent(abc), mid(ca)]``
owned by the dual cell of the primal boundary vertex ``a``.  Extruding each
wall quad into a prism stack is conforming BY CONSTRUCTION:

- consecutive wall quads share their edge-mid / triangle-centroid vertices,
  so the extruded prism side faces pair up exactly (one face, two prisms);
- each wall vertex moves to ONE last-layer position shared by every prism
  that touches it, so the top surface tiles without gaps or overlaps;
- the interior cell that owned a wall face simply swaps that face for the
  top face of the deepest prism (same vertex set, extruded), and its other
  wall-adjacent faces swap the moved wall vertices for the extruded ones —
  the face pairs (owner/neighbour) never change, only vertex positions.

The topology pattern that OpenFOAM accepts (wall-adjacent edges shared by
the two wall quads plus one internal face) is preserved exactly: checkMesh
reports ``Mesh OK`` on the dual input and on the BL output.

Algorithm (per run)
-------------------

1. select the extrusion faces (all boundary faces by default; with
   ``patch_names`` a named subset is used as-is — at the edge between the
   BL region and an unselected patch, each prism side face lies in the
   plane of the adjacent (unselected) wall face and becomes a BOUNDARY
   face of that patch, owned by the prism; the unselected wall faces move
   their shared wall vertices to the last layer so every cell stays
   closed — partial-patch BL is conforming and physically correct);
2. per wall vertex: smoothed normal = normalised sum of incident face
   area-vectors; max height = ``clamp_factor`` times the distance to the
   nearest non-wall vertex of the incident cells (inversion guard);
3. layer heights ``h_k = h1 * r^(k-1)``, cumulative, per-vertex clamped;
4. build the prism cells, move the wall vertices of the wall-adjacent
   internal faces to their last-layer positions, swap wall faces for the
   top faces, orient every new face geometrically (normal owner -> neighbour);
5. validate (owner < neighbour, cell closure, all positive volumes,
   total volume conserved, no unused points) and write atomically via
   `foam_mesh_io`; on validation failure, retry with progressively smaller
   heights (keep-best, mesh never left half-written).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from cfmesh_autogui.core import foam_mesh_io as fio

logger = logging.getLogger(__name__)


@dataclass
class PolyBoundaryLayerResult:
    """Outcome of a BL insertion on the poly mesh."""
    success: bool = False
    n_prism_cells: int = 0
    n_layers: int = 0
    first_height: float = 0.0          # absolute, metres (as requested)
    growth_rate: float = 1.2
    total_thickness: float = 0.0       # absolute, metres (final, after clamp)
    n_wall_faces: int = 0
    wall_patches: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _gil_yield(step: int, every: int = 256) -> None:
    """Release the GIL periodically so the Qt main thread keeps repainting
    while this worker's long pure-Python loops run.  Without this the GUI
    freezes for the minutes the BL step takes (Windows even marks the
    window 'non risponde'), even though the work itself is on a QThread."""
    if step % every == 0:
        time.sleep(0)


def _newell(points: np.ndarray, verts) -> np.ndarray:
    """Newell area vector (magnitude = 2x face area, direction = normal)."""
    p = points[list(verts)]
    n = np.zeros(3)
    for i in range(len(p)):
        j = (i + 1) % len(p)
        n[0] += (p[i, 1] - p[j, 1]) * (p[i, 2] + p[j, 2])
        n[1] += (p[i, 2] - p[j, 2]) * (p[i, 0] + p[j, 0])
        n[2] += (p[i, 0] - p[j, 0]) * (p[i, 1] + p[j, 1])
    return 0.5 * n


def _face_geometry(points: np.ndarray, faces: list[list[int]]):
    """(area vectors, centroids) for every face — OpenFOAM convention:
    centroid = arithmetic mean of the vertices.  Vectorised (padded face
    matrix) so meshes with >1M faces stay fast."""
    n_faces = len(faces)
    max_len = max((len(f) for f in faces), default=0)
    if max_len == 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    pad = np.full((n_faces, max_len), -1, dtype=np.int64)
    for i, f in enumerate(faces):
        _gil_yield(i, 8192)
        pad[i, :len(f)] = f
    # vertex coordinates per (face, position)
    coords = points[pad]  # (n_faces, max_len, 3); -1 sentinel never used
    mask = pad >= 0
    per_face_len = mask.sum(axis=1)  # (n_faces,)
    # next vertex position per slot: k+1, wrapping to 0 at each face's own
    # length (faces have different lengths)
    cols = np.arange(max_len)[None, :]
    nxt_col = np.where(cols + 1 < per_face_len[:, None], cols + 1, 0)
    nxt = coords[np.arange(n_faces)[:, None], nxt_col]
    sf = np.zeros((n_faces, 3))
    for k in range(max_len):
        m = mask[:, k]
        if not m.any():
            continue
        a = coords[m, k]
        b = nxt[m, k]
        # Newell per edge: 0.5 * (p_k x p_k+1); (a-b)x(a+b) = 2(a x b),
        # so summing 0.5*(a x b) gives the same area vector as the
        # reference implementation in tet_poly_dual._newell.
        sf[m] += 0.5 * np.cross(a, b)
    cf = np.zeros((n_faces, 3))
    cnt = mask.sum(axis=1, dtype=np.float64)
    for k in range(max_len):
        cf += np.where(mask[:, k, None], coords[:, k], 0.0)
    cf /= np.maximum(cnt, 1)[:, None]
    return sf, cf


def _cell_centroids(points, faces, owner, neigh, n_int, n_cells):
    """EXACT OpenFOAM-2512 cell centres + volumes — byte-for-byte the
    algorithm in `primitiveMeshTools::makeCellCentresAndVols` (pyramid-volume
    weighted pyramid centres with the face-centre-average apex), so the
    in-process 'face pyramids' gate matches checkMesh's verdict exactly.

    Returns (centres (n,3), volumes (n,)).  Vectorised with add.at."""
    n_faces = len(faces)
    if n_faces == 0:
        return np.zeros((n_cells, 3)), np.zeros(n_cells)
    sf, cf = _face_geometry(points, faces)

    # step 1: cEst = average of the cell's face centres
    c_est = np.zeros((n_cells, 3))
    n_cf = np.zeros(n_cells)
    np.add.at(c_est, owner, cf)
    np.add.at(n_cf, owner, 1.0)
    if n_int:
        np.add.at(c_est, neigh[:n_int], cf[:n_int])
        np.add.at(n_cf, neigh[:n_int], 1.0)
    c_est /= np.maximum(n_cf, 1e-300)[:, None]

    # step 2: accumulate 3*face-pyramid volume and pyramid centres
    C = np.zeros((n_cells, 3))
    V3 = np.zeros(n_cells)
    w_o = (sf * (cf - c_est[owner])).sum(axis=1)
    np.add.at(V3, owner, w_o)
    np.add.at(C, owner, w_o[:, None] * (0.75 * cf + 0.25 * c_est[owner]))
    if n_int:
        w_n = (sf[:n_int] * (c_est[neigh[:n_int]] - cf[:n_int])).sum(axis=1)
        np.add.at(V3, neigh[:n_int], w_n)
        np.add.at(C, neigh[:n_int], w_n[:, None] * (
            0.75 * cf[:n_int] + 0.25 * c_est[neigh[:n_int]]))
    ok = np.abs(V3) > 1e-300
    C[ok] /= V3[ok, None]
    C[~ok] = c_est[~ok]
    return C, V3 / 3.0


def _pyramid_violations(points, faces, owner, neigh, n_int, n_cells):
    """Number of faces whose normal points INTO one of its cells (checkMesh's
    'face pyramids' criterion, exact cell centroids).  checkMesh tests BOTH
    the owner side (normal must point out of the owner's centroid) and the
    neighbour side (normal must point toward the neighbour's centroid)."""
    C, _ = _cell_centroids(points, faces, owner, neigh, n_int, n_cells)
    sf, cf = _face_geometry(points, faces)
    bad_owner = (sf * (cf - C[owner])).sum(axis=1) <= 0.0
    bad = int(np.sum(bad_owner))
    if n_int:
        bad_neigh = (sf[:n_int] * (cf[:n_int] - C[neigh[:n_int]])).sum(axis=1) >= 0.0
        bad += int(np.sum(bad_neigh & ~bad_owner[:n_int]))
    return bad


def _fix_pyramid_faces(points, faces, owner, neigh, n_int, n_cells, rounds=4):
    """Flip faces whose normal points INTO their owner cell — checkMesh's
    'face pyramids' criterion (the authoritative validity test).

    Uses the same OpenFOAM-convention cell centres as checkMesh; each flipped
    face is re-repaired for closure afterwards.  Returns the faces list.
    """
    for _ in range(rounds):
        C, _ = _cell_centroids(points, faces, owner, neigh, n_int, n_cells)
        sf, cf = _face_geometry(points, faces)
        bad = (sf * (cf - C[owner])).sum(axis=1) <= 0.0
        if not bad.any():
            break
        for j, fi in enumerate(np.flatnonzero(bad)):
            _gil_yield(j, 64)
            faces[fi].reverse()
        faces = _repair_closures(points, faces, owner, neigh, n_int, n_cells)
    return faces


def _repair_closures(points, faces, owner, neigh, n_int, n_cells,
                     max_rounds=8, tol=1e-8):
    """Greedy winding repair: flip individual faces until every cell's signed
    area-vector sum (closure) is ~ 0.

    The geometric orientation of the new prism faces uses the prism's
    vertex-average centroid, which is reliable for the near-convex stacks but
    can mis-orient a face on genuinely twisted cells (concave re-entrant
    slivers, e.g. the reference valve).  Flipping a face only reverses its
    winding — the owner/neighbour pair is untouched — so each flip locally
    reduces the residual of a bad cell.  Converges quickly: the number of
    mis-wound faces is small relative to the mesh.

    Returns the (possibly re-wound) faces list.
    """
    n_faces = len(faces)
    sf = np.array([_newell(points, f) for f in faces])
    closure = np.zeros((n_cells, 3))
    scale_m = np.zeros(n_cells)
    cell_faces = [[] for _ in range(n_cells)]
    for fi in range(n_faces):
        o = owner[fi]
        closure[o] += sf[fi]
        m = float(np.linalg.norm(sf[fi]))
        scale_m[o] += m
        cell_faces[o].append(fi)
        if fi < n_int:
            n_ = neigh[fi]
            closure[n_] -= sf[fi]
            scale_m[n_] += m
            cell_faces[n_].append(fi)

    def rel(c: int) -> float:
        return float(np.linalg.norm(closure[c])) / max(scale_m[c], 1e-300)

    for _ in range(max_rounds):
        bad = [c for c in range(n_cells) if rel(c) > tol]
        if not bad:
            break
        improved = False
        for ci, c in enumerate(bad):
            _gil_yield(ci, 64)
            best_fi = -1
            best = rel(c)
            for fi in cell_faces[c]:
                _gil_yield(fi, 256)
                sign = 1.0 if owner[fi] == c else -1.0
                trial = closure[c] - 2.0 * sign * sf[fi]
                tr = float(np.linalg.norm(trial)) / max(scale_m[c], 1e-300)
                if tr < best - 1e-12:
                    best, best_fi = tr, fi
            if best_fi >= 0:
                fi = best_fi
                closure[owner[fi]] -= 2.0 * sf[fi]
                if fi < n_int:
                    closure[neigh[fi]] += 2.0 * sf[fi]
                sf[fi] = -sf[fi]
                faces[fi].reverse()
                improved = True
        if not improved:
            break

    # global orientation: all cells must have positive volume (outward
    # windings); a single global flip fixes the whole mesh
    vols, _, _ = _cell_metrics(points, faces, owner, neigh, n_cells, n_int)
    if np.any(vols <= 0.0):
        faces = [list(reversed(f)) for f in faces]
    return faces


def _cell_metrics(points, faces, owner, neighbour, n_cells, n_int):
    """Signed cell volumes (outward area convention) + closure residuals.

    Returns (volumes (n_cells,), closure_residual (n_cells,), scale (n_cells,)).
    A cell is closed when closure_residual / scale < ~1e-8; its volume is
    positive when volumes > 0 (outward-pointing faces).
    """
    sf, cf = _face_geometry(points, faces)
    v = np.zeros(n_cells)
    closure = np.zeros((n_cells, 3))
    scale = np.zeros(n_cells)
    np.add.at(v, owner, (cf * sf).sum(axis=1) / 3.0)
    np.add.at(v, neighbour[:n_int], -(cf[:n_int] * sf[:n_int]).sum(axis=1) / 3.0)
    np.add.at(closure, owner, sf)
    np.add.at(closure, neighbour[:n_int], -sf[:n_int])
    np.add.at(scale, owner, np.linalg.norm(sf, axis=1))
    np.add.at(scale, neighbour[:n_int], np.linalg.norm(sf[:n_int], axis=1))
    return v, closure, scale


class PolyBoundaryLayerEngine:
    """Adds a prism boundary layer to an existing polyhedral polyMesh.

    Reads ``constant/polyMesh`` (ASCII or binary, ``faceList`` or
    ``faceCompactList`` — the readers in ``foam_mesh_io`` handle all of
    them), inserts the layers, validates, and writes back atomically.
    """

    def __init__(self, case_dir: Path | str, log=None, cancel=None):
        self._case_dir = Path(case_dir).resolve()
        self._log = log or (lambda msg: logger.info(msg))
        self._cancel = cancel or (lambda: False)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def run(
        self,
        n_layers: int = 5,
        first_height: float = 0.005,
        growth_rate: float = 1.2,
        patch_names: list[str] | None = None,
        apply_to_all: bool = True,
        clamp_factor: float = 0.5,
    ) -> PolyBoundaryLayerResult:
        """Insert the boundary layer. `first_height` is ABSOLUTE (metres).

        - `patch_names`: restrict BL to these boundary patches. At the edge
          between the BL region and an unselected patch, each prism side
          face lies in the plane of the adjacent (unselected) wall face and
          becomes a boundary face of that patch owned by the prism; the
          unselected wall faces move their shared wall vertices to the last
          layer so every cell stays closed. `result.wall_patches` reports
          exactly the selected patches; the patches that gained terminator
          faces grow in `nFaces`.
        - `apply_to_all=True` (default): BL on every boundary patch.
        - `clamp_factor`: max layer-stack height per wall vertex as a
          fraction of the distance to the nearest non-wall vertex of the
          incident cells (inversion guard).
        """
        res = PolyBoundaryLayerResult(
            n_layers=int(max(1, min(int(n_layers), 40))),
            first_height=float(first_height),
            growth_rate=float(growth_rate),
        )
        if int(n_layers) < 1:
            res.errors.append("n_layers must be >= 1")
            return res
        if res.first_height <= 0:
            res.errors.append("first_height must be > 0")
            return res
        if res.growth_rate < 1.0:
            res.errors.append("growth_rate must be >= 1.0")
            return res
        growth = min(max(res.growth_rate, 1.0), 2.0)
        if res.growth_rate > 2.0:
            res.warnings.append(
                f"growth_rate {res.growth_rate} clamped to 2.0"
            )
        res.growth_rate = growth

        poly = self._case_dir / "constant" / "polyMesh"
        try:
            points, faces, owner, neighbour, patches = fio.read_polymesh(poly)
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller
            res.errors.append(f"cannot read polyMesh: {exc}")
            return res
        n_int = len(neighbour)
        n_cells = int(max(owner.max(), neighbour.max())) + 1
        total_vol0 = float(
            _cell_metrics(points, faces, owner, neighbour, n_cells, n_int)[0].sum()
        )

        sel, used_patches = self._select_faces(
            faces, owner, patches, n_int, patch_names, apply_to_all,
        )
        if not sel:
            res.errors.append("no boundary faces selected for BL")
            return res
        res.n_wall_faces = len(sel)
        res.wall_patches = used_patches

        # fallback: progressively thinner layers AND tighter per-vertex
        # clamping (the pyramid criterion fails on thin twisted rim cells
        # when the extrusion exceeds a small fraction of the local sliver)
        for scale, clamp in (
            (1.0, 0.5), (0.6, 0.35), (0.35, 0.2),
            (0.2, 0.1), (0.1, 0.05),
        ):
            if self._cancel():
                res.errors.append("cancelled")
                return res
            try:
                built = self._build(
                    points, faces, owner, neighbour, patches, n_int,
                    sel, res.n_layers, res.first_height * scale, growth, clamp,
                )
            except Exception as exc:  # noqa: BLE001 - keep-best fallback
                self._log(f"[bl] attempt scale={scale} clamp={clamp}: {exc}")
                res.warnings.append(f"attempt at scale={scale} failed: {exc}")
                continue
            self._log(f"[bl] validating attempt at scale={scale} "
                      f"clamp={clamp} (checkMesh criteria)...")
            ok, msg = self._validate(built, total_vol0)
            if ok:
                fio.write_polymesh(
                    poly, built["points"], built["faces"],
                    built["owner"], built["neigh"], built["patches"],
                )
                res.success = True
                res.n_prism_cells = len(sel) * res.n_layers
                res.total_thickness = built["heights"][-1]
                res.stats.update(
                    scale=scale,
                    clamp_factor=clamp,
                    n_cells_after=int(built["owner"].max()) + 1,
                    n_faces=len(built["faces"]),
                    n_internal_faces=len(built["neigh"]),
                    n_terminator_faces=int(built["n_terminator_faces"]),
                    min_cell_volume=float(built["min_volume"]),
                    total_volume=float(built["total_volume"]),
                )
                self._log(
                    f"[bl] {len(sel) * res.n_layers:,} prism cells "
                    f"({res.n_layers} layers x {len(sel):,} wall faces), "
                    f"total thickness {res.total_thickness:.6g} m, "
                    f"min cell volume {built['min_volume']:.3g}"
                )
                return res
            res.warnings.append(
                f"validation failed at scale={scale} clamp={clamp}: {msg}"
            )
            self._log(f"[bl] validation failed at scale={scale} clamp={clamp}: {msg}")

        res.errors.append(
            "could not insert a valid boundary layer (all height scales "
            "failed validation); mesh left unchanged"
        )
        return res

    # ------------------------------------------------------------------
    # face selection
    # ------------------------------------------------------------------

    def _select_faces(
        self, faces, owner, patches, n_int, patch_names, apply_to_all,
    ) -> tuple[list[int], list[str]]:
        """Boundary faces to extrude + the patches they belong to.

        The selected set is auto-closed across shared edges so that every
        edge of every selected face has exactly two selected faces (required
        for a conforming prism side-face pairing).  Auto-added faces are
        reported through the returned patch-name list.
        """
        n_bnd = len(faces) - n_int
        patch_of = np.empty(n_bnd, dtype=np.int64)
        for pi, p in enumerate(patches):
            s = p["startFace"] - n_int
            patch_of[s:s + p["nFaces"]] = pi

        if apply_to_all:
            selected = set(range(n_bnd))
        else:
            names = {p["name"] for p in patches}
            wanted = {n for n in (patch_names or []) if n in names}
            if not wanted:
                # nothing matched: fall back to every patch (safe default)
                selected = set(range(n_bnd))
            else:
                selected = {
                    bi for bi in range(n_bnd) if patch_of[bi] in {
                        pi for pi, p in enumerate(patches) if p["name"] in wanted
                    }
                }

        # NO auto-close: the selection is used as-is.  At an edge shared by
        # a selected and an unselected boundary face (the BL/non-BL
        # boundary), ``_build`` emits the prism side faces as BOUNDARY faces
        # on the patch of the adjacent (unselected) wall face, and moves
        # that face's shared wall vertices to the last layer — the layer
        # terminates as a wall band, every cell stays closed.
        used_patches = sorted({patches[pi]["name"] for bi in selected
                               for pi in [int(patch_of[bi])]})
        return sorted(selected), used_patches

    # ------------------------------------------------------------------
    # core construction
    # ------------------------------------------------------------------

    def _build(self, points, faces, owner, neighbour, patches, n_int,
               sel, n_layers, first_height, growth, clamp_factor) -> dict:
        """Build the new mesh arrays. Raises on degenerate input."""
        pts = np.asarray(points, dtype=np.float64)
        n_pts = len(pts)
        bnd = [n_int + bi for bi in sel]

        # --- wall vertices -------------------------------------------------
        wv: dict[int, None] = {}
        for fi in bnd:
            for v in faces[fi]:
                wv[v] = None
        wall_verts = sorted(wv)

        # vertex -> incident faces index (used by the normals, fade and clamp
        # passes below)
        vert_faces: dict[int, list[int]] = {}
        for fi, f in enumerate(faces):
            for v in f:
                vert_faces.setdefault(v, []).append(fi)
        wall_face_of: dict[int, list[int]] = {}
        for fi in bnd:
            for v in faces[fi]:
                wall_face_of.setdefault(v, []).append(fi)

        # --- per-vertex normals (area-weighted face normals) ----------------
        normals = np.zeros((n_pts, 3))
        for fi in bnd:
            _gil_yield(fi, 256)
            n = _newell(pts, faces[fi])
            for v in faces[fi]:
                normals[v] += n
        mag = np.linalg.norm(normals, axis=1)
        # only WALL vertices matter: interior dual points (tet/face centroids,
        # interior edge mids) legitimately carry no wall-face normal.
        bad_wall = [w for w in wall_verts if mag[w] <= 1e-300]
        for w in wall_verts:
            normals[w] = normals[w] / mag[w] if mag[w] > 1e-300 else 0.0
        if bad_wall:
            raise ValueError(
                f"{len(bad_wall)} wall vertices have zero area"
            )

        # --- feature-aware height fade ---------------------------------------
        # At sharp features the incident wall-face normals point in nearly
        # OPPOSITE directions (concave re-entrant slivers, e.g. the valve), so
        # their average is directionally unstable and the extruded quads can
        # self-intersect at any height.  Fade the layer height out with the
        # maximum angular span of the incident face normals — the classic
        # "terminate the layer at the feature" treatment of production BL
        # codes — and cap by a fraction of the smallest incident wall-face
        # edge (a hard bound against twisted slivers).
        angle_fade: dict[int, float] = {}
        min_edge: dict[int, float] = {}
        for wi, w in enumerate(wall_verts):
            _gil_yield(wi, 64)
            f_norms = []
            f_edges = []
            for fi in wall_face_of.get(w, ()):
                f = faces[fi]
                fn = _newell(pts, f)
                fn = fn / (np.linalg.norm(fn) + 1e-300)
                f_norms.append(fn)
                for k in range(len(f)):
                    a, b = f[k], f[(k + 1) % len(f)]
                    f_edges.append(float(np.linalg.norm(pts[a] - pts[b])))
            cos_max = -2.0
            for i in range(len(f_norms)):
                for j in range(i + 1, len(f_norms)):
                    cos_max = max(cos_max, float(f_norms[i] @ f_norms[j]))
            theta = np.degrees(np.arccos(min(max(cos_max, -1.0), 1.0)))
            # theta = 0..180; fade linearly from full at <=60° to 5% at 150°+
            angle_fade[w] = max(0.05, min(1.0, (150.0 - theta) / 90.0))
            min_edge[w] = min(f_edges) if f_edges else float("inf")

        # --- per-vertex inversion clamp ------------------------------------
        # distance from the wall vertex to the nearest NON-wall vertex of the
        # incident cells bounds how far the extruded surface may travel before
        # the wall-adjacent cells invert.
        max_h = {}
        for wi, w in enumerate(wall_verts):
            _gil_yield(wi, 64)
            # interior anchor vertices reachable from w: primal boundary
            # vertices appear ONLY in their wall quads, so probe two hops —
            # wall faces of w -> shared wall vertices -> their internal faces
            probe: set[int] = set()
            for fi in wall_face_of.get(w, ()):
                for w2 in faces[fi]:
                    if w2 == w:
                        continue
                    for fi2 in vert_faces.get(w2, ()):
                        for v in faces[fi2]:
                            if v != w and v not in wv:
                                probe.add(v)
            if not probe:
                raise ValueError(f"wall vertex {w} has no interior support")
            d = min(float(np.linalg.norm(pts[w] - pts[v])) for v in probe)
            if not np.isfinite(d) or d <= 1e-12:
                raise ValueError(f"wall vertex {w} has no interior support")
            # inversion guard (distance to interior) + feature fade (angular
            # divergence) + hard cap on the smallest incident wall-face edge
            max_h[w] = angle_fade[w] * min(
                clamp_factor * d, 0.5 * min_edge[w],
            )

        # --- layer heights --------------------------------------------------
        # cumulative: H_k = h1 * (r^k - 1)/(r - 1); H_total = H_n.
        # growth == 1.0 -> uniform layers.
        h1 = first_height
        cum = np.empty(n_layers)
        if growth > 1.0:
            h = h1
            acc = 0.0
            for k in range(n_layers):
                acc += h
                cum[k] = acc
                h *= growth
        else:
            for k in range(n_layers):
                cum[k] = h1 * (k + 1)
        h_total = float(cum[-1])
        # per-vertex total height (clamped), with proportional layer steps
        hw = {w: min(h_total, max_h[w]) for w in wall_verts}
        for w in wall_verts:
            if hw[w] <= 0.0:
                hw[w] = h_total * 1e-6
        total = max(hw.values())
        frac = cum / h_total  # 0..1 layer progression, shared by every vertex

        # --- new point allocation ------------------------------------------
        # index (w, k): k=0 -> original point; k=1..n_layers -> extruded
        new_pt = {}
        nxt = n_pts
        pts_list = [pts]
        for w in wall_verts:
            hw_w = hw[w]
            if hw_w <= 0.0:
                continue
            n_w = normals[w]  # OUTWARD wall normal — extrude INWARD (-n_w)
            for k in range(1, n_layers + 1):
                new_pt[(w, k)] = nxt
                nxt += 1
                pts_list.append(pts[w] - n_w * (hw_w * frac[k - 1]))
        new_points = np.vstack(pts_list)

        def pt(w: int, k: int) -> int:
            if k == 0:
                return w
            return new_pt[(w, k)]

        # --- cell numbering --------------------------------------------------
        n_cells_old = int(max(owner.max(), neighbour.max())) + 1
        # prism cells get ids n_cells_old .. ; modified cells keep their ids
        prism_start = n_cells_old
        n_wf = len(bnd)

        # --- edge -> (selected boundary faces) map for side-face pairing ----
        # Every edge of the selected set has either TWO selected faces (the
        # usual internal side-face pairing) or exactly ONE (the BL/non-BL
        # boundary: the prism side face becomes a boundary face assigned to
        # the adjacent unselected patch).  Zero/3+ means a non-manifold
        # boundary — invalid input.
        edge_sides: dict[tuple[int, int], list[int]] = {}
        for bi, fi in enumerate(bnd):
            f = faces[fi]
            for k in range(len(f)):
                a, b = f[k], f[(k + 1) % len(f)]
                key = (a, b) if a < b else (b, a)
                edge_sides.setdefault(key, []).append(bi)
        bad = [k for k, lst in edge_sides.items() if len(lst) not in (1, 2)]
        if bad:
            raise ValueError(
                f"{len(bad)} edge(s) with {len(edge_sides[bad[0]])} selected "
                "faces — the boundary is not a valid 2-manifold"
            )

        # ALL boundary edges -> incident boundary faces (terminator lookup).
        # Closed 2-manifold boundary: exactly 2 incident faces per edge.
        bnd_edge_faces: dict[tuple[int, int], list[int]] = {}
        for bi in range(len(faces) - n_int):
            _gil_yield(bi, 4096)
            f = faces[n_int + bi]
            for k in range(len(f)):
                a, b = f[k], f[(k + 1) % len(f)]
                key = (a, b) if a < b else (b, a)
                bnd_edge_faces.setdefault(key, []).append(bi)

        # boundary face index -> patch index (terminator strips are assigned
        # to the patch of the adjacent unselected face)
        n_bnd = len(faces) - n_int
        patch_of = np.empty(n_bnd, dtype=np.int64)
        for pi, p in enumerate(patches):
            s = p["startFace"] - n_int
            if s < 0 or s + p["nFaces"] > n_bnd:
                raise ValueError(
                    f"patch '{p['name']}' startFace/nFaces out of range "
                    f"({s}..{s + p['nFaces']} vs {n_bnd} boundary faces)"
                )
            patch_of[s:s + p["nFaces"]] = pi

        # --- face assembly ----------------------------------------------------
        # internal faces first (modified originals + new prism faces),
        # then boundary faces in the ORIGINAL order (patch offsets hold).
        new_faces: list[list[int]] = []
        new_own: list[int] = []
        new_nb: list[int] = []

        wall_v = set(wall_verts)

        def _move_face(f: list[int], k: int) -> list[int]:
            """Replace wall vertices with their layer-k points."""
            return [pt(v, k) if v in wall_v else v for v in f]

        # 1. original internal faces: move wall vertices to the last layer.
        #    Every output face is a COPY — the repair passes mutate the
        #    output lists in place, and mutating an aliased input face would
        #    corrupt the next fallback attempt's wall normals/windings.
        for fi in range(n_int):
            f = faces[fi]
            if any(v in wall_v for v in f):
                new_faces.append(_move_face(f, n_layers))
            else:
                new_faces.append(list(f))
            new_own.append(int(owner[fi]))
            new_nb.append(int(neighbour[fi]))

        # 2. new prism faces — geometric orientation using the prism
        #    centroid (vertex average; reliable for the near-convex stacks,
        #    and any residual mis-orientation is repaired below)
        prism_cent = np.zeros((n_wf, n_layers, 3))
        for bi, fi in enumerate(bnd):
            _gil_yield(bi, 256)
            f = faces[fi]
            for k in range(n_layers):
                base = [pt(v, k) for v in f]
                top = [pt(v, k + 1) for v in f]
                verts = base + top
                prism_cent[bi, k] = new_points[verts].mean(axis=0)

        # top faces: ORIGINAL owner cell <-> prism(f, last). The original
        # cell has the lower index, so it must be the OWNER (upper-triangular
        # convention owner < neighbour); the normal then points from the
        # interior cell toward the prism — the same direction as the wall
        # face normal, so the top face keeps the wall face's winding.
        for bi, fi in enumerate(bnd):
            _gil_yield(bi, 256)
            f = faces[fi]
            top = [pt(v, n_layers) for v in f]
            own_c = int(owner[fi])
            nb_c = prism_start + bi * n_layers + (n_layers - 1)
            poly = list(top)
            nrm = _newell(new_points, poly)
            wall_nrm = _newell(new_points, [pt(v, 0) for v in f])
            if float(nrm @ wall_nrm) < 0.0:
                poly.reverse()
            new_faces.append(poly)
            new_own.append(own_c)
            new_nb.append(nb_c)

        # inter-layer faces: prism(f, k-1) <-> prism(f, k) across the
        # horizontal face at layer k (every stack layer needs its top face)
        for bi, fi in enumerate(bnd):
            _gil_yield(bi, 256)
            f = faces[fi]
            for k in range(1, n_layers):
                poly = [pt(v, k) for v in f]
                own_c = prism_start + bi * n_layers + (k - 1)
                nb_c = prism_start + bi * n_layers + k
                nrm = _newell(new_points, poly)
                fc = new_points[poly].mean(axis=0)
                if float(nrm @ (fc - prism_cent[bi, k - 1])) < 0.0:
                    poly.reverse()
                new_faces.append(poly)
                new_own.append(own_c)
                new_nb.append(nb_c)

        # side faces: prism(f, k) <-> prism(f', k) across each shared edge.
        # Each wall-face edge is emitted ONCE (from the lower-index face side)
        # — emitting it from both incident faces would create two coincident
        # faces on the same edge.
        # terminator side faces: at the BL/non-BL boundary the prism side
        # quad lies IN THE PLANE of the adjacent (unselected) wall face —
        # it is that wall band, so it becomes a BOUNDARY face owned by the
        # prism and assigned to the patch of the adjacent face (FASE 1).
        # The unselected wall face itself gets its wall vertices moved to
        # the last layer (below), so its edges still match the moved
        # internal faces and the top faces.
        term_by_patch: dict[int, list[tuple[list[int], int]]] = {}
        n_terminators = 0

        prog_stride = max(1, n_wf // 20)
        for bi, fi in enumerate(bnd):
            _gil_yield(bi, 128)
            if bi % prog_stride == 0:
                self._log(
                    f"[bl] building prism side faces "
                    f"{int(100 * bi / max(1, n_wf))}%..."
                )
            f = faces[fi]
            m = len(f)
            for e in range(m):
                va, vb = f[e], f[(e + 1) % m]
                key = (va, vb) if va < vb else (vb, va)
                others = [x for x in edge_sides[key] if x != bi]
                if not others:
                    # terminator: the BL region ends at this edge.  The
                    # adjacent unselected boundary face belongs to a patch
                    # without BL — every layer's side quad is a BOUNDARY
                    # face owned by the prism, appended to that patch.
                    # (bnd_edge_faces holds ORIGINAL boundary indices, so
                    # the current face is sel[bi], not bi.)
                    uf = [x for x in bnd_edge_faces.get(key, ()) if x != sel[bi]]
                    if len(uf) != 1:
                        raise ValueError(
                            f"terminator edge {key} has {len(uf)} "
                            "unselected incident faces"
                        )
                    pi_target = int(patch_of[uf[0]])
                    for k in range(n_layers):
                        poly = [
                            pt(va, k), pt(vb, k),
                            pt(vb, k + 1), pt(va, k + 1),
                        ]
                        nrm = _newell(new_points, poly)
                        fc = new_points[poly].mean(axis=0)
                        # outward for the boundary face: normal away from
                        # the owner (the prism)
                        if float(nrm @ (fc - prism_cent[bi, k])) < 0.0:
                            poly.reverse()
                        term_by_patch.setdefault(pi_target, []).append(
                            (poly, prism_start + bi * n_layers + k)
                        )
                        n_terminators += 1
                    continue
                obi = others[0]
                if bi > obi:
                    continue  # the other incident face emits this side face
                for k in range(n_layers):
                    poly = [
                        pt(va, k), pt(vb, k),
                        pt(vb, k + 1), pt(va, k + 1),
                    ]
                    own_c = prism_start + bi * n_layers + k
                    nb_c = prism_start + obi * n_layers + k
                    nrm = _newell(new_points, poly)
                    fc = new_points[poly].mean(axis=0)
                    if float(nrm @ (fc - prism_cent[bi, k])) < 0.0:
                        poly.reverse()
                    new_faces.append(poly)
                    new_own.append(own_c)
                    new_nb.append(nb_c)

        # sort internal faces by (owner, neighbour) — OpenFOAM convention
        io = np.array(new_own, dtype=np.int64)
        inb = np.array(new_nb, dtype=np.int64)
        if len(io) and np.any(io == inb):
            raise ValueError("internal face with identical owner and neighbour")
        sort_idx = np.lexsort((inb, io))
        new_faces_int = [new_faces[i] for i in sort_idx]
        out_own = io[sort_idx].tolist()
        out_nb = inb[sort_idx].tolist()
        new_n_int = len(new_faces_int)

        # boundary faces: per patch, in ORIGINAL patch order — the original
        # faces (selected -> prism bottoms, re-owned by the first-layer
        # prism, ORIGINAL wall vertices; unselected -> wall vertices moved
        # to the LAST layer so their edges still match the moved internal
        # faces and the top faces — otherwise the owner cell would be left
        # open at the BL border), then the NEW terminator strips assigned
        # to this patch (prism side faces at the BL/non-BL border, owned by
        # the prisms).  nFaces/startFace are rebuilt to cover the strips.
        sel_set = set(sel)
        sel_pos = {b: i for i, b in enumerate(sel)}
        bnd_faces: list[list[int]] = []
        bnd_owner: list[int] = []
        new_patches = []
        start = new_n_int
        for pi, p in enumerate(patches):
            pf: list[list[int]] = []
            po: list[int] = []
            for bi in range(p["nFaces"]):
                b = p["startFace"] - n_int + bi
                f = faces[n_int + b]
                if b in sel_set:
                    # prism bottom (at the wall, layer 0 = original)
                    pf.append(list(f))
                    po.append(prism_start + sel_pos[b] * n_layers)
                else:
                    pf.append(_move_face(f, n_layers))
                    po.append(int(owner[n_int + b]))
            for poly, own_c in term_by_patch.get(pi, ()):
                pf.append(poly)
                po.append(own_c)
            bnd_faces.extend(pf)
            bnd_owner.extend(po)
            new_patches.append({
                "name": p["name"], "type": p.get("type", "patch"),
                "nFaces": len(pf), "startFace": start,
            })
            start += len(pf)

        out_faces = new_faces_int + bnd_faces
        out_own_all = out_own + bnd_owner
        out_nb_all = out_nb  # len == n_int_new (boundary has no neighbour)

        # repair pass: for the few cells whose windings the geometric
        # orientation got wrong (twisted slivers at concave features), flip
        # individual faces until every cell's signed area-vector sum ~ 0,
        # then enforce checkMesh's 'face pyramids' criterion (normal must
        # point out of the owner's centroid)
        n_cells_new = int(max(max(out_own_all), max(out_nb_all))) + 1
        self._log("[bl] prism faces assembled — repairing windings "
                  "(can take a minute on large meshes)...")
        out_faces = _repair_closures(
            new_points, out_faces, out_own_all, out_nb_all,
            new_n_int, n_cells_new,
        )
        out_faces = _fix_pyramid_faces(
            new_points, out_faces, out_own_all, out_nb_all,
            new_n_int, n_cells_new,
        )

        built_terminators = n_terminators

        return {
            "points": new_points,
            "faces": out_faces,
            "owner": np.array(out_own_all, dtype=np.int64),
            "neigh": np.array(out_nb_all, dtype=np.int64),
            "patches": new_patches,
            "n_int": new_n_int,
            "heights": np.concatenate([[0.0], np.asarray(cum) * (
                total / h_total if h_total > 0 else 1.0
            )]),
            "total_volume": None,
            "min_volume": None,
            "n_terminator_faces": built_terminators,
        }

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------

    def _validate(self, built, total_vol0) -> tuple[bool, str]:
        pts = built["points"]
        faces = built["faces"]
        owner = built["owner"]
        neigh = built["neigh"]
        n_int = built["n_int"]
        n_cells = int(max(owner.max(), neigh.max())) + 1

        if len(owner) != len(faces):
            return False, "owner/faces length mismatch"
        if len(neigh) != n_int:
            return False, "neighbour length != n_int"
        if n_int and (neigh.min() < 0 or neigh.max() >= n_cells):
            return False, "neighbour index out of range"
        if owner.min() < 0 or owner.max() >= n_cells:
            return False, "owner index out of range"
        # patch accounting invariants: the boundary block starts at n_int and
        # the patches tile every boundary face exactly (SA-2 hardening — a
        # silent write with a broken table would FATAL at OpenFOAM read).
        patches = built["patches"]
        if patches and patches[0]["startFace"] != n_int:
            return False, "first patch does not start at n_int"
        if sum(p["nFaces"] for p in patches) != len(faces) - n_int:
            return False, "patch nFaces do not cover the boundary faces"

        vols, closure, scale_mag = _cell_metrics(pts, faces, owner, neigh, n_cells, n_int)
        rel_closure = np.linalg.norm(closure, axis=1) / np.maximum(scale_mag, 1e-300)
        if not np.all(rel_closure < 1e-8):
            n_bad = int(np.sum(rel_closure >= 1e-8))
            return False, f"{n_bad} cells not closed (max rel {rel_closure.max():.2e})"
        if np.any(vols <= 0.0):
            n_neg = int(np.sum(vols <= 0.0))
            return False, f"{n_neg} cells with non-positive volume"
        if not np.isfinite(vols).all():
            return False, "non-finite cell volume"

        v_total = float(vols.sum())
        if abs(v_total - total_vol0) > 1e-6 * max(abs(total_vol0), 1e-30):
            return False, (
                f"volume not conserved: {v_total:.6g} vs {total_vol0:.6g}"
            )

        # checkMesh's 'face pyramids' criterion: every face normal must point
        # out of its owner's centroid.  This is what makes the output pass
        # checkMesh on sharp features (twisted rim cells fail here even when
        # closed) — the scale fallback then thins the layers until it holds.
        n_pyr = _pyramid_violations(pts, faces, owner, neigh, n_int, n_cells)
        if n_pyr:
            return False, f"{n_pyr} faces violate the face-pyramid criterion"

        used = set()
        for f in faces:
            used.update(f)
        if len(used) != len(pts):
            unused = len(pts) - len(used)
            return False, f"{unused} unused points"

        built["total_volume"] = v_total
        built["min_volume"] = float(vols.min())
        return True, "ok"
