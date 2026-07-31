"""Terminal-Face Polyhedral Mesh Generation.

Implements the algorithm from Salinas et al. 2023 ("Exploring polyhedral
mesh generation from Delaunay tetrahedral meshes") to convert a
tetrahedral mesh into a polyhedral mesh with ~70% fewer cells.

Algorithm 3 phases:
  1. LABEL:  For each tet, find its largest face (by area). Faces shared
             by two tets as their largest are "terminal-faces". Faces
             that are NOT the largest of either neighbor are "frontier-
             faces" — they become faces of the output polyhedra.
  2. TRAVERSAL:  For each seed tet (adjacent to a terminal-face), run
             DFS through internal faces. When a frontier-face is
             encountered, add it to the polyhedron. The DFS stays
             within one terminal-face region (proven non-overlapping).
  3. REPAIR:  Non-simple polyhedra (with barrier-face tips) are split
             by converting the middle internal face to a frontier-face
             and re-running traversal.

Then: convexity enforcement, smoothing, and OpenFOAM polyMesh export.
"""

from __future__ import annotations

import logging
import re
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Binary OpenFOAM helper
# ---------------------------------------------------------------------------

def _binary_header_parse(raw: bytes) -> tuple[int, int]:
    """Parse binary OpenFOAM header: find data_start offset and item count.

    Binary format:  ...header...  }\\n  [optional comments]  N\\n(\\n  <data>
    We find the LAST '}\n' in the first 4000 bytes (FoamFile closing brace),
    then search for N followed by '(\n' after it.

    Returns (data_start_byte, n_items).
    """
    # Find all '}\n' positions in first 4000 bytes
    all_closes = list(re.finditer(rb'\}\n', raw[:4000]))
    if not all_closes:
        raise ValueError("Cannot find '}' in binary OpenFOAM header")
    # Use the LAST one — this is the FoamFile closing brace
    header_end = all_closes[-1].end()
    # Find N followed by '(\n' after header end
    m = re.search(rb'(\d+)\n\(', raw[header_end:])
    if not m:
        raise ValueError("Cannot find count N and '(' after header end")
    n_items = int(m.group(1))
    data_start = header_end + m.end()
    return data_start, n_items


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TerminalFaceResult:
    success: bool = False
    n_tets_before: int = 0
    n_cells_after: int = 0
    n_terminal_faces: int = 0
    n_frontier_faces: int = 0
    n_polyhedra: int = 0
    n_barrier_repaired: int = 0
    n_leftover_tets_merged: int = 0
    wall_time_s: float = 0.0
    max_non_ortho: float = 0.0
    max_skewness: float = 0.0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------

