"""Polyhedral aggregator — STAR-CCM+ style tetrahedra-to-polyhedra.

STAR-CCM+'s polyhedral mesher (Peric, 2004) generates a tetrahedral
mesh first, then aggregates tetrahedra around each mesh vertex into
polyhedral cells. This produces 3-5x FEWER cells than the original tet
mesh with better numerical properties (more neighbours per cell, lower
skewness, better non-orthogonality).

By contrast, OpenFOAM's ``polyDualMesh`` creates the dual of a HEX mesh,
which INCREASES cell count — the opposite of what STAR-CCM+ does.

This module implements the STAR-CCM+ approach:
  1. Generate tetrahedral mesh via GMSH (with boundary layers if enabled)
  2. Build vertex-to-tetrahedron adjacency (1-ring for each vertex)
  3. Aggregate tets around each vertex into one polyhedral cell
  4. Merge coplanar faces for quality
  5. Boundary patches are preserved identically

Usage::

    agg = PolyAggregator()
    agg.params.min_tets_per_cluster = 20
    result = agg.run(case_dir, geometry_path="geo.step")
    print(f"{result.cells_before} -> {result.cells_after} cells "
          f"({result.reduction_pct:.1f}% reduction)")
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)

__all__ = [
    "AggregationParams",
    "AggregationResult",
    "Face",
    "PolyAggregator",
    "are_coplanar",
    "face_centroid",
    "face_normal",
    "face_skewness",
    "merge_acceptable",
    "main",
    "merge_two_faces",
]


@dataclass
class AggregationParams:
    """Tunable parameters for polyhedral aggregation.

    Attributes:
        min_tets_per_cluster: Minimum tetrahedra to form a poly cell.
            1 = most aggressive (every vertex becomes a poly cell).
            30 = conservative (only dense regions become poly).
            Default 20 targets ~2x reduction (e.g. 10M tet -> 5M poly)
            instead of the ~5-6x an aggressive threshold gives: on the
            benchmark geometry, thresholds below ~20 plateau at max
            skewness ~10 / non-ortho ~91, while >=20 drops to ~2.8 / ~67
            (near the ~0.9 / ~66 of the source tet mesh) -- more
            aggressive clustering pulls in tets whose faces are far from
            coplanar, and OpenFOAM penalises exactly that non-planarity.
        merge_coplanar_faces: Merge faces sharing the same plane.
        coplanar_tolerance: Dot-product threshold for coplanarity.
        quasi_coplanar_tolerance: Cosine slack (0 = off, the default) that
            lets faces merge even when they are only *almost* coplanar.  Each
            such merge is accepted only if the merged polygon's skewness
            (warpage deviation / sqrt(area), see ``face_skewness``) stays
            below ``max_merge_skewness`` — the point of merging is fewer,
            bigger faces per cell; a warped merged face would trade that for
            exactly the non-planarity OpenFOAM penalises.
        max_merge_skewness: Skewness gate for quasi-coplanar merges.
        preserve_boundary_patches: Keep original patch names/types.
        bl_enabled: Generate boundary layers in the tet mesh.
        bl_n_layers: Number of prism layers at boundaries.
        bl_growth_rate: Layer-to-layer growth ratio.
    """
    min_tets_per_cluster: int = 20
    merge_coplanar_faces: bool = True
    coplanar_tolerance: float = 1e-6
    quasi_coplanar_tolerance: float = 0.0
    max_merge_skewness: float = 0.9
    preserve_boundary_patches: bool = True
    bl_enabled: bool = True
    bl_n_layers: int = 5
    bl_growth_rate: float = 1.2


@dataclass
class AggregationResult:
    """Result of polyhedral aggregation."""
    success: bool = False
    cells_before: int = 0
    cells_after: int = 0
    max_non_ortho_before: float = 0.0
    max_non_ortho_after: float = 0.0
    max_skewness_before: float = 0.0
    max_skewness_after: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def reduction_pct(self) -> float:
        if self.cells_before <= 0:
            return 0.0
        return (1.0 - self.cells_after / self.cells_before) * 100.0


# ---------------------------------------------------------------------------
# Mesh parsing helpers
# ---------------------------------------------------------------------------

def _strip_of_comments(text: str) -> str:
    """Remove OpenFOAM comments (/* */ and //)."""
    import re
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _extract_data_block(text: str) -> str:
    """Extract content between outermost parentheses."""
    import re
    text = _strip_of_comments(text)
    m = re.search(r"\(\s*(.*)\s*\)", text, flags=re.DOTALL)
    if not m:
        raise ValueError("Cannot find data block in OpenFOAM file")
    return m.group(1)


def _read_points(path: Path) -> np.ndarray:
    """Read OpenFOAM points file → (N,3) float64 array.

    Handles the format::
        FoamFile { ... }
        N
        (
        (x0 y0 z0)
        (x1 y1 z1)
        ...
        )

    Strips parentheses and surrounding whitespace from each point line
    before parsing the coordinate triplets.
    """
    import re

    from cfmesh_autogui.core.of_reader import read_of_text
    text = read_of_text(path)
    text = _strip_of_comments(text)
    # Find the data block: between outer-most ( and )
    m = re.search(r"\(\s*(.*)\s*\)", text, flags=re.DOTALL)
    if not m:
        raise ValueError(f"Cannot find data block in {path}")
    body = m.group(1)
    # Remove parentheses from each point: "(x y z)" -> "x y z"
    body = re.sub(r"[()]", " ", body)
    # Parse as flat float array (np.fromstring is deprecated)
    arr = np.array(body.split(), dtype=np.float64)
    if arr.size % 3 != 0:
        raise ValueError(
            f"Points data size {arr.size} not divisible by 3 in {path}"
        )
    return arr.reshape(-1, 3)


def _parse_face_list(text: str) -> list[list[int]]:
    """Parse OpenFOAM face list: 'n(v0 v1 ...)' entries."""
    import re
    text = _strip_of_comments(text)
    # Skip header and count line up to first '('
    idx = text.find("(")
    if idx < 0:
        return []
    body = text[idx + 1:]
    faces: list[list[int]] = []
    # Match patterns like 3(0 1 2) or 3 (0 1 2)
    for m in re.finditer(r"(\d+)\s*\(([^)]*)\)", body):
        verts = [int(x) for x in m.group(2).split()]
        faces.append(verts)
    return faces


def _read_owner_neighbour(path: Path) -> np.ndarray:
    """Read OpenFOAM owner/neighbour file → 1D int32 array.
    
    Handles both ASCII and binary OpenFOAM formats.
    """
    from cfmesh_autogui.core.of_reader import _is_binary_format, read_of_text
    if _is_binary_format(path):
        from cfmesh_autogui.core.of_reader import of_label_list
        arr = np.array(of_label_list(path), dtype=np.int32)
        if len(arr) == 0:
            # Fallback: try reading as ASCII anyway
            pass
        else:
            return arr
    text = read_of_text(path)
    data = _extract_data_block(text)
    result = np.array(data.split(), dtype=np.int32)
    if len(result) == 0:
        # Binary file parsed as ASCII produced garbage — try label list
        from cfmesh_autogui.core.of_reader import of_label_list
        result = np.array(of_label_list(path), dtype=np.int32)
    return result


def _read_boundary_patches(path: Path) -> list[dict]:
    """Read OpenFOAM boundary file → list of patch dicts."""
    from cfmesh_autogui.core.boundary_reader import parse_boundary
    if not path.exists():
        return []
    patches = parse_boundary(path)
    return [
        {
            "name": p.name,
            "nFaces": p.n_faces,
            "startFace": p.start_face,
            "type": getattr(p, "type", "patch"),
        }
        for p in patches
    ]


# ---------------------------------------------------------------------------
# Face representation
# ---------------------------------------------------------------------------

@dataclass
class Face:
    """A polygonal face with connectivity data."""
    vertices: list[int]
    normal: np.ndarray | None = None
    owner: int = -1
    neighbour: int = -1
    is_boundary: bool = False
    patch_name: str = ""
    patch_type: str = "patch"

    def __hash__(self) -> int:
        return hash(tuple(sorted(self.vertices)))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Face):
            return False
        return set(self.vertices) == set(other.vertices)

    def reversed(self) -> Face:
        """Return face with reversed vertex order."""
        return Face(
            vertices=list(reversed(self.vertices)),
            normal=self.normal,
            owner=self.neighbour, neighbour=self.owner,
            is_boundary=self.is_boundary,
            patch_name=self.patch_name, patch_type=self.patch_type,
        )


