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

from . import foam_mesh_io

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
        split_boundary_layer_only: bool = False,
        wedge_cells: bool = False,
        smooth: bool = True,
        smooth_iters: int = 3,
        smooth_relax: float = 0.5,
        fix_nonplanar_faces: str = "none",
        planarity_rel_tol: float = 1e-6,
        planarize_iters: int = 20,
        collapse_smooth_edges: bool = False,
        boundary_feature_angle: float = 90.0,
        collapse_volume_tolerance: float = 0.05,
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
        split_boundary_layer_only
            When splitting a concave vertex, only partition the tets that
            touch the boundary (the first layer) into per-region wedges and
            leave the rest of the star as one core cell, instead of flooding
            the region labels through the whole star.  The full-star wedges
            are long thin cells whose centroid can still fall outside their
            own boundary quads; shallow wedges keep the centroid near the
            surface.  MEASURED on the valve: full-star split 895 -> 1,215
            (worse); boundary-layer-only split 895 -> <see bench>.
        wedge_cells
            Direction-A fix for the concave-feature defect: for every concave
            boundary edge whose dual face fails the pyramid test, insert a
            wedge cell W that OWNS the edge and the four surface quads around
            it, so the endpoint cells no longer wrap the concave corner and
            become convex.  Default False = off (the working converter is
            never changed).  MEASURED on the valve: see bench — this is the
            construction that replaces the dead-end vertex-star split.
        smooth
            Quality-driven Laplacian smoothing of interior dual vertices after
            the construction, guided by the in-process checkMesh replica and
            keep-best (never writes a worse mesh). Boundary vertices are
            pinned, topology is unchanged. Default True.
        smooth_iters
            Maximum smoothing passes. Each pass is accepted only if the total
            defect count does not increase and no cell volume turns
            non-positive.
        smooth_relax
            Laplacian relaxation factor in (0, 1]; 0.5 is a safe default.
        fix_nonplanar_faces
            "none" (default): output is byte-identical to the unfixed
                  converter.  The dual's internal faces are rings of tet
                  centroids around each primal edge and are generally NOT
                  planar; OpenFOAM assumes planar faces, so centroids and
                  normals are slightly off (worse non-orthogonality and
                  skewness).  The boundary faces are planar quads by
                  construction, so this only concerns the internal faces.
            "triangulate": replace every non-planar face by a fan of
                  triangles from its centroid (the cfMesh approach).
                  Geometry-exact: the fan tiles the face's area vector
                  exactly, so TOTAL volume, closure and orientation are
                  preserved to machine precision (per-cell volumes shift
                  only where a warped face is split) and cells stay
                  polyhedral.
            "planarize": project the vertices of non-planar faces onto
                  their Newell planes (interior vertices only — boundary
                  vertices are pinned because the dual boundary is an exact
                  subdivision of the input surface).  Keep-best against the
                  in-process checkMesh replica: a pass is kept only if the
                  defect count does not increase and planarity improves, so
                  the written mesh is never worse than the unfixed one.
            A face counts as non-planar when its max vertex deviation from
            the Newell plane exceeds `planarity_rel_tol` times the mesh
            bounding-box diagonal (same criterion as `planarity_report`).
        planarity_rel_tol
            Relative planarity threshold shared by the fix and the
            measurement tool (default 1e-6 of the bounding-box diagonal).
        planarize_iters
            Max projection passes for fix_nonplanar_faces="planarize".
        collapse_smooth_edges
            False (default): the dual boundary is the EXACT subdivision of the
                  primal surface — each boundary triangle (a,b,c) becomes three
                  planar quads, so the surface is reproduced bit-for-bit and the
                  volume is conserved to ~1e-16.  A dual cell therefore carries
                  one boundary quad per incident boundary triangle.
            True: polyDualMesh / STAR-CCM+ semantics — ONE dual boundary face
                  per primal boundary vertex, walked through the incident
                  boundary-face centroids, with a midpoint kept only on FEATURE
                  edges and a fan/arc split at feature edges and points.  Smooth
                  edges are collapsed: no midpoint, no seam vertex.

            Why it matters beyond aesthetics: the boundary face count drops by
            roughly 3x (one polygon per boundary vertex instead of three quads
            per boundary triangle), and `core/bl_poly.py` extrudes ONE prism
            stack per boundary face — so the collapsed dual is what makes the
            boundary layer a set of polygonal prism columns (the commercial
            topology) instead of ~3x as many quad-based prisms.

            The price, stated plainly: the collapsed boundary runs through the
            boundary-face centroids, so it no longer tiles the primal surface
            exactly.  The dual volume differs from the primal by a small surface
            offset (measured -0.19% / -0.46% on the two venturi cases by the
            equivalent path in `hex_poly_dual.py`) — exactly the same behaviour
            as OpenFOAM's polyDualMesh.  The invariants that still hold are
            closure, positive volume and owner<neighbour; the exact-tiling
            contract does NOT.  Volume conservation is therefore checked against
            a relaxed tolerance in this mode (see `_validate`).

            Mutually exclusive with `wedge_cells` and `split_rounds > 0`: both
            of those subdivide the per-corner boundary quads that this mode
            replaces.  When collapse is on they are disabled with a log line
            rather than silently ignored.
        boundary_feature_angle
            Dihedral angle (degrees) at or above which a boundary edge counts as
            a FEATURE edge for `collapse_smooth_edges` (default 90.0, the
            polyDualMesh default).  Non-manifold edges and patch seams are
            always feature edges regardless of angle.  Distinct from
            `feature_angle`, which drives the (off-by-default) vertex-star split.
        collapse_volume_tolerance
            Maximum |dual - primal| / primal volume accepted in collapse mode
            (default 0.05 = 5%).  The surface offset is a second-order effect:
            MEASURED on a cube lattice mapped to a ball (the worst case — a
            sphere resolved by only n cells across), the drift falls as O(h^2):
            n=3 -> -12.2%, n=4 -> -7.3%, n=6 -> -3.5%, n=8 -> -2.0%,
            n=10 -> -1.3% (drift * n^2 is constant to within 20%).  On a flat-
            faced surface the drift is exactly 0 (the centroid dual tiles a
            plane).  Production meshes resolve their curvature with far more
            than 10 cells, which is why the equivalent path in
            `hex_poly_dual.py` measured -0.19% / -0.46% on the two venturi
            cases.  A drift above this tolerance means either an extremely
            coarse mesh (raise the tolerance deliberately) or a broken walk
            (do not raise it).  Ignored when `collapse_smooth_edges` is False,
            where the tiling is exact and the tolerance stays 1e-6.
        """
        self._case_dir = Path(case_dir).resolve()
        self._log_cb = log
        self._cancel_cb = cancel
        self._median_faces = bool(median_faces)
        self._split_rounds = int(split_rounds)
        self._feature_angle = float(feature_angle)
        self._split_boundary_layer_only = bool(split_boundary_layer_only)
        self._wedge_cells = bool(wedge_cells)
        self._smooth = bool(smooth)
        self._smooth_iters = int(smooth_iters)
        self._smooth_relax = float(smooth_relax)
        if fix_nonplanar_faces not in ("none", "triangulate", "planarize"):
            raise ValueError(
                f"fix_nonplanar_faces must be 'none', 'triangulate' or 'planarize', "
                f"got {fix_nonplanar_faces!r}"
            )
        if not 0.0 < planarity_rel_tol:
            raise ValueError(
                f"planarity_rel_tol must be > 0, got {planarity_rel_tol!r}"
            )
        self._fix_nonplanar_faces = fix_nonplanar_faces
        self._planarity_rel_tol = float(planarity_rel_tol)
        self._planarize_iters = int(planarize_iters)
        self._collapse = bool(collapse_smooth_edges)
        self._boundary_feature_angle = float(boundary_feature_angle)
        if not 0.0 < collapse_volume_tolerance < 1.0:
            raise ValueError(
                "collapse_volume_tolerance must be in (0, 1), got "
                f"{collapse_volume_tolerance!r}"
            )
        self._collapse_vol_tol = float(collapse_volume_tolerance)
        if self._collapse:
            # Both subdivide the per-corner boundary quads that the collapsed
            # walk replaces; keeping them would emit two different boundary
            # tessellations for the same surface.  Disabled loudly, never
            # silently.
            if self._wedge_cells:
                self._wedge_cells = False
                logger.info(
                    "tet_poly_dual: wedge_cells disabled — incompatible with "
                    "collapse_smooth_edges (per-corner boundary quads are "
                    "replaced by the collapsed per-vertex walk)"
                )
            if self._split_rounds:
                self._split_rounds = 0
                logger.info(
                    "tet_poly_dual: split_rounds disabled — incompatible with "
                    "collapse_smooth_edges"
                )

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
        best_ctr = None

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
            M.sf, M.cf = sf, cf  # reused by _validate (saves a recompute)
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
                best_defects, best, best_counts, best_ctr = total, M, counts, ctr
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

        # Direction-A wedge cells: insert one wedge cell per failing concave
        # edge so the endpoint cells stop wrapping the concave corner.  This
        # is a second, defect-driven build; it is kept only if it scores
        # better than the plain dual (the working converter is never changed
        # when the flag is off).
        if self._wedge_cells and best_defects and best_defects > 0:
            self._check_cancel()
            t = time.monotonic()
            wedge_edges = _plan_wedge_edges(P, best, best_ctr)
            if wedge_edges:
                self._log(
                    f"[poly 6/9] wedge cells: {len(wedge_edges):,} concave edges "
                    f"to own (defect-driven)"
                )
                M2 = self._build_dual(
                    P, splits, wedge_edges=wedge_edges, ref_ctr=best_ctr,
                )
                self._log(
                    f"[poly 5/9] wedge rebuild: {M2.n_cells:,} dual cells, "
                    f"{len(M2.faces):,} faces ({time.monotonic() - t:.1f}s)"
                )
                self._check_cancel()
                t = time.monotonic()
                sf2, cf2 = _face_geometry(M2.points, M2.faces)
                M2.sf, M2.cf = sf2, cf2
                ctr2, vol2 = _cell_centres(
                    sf2, cf2, M2.owner, M2.neigh, M2.n_int, M2.n_cells,
                )
                bad2, counts2 = _detect_defects(
                    M2.points, M2.faces, sf2, cf2, ctr2,
                    M2.owner, M2.neigh, M2.n_int, M2.n_cells,
                )
                total2 = counts2["pyramid"] + counts2["non_ortho"] + counts2["skew"]
                self._log(
                    f"[poly 6/9] wedge quality: {counts2['pyramid']} inverted face "
                    f"pyramids, {counts2['non_ortho']} non-orthogonality errors, "
                    f"{counts2['skew']} skewness errors "
                    f"({int(bad2.sum()):,} cells) [{time.monotonic() - t:.1f}s]"
                )
                if total2 < best_defects:
                    best_defects, best, best_counts = total2, M2, counts2
                    self._log("[poly 6/9] wedge rebuild kept (fewer defects)")
                else:
                    self._log("[poly 6/9] wedge rebuild rejected (not better)")
            else:
                self._log("[poly 6/9] no concave edges to fix — wedge pass skipped")

        M = best
        res.split_vertices = len(splits)
        res.residual_defects = int(best_defects or 0)
        res.defect_breakdown = dict(best_counts)
        res.n_internal_faces = M.n_int
        res.n_boundary_faces = len(M.faces) - M.n_int

        # Quality-driven smoothing of interior vertices (keep-best against
        # the in-process checkMesh replica; boundary pinned, topology fixed).
        if self._smooth and best_defects is not None and best_defects > 0:
            self._check_cancel()
            t = time.monotonic()
            from cfmesh_autogui.core.poly_smoother import smooth_dual_mesh

            pts, counts = smooth_dual_mesh(
                M.points, M.faces, M.owner, M.neigh, M.n_int, M.n_cells,
                _detect_defects, _face_geometry, _cell_centres,
                iterations=self._smooth_iters,
                relaxation=self._smooth_relax,
                log=self._log,
            )
            if pts is not M.points:
                M.points = pts
                best_counts = dict(counts)
                total = counts["pyramid"] + counts["non_ortho"] + counts["skew"]
                best_defects = total
                res.residual_defects = int(total)
                res.defect_breakdown = dict(counts)
                self._log(
                    f"[poly 7/9] smoothing applied: defects now "
                    f"{counts['pyramid']}/{counts['non_ortho']}/{counts['skew']} "
                    f"[{time.monotonic() - t:.1f}s]"
                )

        # Optional non-planar-face fix (default "none": output unchanged —
        # the only effect when the flag is off is that `planarity_report`
        # below would report the same numbers as before this converter ran).
        if self._fix_nonplanar_faces != "none":
            self._check_cancel()
            self._apply_nonplanar_fix(M)
            res.n_internal_faces = M.n_int
            res.n_boundary_faces = len(M.faces) - M.n_int
            # re-score with the in-process checkMesh replica so the logged
            # and returned defect counts reflect the fixed mesh, and refresh
            # M.sf/M.cf so _validate uses the geometry it validates.
            sf, cf = _face_geometry(M.points, M.faces)
            M.sf, M.cf = sf, cf
            ctr, vol = _cell_centres(sf, cf, M.owner, M.neigh, M.n_int, M.n_cells)
            _, counts = _detect_defects(
                M.points, M.faces, sf, cf, ctr, M.owner, M.neigh, M.n_int, M.n_cells,
            )
            total = counts["pyramid"] + counts["non_ortho"] + counts["skew"]
            res.residual_defects = int(total)
            res.defect_breakdown = dict(counts)
            self._log(
                f"[poly 7/9] after non-planar fix: {counts['pyramid']} inverted "
                f"face pyramids, {counts['non_ortho']} non-orthogonality errors, "
                f"{counts['skew']} skewness errors"
            )

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
        points = foam_mesh_io.read_points(poly_dir / "points")
        faces_raw = foam_mesh_io.read_faces(poly_dir / "faces")
        owner = foam_mesh_io.read_label_list(poly_dir / "owner").astype(np.int64)
        neighbour = foam_mesh_io.read_label_list(
            poly_dir / "neighbour"
        ).astype(np.int64)
        patches = foam_mesh_io.read_boundary(poly_dir / "boundary")

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
        # neighbour may be unpadded (length == n_internal, the write_polymesh
        # convention) or padded with -1 for boundary faces (the
        # msh_to_of_polymesh convention) — count the real internal faces.
        n_int_primal = int((neighbour >= 0).sum())
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
        # ---- boundary feature edges / points (collapse mode only) ----------
        # polyDualMesh's calcFeatures rule: a boundary edge is a FEATURE edge
        # when it is non-manifold, joins two different patches, or its two
        # boundary faces meet at a dihedral angle >= boundary_feature_angle.
        # A boundary vertex with MORE THAN TWO incident feature edges is a
        # feature POINT (a corner, where the dual face fans out).
        bnd_e_faces: dict[int, list[int]] = {}
        feat_edge = np.zeros(n_edges, dtype=bool)
        feat_vertex = np.zeros(n_pi, dtype=bool)
        n_feat_edges = n_feat_verts = 0
        if self._collapse:
            fe_bnd = face_edge[bnd_face_ids]           # (n_bnd, 3) edge ids
            for bi in range(n_bnd):
                for c in range(3):
                    bnd_e_faces.setdefault(int(fe_bnd[bi, c]), []).append(bi)
            bnd_normals = 0.5 * np.cross(
                points[tri[bnd_face_ids][:, 1]] - points[tri[bnd_face_ids][:, 0]],
                points[tri[bnd_face_ids][:, 2]] - points[tri[bnd_face_ids][:, 0]],
            )
            bnd_nmag = np.linalg.norm(bnd_normals, axis=1)
            cos_limit = float(np.cos(np.deg2rad(self._boundary_feature_angle)))
            pob = patch_of_bnd
            feat_count = np.zeros(n_pi, dtype=np.int64)
            for ei, bis in bnd_e_faces.items():
                is_feature = False
                if len(bis) != 2:
                    is_feature = True            # non-manifold surface edge
                else:
                    f1, f2 = bis
                    if pob[f1] != pob[f2]:
                        is_feature = True        # patch seam
                    elif bnd_nmag[f1] < 1e-300 or bnd_nmag[f2] < 1e-300:
                        is_feature = True        # degenerate triangle
                    else:
                        cosang = float(bnd_normals[f1] @ bnd_normals[f2]) / (
                            bnd_nmag[f1] * bnd_nmag[f2]
                        )
                        if cosang < cos_limit:
                            is_feature = True    # sharp (>= feature angle)
                if is_feature:
                    feat_edge[ei] = True
                    key = uniq_key[ei]
                    feat_count[key // n_pi] += 1
                    feat_count[key % n_pi] += 1
            feat_vertex[feat_count > 2] = True
            n_feat_edges = int(feat_edge.sum())
            n_feat_verts = int(feat_vertex.sum())

        if self._collapse:
            self._log(
                f"[poly 4/9] {len(patches)} boundary patch(es); collapse mode: "
                f"{n_feat_edges:,} feature edges, {n_feat_verts:,} feature points "
                f"(featureAngle={self._boundary_feature_angle:.0f} deg) — one dual "
                f"boundary face per boundary vertex"
            )
        else:
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
            bnd_e_faces=bnd_e_faces, feat_edge=feat_edge, feat_vertex=feat_vertex,
            n_feat_edges=n_feat_edges, n_feat_verts=n_feat_verts,
        )

    # ------------------------------------------------------------------
    # P5: build the dual complex for a given vertex-star partition
    # ------------------------------------------------------------------

    def _build_dual(
        self, P, splits: dict[int, dict[int, int]],
        wedge_edges: dict[int, int] | None = None,
        ref_ctr: np.ndarray | None = None,
    ) -> SimpleNamespace:
        """Build the dual complex.

        `wedge_edges` maps a concave edge key -> wedge cell index (Direction-A
        fix): those edges get a wedge cell W that owns the edge and the four
        surface quads around it, and the endpoint cells stop wrapping the
        concave corner.  `ref_ctr` is the cell-centroid array of the plain
        dual (used only to orient the wedge faces); it must be provided
        whenever `wedge_edges` is.
        """
        n_pi = P.n_pi
        median = self._median_faces

        # ---- cell numbering: one cell per (vertex, star group) + wedges ----
        n_groups = P.used.astype(np.int64)
        for v, m in splits.items():
            n_groups[v] = max(m.values()) + 1
        base = np.zeros(n_pi + 1, dtype=np.int64)
        np.cumsum(n_groups, out=base[1:])
        n_vertex_cells = int(base[n_pi])
        cell_vertex = np.repeat(np.arange(n_pi, dtype=np.int64), n_groups)

        wedge_cell: dict[int, int] = {}
        if wedge_edges:
            for i, key in enumerate(sorted(wedge_edges)):
                wedge_cell[key] = n_vertex_cells + i
            n_cells = n_vertex_cells + len(wedge_edges)
            cell_vertex = np.concatenate([
                cell_vertex, np.full(len(wedge_edges), -1, dtype=np.int64),
            ])
        else:
            n_cells = n_vertex_cells
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

        collapse = self._collapse
        feat_edge_l = P.feat_edge.tolist() if collapse else []

        int_faces: list[list[int]] = []
        int_own: list[int] = []
        int_nb: list[int] = []

        # ---- internal dual faces, one run per primal edge ---------------
        heartbeat = max(1, P.n_edges // 8)
        for ei in range(P.n_edges):
            if ei % 32768 == 0:
                time.sleep(0)  # release the GIL — the Qt UI thread starves otherwise
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

            # Direction-A wedge edge: build the two faces Fa (cell a <-> W)
            # and Fb (cell b <-> W) instead of the single dual face F(a,b).
            wc = wedge_cell.get(int(key)) if wedge_cell else None
            if wc is not None:
                if closed:
                    raise RuntimeError(
                        f"Concave edge ({va},{vb}) has a closed fan — not a "
                        "boundary edge; cannot build a wedge cell."
                    )
                T1 = link[0]
                T2 = link[n]
                c = next(int(v) for v in P.tri[T1] if v != va and v != vb)
                d = next(int(v) for v in P.tri[T2] if v != va and v != vb)
                e_ca = _edge_index_in_tri(P, T1, c, va)
                e_da = _edge_index_in_tri(P, T2, d, va)
                e_bc = _edge_index_in_tri(P, T1, vb, c)
                e_bd = _edge_index_in_tri(P, T2, vb, d)
                ca = base_l[va]
                cb = base_l[vb]
                # surface centre of W: centroid of the 4 quads' vertices.
                # This lies on the surface side of Fa/Fb, so it orients the
                # wedge faces with the normal pointing from the endpoint cell
                # toward the wedge (perpendicular to the edge, not along it).
                sc = (
                    pts_in[va] + pts_in[vb] + emid[ei]
                    + fc_all[T1] + fc_all[T2]
                    + emid[e_ca] + emid[e_bc] + emid[e_da] + emid[e_bd]
                ) / 9.0
                Fa = [pt_vert(va), pt_edge(e_ca), pt_face(T1), ring[0]]
                for k in range(1, n):
                    if median:
                        Fa.append(pt_face(link[k]))
                    Fa.append(ring[k])
                Fa.append(pt_face(T2))
                Fa.append(pt_edge(e_da))
                Fb = [pt_vert(vb), pt_edge(e_bc), pt_face(T1), ring[0]]
                for k in range(1, n):
                    if median:
                        Fb.append(pt_face(link[k]))
                    Fb.append(ring[k])
                Fb.append(pt_face(T2))
                Fb.append(pt_edge(e_bd))
                _emit_wedge_face(Fa, ca, wc, xyz, sc, int_faces, int_own, int_nb)
                _emit_wedge_face(Fb, cb, wc, xyz, sc, int_faces, int_own, int_nb)
                continue

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

            # Collapse mode: a SMOOTH boundary edge loses its midpoint — the
            # seam face survives but without the seam vertex, so the two
            # endpoint dual cells meet directly through the boundary.  This is
            # exactly polyDualMesh's collapse, and it is what makes the dual
            # boundary face of a vertex a single polygon instead of a fan of
            # per-triangle quads.  Feature edges keep their midpoint.
            drop_mid = collapse and not closed and not feat_edge_l[ei]
            mid = -1 if drop_mid else pt_edge(ei)
            i = 0
            while i < n:
                j = i
                while j + 1 < n and pa[j + 1] == pa[i] and pb[j + 1] == pb[i]:
                    j += 1
                poly = ([pt_face(link[i]), ring[i]] if drop_mid
                        else [mid, pt_face(link[i]), ring[i]])
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

        # ---- boundary dual faces ----------------------------------------
        bnd_by_patch: list[list[list[int]]] = [[] for _ in P.patches]
        bown_by_patch: list[list[int]] = [[] for _ in P.patches]
        bnd_tri_l = P.bnd_tri.tolist()
        pob_l = P.patch_of_bnd.tolist()
        fe_all = P.face_edge
        if collapse:
            # polyDualMesh semantics: ONE face per boundary vertex.
            self._build_boundary_collapsed(
                P, xyz, pt_face, pt_edge, pt_vert, base_l,
                bnd_by_patch, bown_by_patch,
            )
            self._log(
                f"[poly 5/9] collapsed boundary: "
                f"{sum(len(b) for b in bnd_by_patch):,} dual boundary faces "
                f"(was {3 * P.n_bnd:,} per-corner quads)"
            )
        # exact per-corner subdivision (default mode); empty when collapsed
        for bi in (() if collapse else range(P.n_bnd)):
            if bi % 4096 == 0:
                time.sleep(0)  # release the GIL during boundary face emission
            f = P.n_int_primal + bi
            t1 = owner_l[f]
            vs = bnd_tri_l[bi]
            fe = fe_all[f]
            fcp = pt_face(f)
            pi = pob_l[bi]
            for c in range(3):
                x = vs[c]
                # the quad at corner c is adjacent to edges (vs[c], vs[c+1])
                # and (vs[c-1], vs[c]); a wedge cell owns it if either edge
                # is a concave fix-set edge (split if both are).
                if wedge_cell:
                    k1 = min(vs[c], vs[(c + 1) % 3]) * n_pi + max(vs[c], vs[(c + 1) % 3])
                    k2 = min(vs[c - 1], vs[c]) * n_pi + max(vs[c - 1], vs[c])
                    w1 = wedge_cell.get(k1)
                    w2 = wedge_cell.get(k2)
                    if w1 is not None and w2 is not None:
                        tri1 = [pt_vert(x), pt_edge(int(fe[c])), fcp]
                        tri2 = [pt_vert(x), fcp, pt_edge(int(fe[c - 1]))]
                        bnd_by_patch[pi].append(tri1)
                        bown_by_patch[pi].append(w1)
                        bnd_by_patch[pi].append(tri2)
                        bown_by_patch[pi].append(w2)
                        continue
                    if w1 is not None:
                        quad = [pt_vert(x), pt_edge(int(fe[c])), fcp,
                                pt_edge(int(fe[c - 1]))]
                        bnd_by_patch[pi].append(quad)
                        bown_by_patch[pi].append(w1)
                        continue
                    if w2 is not None:
                        quad = [pt_vert(x), pt_edge(int(fe[c])), fcp,
                                pt_edge(int(fe[c - 1]))]
                        bnd_by_patch[pi].append(quad)
                        bown_by_patch[pi].append(w2)
                        continue
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
    # P5b: collapsed boundary dual faces (polyDualMesh semantics)
    # ------------------------------------------------------------------

    def _build_boundary_collapsed(
        self, P, xyz, pt_face, pt_edge, pt_vert, base_l,
        bnd_by_patch, bown_by_patch,
    ) -> None:
        """One dual boundary face per primal boundary vertex.

        The face is the rotational walk around the vertex through the incident
        boundary-triangle centroids, with a midpoint inserted only where the
        walk crosses a FEATURE edge.  Smooth edges are collapsed (no midpoint,
        no seam vertex), so a vertex of valence k yields ONE polygon of k
        points instead of k quads.

        Faithful to OpenFOAM's ``polyDualMesh`` (``dualPatch`` /
        ``collectPatchInternalFace`` / ``splitFace``) and to the already
        verified port in ``core/hex_poly_dual.py``, with one deliberate
        difference: each split sub-face is assigned the patch of the boundary
        faces it actually spans, rather than the patch of the walk's start
        face.  Patch seams are feature edges, so an arc between two consecutive
        feature midpoints lies wholly inside one patch — taking the start
        face's patch for every sub-face would misfile the sub-faces on the far
        side of a seam.
        """
        n_pi = P.n_pi
        n_int_primal = P.n_int_primal
        bnd_tri_l = P.bnd_tri.tolist()
        fe_bnd = P.face_edge[P.bnd_face_ids].tolist()
        pob_l = P.patch_of_bnd.tolist()
        feat_edge = P.feat_edge
        feat_vertex = P.feat_vertex
        bnd_e_faces = P.bnd_e_faces
        uniq = P.uniq_key
        pts_in = P.points

        # incident boundary edges per boundary vertex, and an outward
        # reference normal (primal boundary winding is outward)
        vert_edges: dict[int, list[int]] = {}
        vert_out: dict[int, np.ndarray] = {}
        for ei, bis in bnd_e_faces.items():
            key = int(uniq[ei])
            a, b = key // n_pi, key % n_pi
            vert_edges.setdefault(a, []).append(ei)
            vert_edges.setdefault(b, []).append(ei)
        for bi in range(P.n_bnd):
            vs = bnd_tri_l[bi]
            nrm = 0.5 * np.cross(
                pts_in[vs[1]] - pts_in[vs[0]], pts_in[vs[2]] - pts_in[vs[0]],
            )
            for v in vs:
                if v in vert_out:
                    vert_out[v] += nrm
                else:
                    vert_out[v] = nrm.copy()

        guard = 4 * len(bnd_e_faces) + 4
        for n_done, v in enumerate(sorted(vert_edges)):
            if n_done % 4096 == 0:
                time.sleep(0)  # release the GIL — the Qt UI thread starves otherwise
            eis = vert_edges[v]
            e0 = eis[0]
            bis0 = bnd_e_faces.get(e0)
            if bis0 is None or len(bis0) != 2:
                raise RuntimeError(
                    f"Non-manifold boundary edge at vertex {v} "
                    f"({0 if bis0 is None else len(bis0)} incident boundary "
                    "faces, expected 2) — the input surface is not a closed "
                    "2-manifold."
                )
            cur_bi = bis0[0]
            cur_edge = e0
            dual_face: list[int] = []
            face_patch: list[int] = []   # patch of each face-centroid entry
            feat_idx: list[int] = []     # positions of feature midpoints
            while True:
                vs = bnd_tri_l[cur_bi]
                dual_face.append(pt_face(n_int_primal + cur_bi))
                face_patch.append(pob_l[cur_bi])
                c = vs.index(v)
                fe = fe_bnd[cur_bi]
                e_next = int(fe[c])       # edge (v_c, v_c+1)
                e_prev = int(fe[c - 1])   # edge (v_c-1, v_c)
                if cur_edge not in (e_next, e_prev):
                    raise RuntimeError(
                        f"Boundary walk desync at vertex {v} (face {cur_bi})"
                    )
                new_edge = e_next if cur_edge == e_prev else e_prev
                if feat_edge[new_edge]:
                    dual_face.append(pt_edge(new_edge))
                    face_patch.append(-1)
                    feat_idx.append(len(dual_face) - 1)
                if new_edge == e0:
                    break
                bis = bnd_e_faces.get(new_edge)
                if bis is None or len(bis) != 2:
                    raise RuntimeError(
                        f"Non-manifold boundary edge at vertex {v} — the input "
                        "surface is not a closed 2-manifold."
                    )
                cur_bi = bis[0] if bis[1] == cur_bi else bis[1]
                cur_edge = new_edge
                if len(dual_face) > guard:
                    raise RuntimeError(
                        f"Boundary walk around vertex {v} did not terminate."
                    )

            # orient outward (away from the dual cell of v)
            ref = vert_out.get(v)
            if ref is None or float(np.linalg.norm(ref)) < 1e-300:
                raise RuntimeError(
                    f"Boundary vertex {v} has no outward reference normal."
                )
            if _newell_dot(xyz, dual_face, ref) < 0.0:
                dual_face.reverse()
                face_patch.reverse()
                m = len(dual_face) - 1
                feat_idx = [m - i for i in reversed(feat_idx)]

            cell = base_l[v]

            def _patch_of(sub_positions: list[int]) -> int:
                for k in sub_positions:
                    if face_patch[k] >= 0:
                        return face_patch[k]
                raise RuntimeError(
                    f"Dual boundary sub-face at vertex {v} contains no "
                    "primal boundary face — cannot assign a patch."
                )

            nf = len(feat_idx)
            if nf < 2:
                # no split: the whole walk is one polygon
                pi = _patch_of(list(range(len(dual_face))))
                bnd_by_patch[pi].append(dual_face)
                bown_by_patch[pi].append(cell)
                continue

            m = len(dual_face)
            if feat_vertex[v]:
                # feature POINT: fan from the primal vertex (polyDualMesh's
                # "feature point becomes a face centre")
                vp = pt_vert(v)
                for i in range(nf):
                    start, end = feat_idx[i], feat_idx[(i + 1) % nf]
                    pos = []
                    k = start
                    while True:
                        pos.append(k)
                        if k == end:
                            break
                        k = (k + 1) % m
                    sub = [vp] + [dual_face[k] for k in pos]
                    if len(sub) < 3:
                        continue
                    pi = _patch_of(pos)
                    bnd_by_patch[pi].append(sub)
                    bown_by_patch[pi].append(cell)
            else:
                # feature EDGE run: arcs between consecutive feature midpoints
                for i in range(nf):
                    start, end = feat_idx[i], feat_idx[(i + 1) % nf]
                    pos = []
                    k = start
                    while True:
                        pos.append(k)
                        if k == end:
                            break
                        k = (k + 1) % m
                    if len(pos) < 3:
                        continue
                    pi = _patch_of(pos)
                    bnd_by_patch[pi].append([dual_face[k] for k in pos])
                    bown_by_patch[pi].append(cell)

    # ------------------------------------------------------------------
    # P6: decide which vertex stars to split
    # ------------------------------------------------------------------

    def _plan_splits(self, P, M, bad, splits) -> dict[int, dict[int, int]]:
        verts = np.unique(M.cell_vertex[np.flatnonzero(bad)])
        cos_limit = np.cos(np.deg2rad(self._feature_angle))
        # Only vertices on a CONCAVE feature edge may be split.  The earlier
        # version clustered by the unsigned dihedral angle, so it also split
        # harmless convex 90-degree edges and made things worse (895 -> 1,215
        # on the valve).  A convex edge must never be split: its dual cell is
        # already convex there.  Concavity is a signed test (see
        # _concave_boundary_edges) — verified on a cube (0 concave edges) and
        # an L-shaped groove (concave re-entrant edges detected).
        concave_verts = _concave_boundary_vertices(P)
        new: dict[int, dict[int, int]] = {}
        for v in verts.tolist():
            if v in splits:
                continue  # already split as far as this criterion can take it
            if v not in concave_verts:
                continue  # convex vertex — splitting it cannot help
            g = _split_vertex_star(
                P, v, cos_limit, boundary_layer_only=self._split_boundary_layer_only,
            )
            if g:
                new[v] = g
        return new

    # ------------------------------------------------------------------
    # optional non-planar-face fix (off by default — see fix_nonplanar_faces)
    # ------------------------------------------------------------------

    def _apply_nonplanar_fix(self, M) -> None:
        """Fix internal faces whose vertices deviate from their Newell plane
        beyond the tolerance, in place on the assembled dual `M`.

        Only faces flagged by the same criterion `planarity_report` uses are
        touched; everything else is copied through unchanged.  The default
        "none" never reaches this method.
        """
        _, _, dev = _face_planarity(M.points, M.faces)
        bbox_diag = float(np.linalg.norm(M.points.max(axis=0) - M.points.min(axis=0)))
        tol = self._planarity_rel_tol * max(bbox_diag, 1e-300)
        bad = np.flatnonzero(dev > tol)
        n_bad = int(len(bad))
        n_int_bad = int(np.count_nonzero(bad < M.n_int))
        self._log(
            f"[poly 7/9] non-planar faces: {n_bad:,} beyond tol={tol:.3e} "
            f"({n_int_bad:,} internal, {n_bad - n_int_bad:,} boundary)"
        )
        if n_bad == 0:
            return
        if self._fix_nonplanar_faces == "triangulate":
            self._triangulate_nonplanar(M, bad, tol)
        else:  # "planarize"
            self._planarize_nonplanar(M, bad, tol)

    def _triangulate_nonplanar(self, M, bad, tol) -> None:
        """Fan-triangulate every non-planar face from its centroid.

        New centroid points are appended to the point list.  The fan of a
        face tiles its area vector exactly (the centroid terms telescope), so
        TOTAL volume, closure and owner->neighbour orientation are preserved
        to machine precision (per-cell volumes shift only where a warped face
        is split) and the cells stay polyhedral — this is the cfMesh
        approach.  Boundary faces are handled too (they are planar by
        construction, so in practice only internal faces get triangulated).
        """
        faces = M.faces
        n_int = M.n_int
        n_old = len(M.points)
        _, cf_all = _face_geometry(M.points, faces)
        new_points = np.vstack([M.points, cf_all[bad]])
        pos_of = {int(i): int(np.searchsorted(bad, i)) for i in bad}
        out_faces: list[list[int]] = []
        out_own: list[int] = []
        out_nb: list[int] = []
        n_added = 0
        for i in range(n_int):
            if i % 8192 == 0:
                time.sleep(0)  # release the GIL during face-split repair
            f = faces[i]
            c = pos_of.get(i)
            if c is None:
                out_faces.append(f)
                out_own.append(int(M.owner[i]))
                out_nb.append(int(M.neigh[i]))
            else:
                cpt = n_old + c
                k = len(f)
                for j in range(k):
                    out_faces.append([f[j], f[(j + 1) % k], cpt])
                    out_own.append(int(M.owner[i]))
                    out_nb.append(int(M.neigh[i]))
                n_added += k - 1
        new_n_int = n_int + n_added
        new_patches: list[dict] = []
        start = new_n_int
        for p in M.patches:
            k_total = 0
            for i in range(int(p["startFace"]), int(p["startFace"]) + int(p["nFaces"])):
                f = faces[i]
                c = pos_of.get(i)
                if c is None:
                    out_faces.append(f)
                    out_own.append(int(M.owner[i]))
                    k_total += 1
                else:
                    cpt = n_old + c
                    k = len(f)
                    for j in range(k):
                        out_faces.append([f[j], f[(j + 1) % k], cpt])
                        out_own.append(int(M.owner[i]))
                    k_total += k
            new_patches.append({
                "name": p["name"], "type": p.get("type", "patch"),
                "nFaces": k_total, "startFace": start,
            })
            start += k_total
        M.points = new_points
        M.faces = out_faces
        M.owner = np.array(out_own, dtype=np.int64)
        M.neigh = np.array(out_nb, dtype=np.int64)
        M.n_int = new_n_int
        M.patches = new_patches
        self._log(
            f"[poly 7/9] triangulated {len(pos_of):,} non-planar faces "
            f"({len(out_faces) - len(faces):,} extra faces, "
            f"{len(new_points) - n_old:,} new centroid points)"
        )

    def _planarize_nonplanar(self, M, bad, tol) -> None:
        """Project the vertices of non-planar faces onto their Newell planes,
        keep-best against the defect detector (never writes a worse mesh).

        Only interior vertices move: a vertex that belongs to any boundary
        face is pinned, because the dual boundary is an exact subdivision of
        the input surface and must stay on it.
        """
        pts = _planarize(
            M.points, M.faces, M.owner, M.neigh, M.n_int, M.n_cells,
            bad.tolist(), tol,
            iterations=self._planarize_iters,
            log=self._log,
        )
        if pts is not M.points:
            moved = int(np.count_nonzero(np.linalg.norm(pts - M.points, axis=1) > 1e-300))
            M.points = pts
            self._log(f"[poly 7/9] planarize applied ({moved:,} vertices moved)")

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

        # Reuse the face geometry already computed for the quality check when
        # available (the run loop computes it once per round) — recomputing it
        # here costs ~2.5s on the valve for identical values.
        sf, cf = getattr(M, "sf", None), getattr(M, "cf", None)
        if sf is None or cf is None:
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
        # In collapse mode the dual boundary runs through the boundary-face
        # centroids instead of tiling the primal triangles, so the dual is NOT
        # a partition of the primal volume: it loses a thin surface-offset
        # shell.  This is polyDualMesh's own behaviour (measured -0.19% and
        # -0.46% on the two venturi cases in hex_poly_dual's A/B).  The exact
        # tiling contract only applies to the default mode; here the invariants
        # are closure + positive volume, and the drift is bounded and REPORTED,
        # never silently accepted at an arbitrary size.
        vol_tol = self._collapse_vol_tol if self._collapse else 1e-6
        if rel_vol > vol_tol:
            extra = ""
            if self._collapse:
                extra = (
                    " — collapse mode: the surface offset falls as O(h^2), so "
                    "this size of drift means either a very coarse mesh "
                    "(raise collapse_volume_tolerance deliberately) or a "
                    "broken boundary walk (do not raise it)"
                )
            raise RuntimeError(
                f"Volume not conserved: primal {res.volume_before:.6e} vs dual "
                f"{res.volume_after:.6e} (relative {rel_vol:.3e}, tolerance "
                f"{vol_tol:.0e}){extra}"
            )
        self._log(
            f"[poly 8/9] invariants OK — closure {res.max_closure_error:.2e}, "
            f"min cell volume {res.min_cell_volume:.3e}, volume drift {rel_vol:.2e}"
            + (" (surface offset, collapse mode)" if self._collapse else "")
        )

    # ------------------------------------------------------------------
    # writing
    # ------------------------------------------------------------------

    def _write(self, points, faces, owner, neigh, patches) -> None:
        foam_mesh_io.write_polymesh(
            self._case_dir / "constant" / "polyMesh",
            points, faces, owner, neigh, patches,
        )


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
    # Orientation sign only: Newell normal dotted with (vb - va).  Computed
    # inline (no normal array allocation) — this runs once per primal edge
    # (~1M times on the valve), so it is the single hottest path in the
    # converter and a pure-Python loop beats numpy for these small polygons.
    if _newell_dot(xyz, poly, pts_in[vb] - pts_in[va]) < 0.0:
        poly.reverse()
    if ca > cb:
        ca, cb = cb, ca
        poly.reverse()
    faces.append(poly)
    own.append(ca)
    nb.append(cb)


def _emit_wedge_face(poly, ca, cb, xyz, ref_pt, faces, own, nb):
    """Append one wedge face, wound so its normal points owner -> neighbour.

    `ref_pt` is a point on the neighbour (wedge) side of the face — the edge
    midpoint, which lies on the wedge cell's axis — so the face is oriented
    with the owner cell's centroid on the negative side of the normal.
    """
    if len(poly) < 3:
        raise RuntimeError(f"Degenerate wedge face: {poly}")
    nrm = _newell(xyz, poly)
    if float(nrm @ (ref_pt - xyz[poly[0]])) < 0.0:
        poly.reverse()
    if ca > cb:
        ca, cb = cb, ca
        poly.reverse()
    faces.append(poly)
    own.append(ca)
    nb.append(cb)


def _concave_boundary_vertices(P) -> set[int]:
    """Vertices that lie on a CONCAVE boundary feature edge.

    A boundary edge (a, b) is shared by exactly two boundary triangles
    T1 = (a, b, c) and T2 = (a, b, d).  With n1 the OUTWARD normal of T1 and
    c1 its centroid, the edge is concave iff the far vertex of T2 lies on the
    outward side of T1:

        (p2 - c1) . n1 > 0

    (convex edges give < 0; flat/coplanar edges give ~0).  This is the signed
    test the earlier split was missing — it used the unsigned dihedral angle,
    so it also split harmless convex 90-degree edges.  Verified numerically on
    a cube (0 concave edges) and an L-shaped groove (concave re-entrant edges
    detected).  n1 is oriented outward using the owner tet's centroid, so the
    test is robust to the input boundary winding.
    """
    bnd = P.bnd_tri
    n_bnd = P.n_bnd
    edge_tris: dict[tuple[int, int], list[int]] = defaultdict(list)
    for bi in range(n_bnd):
        a, b, c = (int(x) for x in bnd[bi])
        for x, y in ((a, b), (b, c), (c, a)):
            edge_tris[(min(x, y), max(x, y))].append(bi)

    pts = P.points
    owner_l = P.owner
    cc = P.cell_centroid
    concave_verts: set[int] = set()
    for ek, tris in edge_tris.items():
        if len(tris) != 2:
            continue  # non-manifold surface is rejected elsewhere
        t1, t2 = tris
        v1 = bnd[t1]
        v2 = bnd[t2]
        a, b = ek
        p2 = next(int(v) for v in v2 if v != a and v != b)
        c1 = pts[v1].mean(axis=0)
        n1 = np.cross(pts[v1[1]] - pts[v1[0]], pts[v1[2]] - pts[v1[0]])
        ln = float(np.linalg.norm(n1))
        if ln < 1e-300:
            continue
        n1 = n1 / ln
        # orient outward using the owner tet (inside the solid)
        f1 = P.n_int_primal + t1
        o1 = int(owner_l[f1])
        if float(n1 @ (c1 - cc[o1])) < 0.0:
            n1 = -n1
        if float((pts[p2] - c1) @ n1) > 1e-12:
            concave_verts.add(a)
            concave_verts.add(b)
    return concave_verts


def _concave_edges(P) -> set[int]:
    """Edge keys of every CONCAVE boundary edge (signed test).

    Same test as `_concave_boundary_vertices` but returns the edge keys
    (min(a,b)*n_pi + max(a,b)) instead of the endpoint vertices, so the
    Direction-A wedge construction can own individual concave edges.
    """
    bnd = P.bnd_tri
    n_bnd = P.n_bnd
    edge_tris: dict[tuple[int, int], list[int]] = defaultdict(list)
    for bi in range(n_bnd):
        a, b, c = (int(x) for x in bnd[bi])
        for x, y in ((a, b), (b, c), (c, a)):
            edge_tris[(min(x, y), max(x, y))].append(bi)

    pts = P.points
    owner_l = P.owner
    cc = P.cell_centroid
    n_pi = P.n_pi
    out: set[int] = set()
    for ek, tris in edge_tris.items():
        if len(tris) != 2:
            continue
        t1, t2 = tris
        v1 = bnd[t1]
        v2 = bnd[t2]
        a, b = ek
        p2 = next(int(v) for v in v2 if v != a and v != b)
        c1 = pts[v1].mean(axis=0)
        n1 = np.cross(pts[v1[1]] - pts[v1[0]], pts[v1[2]] - pts[v1[0]])
        ln = float(np.linalg.norm(n1))
        if ln < 1e-300:
            continue
        n1 = n1 / ln
        f1 = P.n_int_primal + t1
        o1 = int(owner_l[f1])
        if float(n1 @ (c1 - cc[o1])) < 0.0:
            n1 = -n1
        if float((pts[p2] - c1) @ n1) > 1e-12:
            out.add(min(a, b) * n_pi + max(a, b))
    return out


def _edge_index_in_tri(P, f: int, x: int, y: int) -> int:
    """Index of the primal edge (x, y) within triangle f (via face_edge)."""
    vs = P.tri[f]
    fe = P.face_edge[f]
    for i in range(3):
        if (vs[i] == x and vs[(i + 1) % 3] == y) or (
            vs[i] == y and vs[(i + 1) % 3] == x
        ):
            return int(fe[i])
    raise RuntimeError(f"Edge ({x},{y}) not found in triangle {f}")


def _split_vertex_star(
    P, v: int, cos_limit: float, boundary_layer_only: bool = False,
) -> dict[int, int] | None:
    """Partition the tets around vertex `v` by smooth surface region.

    The boundary triangles at `v` are clustered by dihedral angle, then that
    labelling is flooded inwards over the tets of the star. Returns
    {tet: group} with at least two groups, or None if `v` is not a
    multi-region boundary vertex.

    With `boundary_layer_only=True` the flood is skipped: only the tets that
    own a boundary triangle at `v` (the first layer) get per-region labels,
    and every other tet of the star stays in group 0 (one core cell).  The
    full-star wedges are long thin cells whose centroid can still fall
    outside their own boundary quads; shallow wedges keep the centroid near
    the surface.
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
    if not boundary_layer_only:
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