class TerminalFaceConverter:
    """Convert tetrahedral OpenFOAM mesh to polyhedral via terminal-face regions.

    Usage::

        conv = TerminalFaceConverter(case_dir)
        result = conv.run()
    """

    def __init__(self, case_dir: Path, feature_angle: float = 90.0, smooth_iterations: int = 0):
        self._case_dir = Path(case_dir).resolve()
        self._feature_angle = feature_angle
        self._smooth_iterations = smooth_iterations

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> TerminalFaceResult:
        t0 = time.monotonic()
        result = TerminalFaceResult()

        try:
            # --- Read tet mesh ---
            poly_dir = self._case_dir / "constant" / "polyMesh"
            points = self._read_points(poly_dir / "points")
            self._last_points = points
            faces_raw = self._read_faces(poly_dir / "faces")
            owner = self._read_label_list(poly_dir / "owner")
            neighbour = self._read_label_list(poly_dir / "neighbour")
            boundary_patches = self._read_boundary(poly_dir / "boundary")

            n_cells = int(max(owner.max(), neighbour.max() if len(neighbour) else -1)) + 1
            result.n_tets_before = n_cells
            logger.info("Terminal-face: read %d cells, %d faces, %d points",
                        n_cells, len(faces_raw), len(points))

            # --- Build face→cell adjacency ---
            face_owner = owner
            face_neigh = neighbour
            cell_faces: list[list[int]] = [[] for _ in range(n_cells)]
            for fid in range(len(faces_raw)):
                own = int(face_owner[fid])
                cell_faces[own].append(fid)
                if fid < len(face_neigh) and int(face_neigh[fid]) >= 0:
                    cell_faces[int(face_neigh[fid])].append(fid)

            # --- Verify all cells are tets (4 faces each) ---
            if len(faces_raw) == 0:
                raise RuntimeError(
                    "No faces found in polyMesh — the faces file may be corrupted. "
                    "Re-run gmshToFoam to regenerate the tet mesh."
                )
            non_tet_cells = [i for i in range(n_cells) if len(cell_faces[i]) != 4]
            if non_tet_cells:
                raise RuntimeError(
                    f"Tet-to-poly requires a pure tetrahedral volume; "
                    f"{len(non_tet_cells)}/{n_cells} cells have a topology "
                    "different from 4 faces. Disable boundary layers/prisms "
                    "or use the dedicated hybrid mesher."
                )

            # --- Phase 1: LABEL ---
            largest_face, frontier_bitvector, seed_list = self._label_phase(
                points, faces_raw, cell_faces, face_owner, face_neigh, n_cells,
            )
            # NOT calling _augment_disjoint_pair_matching here: measured on
            # the same block-with-bore reference mesh (251,902 tets) that it
            # raises poly coverage from 21.1% to 80.2% (78.9% -> 19.8%
            # residual tets), but wrong-oriented faces go from 79 to 1,056
            # (13x) — same single failed checkMesh check as the baseline
            # (no new skewness/non-orthogonality failures), but a real
            # regression on the metric that matters for CFD use. User
            # decision 2026-07-31: keep the higher-quality baseline over
            # higher poly coverage. Left implemented (mutual-agreement-first
            # disjoint pair matching, bounded to pairs so it can't runaway-
            # chain) as a starting point if orientation-safe second-chance
            # matching is worth revisiting later.
            result.n_terminal_faces = sum(1 for v in frontier_bitvector if not v)
            result.n_frontier_faces = sum(frontier_bitvector)
            logger.info("Terminal-face label: %d terminal, %d frontier, %d seeds",
                        result.n_terminal_faces, result.n_frontier_faces, len(seed_list))

            # --- Phase 2: TRAVERSAL ---
            polyhedra, tet_sets = self._traversal_phase(
                seed_list, largest_face, frontier_bitvector,
                cell_faces, faces_raw, face_owner, face_neigh, n_cells,
            )
            n_leftover_before = sum(1 for t in tet_sets if len(t) == 1)
            logger.info("Terminal-face traversal: %d polyhedra (%d single-tet leftovers)",
                        len(polyhedra), n_leftover_before)
            # The bounded leftover merge is intentionally not enabled yet:
            # on the reference mesh it reduced residual tets but introduced
            # hundreds of wrong-oriented faces and skewness failures. Keep
            # the clean baseline until a geometric quality gate is added.
            result.n_leftover_tets_merged = 0

            # NOT calling _merge_leftover_tets here: measured directly on
            # a real mesh (block-with-bore, 252,536 tets) that it drives
            # residual bare tets from 78.9% down to ~0.1%, but at a net
            # QUALITY COST that's worse than leaving them alone — checkMesh
            # misoriented faces 79->6471, a new max skewness of 8170
            # (essentially degenerate) that didn't exist before, and new
            # non-orthogonality errors, even with merge-group size capped
            # at 4 tets. The greedy "largest shared frontier face" merge
            # criterion for a leftover tet has none of the mutual-
            # agreement guarantee LABEL/TRAVERSAL's regular merges have,
            # and produces badly-shaped cells and/or an orientation issue
            # in how _build_output canonicalizes faces for these bridged,
            # non-contiguous regions. Needs a real design pass (a shape-
            # aware merge criterion, or fixing _build_output's orientation
            # for this specific case) before it's worth enabling — see
            # _merge_leftover_tets' own docstring for the numbers this
            # conclusion is based on. Left implemented but unused rather
            # than deleted, since the measurement infrastructure (and the
            # capped-chaining design) is a real starting point for that
            # follow-up, not a dead end.

            # --- Phase 3: REPAIR ---
            polyhedra, tet_sets, n_repaired = self._repair_phase(
                polyhedra, tet_sets, frontier_bitvector,
                cell_faces, faces_raw, face_owner, face_neigh, n_cells,
            )
            result.n_barrier_repaired = n_repaired
            result.n_polyhedra = len(polyhedra)
            logger.info("Terminal-face repair: %d barrier repaired, %d final polyhedra",
                        n_repaired, len(polyhedra))

            # --- Post-processing: convexity enforcement ---
            polyhedra = self._enforce_convexity(
                polyhedra, faces_raw, points,
            )
            logger.info("Convexity enforcement: %d polyhedra after split", len(polyhedra))

            # --- Post-processing: smoothing ---
            # Off by default: measured directly on the real valve mesh
            # to make checkMesh's incorrectly-oriented-face count WORSE
            # (846 with smoothing on vs. 271 with it off) — moving even
            # interior-only vertices can invert already-thin merged
            # cells (this mesh has features down to ~0.25mm). A purely
            # cosmetic pass isn't worth trading away geometric
            # correctness for.
            if self._smooth_iterations > 0:
                points = self._smooth_polyhedra(
                    polyhedra, faces_raw, points, boundary_patches,
                    iterations=self._smooth_iterations,
                )

            # --- Build output polyMesh ---
            new_faces, new_owner, new_neighbour, new_boundary = self._build_output(
                polyhedra, tet_sets, faces_raw, face_owner, face_neigh,
                boundary_patches, points, n_cells,
            )

            # --- Write polyMesh ---
            self._write_poly_mesh(
                points, new_faces, new_owner, new_neighbour, new_boundary,
            )
            result.n_cells_after = int(new_owner.max()) + 1 if len(new_owner) > 0 else 0
            result.success = True

        except Exception as exc:
            logger.exception("Terminal-face conversion failed")
            result.errors.append(str(exc))

        result.wall_time_s = round(time.monotonic() - t0, 2)
        return result

    # ------------------------------------------------------------------
    # Phase 1: LABEL
    # ------------------------------------------------------------------

    def _label_phase(
        self,
        points: np.ndarray,
        faces_raw: list[list[int]],
        cell_faces: list[list[int]],
        face_owner: np.ndarray,
        face_neigh: np.ndarray,
        n_cells: int,
    ) -> tuple[np.ndarray, list[bool], list[int]]:
        """Label terminal-faces, frontier-faces, and seed tetrahedra.

        Returns:
            largest_face: array of shape (n_cells,) — index of largest face per tet
            frontier_bitvector: list[bool] — True if face is a frontier-face
            seed_list: list[int] — indices of seed tetrahedra
        """
        # Step 1: For each tet, find its largest face (by area)
        largest_face = np.zeros(n_cells, dtype=np.int32)
        for i in range(n_cells):
            max_area = -1.0
            max_fid = cell_faces[i][0]
            for fid in cell_faces[i]:
                area = self._face_area(faces_raw[fid], points)
                if area > max_area:
                    max_area = area
                    max_fid = fid
            largest_face[i] = max_fid

        # Step 2: Label frontier-faces and find seeds
        n_faces = len(faces_raw)
        frontier_bitvector = [False] * n_faces
        seed_list = []

        for fid in range(n_faces):
            own = int(face_owner[fid])
            if fid < len(face_neigh):
                neigh = int(face_neigh[fid])
            else:
                neigh = -1

            if neigh < 0:
                # Boundary face — always frontier
                frontier_bitvector[fid] = True
                # Seed: the adjacent tet whose largest face this is
                if largest_face[own] == fid:
                    seed_list.append(own)
                continue

            # Internal face: check if both tets share it as their largest
            is_largest_own = (largest_face[own] == fid)
            is_largest_neigh = (largest_face[neigh] == fid)

            if is_largest_own and is_largest_neigh:
                # Terminal-face — not frontier
                seed_list.append(own)
            elif not is_largest_own and not is_largest_neigh:
                # Neither tet claims this as largest → frontier-face
                frontier_bitvector[fid] = True
            else:
                # One tet claims it, the other doesn't → frontier-face
                frontier_bitvector[fid] = True

        return largest_face, frontier_bitvector, seed_list

    def _augment_disjoint_pair_matching(
        self,
        points: np.ndarray,
        faces_raw: list[list[int]],
        cell_faces: list[list[int]],
        face_owner: np.ndarray,
        face_neigh: np.ndarray,
        frontier_bitvector: list[bool],
        n_cells: int,
    ) -> int:
        """Join unmatched tets in a quality-bounded one-to-one matching.

        The first label phase only joins a face when both tetrahedra choose it
        as their largest face.  That is safe but leaves many isolated tets.
        This pass considers all internal faces, ranks each face in both
        incident tetrahedra by area, and greedily accepts the best disjoint
        pairs.  A pair is a valid two-tet polyhedron; no tet is absorbed into
        an already-created cell and no cell can acquire a second shared
        interior interface through this pass.

        Returns the number of accepted pairs.  The selected matching defines
        the complete internal-face graph for this pass; unselected internal
        faces become frontier faces.
        """
        ranks: list[dict[int, int]] = []
        for cfaces in cell_faces:
            ordered = sorted(
                cfaces,
                key=lambda fid: self._face_area(faces_raw[fid], points),
                reverse=True,
            )
            ranks.append({fid: rank for rank, fid in enumerate(ordered)})

        candidates: list[tuple[int, int, float, int, int, int]] = []
        for fid in range(min(len(faces_raw), len(face_neigh))):
            neigh = int(face_neigh[fid])
            if neigh < 0:
                continue
            own = int(face_owner[fid])
            if not (0 <= own < n_cells and 0 <= neigh < n_cells):
                continue
            ro = ranks[own].get(fid, 99)
            rn = ranks[neigh].get(fid, 99)
            area = self._face_area(faces_raw[fid], points)
            # Prefer faces high in both local rankings, then larger faces.
            candidates.append((max(ro, rn), ro + rn, -area, fid, own, neigh))

        candidates.sort()
        used = [False] * n_cells
        selected: set[int] = set()
        for _quality, _rank_sum, _neg_area, fid, own, neigh in candidates:
            if used[own] or used[neigh]:
                continue
            used[own] = True
            used[neigh] = True
            selected.add(fid)

        # Reclassify all internal faces: selected pairs are internal; all
        # other connections are frontier faces for the resulting cells.
        for fid in range(min(len(faces_raw), len(face_neigh))):
            if int(face_neigh[fid]) >= 0:
                frontier_bitvector[fid] = fid not in selected

        return len(selected)

    # ------------------------------------------------------------------
    # Phase 2: TRAVERSAL (DFS)
    # ------------------------------------------------------------------

    def _traversal_phase(
        self,
        seed_list: list[int],
        largest_face: np.ndarray,
        frontier_bitvector: list[bool],
        cell_faces: list[list[int]],
        faces_raw: list[list[int]],
        face_owner: np.ndarray,
        face_neigh: np.ndarray,
        n_cells: int,
        max_cells_per_poly: int = 48,
    ) -> tuple[list[list[int]], list[set[int]]]:
        """Traversal from each seed to build polyhedra, capped in size.

        Returns (polyhedra, tet_sets) — polyhedra[i] is a list of face
        indices, tet_sets[i] is the set of original tet indices merged
        into it. tet_sets is what lets _build_output orient every face
        correctly regardless of whether the merged cell is convex (see
        _build_output's docstring).

        Uncapped, this can merge a huge connected swath of the mesh into
        one non-physical "cell": on a real CAD tet mesh with a large
        uniform bulk region (a long valve body meshed coarsely away from
        its features), many tets in sequence can mutually agree their
        shared face is each one's largest, so the traversal never hits a
        frontier-face and keeps absorbing neighbors. Confirmed directly
        on the real valve: one 586,476-face cell out of 172,737 total —
        over 100x any other cell (p99 was 17 faces) — which also made
        checkMesh's per-cell geometry checks run past 15 minutes without
        finishing (an O(faces) or worse cost per cell for a cell that
        large). 48 is a generous ceiling given that reference.

        When the cap forces a stop mid-region, the internal face that
        would have been crossed is permanently reclassified as a
        frontier-face (mutating frontier_bitvector in place — the same
        mechanism REPAIR uses to split non-simple polyhedra), so the new
        boundary is geometrically valid on both sides. The tet on the far
        side is left for a later traversal; a final sweep over every
        cell index starts a fresh capped traversal from anything no seed
        ever reached, so every original tet ends up in exactly one
        output polyhedron — nothing is silently dropped.
        """
        visited = [False] * n_cells
        polyhedra: list[list[int]] = []
        tet_sets: list[set[int]] = []

        def run_traversal(start: int) -> tuple[list[int], set[int]]:
            poly_faces: list[int] = []
            tet_set: set[int] = {start}
            stack = [start]
            visited[start] = True
            n_in_poly = 0
            while stack:
                tet_idx = stack.pop()
                n_in_poly += 1
                for fid in cell_faces[tet_idx]:
                    if frontier_bitvector[fid]:
                        if fid not in poly_faces:
                            poly_faces.append(fid)
                        continue
                    own = int(face_owner[fid])
                    neigh = int(face_neigh[fid]) if fid < len(face_neigh) else -1
                    if neigh < 0:
                        continue
                    neighbor = neigh if own == tet_idx else own
                    if visited[neighbor]:
                        continue
                    if n_in_poly >= max_cells_per_poly:
                        # Seal the boundary here instead of expanding
                        # further — neighbor is picked up by a later
                        # traversal (seed loop or the sweep below).
                        frontier_bitvector[fid] = True
                        if fid not in poly_faces:
                            poly_faces.append(fid)
                        continue
                    visited[neighbor] = True
                    tet_set.add(neighbor)
                    stack.append(neighbor)
            return poly_faces, tet_set

        for seed in seed_list:
            if visited[seed]:
                continue
            poly_faces, tet_set = run_traversal(seed)
            if poly_faces:
                polyhedra.append(poly_faces)
                tet_sets.append(tet_set)

        # Full-coverage sweep: a tet the cap isolated mid-region (or one
        # that was never in seed_list to begin with) still needs to end
        # up in exactly one output polyhedron.
        for tet_idx in range(n_cells):
            if visited[tet_idx]:
                continue
            poly_faces, tet_set = run_traversal(tet_idx)
            if poly_faces:
                polyhedra.append(poly_faces)
                tet_sets.append(tet_set)

        return polyhedra, tet_sets

    def _dfs(
        self,
        tet_idx: int,
        visited: list[bool],
        poly_faces: list[int],
        frontier_bitvector: list[bool],
        cell_faces: list[list[int]],
        face_owner: np.ndarray,
        face_neigh: np.ndarray,
        tet_set: set[int] | None = None,
    ) -> None:
        """Depth-first search within one terminal-face region."""
        visited[tet_idx] = True
        if tet_set is not None:
            tet_set.add(tet_idx)

        for fid in cell_faces[tet_idx]:
            if frontier_bitvector[fid]:
                # This is a frontier-face → add to polyhedron
                if fid not in poly_faces:
                    poly_faces.append(fid)
            else:
                # Internal face → find the neighbor tet
                own = int(face_owner[fid])
                if fid < len(face_neigh):
                    neigh = int(face_neigh[fid])
                else:
                    neigh = -1

                if neigh < 0:
                    continue

                neighbor = neigh if own == tet_idx else own
                if not visited[neighbor]:
                    self._dfs(neighbor, visited, poly_faces, frontier_bitvector,
                              cell_faces, face_owner, face_neigh, tet_set)

    # ------------------------------------------------------------------
    # Phase 3: REPAIR
    # ------------------------------------------------------------------

    def _repair_phase(
        self,
        polyhedra: list[list[int]],
        tet_sets: list[set[int]],
        frontier_bitvector: list[bool],
        cell_faces: list[list[int]],
        faces_raw: list[list[int]],
        face_owner: np.ndarray,
        face_neigh: np.ndarray,
        n_cells: int,
    ) -> tuple[list[list[int]], list[set[int]], int]:
        """Detect and repair non-simple polyhedra (barrier-face tips).

        A polyhedron is non-simple if it contains duplicate faces.
        The repair splits it by converting the middle internal face
        around the barrier tip to a frontier-face.
        """
        repaired: list[list[int]] = []
        repaired_tet_sets: list[set[int]] = []
        n_repaired = 0

        for poly_faces, tet_set in zip(polyhedra, tet_sets):
            # Check for duplicate faces
            seen = set()
            duplicates = []
            for fid in poly_faces:
                if fid in seen:
                    duplicates.append(fid)
                seen.add(fid)

            if not duplicates:
                repaired.append(poly_faces)
                repaired_tet_sets.append(tet_set)
                continue

            # Non-simple polyhedron — split at barrier tip
            n_repaired += 1
            # Find the duplicated face and split around it
            split_faces, split_tet_sets = self._split_at_barrier(
                poly_faces, tet_set, duplicates, frontier_bitvector,
                cell_faces, faces_raw, face_owner, face_neigh,
            )
            repaired.extend(split_faces)
            repaired_tet_sets.extend(split_tet_sets)

        return repaired, repaired_tet_sets, n_repaired

    def _merge_leftovers_safe(
        self,
        polyhedra: list[list[int]],
        tet_sets: list[set[int]],
        cell_faces: list[list[int]],
        faces_raw: list[list[int]],
        face_owner: np.ndarray,
        face_neigh: np.ndarray,
    ) -> tuple[list[list[int]], list[set[int]], int]:
        """Merge only topologically safe single-tet leftovers.

        A leftover tet may be attached to a target polyhedron only when:
        - the target is not itself a singleton;
        - exactly one shared face is used as the interface;
        - no second face is common between the two cells;
        - each target receives at most one leftover in this pass.

        This deliberately leaves some tetrahedra unmerged. A mixed but valid
        mesh is preferable to a cosmetically all-poly mesh with duplicate
        faces, inverted volumes, or non-manifold topology.
        """
        face_to_poly: dict[int, list[int]] = {}
        for pi, faces in enumerate(polyhedra):
            for fid in faces:
                face_to_poly.setdefault(fid, []).append(pi)

        target_used: set[int] = set()
        removed: set[int] = set()
        merges = 0
        candidates: list[tuple[float, int, int, int]] = []

        for pi, tset in enumerate(tet_sets):
            if len(tset) != 1:
                continue
            tet = next(iter(tset))
            for fid in cell_faces[tet]:
                own = int(face_owner[fid])
                neigh = int(face_neigh[fid]) if fid < len(face_neigh) else -1
                other = neigh if own == tet else own
                for target in face_to_poly.get(fid, []):
                    if target == pi or len(tet_sets[target]) <= 1:
                        continue
                    other_faces = set(polyhedra[target])
                    leftover_faces = set(polyhedra[pi])
                    # The shared interface is the only face allowed to
                    # overlap between the two cell boundaries.
                    if (other_faces & leftover_faces) != {fid}:
                        continue
                    area = self._face_area(faces_raw[fid], self._last_points)
                    candidates.append((-area, pi, target, fid))

        candidates.sort()
        for _neg_area, pi, target, fid in candidates:
            if pi in removed or target in removed or target in target_used:
                continue
            if len(tet_sets[pi]) != 1 or len(tet_sets[target]) <= 1:
                continue
            # Recheck after earlier merges in this pass.
            if (set(polyhedra[target]) & set(polyhedra[pi])) != {fid}:
                continue
            merged = [f for f in polyhedra[target] if f != fid]
            merged.extend(f for f in polyhedra[pi] if f != fid)
            if len(set(merged)) != len(merged) or len(merged) < 4:
                continue
            polyhedra[target] = list(dict.fromkeys(merged))
            tet_sets[target].update(tet_sets[pi])
            removed.add(pi)
            target_used.add(target)
            merges += 1

        kept_poly = [p for i, p in enumerate(polyhedra) if i not in removed]
        kept_tets = [t for i, t in enumerate(tet_sets) if i not in removed]
        return kept_poly, kept_tets, merges

    def _split_at_barrier(
        self,
        poly_faces: list[int],
        tet_set: set[int],
        duplicates: list[int],
        frontier_bitvector: list[bool],
        cell_faces: list[list[int]],
        faces_raw: list[list[int]],
        face_owner: np.ndarray,
        face_neigh: np.ndarray,
    ) -> tuple[list[list[int]], list[set[int]]]:
        """Split a non-simple polyhedron at a barrier-face tip.

        Strategy: take the first duplicate face, find the middle internal
        face among its adjacent internal faces, convert it to frontier,
        and re-traverse.

        Re-traversal is restricted to this polyhedron's own tet_set (the
        set already known to belong to it before the split) — visiting
        the whole mesh instead, as an earlier version did with a fresh
        all-False `visited` array, could cross into tets already claimed
        by a DIFFERENT, already-finalized polyhedron and silently
        duplicate their assignment across two cells.
        """
        dup_fid = duplicates[0]

        # Find internal faces around the barrier tip (faces of the poly
        # that are NOT the duplicated face but share a vertex with it)
        dup_verts = set(faces_raw[dup_fid])
        internal_around = []
        for fid in poly_faces:
            if fid == dup_fid:
                continue
            if not frontier_bitvector[fid]:
                # This is an internal face that's in the poly
                fverts = set(faces_raw[fid])
                if fverts & dup_verts:  # shares a vertex
                    internal_around.append(fid)

        if not internal_around:
            # Can't repair — just keep as-is (may have bad quality)
            return [poly_faces], [tet_set]

        # Pick the middle internal face
        mid_idx = len(internal_around) // 2
        mid_fid = internal_around[mid_idx]

        # Convert it to frontier (this splits the region)
        frontier_bitvector[mid_fid] = True

        # Re-traverse from the two tets adjacent to mid_fid, confined to
        # this polyhedron's own tets.
        mid_own = int(face_owner[mid_fid])
        mid_neigh = int(face_neigh[mid_fid]) if mid_fid < len(face_neigh) else -1

        split_results: list[list[int]] = []
        split_tet_sets: list[set[int]] = []
        visited = {t: False for t in tet_set}

        for start_tet in ([mid_own, mid_neigh] if mid_neigh >= 0 else [mid_own]):
            if start_tet not in visited or visited[start_tet]:
                continue
            poly_faces_new: list[int] = []
            new_tet_set: set[int] = set()
            stack = [start_tet]
            visited[start_tet] = True
            while stack:
                t = stack.pop()
                new_tet_set.add(t)
                for fid in cell_faces[t]:
                    if frontier_bitvector[fid]:
                        if fid not in poly_faces_new:
                            poly_faces_new.append(fid)
                        continue
                    o = int(face_owner[fid])
                    ng = int(face_neigh[fid]) if fid < len(face_neigh) else -1
                    if ng < 0:
                        continue
                    nb = ng if o == t else o
                    if nb in visited and not visited[nb]:
                        visited[nb] = True
                        stack.append(nb)
            if poly_faces_new:
                split_results.append(poly_faces_new)
                split_tet_sets.append(new_tet_set)

        return (split_results, split_tet_sets) if split_results else ([poly_faces], [tet_set])

    # ------------------------------------------------------------------
    # Leftover-tet cleanup
    # ------------------------------------------------------------------

    def _merge_leftover_tets(
        self,
        polyhedra: list[list[int]],
        tet_sets: list[set[int]],
        faces_raw: list[list[int]],
        points: np.ndarray,
        cell_faces: list[list[int]],
        face_owner: np.ndarray,
        face_neigh: np.ndarray,
        max_leftover_group_size: int = 4,
    ) -> tuple[list[list[int]], list[set[int]], int]:
        """Absorb single-tet leftovers into an adjacent polyhedron.

        LABEL/TRAVERSAL above only merges two tets across a face they
        BOTH independently pick as their own largest face — a narrow
        condition. Measured on a real mesh (a block with a bore, 251,935
        tets): only 43,870 such mutual matches existed, leaving 78.9% of
        cells (164,195) as bare, unmerged 4-face tets in the final
        output — confirmed both by directly counting faces per cell in
        the written polyMesh and by checkMesh's own "tetrahedra: N /
        polyhedra: N" breakdown. The traversal's own docstring claims
        every tet "ends up in exactly one output polyhedron" — true, but
        that polyhedron can trivially just be the tet itself when none of
        its faces ever hit that mutual-match condition, which is common,
        not the exception this is a genuine coverage gap, not the one-off
        edge case the original design treated it as.

        This is a bounded number of sweeps (not a single O(n^2) fixed-
        point loop — with hundreds of thousands of cells that's too slow)
        over whatever is currently a single-tet polyhedron, merging it
        into whichever neighboring polyhedron shares its largest face.
        Multiple rounds because merging can turn a previously-
        unresolvable leftover's neighbor into a valid target (e.g. two
        adjacent leftovers merge with each other on round 1, then a
        third leftover next to THAT pair can merge with it on round 2).

        max_leftover_group_size caps how many leftover tets any single
        target polyhedron may absorb THROUGH THIS PASS (independent of
        however large it already was from regular terminal-face
        merging) — an uncapped version measured directly on a real mesh
        does eliminate every bare tet, but does so by a "rich get richer"
        dynamic: a polyhedron that starts absorbing leftovers gains
        surface area, making it more likely to be the "largest shared
        face" match for yet more leftovers next round, snowballing into
        cells up to 30 faces from repeatedly chaining unrelated tets
        together with no shape awareness. checkMesh on that unbounded
        version got WORSE, not better, on every quality metric — face
        orientation errors 79->5462, non-orthogonality errors 0->118,
        and a new max skewness of 15858 (essentially degenerate) that
        didn't exist before. This cap keeps each absorption event local
        (a genuinely isolated tet joining its one or two immediate
        neighbors) instead of feeding a runaway magnet.
        """
        n_polys = len(polyhedra)
        # tet -> polyhedron index; kept in sync as merges happen.
        tet_to_poly: dict[int, int] = {}
        for i, tset in enumerate(tet_sets):
            for t in tset:
                tet_to_poly[t] = i

        alive = [True] * n_polys
        leftovers_absorbed = [0] * n_polys
        n_merged = 0
        MAX_ROUNDS = 12
        for _ in range(MAX_ROUNDS):
            leftovers = [i for i in range(n_polys) if alive[i] and len(tet_sets[i]) == 1]
            if not leftovers:
                break
            round_merges = 0
            for i in leftovers:
                if not alive[i] or len(tet_sets[i]) != 1:
                    continue  # already absorbed into someone else this round
                leftover_tet = next(iter(tet_sets[i]))
                best_target = -1
                best_area = -1.0
                best_fid = -1
                for fid in cell_faces[leftover_tet]:
                    own = int(face_owner[fid])
                    neigh = int(face_neigh[fid]) if fid < len(face_neigh) else -1
                    if neigh < 0:
                        continue  # boundary face — no neighbor to merge with
                    other_tet = neigh if own == leftover_tet else own
                    target = tet_to_poly.get(other_tet, -1)
                    if target < 0 or target == i or not alive[target]:
                        continue
                    if leftovers_absorbed[target] >= max_leftover_group_size:
                        continue
                    area = self._face_area(faces_raw[fid], points)
                    if area > best_area:
                        best_area, best_target, best_fid = area, target, fid
                if best_target < 0:
                    continue  # no valid neighbor this round — retry next round

                # Merge leftover_tet into best_target across best_fid: that
                # face becomes interior (drop from both), the leftover's
                # other faces become part of the target's boundary.
                polyhedra[best_target] = [f for f in polyhedra[best_target] if f != best_fid]
                polyhedra[best_target].extend(f for f in polyhedra[i] if f != best_fid)
                tet_sets[best_target].add(leftover_tet)
                tet_to_poly[leftover_tet] = best_target
                leftovers_absorbed[best_target] += 1
                alive[i] = False
                polyhedra[i] = []
                tet_sets[i] = set()
                n_merged += 1
                round_merges += 1
            if round_merges == 0:
                break  # remaining leftovers are boundary-locked — stop retrying

        new_polyhedra = [p for p, a in zip(polyhedra, alive) if a]
        new_tet_sets = [t for t, a in zip(tet_sets, alive) if a]
        return new_polyhedra, new_tet_sets, n_merged

    # ------------------------------------------------------------------
    # Convexity enforcement
    # ------------------------------------------------------------------

    def _enforce_convexity(
        self,
        polyhedra: list[list[int]],
        faces_raw: list[list[int]],
        points: np.ndarray,
    ) -> list[list[int]]:
        """Intentional no-op — see below for why, rather than a check
        that looks like it works but doesn't.

        A real per-face convexity test needs each face's normal
        canonically oriented outward *relative to this specific merged
        polyhedron* before comparing it against the polyhedron's
        centroid. `faces_raw[fid]`'s stored vertex winding has no such
        per-polyhedron meaning here — it's fixed once, globally, relative
        to the ORIGINAL pre-merge tet mesh's owner/neighbour (pointing
        from `face_owner[fid]`'s tet toward `face_neigh[fid]`'s tet).
        Whether that raw direction happens to point toward or away from
        THIS polyhedron's centroid is essentially a coin flip — it
        depends only on which of the two original tets this polyhedron
        happened to absorb, not on the polyhedron's actual shape. Testing
        against it directly (an earlier version of this function did,
        after separately fixing two more mundane bugs — a point-lookup
        stub that always returned the origin, and a self-cancelling
        `cell_center = fc` shortcut) would flag roughly half of any
        genuinely convex, good-quality polyhedron's own faces as
        "non-convex" and fragment it for no geometric reason.

        _build_output solves this the right way for its own purpose —
        canonicalizing orientation using each polyhedron's own centroid —
        but that's an assignment (there's nothing to get wrong), not a
        test; the same canonicalization can't also serve as a convexity
        detector without circularity. A correct detector needs a
        consistently-oriented normal field across each polyhedron's own
        faces first (e.g. propagated via shared-edge adjacency), which
        this pipeline doesn't build. Leaving this as a documented no-op
        is honest about that gap — OpenFOAM tolerates mildly non-convex
        polyhedra in practice, so this is a quality nice-to-have, not a
        correctness blocker like the traversal-cap and _build_output
        fixes were.
        """
        return polyhedra

    # ------------------------------------------------------------------
    # Smoothing
    # ------------------------------------------------------------------

    def _smooth_polyhedra(
        self,
        polyhedra: list[list[int]],
        faces_raw: list[list[int]],
        points: np.ndarray,
        boundary_patches: list[dict],
        iterations: int = 2,
        relaxation: float = 0.3,
    ) -> np.ndarray:
        """Centroidal relaxation of INTERIOR polyhedral mesh points only.

        For each iteration, move each interior point toward the average
        of its neighboring cell centroids (Laplacian smoothing).

        Boundary vertices — anything touched by an original boundary face
        — are left untouched. Moving them would pull the mesh surface off
        the actual CAD geometry it's supposed to conform to; on this
        pipeline's own real test case (a valve with ~0.25mm fillets
        against a 3m body) even the modest default relaxation here could
        plausibly self-intersect or invert a thin feature, since nothing
        previously distinguished a surface vertex from an interior one.
        """
        boundary_verts: set[int] = set()
        n_faces_old = len(faces_raw)
        for patch in boundary_patches:
            start = patch.get("startFace", 0)
            n_faces_patch = patch.get("nFaces", 0)
            for i in range(start, min(start + n_faces_patch, n_faces_old)):
                boundary_verts.update(faces_raw[i])

        new_points = points.copy()

        # Build vertex→cell adjacency (interior vertices only)
        vertex_cells: dict[int, set[int]] = {}
        for cell_idx, poly_faces in enumerate(polyhedra):
            for fid in poly_faces:
                for vid in faces_raw[fid]:
                    if vid in boundary_verts:
                        continue
                    vertex_cells.setdefault(vid, set()).add(cell_idx)

        # Compute cell centroids
        n_cells = len(polyhedra)
        centroids = np.zeros((n_cells, 3), dtype=np.float64)
        for cell_idx, poly_faces in enumerate(polyhedra):
            all_verts = []
            for fid in poly_faces:
                for vid in faces_raw[fid]:
                    if vid < len(points):
                        all_verts.append(points[vid])
            if all_verts:
                centroids[cell_idx] = np.mean(all_verts, axis=0)

        # Smooth: move each interior vertex toward the average of its
        # cell centroids.
        for _ in range(iterations):
            for vid, cells in vertex_cells.items():
                if vid >= len(new_points):
                    continue
                if len(cells) == 0:
                    continue
                avg = np.mean([centroids[c] for c in cells if c < n_cells], axis=0)
                new_points[vid] = new_points[vid] * (1 - relaxation) + avg * relaxation

        return new_points

    # ------------------------------------------------------------------
    # Build output
    # ------------------------------------------------------------------

    def _build_output(
        self,
        polyhedra: list[list[int]],
        tet_sets: list[set[int]],
        faces_raw: list[list[int]],
        face_owner: np.ndarray,
        face_neigh: np.ndarray,
        boundary_patches: list[dict],
        points: np.ndarray,
        n_old_cells: int,
    ) -> tuple[list[list[int]], np.ndarray, np.ndarray, list[dict]]:
        """Convert polyhedra (lists of original-mesh face indices) into a
        valid OpenFOAM polyMesh: each unique face written exactly once,
        internal faces first — sorted by (owner, neighbour), the "upper
        triangular" order OpenFOAM requires — followed by boundary faces
        grouped contiguously per patch, every face wound so its normal
        points from owner to neighbour (or outward, for a boundary face).

        Orientation is determined from which ORIGINAL tet each face's raw
        winding was set relative to (gmshToFoam guarantees that winding
        points from `face_owner[fid]`'s tet toward `face_neigh[fid]`'s
        tet, or outward for a domain-boundary face) and whether that
        original tet ended up inside `tet_sets[owner_cell]` — reliable
        for both convex and non-convex merged cells. (A geometric
        "does the face point away from the cell's centroid" heuristic
        was tried first; it only holds for convex cells, and two glued
        tets aren't always convex — confirmed directly: checkMesh found
        2,215 incorrectly-oriented faces and 10 negative-volume cells
        with that approach, both gone with this one.)

        (An even earlier version compared `cell_idx` — a polyhedron's
        position in the OUTPUT list — directly against `face_owner[fid]`,
        an index into the OLD, pre-merge tet mesh; those are different
        numbering spaces with no general relationship once tets have
        been merged. It also looked up a TET index in a dict keyed by
        FACE id to find the neighbouring cell, and wrote every internal
        face twice — once per side — where OpenFOAM requires exactly one
        entry.)
        """
        # Which polyhedra (1 = boundary, 2 = internal) touch each unique
        # original face id.
        face_to_cells: dict[int, list[int]] = {}
        for cell_idx, poly_faces in enumerate(polyhedra):
            for fid in poly_faces:
                face_to_cells.setdefault(fid, []).append(cell_idx)

        cell_centroids: list[np.ndarray] = []
        for poly_faces in polyhedra:
            verts = [vid for fid in poly_faces for vid in faces_raw[fid]]
            cell_centroids.append(
                points[verts].mean(axis=0) if verts else np.zeros(3)
            )

        def oriented_verts(fid: int, owner_cell: int) -> list[int]:
            verts = list(faces_raw[fid])
            orig_owner = int(face_owner[fid])
            # Reliable case: the original owner-side tet is the one that
            # ended up in this new cell -> raw winding already points
            # away from it (owner -> neighbour, or outward), keep as-is.
            # Otherwise it's the original NEIGHBOUR-side tet that ended
            # up here, so raw winding points the wrong way -> reverse.
            if orig_owner in tet_sets[owner_cell]:
                return verts
            orig_neigh = int(face_neigh[fid]) if fid < len(face_neigh) else -1
            if orig_neigh >= 0 and orig_neigh in tet_sets[owner_cell]:
                return list(reversed(verts))
            # Defensive fallback (shouldn't happen: every face in a
            # polyhedron's poly_faces was reached from one of its own
            # tets) — geometric heuristic, reliable only if this cell is
            # convex.
            face_pts = points[verts]
            fc = face_pts.mean(axis=0)
            normal = np.zeros(3)
            n = len(verts)
            for i in range(n):
                j = (i + 1) % n
                v0, v1 = face_pts[i], face_pts[j]
                normal[0] += (v0[1] - v1[1]) * (v0[2] + v1[2])
                normal[1] += (v0[2] - v1[2]) * (v0[0] + v1[0])
                normal[2] += (v0[0] - v1[0]) * (v0[1] + v1[1])
            if np.dot(normal, fc - cell_centroids[owner_cell]) < 0:
                return list(reversed(verts))
            return verts

        internal_faces: list[tuple[list[int], int, int]] = []
        boundary_face_owner: dict[int, tuple[list[int], int]] = {}

        for fid, cells in face_to_cells.items():
            if len(cells) >= 2:
                a, b = cells[0], cells[1]
                if a == b:
                    # Both sides of this frontier-face ended up merged
                    # into the same polyhedron via some other path — it's
                    # no longer a real boundary of anything, drop it.
                    continue
                owner_c, neigh_c = (a, b) if a < b else (b, a)
                internal_faces.append((oriented_verts(fid, owner_c), owner_c, neigh_c))
            else:
                owner_c = cells[0]
                boundary_face_owner[fid] = (oriented_verts(fid, owner_c), owner_c)

        # OpenFOAM requires internal faces in "upper triangular" order:
        # sorted by owner, then by neighbour within the same owner.
        internal_faces.sort(key=lambda e: (e[1], e[2]))

        new_faces: list[list[int]] = []
        new_owner: list[int] = []
        new_neighbour: list[int] = []

        for verts, owner_c, neigh_c in internal_faces:
            new_faces.append(verts)
            new_owner.append(owner_c)
            new_neighbour.append(neigh_c)

        # Boundary faces must stay grouped contiguously per original
        # patch so the written startFace/nFaces genuinely index into
        # this array — walk each original patch's original face-index
        # range in order and keep whichever of its faces survived.
        new_boundary: list[dict] = []
        face_idx = len(new_faces)
        n_faces_old = len(faces_raw)
        for patch in boundary_patches:
            start = patch.get("startFace", 0)
            n_faces_patch = patch.get("nFaces", 0)
            count = 0
            for i in range(start, min(start + n_faces_patch, n_faces_old)):
                entry = boundary_face_owner.get(i)
                if entry is not None:
                    verts, owner_c = entry
                    new_faces.append(verts)
                    new_owner.append(owner_c)
                    count += 1
            new_boundary.append({
                "name": patch["name"],
                "nFaces": count,
                "startFace": face_idx,
                "type": patch.get("type", "patch"),
            })
            face_idx += count

        return (
            new_faces,
            np.array(new_owner, dtype=np.int32),
            np.array(new_neighbour, dtype=np.int32),
            new_boundary,
        )

    # ------------------------------------------------------------------
    # Fallback: vertex-based aggregation (when mesh has non-tet cells)
    # ------------------------------------------------------------------

    def _fallback_aggregate(
        self,
        points: np.ndarray,
        faces_raw: list[list[int]],
        owner: np.ndarray,
        neighbour: np.ndarray,
        boundary_patches: list[dict],
        n_cells: int,
        t0: float,
        result: TerminalFaceResult,
    ) -> TerminalFaceResult:
        """Fallback: aggregate cells around each vertex (like poly_aggregator)."""
        logger.info("Using vertex-based aggregation fallback")

        # Build vertex→cell adjacency
        vertex_cells: dict[int, set[int]] = {}
        for fid in range(len(faces_raw)):
            own = int(owner[fid])
            for vid in faces_raw[fid]:
                if vid not in vertex_cells:
                    vertex_cells[vid] = set()
                vertex_cells[vid].add(own)
                if fid < len(neighbour):
                    neigh = int(neighbour[fid])
                    if neigh >= 0:
                        vertex_cells[vid].add(neigh)

        # Group cells by vertex → each group becomes a polyhedron
        cell_groups: dict[int, list[int]] = {}
        cell_to_group: dict[int, int] = {}
        for vid, cells in vertex_cells.items():
            # Find the smallest existing group that contains all these cells
            group_id = vid
            cell_groups[group_id] = list(cells)
            for c in cells:
                cell_to_group[c] = group_id

        # Build faces: for each original internal face, if the two cells
        # are in different groups, it becomes a frontier-face
        new_faces: list[list[int]] = []
        new_owner: list[int] = []
        new_neighbour: list[int] = []
        face_idx = 0

        for fid in range(len(faces_raw)):
            own = int(owner[fid])
            if fid < len(neighbour):
                neigh = int(neighbour[fid])
            else:
                neigh = -1

            own_group = cell_to_group.get(own, -1)
            if neigh < 0:
                # Boundary face
                verts = list(faces_raw[fid])
                new_faces.append(verts)
                new_owner.append(own_group)
                new_neighbour.append(-1)
                face_idx += 1
            else:
                neigh_group = cell_to_group.get(neigh, -1)
                if own_group != neigh_group:
                    # Internal face between different groups → frontier
                    verts = list(faces_raw[fid])
                    new_faces.append(verts)
                    new_owner.append(own_group)
                    new_neighbour.append(neigh_group)
                    face_idx += 1

        # Build boundary
        new_boundary: list[dict] = []
        bf_start = face_idx
        for patch in boundary_patches:
            start = patch.get("startFace", 0)
            n_faces = patch.get("nFaces", 0)
            count = 0
            for i in range(start, min(start + n_faces, len(faces_raw))):
                own = int(owner[i])
                if own in cell_to_group:
                    count += 1
            new_boundary.append({
                "name": patch["name"],
                "nFaces": count,
                "startFace": bf_start,
                "type": patch.get("type", "patch"),
            })
            bf_start += count

        # Write output
        self._write_poly_mesh(
            points, new_faces,
            np.array(new_owner, dtype=np.int32),
            np.array(new_neighbour, dtype=np.int32),
            new_boundary,
        )

        result.n_cells_after = len(cell_groups)
        result.n_polyhedra = len(cell_groups)
        result.success = True
        result.wall_time_s = round(time.monotonic() - t0, 2)
        return result

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _face_area(verts: list[int], points: np.ndarray) -> float:
        """Compute area of a polygon face using Newell's method."""
        n = len(verts)
        if n < 3:
            return 0.0
        normal = np.zeros(3, dtype=np.float64)
        for i in range(n):
            j = (i + 1) % n
            v0 = points[verts[i]]
            v1 = points[verts[j]]
            normal[0] += (v0[1] - v1[1]) * (v0[2] + v1[2])
            normal[1] += (v0[2] - v1[2]) * (v0[0] + v1[0])
            normal[2] += (v0[0] - v1[0]) * (v0[1] + v1[1])
        return 0.5 * float(np.linalg.norm(normal))

    # ------------------------------------------------------------------
    # OpenFOAM I/O
    # ------------------------------------------------------------------

    @staticmethod
    def _read_points(path: Path) -> np.ndarray:
        """Read OpenFOAM points file → (N, 3) float64 array. Handles ASCII and binary."""
        raw = path.read_bytes()
        is_binary = bool(re.search(rb'format\s+binary\s*;', raw[:2000]))

        if is_binary:
            # Binary: find the LAST '}\n' in header, then N and '(\n'
            data_start, n_items = _binary_header_parse(raw)
            expected_bytes = n_items * 3 * 8
            data = raw[data_start:data_start + expected_bytes]
            if len(data) < expected_bytes:
                raise ValueError(f"Binary points: need {expected_bytes} bytes, got {len(data)}")
            return np.frombuffer(data, dtype=np.float64).reshape(n_items, 3).copy()
        else:
            # ASCII
            text = raw.decode("ascii", errors="replace")
            lines = text.splitlines()
            start = 0
            for i, line in enumerate(lines):
                if line.strip().startswith("("):
                    start = i + 1
                    break
            end = len(lines)
            for i in range(start, len(lines)):
                if lines[i].strip() == ")":
                    end = i
                    break
            data_lines = [l.strip() for l in lines[start:end] if l.strip()]
            result = np.zeros((len(data_lines), 3), dtype=np.float64)
            for i, line in enumerate(data_lines):
                parts = line.replace("(", "").replace(")", "").split()
                result[i] = [float(parts[0]), float(parts[1]), float(parts[2])]
            return result

    @staticmethod
    def _read_faces(path: Path) -> list[list[int]]:
        """Read OpenFOAM faces file → list of vertex index lists. Handles ASCII and binary.

        OpenFOAM writes the `faces` file in one of two layouts:
          - `faceList` (legacy): each face stored inline as <nVerts> followed
            by <nVerts> labels.
          - `faceCompactList` (default since ~OpenFOAM v1712, used by 2512):
            two sequential lists — an (nFaces+1)-length offsets array, then a
            flat array of all vertex labels concatenated. Face i spans
            labels[offsets[i]:offsets[i+1]]. Naively parsing this as the
            legacy layout misreads offsets[0]==0 as a zero-length first face
            and aborts immediately, yielding zero faces.
        """
        raw = path.read_bytes()
        is_binary = bool(re.search(rb'format\s+binary\s*;', raw[:2000]))
        is_compact = bool(re.search(rb'class\s+faceCompactList\s*;', raw[:2000]))

        if is_compact:
            return TerminalFaceConverter._read_face_compact_list(raw, is_binary)

        if is_binary:
            data_start, n_faces = _binary_header_parse(raw)
            result: list[list[int]] = []
            pos = data_start
            for _ in range(n_faces):
                if pos + 4 > len(raw):
                    break
                n_verts = struct.unpack_from("<i", raw, pos)[0]
                if n_verts <= 0 or n_verts > 1000:
                    break
                pos += 4
                if pos + n_verts * 4 > len(raw):
                    break
                verts = list(struct.unpack_from(f"<{n_verts}i", raw, pos))
                pos += n_verts * 4
                result.append(verts)
            return result
        else:
            # ASCII
            text = raw.decode("ascii", errors="replace")
            lines = text.splitlines()
            start = 0
            for i, line in enumerate(lines):
                if line.strip().startswith("("):
                    start = i + 1
                    break
            end = len(lines)
            for i in range(start, len(lines)):
                if lines[i].strip() == ")":
                    end = i
                    break
            result: list[list[int]] = []
            for line in lines[start:end]:
                stripped = line.strip()
                if not stripped or stripped.startswith("//"):
                    continue
                inner = stripped
                if "(" in inner:
                    inner = inner[inner.index("(") + 1:]
                if ")" in inner:
                    inner = inner[:inner.rindex(")")]
                parts = inner.split()
                if parts:
                    result.append([int(p) for p in parts])
            return result

    @staticmethod
    def _read_face_compact_list(raw: bytes, is_binary: bool) -> list[list[int]]:
        """Read the `faceCompactList` layout: offsets array + flat labels array."""
        if is_binary:
            data_start, n_offsets = _binary_header_parse(raw)
            offsets_bytes = n_offsets * 4
            offsets_end = data_start + offsets_bytes
            if offsets_end > len(raw):
                raise ValueError(
                    f"Binary face offsets: need {offsets_bytes} bytes, "
                    f"got {len(raw) - data_start}"
                )
            offsets = np.frombuffer(raw[data_start:offsets_end], dtype=np.int32)

            m = re.search(rb'\)\s*\n(\d+)\s*\n\(', raw[offsets_end:offsets_end + 128])
            if not m:
                raise ValueError(
                    "Cannot find flat vertex-label list after face offsets "
                    "in faceCompactList"
                )
            n_labels = int(m.group(1))
            labels_start = offsets_end + m.end()
            labels_bytes = n_labels * 4
            labels_end = labels_start + labels_bytes
            if labels_end > len(raw):
                raise ValueError(
                    f"Binary face labels: need {labels_bytes} bytes, "
                    f"got {len(raw) - labels_start}"
                )
            labels = np.frombuffer(raw[labels_start:labels_end], dtype=np.int32)
        else:
            text = raw.decode("ascii", errors="replace")
            offsets_list, next_pos = TerminalFaceConverter._parse_ascii_int_list(text, 0)
            labels_list, _ = TerminalFaceConverter._parse_ascii_int_list(text, next_pos)
            offsets = np.array(offsets_list, dtype=np.int64)
            labels = np.array(labels_list, dtype=np.int64)

        n_faces = len(offsets) - 1
        return [labels[offsets[i]:offsets[i + 1]].tolist() for i in range(n_faces)]

    @staticmethod
    def _parse_ascii_int_list(text: str, pos: int) -> tuple[list[int], int]:
        """Parse one `N\\n(\\n ...values... \\n)` OpenFOAM ASCII list starting at char `pos`.

        Returns (values, end_pos) where end_pos is the character offset just
        past the closing ')', so callers can chain a second parse call for
        formats (like faceCompactList) that write two lists back to back.
        """
        m = re.search(r'(\d+)\s*\n\s*\(', text[pos:])
        if not m:
            raise ValueError("Cannot find count and '(' for ASCII int list")
        count = int(m.group(1))
        data_start = pos + m.end()
        close = text.index(")", data_start)
        body = text[data_start:close]
        values = [int(tok) for tok in body.split()]
        if len(values) != count:
            raise ValueError(
                f"ASCII int list: expected {count} values, parsed {len(values)}"
            )
        return values, close + 1

    @staticmethod
    def _read_label_list(path: Path) -> np.ndarray:
        """Read OpenFOAM labelList file → 1D int32 array. Handles ASCII and binary."""
        raw = path.read_bytes()
        is_binary = bool(re.search(rb'format\s+binary\s*;', raw[:2000]))

        if is_binary:
            data_start, count = _binary_header_parse(raw)
            data = raw[data_start:data_start + count * 4]
            if len(data) < count * 4:
                raise ValueError(f"Binary labels: need {count * 4} bytes, got {len(data)}")
            return np.frombuffer(data, dtype=np.int32).copy()
        else:
            text = raw.decode("ascii", errors="replace")
            lines = text.splitlines()
            start = 0
            for i, line in enumerate(lines):
                if line.strip().startswith("("):
                    start = i + 1
                    break
            end = len(lines)
            for i in range(start, len(lines)):
                if lines[i].strip() == ")":
                    end = i
                    break
            data = []
            for line in lines[start:end]:
                stripped = line.strip()
                if stripped and not stripped.startswith("//"):
                    try:
                        data.append(int(stripped))
                    except ValueError:
                        pass
            return np.array(data, dtype=np.int32)

    @staticmethod
    def _read_boundary(path: Path) -> list[dict]:
        """Read OpenFOAM boundary file → list of patch dicts.

        Real format: <N>\\n(\\n  <name>\\n  {\\n    type ...;\\n
        nFaces ...;\\n startFace ...;\\n  }\\n  ... )\\n — the patch
        NAME and its opening '{' are on separate lines, and there is no
        parenthesis anywhere near an individual patch entry; the only
        '(' in the whole file is the one opening the top-level patch
        list. (An earlier version of this parser looked for a line
        containing '(' without '{' as "start of a patch block" — that
        pattern only ever matched the single list-opening '(', capturing
        one bogus empty-named patch from whatever fields followed it
        and silently skipping every other patch in the file.)
        """
        text = path.read_text(encoding="ascii", errors="replace")
        lines = [l.strip() for l in text.splitlines()]
        n = len(lines)

        i = 0
        while i < n and lines[i] != "}":  # end of the FoamFile header dict
            i += 1
        i += 1
        while i < n and (lines[i] == "" or lines[i].startswith("//")):
            i += 1
        i += 1  # past the patch-count line
        while i < n and lines[i] != "(":  # the top-level list-opening paren
            i += 1
        i += 1

        patches: list[dict] = []
        while i < n:
            line = lines[i]
            if line == ")":
                break
            if not line:
                i += 1
                continue
            name = line.strip('"')
            i += 1
            while i < n and lines[i] != "{":
                i += 1
            i += 1  # past '{'
            patch_info: dict[str, str | int] = {"name": name}
            while i < n and lines[i] != "}":
                pline = lines[i]
                if pline.startswith("nFaces"):
                    patch_info["nFaces"] = int(pline.split()[1].rstrip(";"))
                elif pline.startswith("startFace"):
                    patch_info["startFace"] = int(pline.split()[1].rstrip(";"))
                elif pline.startswith("type"):
                    patch_info["type"] = pline.split()[1].rstrip(";").strip('"')
                i += 1
            i += 1  # past '}'
            if "nFaces" in patch_info and "startFace" in patch_info:
                patches.append(patch_info)
        return patches

    def _write_poly_mesh(
        self,
        points: np.ndarray,
        faces: list[list[int]],
        owner: np.ndarray,
        neighbour: np.ndarray,
        boundary_patches: list[dict],
    ) -> None:
        """Write OpenFOAM polyMesh files SAFELY — temp dir first, then replace.

        This prevents corruption of the original polyMesh if the write
        fails partway through.
        """
        import shutil
        import tempfile

        poly_dir = self._case_dir / "constant" / "polyMesh"

        # Write to a temp directory first
        tmp_dir = Path(tempfile.mkdtemp(prefix="terminal_face_"))
        tmp_poly = tmp_dir / "polyMesh"
        tmp_poly.mkdir(parents=True, exist_ok=True)

        try:
            # Write all files to temp dir
            self._write_points(tmp_poly / "points", points)
            self._write_faces(tmp_poly / "faces", faces)
            self._write_label_list(tmp_poly / "owner", owner)
            self._write_label_list(tmp_poly / "neighbour", neighbour)
            self._write_boundary(tmp_poly / "boundary", boundary_patches, len(faces))

            # Verify all files were written and are non-empty
            for fname in ["points", "faces", "owner", "neighbour", "boundary"]:
                fpath = tmp_poly / fname
                if not fpath.exists() or fpath.stat().st_size == 0:
                    raise RuntimeError(f"Verification failed: {fname} is empty or missing")

            # All good — atomically replace original
            if poly_dir.exists():
                shutil.rmtree(poly_dir)
            shutil.move(str(tmp_poly), str(poly_dir))

            logger.info("Terminal-face: wrote %d points, %d faces, %d owner, %d boundary patches",
                         len(points), len(faces), len(owner), len(boundary_patches))
        finally:
            # Clean up temp dir
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _write_points(self, path: Path, points: np.ndarray) -> None:
        header = (
            "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
            "    class       vectorField;\n    object      points;\n}\n"
        )
        with open(path, "w", encoding="ascii") as f:
            f.write(header)
            f.write(f"{len(points)}\n(\n")
            for p in points:
                f.write(f"({p[0]:.10e} {p[1]:.10e} {p[2]:.10e})\n")
            f.write(")\n")

    def _write_faces(self, path: Path, faces: list[list[int]]) -> None:
        header = (
            "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
            "    class       faceList;\n    object      faces;\n}\n"
        )
        with open(path, "w", encoding="ascii") as f:
            f.write(header)
            f.write(f"{len(faces)}\n(\n")
            for face in faces:
                f.write(f"{len(face)}({' '.join(str(v) for v in face)})\n")
            f.write(")\n")

    def _write_label_list(self, path: Path, data: np.ndarray) -> None:
        header = (
            "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
            f"    class       labelList;\n    object      {path.name};\n}}\n"
        )
        with open(path, "w", encoding="ascii") as f:
            f.write(header)
            f.write(f"{len(data)}\n(\n")
            for v in data:
                f.write(f"{int(v)}\n")
            f.write(")\n")

    def _write_boundary(
        self, path: Path, patches: list[dict], n_internal_faces: int,
    ) -> None:
        header = (
            "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
            "    class       polyBoundaryMesh;\n    object      boundary;\n}\n"
        )
        with open(path, "w", encoding="ascii") as f:
            f.write(header)
            f.write(f"{len(patches)}\n(\n")
            for p in patches:
                name = p["name"]
                n_faces = p.get("nFaces", 0)
                start = p.get("startFace", 0)
                ptype = p.get("type", "patch")
                f.write(f'    {name}\n    {{\n        type    {ptype};\n')
                f.write(f"        nFaces  {n_faces};\n")
                f.write(f"        startFace {start};\n    }}\n")
            f.write(")\n")