def face_normal(verts: list[np.ndarray]) -> np.ndarray:
    """Compute face normal via Newell's method."""
    n = np.zeros(3)
    nv = len(verts)
    for i in range(nv):
        j = (i + 1) % nv
        n[0] += (verts[i][1] - verts[j][1]) * (verts[i][2] + verts[j][2])
        n[1] += (verts[i][2] - verts[j][2]) * (verts[i][0] + verts[j][0])
        n[2] += (verts[i][0] - verts[j][0]) * (verts[i][1] + verts[j][1])
    norm = np.linalg.norm(n)
    if norm < 1e-15:
        return n
    return n / norm


def face_centroid(verts: list[np.ndarray]) -> np.ndarray:
    """Centroid of polygonal face = average of vertices."""
    return np.mean(verts, axis=0)


def are_coplanar(
    verts_a: list[np.ndarray], verts_b: list[np.ndarray],
    tol: float = 1e-6,
) -> bool:
    """Check if two faces lie on the same plane.

    Two faces are coplanar if their normals are parallel (dot >= 1-tol)
    and the centroid of one lies on the plane of the other.
    """
    na = face_normal(verts_a)
    nb = face_normal(verts_b)
    dot = abs(np.dot(na, nb))
    if dot < 1.0 - tol:
        return False
    # Check that centroid of B lies on plane of A
    ca = face_centroid(verts_a)
    cb = face_centroid(verts_b)
    d = np.dot(cb - ca, na)
    return abs(d) < tol


def merge_two_faces(
    va: list[np.ndarray], vb: list[np.ndarray],
) -> list[np.ndarray]:
    """Merge two coplanar faces sharing an edge into one polygon.

    Uses the shared edge as pivot: removes it from both rings and splices the
    two remaining open chains into a simple union polygon.  The B chain is
    walked in the direction that moves *away* from the shared edge (following
    the ring from the shared endpoint toward its non-shared neighbour), which
    works for both same-winding and opposite-winding rings — the naive
    "walk A then walk B" produces a self-intersecting bowtie or a duplicated
    endpoint otherwise.  Falls back to returning A if no shared edge exists.
    """
    va_tuples = [tuple(v) for v in va]
    vb_tuples = [tuple(v) for v in vb]

    def _edges(verts):
        n = len(verts)
        return [tuple(sorted((verts[i], verts[(i + 1) % n]))) for i in range(n)]

    edges_a = _edges(va_tuples)
    edges_b = _edges(vb_tuples)
    shared = None
    for i, ea in enumerate(edges_a):
        if ea in edges_b:
            shared = ea
            j = edges_b.index(ea)
            break
    if shared is None:
        return list(va)

    n_a, n_b = len(va_tuples), len(vb_tuples)
    s0, s1 = shared
    # A's ring enters the shared edge at A[i] and leaves at A[i+1]
    # (chain_a runs A[i+1] .. A[i], the polygon minus the shared edge).
    chain_a = [va_tuples[(i + 1 + k) % n_a] for k in range(n_a)]
    # Locate the shared endpoints in B's ring.
    ia = next(k for k in range(n_b) if vb_tuples[k] == s0)
    ib = next(k for k in range(n_b) if vb_tuples[k] == s1)
    # Walk B away from the shared edge.  Which of the two possible
    # directions yields the simple union depends on the rings' relative
    # winding (same-winding vs opposite-winding neighbours both occur), so
    # build both candidate rings and keep the one with the larger |area| —
    # a simple union has area(A)+area(B), a self-crossing bowtie cancels to
    # |area(A)-area(B)| ≈ 0.
    def _ring(step):
        chain_b = [vb_tuples[(ia + k * step) % n_b] for k in range(n_b)]
        ring = chain_a + chain_b[1:-1]
        if len(ring) > 1 and ring[-1] == ring[0]:
            ring.pop()
        return ring

    def _newell_mag(ring):
        # 3D Newell area-vector magnitude (2*area) — works for faces in any
        # orientation, unlike a 2D shoelace projection
        n = np.zeros(3)
        m = len(ring)
        for k in range(m):
            x0, y0, z0 = ring[k]
            x1, y1, z1 = ring[(k + 1) % m]
            n[0] += (y0 - y1) * (z0 + z1)
            n[1] += (z0 - z1) * (x0 + x1)
            n[2] += (x0 - x1) * (y0 + y1)
        return float(np.linalg.norm(n))

    r_plus = _ring(+1)
    r_minus = _ring(-1)
    a_p = _newell_mag(r_plus)
    a_m = _newell_mag(r_minus)

    def _unique_count(ring):
        return len({tuple(v) for v in ring})

    if abs(a_p - a_m) > 1e-9 * max(a_p, a_m, 1.0):
        result = r_plus if a_p > a_m else r_minus
    else:
        # equal area: prefer the ring without a duplicated vertex; if both
        # are degenerate the merge is not well-defined — fall back to A
        n_p, n_m = _unique_count(r_plus), _unique_count(r_minus)
        if n_p > n_m:
            result = r_plus
        elif n_m > n_p:
            result = r_minus
        else:
            return list(va)
    return [np.asarray(v, dtype=np.float64) for v in result]