def _plan_wedge_edges(P, M, ctr) -> dict[int, int]:
    """Concave edges to own with a wedge cell (the Direction-A fix set).

    Defect-driven: an edge is fixed only if one of its dual faces fails
    the pyramid test — either a boundary quad adjacent to the edge, or
    the internal dual face of the edge itself.  Returns {edge_key: 0}
    (values are placeholder cell offsets; `_build_dual` renumbers them).
    """
    sf, cf = M.sf, M.cf
    pv_own = (sf * (cf - ctr[M.owner])).sum(axis=1)
    pv_nb = -(sf[:M.n_int] * (cf[:M.n_int] - ctr[M.neigh])).sum(axis=1)
    fail_own = np.flatnonzero(pv_own <= 0.0)
    fail_nb = np.flatnonzero(pv_nb <= 0.0)

    concave = _concave_edges(P)
    n_pi = P.n_pi
    fix: dict[int, int] = {}
    for f in np.concatenate([fail_own, fail_nb]):
        f = int(f)
        if f >= M.n_int:
            bi, corner = divmod(f - M.n_int, 3)
            vs = P.bnd_tri[bi]
            k1 = min(vs[corner], vs[(corner + 1) % 3]) * n_pi + max(
                vs[corner], vs[(corner + 1) % 3]
            )
            k2 = min(vs[corner - 1], vs[corner]) * n_pi + max(
                vs[corner - 1], vs[corner]
            )
            if k1 in concave:
                fix[k1] = 0
            if k2 in concave:
                fix[k2] = 0
        else:
            o = int(M.cell_vertex[M.owner[f]])
            nb = int(M.cell_vertex[M.neigh[f]])
            if o >= 0 and nb >= 0:
                key = min(o, nb) * n_pi + max(o, nb)
                if key in concave:
                    fix[key] = 0
    return fix


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def _newell(points: np.ndarray, verts: list[int]) -> np.ndarray:
    """Newell area-vector of a polygon (pure Python — faster than numpy for
    the small polygons this converter builds, and this is the hottest path)."""
    n0 = n1 = n2 = 0.0
    p = points
    k = len(verts)
    for i in range(k):
        j = i + 1
        if j == k:
            j = 0
        vi = p[verts[i]]
        vj = p[verts[j]]
        n0 += (vi[1] - vj[1]) * (vi[2] + vj[2])
        n1 += (vi[2] - vj[2]) * (vi[0] + vj[0])
        n2 += (vi[0] - vj[0]) * (vi[1] + vj[1])
    return 0.5 * np.array([n0, n1, n2])