def face_skewness(verts: list[np.ndarray]) -> float:
    """Warpage skewness of a polygon face.

    Max over the vertices of |(v − centroid)·unit_normal| / sqrt(area), the
    classic warpage measure: 0 for a planar face, growing with the maximum
    vertex deviation from the best-fit (Newell) plane relative to the face
    size.  Used to gate quasi-coplanar merges — merging two almost-coplanar
    faces creates one polygon whose skewness is this number; if it exceeds
    ``max_merge_skewness`` the merge is refused.
    """
    arr = np.asarray(verts, dtype=np.float64)
    n = len(arr)
    if n < 3:
        return float("inf")
    nrm = np.zeros(3)
    for i in range(n):
        j = (i + 1) % n
        nrm[0] += (arr[i][1] - arr[j][1]) * (arr[i][2] + arr[j][2])
        nrm[1] += (arr[i][2] - arr[j][2]) * (arr[i][0] + arr[j][0])
        nrm[2] += (arr[i][0] - arr[j][0]) * (arr[i][1] + arr[j][1])
    mag = float(np.linalg.norm(nrm))
    if mag < 1e-300:
        return float("inf")
    c = arr.mean(axis=0)
    dev = float(np.abs((arr - c) @ nrm).max()) / mag
    area = 0.5 * mag
    return dev / max(np.sqrt(area), 1e-300)


def merge_acceptable(
    verts_a: list[np.ndarray],
    verts_b: list[np.ndarray],
    tol: float = 1e-6,
    quasi_tol: float = 0.0,
    max_skew: float = 0.9,
    na: np.ndarray | None = None,
    nb: np.ndarray | None = None,
) -> bool:
    """Whether faces A and B may merge (strictly or quasi-) coplanarly.

    Strictly coplanar pairs (the ``are_coplanar`` criterion) always merge —
    the result is planar, so the skewness gate is trivially satisfied.  When
    ``quasi_tol > 0``, pairs whose normals agree within ``1 - tol - quasi_tol``
    are additionally considered, and the merged polygon is accepted only if
    its warpage skewness stays at or below ``max_skew``.

    ``na``/``nb`` let a caller pass already-computed face normals (e.g. the
    cached ``Face.normal``) instead of recomputing them from ``verts_a``/
    ``verts_b`` on every pairwise comparison.
    """
    na = face_normal(verts_a) if na is None else na
    nb = face_normal(verts_b) if nb is None else nb
    dot = float(abs(np.dot(na, nb)))
    if dot < 1.0 - tol:
        if quasi_tol <= 0.0 or dot < 1.0 - tol - quasi_tol:
            return False
        merged = merge_two_faces(verts_a, verts_b)
        if len(merged) < 3:
            return False
        return face_skewness(merged) <= max_skew
    # normals agree — strict coplanarity (centroid-in-plane is implicit in
    # the merged polygon being planar within the same tolerance)
    ca = face_centroid(verts_a)
    cb = face_centroid(verts_b)
    return abs(float(np.dot(cb - ca, na))) < tol


# ---------------------------------------------------------------------------
# Polyhedral Aggregator
# ---------------------------------------------------------------------------