def _newell_dot(points: np.ndarray, verts: list[int], d: np.ndarray) -> float:
    """Newell area-vector of a polygon dotted with `d`, without allocating
    the normal array.  Used for face-orientation decisions (sign only)."""
    n0 = n1 = n2 = 0.0
    p = points
    k = len(verts)
    for i in range(k):
        j = i + 1
        if j == k:
            j = 0
        vi = p[verts[i]]
        vj = p[verts[j]]
        n0 += (vi[1] - vj[1]) * (vi[2] + vj[2])
        n1 += (vi[2] - vj[2]) * (vi[0] + vj[0])
        n2 += (vi[0] - vj[0]) * (vi[1] + vj[1])
    return 0.5 * (n0 * d[0] + n1 * d[1] + n2 * d[2])


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
# non-planarity measurement and fix (see planarity_report / fix_nonplanar_faces)
# ---------------------------------------------------------------------------

def _face_planarity(points: np.ndarray, faces: list[list[int]], idx=None):
    """Newell plane and worst vertex deviation for each face.

    Returns ``(normal, centroid, dev)`` arrays, one entry per face in `idx`
    (default: every face):

      normal    the face's Newell area vector (unnormalised, 2*area for a
                planar polygon — same direction as `_newell`).
      centroid  the polygon's vertex mean (the plane anchor).
      dev       max over the face's vertices of ``|normal . (v - centroid)| /
                |normal|`` — the worst distance of a vertex from the Newell
                plane, in absolute length units.

    Degenerate (zero-area) faces get a NaN normal and +inf deviation, so
    they are always flagged by any threshold and never divide by zero.
    Vectorised by face-size bucket like `_face_geometry`.
    """
    n_sel = len(faces) if idx is None else len(idx)
    if n_sel == 0:
        return (np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0))
    if idx is None:
        idx = np.arange(n_sel)
    idx = np.asarray(idx, dtype=np.int64)
    sizes = np.fromiter((len(faces[i]) for i in idx), dtype=np.int64, count=n_sel)
    normal = np.full((n_sel, 3), np.nan, dtype=np.float64)
    centroid = np.zeros((n_sel, 3), dtype=np.float64)
    dev = np.full(n_sel, np.inf, dtype=np.float64)
    for k in np.unique(sizes):
        sel = np.flatnonzero(sizes == k)
        fids = idx[sel]
        v = np.array([faces[i] for i in fids], dtype=np.int64)
        p = points[v]                                # (m, k, 3)
        nv = np.cross(p, np.roll(p, -1, axis=1)).sum(axis=1)   # 2*Newell
        c = p.mean(axis=1)                           # (m, 3)
        mag = np.linalg.norm(nv, axis=1)
        good = mag > 0.0
        normal[sel[good]] = nv[good]
        centroid[sel] = c
        d = np.zeros(len(sel), dtype=np.float64)
        if np.any(good):
            dot = ((p[good] - c[good][:, None, :]) * nv[good][:, None, :]).sum(axis=2)
            d[good] = np.abs(dot).max(axis=1) / mag[good]
        dev[sel] = np.where(good, d, np.inf)
    return normal, centroid, dev