class PolyAggregator:
    """Aggregate tetrahedra into polyhedral cells — STAR-CCM+ approach.

    Algorithm:
      1. Generate tet mesh with GMSH (automatic, with BL if requested)
      2. Read the tet mesh into memory (points, faces, cells, boundary)
      3. For each vertex V, find all tets containing V (the 1-ring)
      4. For vertices with >= min_tets_per_cluster adjacent tets:
         a. Collect all faces from those tets
         b. Internal faces (shared by two tets both in the cluster) -> REMOVE
         c. Boundary faces (only one tet in the cluster) -> KEEP
         d. Merge coplanar faces into single poly faces
         e. Create one poly cell from the merged boundary faces
      5. Cells below the threshold remain as tetrahedra
      6. Write the mixed tet/poly mesh back to constant/polyMesh
      7. Run checkMesh and compare quality metrics
    """

    def __init__(self) -> None:
        self.params = AggregationParams()
        self._progress_callback: callable | None = None

    def set_progress_callback(self, callback: callable | None) -> None:
        """Set a progress callback ``fn(percent: float, message: str)``."""
        self._progress_callback = callback

    def _report(self, pct: float, msg: str) -> None:
        """Report progress if callback is set."""
        if self._progress_callback:
            try:
                self._progress_callback(pct, msg)
            except (TypeError, ValueError):
                pass

    def run(
        self, case_dir: Path | str,
        geometry_path: str = "",
    ) -> AggregationResult:
        """Run the full aggregation pipeline.

        Args:
            case_dir: OpenFOAM case directory.
            geometry_path: Path to geometry file (STEP/STL). If empty,
                looks for constant/triSurface/surface.stl.

        Returns:
            AggregationResult with before/after metrics.
        """
        case_dir = Path(case_dir).resolve()
        result = AggregationResult()
        self._cleanup()

        octo.log_event("poly_aggregator", "start", {
            "case_dir": str(case_dir),
            "min_tets": self.params.min_tets_per_cluster,
        })

        try:
            # Phase 1: generate tet mesh with GMSH
            self._report(5, "Generating tetrahedral mesh (GMSH)...")
            self._ensure_tet_mesh(case_dir, geometry_path)

            # Phase 2: read mesh directly (no WSL checkMesh needed)
            poly_dir = case_dir / "constant" / "polyMesh"
            self._report(20, "Reading mesh...")
            points = _read_points(poly_dir / "points")
            faces = _parse_face_list(
                (poly_dir / "faces").read_text(encoding="ascii", errors="replace")
            )
            owner = _read_owner_neighbour(poly_dir / "owner")
            neighbour = _read_owner_neighbour(poly_dir / "neighbour")
            boundary_patches = _read_boundary_patches(poly_dir / "boundary")

            # Count cells from owner array.  Some cells may only ever appear
            # as a NEIGHBOUR (never an owner) when the mesh writer assigns
            # non-contiguous cell indices, so the true cell count is the max
            # over BOTH arrays — using owner.max()+1 alone under-sizes the
            # adjacency and crashes with an IndexError on such meshes.
            n_cells = int(max(owner.max(), neighbour.max())) + 1
            result.cells_before = n_cells
            if n_cells == 0:
                raise RuntimeError("No cells found in tet mesh")

            # Build cell→faces adjacency
            cell_faces: list[list[int]] = [[] for _ in range(n_cells)]
            for fid, own in enumerate(owner):
                cell_faces[int(own)].append(fid)
            for fid, neigh in enumerate(neighbour):
                if neigh >= 0:
                    cell_faces[int(neigh)].append(fid)

            # Validate face indices are within range
            n_faces_file = len(faces)
            for cid, cfaces in enumerate(cell_faces):
                for fid in cfaces:
                    if fid >= n_faces_file:
                        raise RuntimeError(
                            f"Cell {cid} references face {fid} but file "
                            f"only has {n_faces_file} faces"
                        )

            # Validate tetrahedral — polyhedral meshes cannot be aggregated
            max_faces = max(len(cf) for cf in cell_faces) if cell_faces else 0
            if max_faces > 4:
                raise RuntimeError(
                    f"Mesh has cells with {max_faces} faces (not tetrahedral). "
                    "Polyhedral aggregation requires a tetrahedral mesh. "
                    "The mesh is already polyhedral — skipping aggregation."
                )

            # Quality before (optional, requires WSL)
            quality_before = self._get_quality(case_dir)
            result.max_non_ortho_before = quality_before.get("max_non_ortho", 0.0)
            result.max_skewness_before = quality_before.get("max_skewness", 0.0)

            # Phase 3: build vertex→tet adjacency
            self._report(40, "Building vertex adjacency...")
            vt_adj = self._build_vertex_adjacency(cell_faces, faces)

            # Phase 4: aggregate
            self._report(50, "Aggregating tetrahedra into polyhedral cells...")
            poly_cells, new_faces, new_owner, new_neighbour = (
                self._aggregate(
                    points, faces, owner, neighbour,
                    cell_faces, vt_adj, boundary_patches,
                )
            )
            result.cells_after = len(poly_cells)

            # Phase 5: backup + write
            self._report(70, "Backing up original mesh...")
            backup_dir = case_dir / "constant" / "polyMesh_tet"
            self._backup_mesh(poly_dir, backup_dir)
            self._report(80, "Writing polyhedral mesh...")
            self._write_poly_mesh(
                poly_dir, points, new_faces, new_owner, new_neighbour,
                boundary_patches,
            )

            # Phase 6: quality check (optional, may fail without WSL)
            quality_after = self._get_quality(case_dir)
            result.max_non_ortho_after = quality_after.get("max_non_ortho", 0.0)
            result.max_skewness_after = quality_after.get("max_skewness", 0.0)
            result.success = True

            logger.info(
                "PolyAggregator: %d -> %d cells (%.1f%% reduction) "
                "nonOrtho=%.1f->%.1f skew=%.3f->%.3f",
                result.cells_before, result.cells_after, result.reduction_pct,
                result.max_non_ortho_before, result.max_non_ortho_after,
                result.max_skewness_before, result.max_skewness_after,
            )

        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result.errors.append(str(exc))
            logger.exception("PolyAggregator failed")

        octo.log_event("poly_aggregator", "end", {
            "success": result.success,
            "cells_before": result.cells_before,
            "cells_after": result.cells_after,
            "reduction_pct": result.reduction_pct,
        })
        return result

    def _ensure_tet_mesh(
        self, case_dir: Path, geometry_path: str,
    ) -> None:
        """Generate tet mesh with GMSH + optional boundary layers.

        Strategy (hybrid, come richiesto):
          1. Tenta GMSH con BL (prism layers via GMSH Field "BoundaryLayer")
          2. Se BL fallisce → genera tet mesh senza BL
          3. Se GMSH stesso fallisce → usa tetrahedral fallback (cfMesh o mesh_dict)
        """
        poly_dir = case_dir / "constant" / "polyMesh"
        if (poly_dir / "points").exists():
            try:
                owner = _read_owner_neighbour(poly_dir / "owner")
                n_cells = int(owner.max()) + 1 if len(owner) > 0 else 0
                if n_cells > 0:
                    logger.info("Mesh already exists in %s — skipping GMSH", poly_dir)
                    return
            except (ValueError, OSError, RuntimeError) as exc:
                logger.warning(
                    "Could not read existing mesh owner: %s. "
                    "Will generate tet mesh via GMSH.",
                    exc,
                )

        # Resolve geometry path
        if not geometry_path:
            stl = case_dir / "constant" / "triSurface" / "surface.stl"
            stl_fms = case_dir / "constant" / "triSurface" / "surface.fms"
            if stl_fms.exists():
                geometry_path = str(stl_fms)
            elif stl.exists():
                geometry_path = str(stl)
            else:
                raise FileNotFoundError(
                    f"No geometry found. Provide geometry_path or place "
                    f"surface.stl in {case_dir / 'constant' / 'triSurface'}"
                )

        from cfmesh_autogui.core.gmsh_wrapper import generate_volume_mesh
        from cfmesh_autogui.core.mesh_converter import msh_to_of_polymesh

        msh_path = case_dir / "mesh.msh"
        n_layers = self.params.bl_n_layers if self.params.bl_enabled else 0

        # Tentativo 1: GMSH con BL
        bl_ok = False
        if n_layers > 0:
            try:
                logger.info("GMSH tet mesh with %d BL layers...", n_layers)
                msh_path, _ = generate_volume_mesh(
                    geometry_path, msh_path, detail="medium",
                    n_layers=n_layers,
                )
                msh_to_of_polymesh(msh_path, case_dir)
                bl_ok = True
                logger.info("GMSH tet mesh with BL: OK")
            except (OSError, ValueError, RuntimeError) as bl_exc:
                logger.warning(
                    "GMSH BL failed (%s). Falling back to tet mesh without BL.",
                    bl_exc,
                )

        # Tentativo 2: GMSH senza BL (se il primo non è riuscito)
        if not bl_ok:
            logger.info("GMSH tet mesh without BL...")
            try:
                msh_path, _ = generate_volume_mesh(
                    geometry_path, msh_path, detail="medium",
                    n_layers=0,
                )
                msh_to_of_polymesh(msh_path, case_dir)
                logger.info("GMSH tet mesh without BL: OK")
            except (OSError, ValueError, RuntimeError) as gmsh_exc:
                logger.warning(
                    "GMSH failed (%s). Falling back to mesh_dict tetrahedral.",
                    gmsh_exc,
                )
                # Tentativo 3: cfMesh tetrahedral fallback
                self._fallback_tet_mesh(case_dir, geometry_path)

    def _fallback_tet_mesh(self, case_dir: Path, geometry_path: str) -> None:
        """Fallback: generate tet mesh via cfMesh cartesianHex + split.

        NOTE: cfMesh produces a hex-dominant mesh. The aggregator works best
        on tetrahedral meshes but can also process hex-dominant ones (with
        less dramatic cell reduction, typically 20-40% instead of 60-80%).
        """
        logger.info("Fallback: cfMesh cartesianMesh (hex-dominant)...")
        import subprocess

        from cfmesh_autogui.config import OFConfig
        from cfmesh_autogui.core.meshdict_gen import write_meshdict

        cfg = OFConfig()
        write_meshdict(
            case_dir,
            max_cell_size=0.1, min_cell_size=0.01,
            surface_file=geometry_path
            if "surface.stl" in geometry_path
            else "constant/triSurface/surface.stl",
        )
        (case_dir / "system" / "controlDict").write_text(
            "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
            "application cartesianMesh;\n"
            "startFrom startTime; startTime 0;\n"
            "stopAt endTime; endTime 1000;\n"
            "deltaT 1;\n"
            "writeControl timeStep; writeInterval 1;\n"
            "writeFormat binary; writePrecision 6;\n"
            "runTimeModifiable true;\n",
            encoding="ascii",
        )
        cmd = cfg.build_command(case_dir)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        if r.returncode != 0:
            raise RuntimeError(
                f"cfMesh fallback failed (exit {r.returncode}): "
                f"{r.stderr[-300:] if r.stderr else ''}"
            )
        logger.info("cfMesh fallback mesh: OK (hex-dominant — aggregation reduction will be lower)")

    def _build_vertex_adjacency(
        self,
        cell_faces: list[list[int]],
        faces: list[list[int]],
    ) -> dict[int, list[int]]:
        """Build vertex->cell adjacency.

        For each vertex, records which cells (tetrahedra) contain it.
        Returns dict: vertex_id -> list of cell_ids.
        """
        n_faces = len(faces)
        cell_arrs: list[np.ndarray] = []
        id_arrs: list[np.ndarray] = []
        for cell_id, face_ids in enumerate(cell_faces):
            verts_this_cell: set[int] = set()
            for fid in face_ids:
                if fid < n_faces:
                    verts_this_cell.update(faces[fid])
            if not verts_this_cell:
                continue
            arr = np.fromiter(verts_this_cell, dtype=np.int64, count=len(verts_this_cell))
            cell_arrs.append(arr)
            id_arrs.append(np.full(arr.shape[0], cell_id, dtype=np.int64))

        if not cell_arrs:
            return {}

        verts_cat = np.concatenate(cell_arrs)
        cells_cat = np.concatenate(id_arrs)
        # Stable sort so, for a given vertex, the grouped cell ids keep the
        # ascending cell-id order the original incremental-append loop
        # produced (ties broken by original position).
        order = np.argsort(verts_cat, kind="stable")
        verts_sorted = verts_cat[order]
        cells_sorted = cells_cat[order]
        uniq_verts, start_idx, counts = np.unique(
            verts_sorted, return_index=True, return_counts=True
        )

        vt_adj: dict[int, list[int]] = {}
        for v, start, count in zip(
            uniq_verts.tolist(), start_idx.tolist(), counts.tolist()
        ):
            vt_adj[v] = cells_sorted[start:start + count].tolist()
        return vt_adj

    def _aggregate(
        self,
        points: np.ndarray,
        faces: list[list[int]],
        owner: np.ndarray,
        neighbour: np.ndarray,
        cell_faces: list[list[int]],
        vt_adj: dict[int, list[int]],
        boundary_patches: list[dict],
    ) -> tuple[
        list[dict[str, Any]],
        list[list[int]],
        np.ndarray,
        np.ndarray,
    ]:
        """Core aggregation algorithm.

        Returns:
            (poly_cells, new_faces, new_owner, new_neighbour)
        """
        threshold = self.params.min_tets_per_cluster
        n_cells = len(cell_faces)

        # Build boundary face -> patch mapping
        bface_to_patch: dict[int, tuple[str, str]] = {}
        for patch in boundary_patches:
            start = patch["startFace"]
            for i in range(patch["nFaces"]):
                bface_to_patch[start + i] = (patch["name"], patch["type"])

        # Process each vertex with enough tets
        used_cells: set[int] = set()
        cluster_map: dict[int, int] = {}  # cell_id -> new poly cell_id
        new_poly_cells: list[dict] = []

        # Sort vertices by number of adjacent tets (descending)
        sorted_verts = sorted(
            [(v, tids) for v, tids in vt_adj.items() if len(tids) >= threshold],
            key=lambda x: -len(x[1]),
        )

        for _vert_id, tet_ids in sorted_verts:
            # Skip if any of these tets already belong to another cluster
            if any(tid in used_cells for tid in tet_ids):
                continue

            # Mark as used
            used_cells.update(tet_ids)
            poly_id = len(new_poly_cells)
            for tid in tet_ids:
                cluster_map[tid] = poly_id

            # Collect all unique faces from these tets
            cluster_faces: list[Face] = []
            for tid in tet_ids:
                for fid in cell_faces[tid]:
                    fverts = faces[fid]
                    is_bface = fid in bface_to_patch
                    pname, ptype = bface_to_patch.get(fid, ("", ""))
                    fown = int(owner[fid])
                    fnei = int(neighbour[fid]) if fid < len(neighbour) and neighbour[fid] >= 0 else -1

                    # Determine if this face is internal to the cluster
                    other_cell = fnei if fown == tid else fown
                    is_internal = other_cell >= 0 and other_cell in used_cells and other_cell in tet_ids

                    if not is_internal:
                        # ``faces[fid]`` is wound once, globally, as "normal
                        # points from the ORIGINAL tet owner to the ORIGINAL
                        # tet neighbour". If this cluster is built from the
                        # original neighbour side (fown != tid), that
                        # winding points INTO this cluster, not out of it --
                        # flip it so every face this cluster emits is
                        # outward-facing from ITS OWN cell, regardless of
                        # which side of the original tet pair it came from.
                        if fown != tid:
                            fverts = list(reversed(fverts))
                        v_pos = [points[v] for v in fverts]
                        nrm = face_normal(v_pos)
                        cluster_faces.append(Face(
                            vertices=fverts,
                            normal=nrm,
                            owner=poly_id,
                            neighbour=-1,
                            is_boundary=is_bface,
                            patch_name=pname,
                            patch_type=ptype,
                        ))

            # Merge coplanar faces -- BOUNDARY faces only. An internal face
            # (is_boundary=False, shared with another final cell) is built
            # independently on each side from the original tet mesh's face
            # ids, so both sides start out with identical vertex sets and
            # the frozenset-based internal/boundary dedup below can match
            # them. Merging internal faces changes their vertex set on one
            # side only (the other cell doesn't know about the merge),
            # breaking that match: the "merged" internal face is then
            # mistaken for two separate boundary faces, corrupting
            # owner/neighbour topology (observed as skewness in the
            # hundreds and non-orthogonality pinned near the 180 deg cap,
            # i.e. inverted-normal faces).
            if self.params.merge_coplanar_faces and len(cluster_faces) > 1:
                boundary_subset = [f for f in cluster_faces if f.is_boundary]
                internal_subset = [f for f in cluster_faces if not f.is_boundary]
                if len(boundary_subset) > 1:
                    boundary_subset = self._merge_cluster_faces(boundary_subset, points)
                cluster_faces = internal_subset + boundary_subset

            new_poly_cells.append({
                "id": poly_id,
                "faces": cluster_faces,
                "vertices": list({v for f in cluster_faces for v in f.vertices}),
            })

        # Remaining tets stay as tetrahedra
        for cid in range(n_cells):
            if cid not in used_cells:
                poly_id = len(new_poly_cells)
                cluster_map[cid] = poly_id
                remaining_faces: list[Face] = []
                for fid in cell_faces[cid]:
                    fverts = faces[fid]
                    is_bface = fid in bface_to_patch
                    pname, ptype = bface_to_patch.get(fid, ("", ""))
                    # A leftover (unclustered) tet keeps ALL of its faces --
                    # it isn't merged with anything, so unlike the cluster
                    # case above there is no "internal to this cell" face to
                    # drop. Dropping faces whose other side happens to
                    # already belong to a poly cluster (the previous
                    # behaviour) silently deleted real faces of this tet,
                    # producing illegal cells with < 4 faces once enough of
                    # its neighbours had been clustered.
                    #
                    # Same winding fix as the cluster loop above: flip when
                    # this tet is the original neighbour side, so the face
                    # is outward-facing from THIS cell.
                    if int(owner[fid]) != cid:
                        fverts = list(reversed(fverts))
                    v_pos = [points[v] for v in fverts]
                    nrm = face_normal(v_pos)
                    remaining_faces.append(Face(
                        vertices=fverts,
                        normal=nrm,
                        owner=poly_id,
                        neighbour=-1,
                        is_boundary=is_bface,
                        patch_name=pname,
                        patch_type=ptype,
                    ))
                new_poly_cells.append({
                    "id": poly_id,
                    "faces": remaining_faces,
                    "vertices": list({v for f in remaining_faces for v in f.vertices}),
                })

        # Filter out cells with 0 faces (clusters where all faces were
        # internal), THEN renumber the survivors 0..N-1 contiguously.
        # Cell "id" was assigned as len(new_poly_cells) at creation time
        # (before this filter), so a filtered-out cell left a gap in the
        # id sequence; writing owner/neighbour with those stale, gapped
        # ids made checkMesh infer a phantom cell (0 faces) at every gap.
        new_poly_cells = [c for c in new_poly_cells if c["faces"]]
        for new_id, cell in enumerate(new_poly_cells):
            cell["id"] = new_id

        # Collect every face row as emitted by its owning cell above. A
        # genuine internal face was independently appended TWICE -- once by
        # each of the two cells it borders (a cluster's construction and a
        # neighbouring leftover tet's, or two different clusters) -- so
        # grouping by vertex set recovers the true topology: a group of one
        # row is a real boundary face, a group of two is one internal face
        # shared by exactly those two cells (a single row, not two).
        raw_faces: list[list[int]] = []
        raw_owner: list[int] = []
        raw_patch: list[tuple[str, str] | None] = []
        for cell in new_poly_cells:
            for f in cell["faces"]:
                raw_faces.append(f.vertices)
                raw_owner.append(cell["id"])
                raw_patch.append(
                    (f.patch_name, f.patch_type)
                    if f.is_boundary and f.patch_name else None
                )

        groups: dict[frozenset[int], list[int]] = {}
        for i, verts in enumerate(raw_faces):
            groups.setdefault(frozenset(verts), []).append(i)

        internal_faces: list[list[int]] = []
        internal_owner: list[int] = []
        internal_neighbour: list[int] = []
        boundary_faces: list[list[int]] = []
        boundary_owner: list[int] = []
        boundary_patch: list[tuple[str, str]] = []

        for idxs in groups.values():
            if len(idxs) == 2 and raw_owner[idxs[0]] != raw_owner[idxs[1]]:
                i, j = idxs
                lo, hi = (i, j) if raw_owner[i] < raw_owner[j] else (j, i)
                internal_faces.append(raw_faces[lo])
                internal_owner.append(raw_owner[lo])
                internal_neighbour.append(raw_owner[hi])
            else:
                # len == 1 (genuine boundary face), a same-owner pair
                # (can happen after coplanar-face merging collapses two
                # distinct faces onto the same vertex set), or >= 3 rows
                # (non-manifold input) -- keep every row as its own
                # boundary face rather than guessing a merge.
                for i in idxs:
                    boundary_faces.append(raw_faces[i])
                    boundary_owner.append(raw_owner[i])
                    boundary_patch.append(raw_patch[i] or ("walls", "patch"))

        # OpenFOAM's polyMesh format requires internal faces first, sorted
        # upper-triangular (owner ascending, then neighbour ascending),
        # followed by boundary faces grouped contiguously by patch.
        order = sorted(
            range(len(internal_faces)),
            key=lambda k: (internal_owner[k], internal_neighbour[k]),
        )
        internal_faces = [internal_faces[k] for k in order]
        internal_owner = [internal_owner[k] for k in order]
        internal_neighbour = [internal_neighbour[k] for k in order]

        patch_order: list[str] = []
        patch_seen: set[str] = set()
        for name, _ in boundary_patch:
            if name not in patch_seen:
                patch_seen.add(name)
                patch_order.append(name)
        patch_rank = {name: k for k, name in enumerate(patch_order)}
        border = sorted(
            range(len(boundary_faces)), key=lambda k: patch_rank[boundary_patch[k][0]]
        )
        boundary_faces = [boundary_faces[k] for k in border]
        boundary_owner = [boundary_owner[k] for k in border]
        boundary_patch = [boundary_patch[k] for k in border]

        new_faces = internal_faces + boundary_faces
        new_owner = internal_owner + boundary_owner
        new_neighbour = internal_neighbour + [-1] * len(boundary_faces)

        # Store for _write_boundary / _compute_boundary_patches
        self._face_patch_map = {
            len(internal_faces) + k: boundary_patch[k]
            for k in range(len(boundary_faces))
        }

        return (
            new_poly_cells,
            new_faces,
            np.array(new_owner, dtype=np.int32),
            np.array(new_neighbour, dtype=np.int32),
        )

    def _merge_cluster_faces(
        self, faces_list: list[Face], points: np.ndarray,
    ) -> list[Face]:
        """Merge coplanar faces within a cluster.

        Repeatedly merges pairs of faces that are coplanar and share
        an edge, until no more merges are possible.
        """
        if len(faces_list) < 2:
            return faces_list

        result = list(faces_list)
        changed = True
        while changed:
            changed = False
            new_result: list[Face] = []
            skip: set[int] = set()
            for i in range(len(result)):
                if i in skip:
                    continue
                merged = False
                for j in range(i + 1, len(result)):
                    if j in skip:
                        continue
                    va = [points[v] for v in result[i].vertices]
                    vb = [points[v] for v in result[j].vertices]
                    if merge_acceptable(
                        va, vb,
                        tol=self.params.coplanar_tolerance,
                        quasi_tol=self.params.quasi_coplanar_tolerance,
                        max_skew=self.params.max_merge_skewness,
                        na=result[i].normal,
                        nb=result[j].normal,
                    ):
                        merged_v = merge_two_faces(va, vb)
                        # Map merged vertex coordinates back to original indices
                        merged_indices = self._map_verts_to_indices(
                            merged_v, result[i].vertices, result[j].vertices,
                            points,
                        )
                        new_face = Face(
                            vertices=merged_indices,
                            normal=face_normal(
                                [points[v] for v in merged_indices]
                            ),
                            owner=result[i].owner,
                            neighbour=-1,
                            is_boundary=result[i].is_boundary,
                            patch_name=result[i].patch_name or result[j].patch_name,
                            patch_type=result[i].patch_type or result[j].patch_type,
                        )
                        new_result.append(new_face)
                        skip.add(j)
                        merged = True
                        changed = True
                        break
                if not merged:
                    new_result.append(result[i])
            result = new_result
        return result

    @staticmethod
    def _map_verts_to_indices(
        merged_verts: list[np.ndarray],
        verts_i: list[int], verts_j: list[int],
        points: np.ndarray,
    ) -> list[int]:
        """Map merged vertex coordinates back to original point indices.

        For each vertex in merged_verts, finds the closest original point
        index from either face i or face j by Euclidean distance.
        """
        # Build lookup: coordinate tuple → index
        lookup: dict[tuple[float, float, float], int] = {}
        for idx in set(verts_i) | set(verts_j):
            p = tuple(points[idx])
            lookup[p] = idx
        # Also try rounding for floating-point tolerance
        candidate_idx = np.fromiter(set(verts_i) | set(verts_j), dtype=np.int64)
        candidate_pts = points[candidate_idx]
        result: list[int] = []
        for mv in merged_verts:
            mt = tuple(mv)
            if mt in lookup:
                result.append(lookup[mt])
            else:
                # Vectorized nearest-point search over the (small) candidate
                # set, instead of a Python min()/lambda calling
                # np.linalg.norm once per candidate.
                dists = np.linalg.norm(candidate_pts - mv, axis=1)
                result.append(int(candidate_idx[np.argmin(dists)]))
        return result

    def _cleanup(self) -> None:
        """Reset internal state between runs."""
        self._face_patch_map = {}

    def _backup_mesh(self, src: Path, dst: Path) -> None:
        """Backup original mesh before overwriting."""
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        logger.info("Backed up original mesh to %s", dst)

    def _write_poly_mesh(
        self,
        poly_dir: Path,
        points: np.ndarray,
        new_faces: list[list[int]],
        new_owner: np.ndarray,
        new_neighbour: np.ndarray,
        boundary_patches: list[dict],
    ) -> None:
        """Write aggregated polyhedral mesh in OpenFOAM format."""
        if len(new_faces) == 0:
            raise RuntimeError("Cannot write mesh: 0 faces produced by aggregation")
        if len(new_owner) == 0:
            raise RuntimeError("Cannot write mesh: 0 cells produced by aggregation")

        # Boundary — compute real patch distribution from new faces
        patches = self._compute_boundary_patches(new_faces, new_neighbour)

        # Single writer: foam_mesh_io.write_polymesh (atomic temp-dir + replace).
        # NOTE: ``boundary_patches`` is intentionally unused — the real patch
        # split comes from ``_face_patch_map`` built during aggregation (the
        # argument predates that map and is kept for call-site compatibility).
        from cfmesh_autogui.core import foam_mesh_io

        foam_mesh_io.write_polymesh(
            poly_dir,
            points,
            new_faces,
            np.asarray(new_owner, dtype=np.int64),
            np.asarray(new_neighbour, dtype=np.int64),
            patches,
        )

        logger.info(
            "Written polyhedral mesh: %d points, %d faces, %d cells",
            len(points), len(new_faces), len(new_owner),
        )

    def _compute_boundary_patches(
        self,
        new_faces: list[list[int]],
        new_neighbour: np.ndarray,
    ) -> list[dict]:
        """Compute the real patch distribution for the aggregated mesh.

        Groups boundary faces (neighbour == -1) by patch name using the
        ``_face_patch_map`` built during aggregation. Falls back to a single
        ``walls`` patch if no mapping exists. Returns the sorted patch list
        (name/type/nFaces/startFace) for foam_mesh_io.write_polymesh.
        """
        n_faces = len(new_faces)
        bface_ids = [i for i in range(n_faces) if new_neighbour[i] < 0]

        # Build new patch list from face→patch mapping
        face_patch_map = getattr(self, "_face_patch_map", {})
        patch_groups: dict[str, dict] = {}
        for fid in bface_ids:
            name, ptype = face_patch_map.get(fid, ("walls", "patch"))
            if name not in patch_groups:
                patch_groups[name] = {"type": ptype, "faces": []}
            patch_groups[name]["faces"].append(fid)

        # Sort patches by first face index for valid OpenFOAM boundary
        sorted_patches = sorted(
            patch_groups.items(),
            key=lambda x: min(x[1]["faces"]) if x[1]["faces"] else 0,
        )

        patches = [
            {
                "name": name,
                "type": info["type"],
                "nFaces": len(info["faces"]),
                "startFace": min(info["faces"]) if info["faces"] else 0,
            }
            for name, info in sorted_patches
        ]
        if not patches:
            patches.append({
                "name": "walls",
                "type": "patch",
                "nFaces": 0,
                "startFace": 0,
            })

        logger.info(
            "Boundary: %d patches (%d boundary faces)",
            len(patches), len(bface_ids),
        )
        return patches

    def _get_quality(self, case_dir: Path) -> dict[str, float]:
        """Run checkMesh and return key quality metrics."""
        from cfmesh_autogui.config import OFConfig
        from cfmesh_autogui.core.openfoam_runner import parse_checkmesh_output
        cfg = OFConfig()
        try:
            cmd = cfg.build_check_mesh_cmd(case_dir)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
            report = parse_checkmesh_output(r.stdout + r.stderr)
            return {
                "cells": report.cells,
                "max_non_ortho": report.max_non_ortho,
                "avg_non_ortho": report.avg_non_ortho,
                "max_skewness": report.max_skewness,
                "avg_skewness": report.avg_skewness,
                "max_aspect_ratio": report.max_aspect_ratio,
                "neg_cells": report.neg_cells,
                "passed": report.passed,
            }
        except OSError as exc:
            logger.warning("checkMesh WSL call failed: %s", exc)
            return {}
        except (ValueError, IndexError, subprocess.TimeoutExpired) as exc:
            logger.warning("Quality check failed: %s", exc)
            return {}