def _project_once(points, faces, n_int, bad, boundary):
    """One planarize pass: move every interior vertex of a non-planar face
    toward the average of its projections onto the incident faces' Newell
    planes.  Boundary vertices are pinned (the dual boundary is an exact
    subdivision of the input surface).  Returns new points."""
    out = points.copy()
    nrm, ctr = _face_planarity(points, faces, bad)[:2]
    mag = np.linalg.norm(nrm, axis=1)
    good = mag > 0.0
    hat = np.zeros_like(nrm)
    hat[good] = nrm[good] / mag[good][:, None]
    acc = np.zeros_like(points)
    cnt = np.zeros(len(points))
    for fi, f in enumerate(bad):
        if not good[fi]:
            continue
        h = hat[fi]
        c = ctr[fi]
        verts = np.asarray(faces[f], dtype=np.int64)
        movable = ~boundary[verts]
        if not movable.any():
            continue
        delta = -(((points[verts] - c) @ h)[:, None]) * h   # projection shift
        np.add.at(acc, verts[movable], delta[movable])
        cnt[verts[movable]] += 1
    move = cnt > 0
    out[move] = points[move] + acc[move] / cnt[move][:, None]
    return out


def _planarize(
    points,
    faces,
    owner,
    neigh,
    n_int,
    n_cells,
    bad,
    tol,
    iterations: int = 20,
    log=None,
) -> np.ndarray:
    """Keep-best planarization of non-planar faces (fix_nonplanar_faces
    "planarize"), mirroring the P1 poly smoother's contract: a pass is
    accepted only if the in-process checkMesh replica's defect count does not
    increase AND the summed planarity deviation decreases, and no cell volume
    turns non-positive.  Returns the best points seen — never worse than the
    input, so the written mesh is never regressed.
    """
    if iterations <= 0 or not bad:
        return points
    boundary = np.zeros(len(points), dtype=bool)
    for f in faces[n_int:]:
        boundary[f] = True

    def planarity_sum(pts: np.ndarray) -> float:
        # Sum of the max-vertex deviation over the faces flagged as
        # non-planar.  The keep-best safety net is the defect detector (see
        # below): a pass may trade a little planarity on neighbouring faces
        # for a lot on the bad ones, but it may never increase the in-process
        # checkMesh defect count nor turn a cell volume non-positive.
        d = _face_planarity(pts, faces, bad)[2]
        m = np.isfinite(d)
        return float(d[m].sum()) if np.any(m) else float("inf")

    def score(pts):
        sf, cf = _face_geometry(pts, faces)
        ctr, vol = _cell_centres(sf, cf, owner, neigh, n_int, n_cells)
        _, counts = _detect_defects(pts, faces, sf, cf, ctr, owner, neigh, n_int, n_cells)
        return counts, vol

    counts0, _ = score(points)
    best_total = counts0["pyramid"] + counts0["non_ortho"] + counts0["skew"]
    best_pdev = planarity_sum(points)
    best = points
    cur = points
    for it in range(1, iterations + 1):
        cand = _project_once(cur, faces, n_int, bad, boundary)
        counts, vol = score(cand)
        if np.any(vol <= 0.0):
            if log:
                log(f"[poly 7/9] planarize iter {it}: rejected (non-positive cell volume)")
            break
        total = counts["pyramid"] + counts["non_ortho"] + counts["skew"]
        pdev = planarity_sum(cand)
        if log:
            log(
                f"[poly 7/9] planarize iter {it}: defects "
                f"{counts['pyramid']}/{counts['non_ortho']}/{counts['skew']} "
                f"(total {total}), planarity sum {pdev:.3e} "
                f"(was {planarity_sum(cur):.3e})"
            )
        if (
            (total < best_total and pdev <= best_pdev)
            or (total == best_total and pdev < best_pdev)
        ):
            gain = best_pdev - pdev
            best, best_total, best_pdev = cand, total, pdev
            cur = cand
            if total == 0 and pdev == 0.0:
                break
            # stop when the remaining gain per pass is negligible
            if gain < 1e-3 * max(best_pdev + gain, 1e-300):
                break
        else:
            # regressed (defects or planarity) or made no progress — stop,
            # keep the previous best
            break
    return best


def planarity_report(
    points: np.ndarray,
    faces: list[list[int]],
    owner=None,
    neigh=None,
    n_int=None,
    rel_tol: float = 1e-6,
    top_k: int = 20,
) -> dict:
    """Measure face planarity of a polyhedral mesh (the dual, or any polyMesh).

    For every face the max deviation of its vertices from the face's Newell
    plane is computed (see `_face_planarity`); a face counts as non-planar
    when that deviation exceeds ``rel_tol * bbox_diag``.  Returns a dict:

        n_faces, bbox_diag, rel_tol, tolerance,
        n_nonplanar, pct_nonplanar,
        max_dev / mean_dev / p90_dev   (distribution of per-face max
                                        deviation, absolute length units),
        max_rel_dev                    (max_dev / bbox_diag),
        n_degenerate                   (zero-area faces, flagged separately),
        n_nonplanar_internal / n_nonplanar_boundary  (when n_int/owner given),
        worst_faces                    [(face_index, deviation), ...] top_k.

    `points` may be an (N,3) float array; `faces` a list of vertex-index
    lists.  This is the metric the ``fix_nonplanar_faces`` option of
    `TetPolyDualConverter` uses to decide which faces to fix.
    """
    points = np.asarray(points, dtype=np.float64)
    n_faces = len(faces)
    if n_faces == 0:
        raise ValueError("planarity_report: mesh has no faces")
    bbox_diag = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    tol = rel_tol * max(bbox_diag, 1e-300)
    _, _, dev = _face_planarity(points, faces)
    finite = np.isfinite(dev)
    n_degenerate = int(np.count_nonzero(~finite))
    n_nonplanar = int(np.count_nonzero(dev > tol))
    out: dict = {
        "n_faces": n_faces,
        "bbox_diag": bbox_diag,
        "rel_tol": float(rel_tol),
        "tolerance": tol,
        "n_nonplanar": n_nonplanar,
        "pct_nonplanar": 100.0 * n_nonplanar / max(n_faces, 1),
        "n_degenerate": n_degenerate,
        "max_dev": float(dev[finite].max()) if np.any(finite) else float("inf"),
        "mean_dev": float(dev[finite].mean()) if np.any(finite) else float("inf"),
        "sum_dev": float(dev[finite].sum()) if np.any(finite) else float("inf"),
        "p90_dev": float(np.percentile(dev[finite], 90)) if np.any(finite) else float("inf"),
        "max_rel_dev": (
            float(dev[finite].max() / max(bbox_diag, 1e-300))
            if np.any(finite) else float("inf")
        ),
        "worst_faces": [],
    }
    if n_int is not None and owner is not None:
        n_int = int(n_int)
        out["n_nonplanar_internal"] = int(np.count_nonzero(dev[:n_int] > tol))
        out["n_nonplanar_boundary"] = int(np.count_nonzero(dev[n_int:] > tol))
    order = np.argsort(-dev)
    for i in order[:top_k]:
        if not np.isfinite(dev[i]):
            break
        out["worst_faces"].append((int(i), float(dev[i])))
    return out