def main(argv: list[str] | None = None) -> int:
    """CLI entry: polyhedral aggregation headless.

    Usage::

        python -m cfmesh_autogui.commercial.poly_aggregator \\
            --case-dir ./case --geometry model.stl
    """
    import argparse
    parser = argparse.ArgumentParser(
        description="STAR-CCM+ style polyhedral aggregation (headless)",
    )
    parser.add_argument("--version", action="version", version="CFMesh-AutoGUI PolyAggregator 1.0.0")
    parser.add_argument("--case-dir", "-c", required=True, help="OpenFOAM case directory")
    parser.add_argument("--geometry", "-g", default="", help="Geometry file (STEP/STL)")
    parser.add_argument("--min-tets", type=int, default=20, help="Min tets per cluster (1=aggressive, 30=conservative)")
    parser.add_argument("--no-bl", action="store_true", help="Disable boundary layers")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    agg = PolyAggregator()
    agg.params.min_tets_per_cluster = args.min_tets
    agg.params.bl_enabled = not args.no_bl
    result = agg.run(args.case_dir, geometry_path=args.geometry)

    if result.success:
        print(
            f"OK: {result.cells_before} -> {result.cells_after} cells "
            f"({result.reduction_pct:.1f}% reduction)"
        )
        return 0
    print(f"FAIL: {'; '.join(result.errors)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