def _print_planarity_report(rep: dict, label: str = "") -> None:
    """Human-readable rendering of `planarity_report`'s dict."""
    if label:
        print(f"\n=== non-planarity report: {label} ===")
    else:
        print("\n=== non-planarity report ===")
    print(f"faces          {rep['n_faces']:,}")
    print(f"bbox diagonal  {rep['bbox_diag']:.6e}")
    print(f"threshold      dev > {rep['tolerance']:.3e}  "
          f"(rel_tol {rep['rel_tol']:.1e} x bbox_diag)")
    print(
        f"non-planar     {rep['n_nonplanar']:,} / {rep['n_faces']:,} "
        f"({rep['pct_nonplanar']:.3f}%)"
    )
    if "n_nonplanar_internal" in rep:
        print(
            f"  internal     {rep['n_nonplanar_internal']:,}   "
            f"boundary {rep['n_nonplanar_boundary']:,}"
        )
    print(
        f"max dev        {rep['max_dev']:.6e}   "
        f"mean {rep['mean_dev']:.6e}   p90 {rep['p90_dev']:.6e}"
    )
    print(f"sum dev        {rep.get('sum_dev', float('nan')):.6e}")
    print(f"max dev / bbox {rep['max_rel_dev']:.6e}")
    if rep["n_degenerate"]:
        print(f"degenerate     {rep['n_degenerate']:,} (zero-area faces)")
    print("worst faces (index: deviation):")
    for i, d in rep["worst_faces"]:
        print(f"  {i:>10,}: {d:.6e}")


def main(argv=None) -> int:
    """CLI entry point.

        python -m cfmesh_autogui.core.tet_poly_dual --planarity <case_dir>

    reads ``<case_dir>/constant/polyMesh`` and prints the non-planarity
    report.  ``--convert`` instead runs the tet->poly conversion (optionally
    with a non-planar face fix) and prints the report before and after.
    """
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m cfmesh_autogui.core.tet_poly_dual",
        description=__doc__.splitlines()[0],
    )
    ap.add_argument("--planarity", type=Path, default=None,
                    help="OpenFOAM case dir with constant/polyMesh to measure")
    ap.add_argument("--convert", type=Path, default=None,
                    help="tet case dir to convert, then measure before/after")
    ap.add_argument("--fix", choices=["none", "triangulate", "planarize"],
                    default="none", help="non-planar face fix to apply (--convert only)")
    ap.add_argument("--rel-tol", type=float, default=1e-6,
                    help="planarity threshold as fraction of the bbox diagonal")
    ap.add_argument("--top-k", type=int, default=20, help="worst faces to print")
    a = ap.parse_args(argv)

    if a.planarity is None and a.convert is None:
        ap.print_help()
        return 2

    if a.planarity is not None:
        poly = a.planarity / "constant" / "polyMesh"
        if not (poly / "owner").exists():
            print(f"error: {poly} does not look like an OpenFOAM polyMesh")
            return 2
        points, faces, owner, neigh, _ = foam_mesh_io.read_polymesh(poly)
        rep = planarity_report(points, faces, owner, neigh, len(neigh),
                               rel_tol=a.rel_tol, top_k=a.top_k)
        _print_planarity_report(rep, str(a.planarity))
        return 0

    case = a.convert.resolve()
    poly = case / "constant" / "polyMesh"
    if not (poly / "owner").exists():
        print(f"error: {poly} does not look like an OpenFOAM polyMesh")
        return 2
    points, faces, owner, neigh, _ = foam_mesh_io.read_polymesh(poly)
    rep_before = planarity_report(points, faces, owner, neigh, len(neigh),
                                  rel_tol=a.rel_tol, top_k=a.top_k)
    _print_planarity_report(rep_before, f"{case} (input tet mesh)")

    conv = TetPolyDualConverter(case, log=lambda m: print(m, flush=True),
                                fix_nonplanar_faces=a.fix,
                                planarity_rel_tol=a.rel_tol)
    res = conv.run()
    print("")
    if not res.success:
        print("CONVERSION FAILED — the original polyMesh was left untouched:")
        for e in res.errors:
            print(f"  {e}")
        return 1
    print(f"cells          {res.n_tets_before:,} tetrahedra -> "
          f"{res.n_cells_after:,} polyhedra (fix={a.fix!r})")
    print(f"predicted defects: {res.defect_breakdown}")

    points, faces, owner, neigh, _ = foam_mesh_io.read_polymesh(poly)
    rep_after = planarity_report(points, faces, owner, neigh, len(neigh),
                                 rel_tol=a.rel_tol, top_k=a.top_k)
    _print_planarity_report(rep_after, f"{case} (converted dual, fix={a.fix!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

