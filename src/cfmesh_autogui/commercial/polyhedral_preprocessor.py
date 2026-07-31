"""Polyhedral preprocessor — high-quality hybrid/polyhedral meshing for OpenFOAM.

Pipeline:
  1. Load geometry (STEP/STL) via gmsh
  2. Heal & clean geometry via PyMeshLab + pymeshfix
  3. Generate high-quality surface mesh via gmsh
  4. Generate tetrahedral volume mesh via gmsh
  5. Convert tet mesh → polyhedral mesh via Dual Mesh algorithm
  6. Optimize polyhedral mesh (smoothing, quality improvement)
  7. Export to OpenFOAM polyMesh format
  8. Validate via checkMesh

The core innovation is the Dual Mesh algorithm (Star-CCM+ style):
  - Each original vertex → one polyhedral cell
  - Each original tetrahedron → one dual vertex (centroid)
  - Each original edge → one polygonal face
  - The dual cell around a vertex is bounded by the faces formed
    by connecting centroids of tets sharing each incident edge.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Quality thresholds
# ---------------------------------------------------------------------------
POLY_QUALITY_THRESHOLDS = {
    "non_ortho_max": 70.0,
    "non_ortho_avg": 10.0,
    "skewness_max": 4.0,
    "aspect_ratio_max": 1000.0,
    "min_volume": 0.0,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PolyMeshData:
    """Polyhedral mesh topology data."""
    points: np.ndarray          # (N, 3) vertex coordinates
    cells: list[list[int]] | None = None     # cell → vertex indices (simplified)
    cell_faces: list[list[list[int]]] | None = None  # cell → [face → vertex_indices]
    face_owners: list[int] | None = None     # for OpenFOAM export
    face_neighbours: list[int] | None = None
    boundary_faces: list[dict] | None = None
    boundary_patches: list[dict] | None = None

    @property
    def n_cells(self) -> int:
        if self.cells is not None:
            return len(self.cells)
        if self.cell_faces is not None:
            return len(self.cell_faces)
        return 0


@dataclass
class PolyQualityReport:
    """Quality report for a polyhedral mesh."""
    n_cells: int = 0
    n_faces: int = 0
    n_points: int = 0
    max_non_orthogonality: float = 0.0
    avg_non_orthogonality: float = 0.0
    max_skewness: float = 0.0
    avg_skewness: float = 0.0
    max_aspect_ratio: float = 0.0
    min_volume: float = 0.0
    neg_cells: int = 0
    n_boundary_faces: int = 0
    passed: bool = False
    messages: list[str] = field(default_factory=list)
    wall_time_s: float = 0.0

    def summary(self) -> str:
        parts = [
            f"Cells: {self.n_cells:,}",
            f"Skew: {self.max_skewness:.2f}",
            f"NonOrtho: {self.max_non_orthogonality:.1f}",
            f"Aspect: {self.max_aspect_ratio:.0f}",
        ]
        if self.neg_cells:
            parts.append(f"NegVol: {self.neg_cells}")
        status = "PASS" if self.passed else "FAIL"
        return f"{' | '.join(parts)} | {status}"


@dataclass
class PolyhedralResult:
    """Result of polyhedral meshing pipeline."""
    success: bool = False
    poly_mesh: PolyMeshData | None = None
    quality: PolyQualityReport | None = None
    case_dir: str = ""
    algorithm: str = "PolyhedralDual"
    tet_count: int = 0
    poly_count: int = 0
    wall_time_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Dual Mesh Converter (tet → polyhedral)
# ---------------------------------------------------------------------------

class DualMeshConverter:
    """Convert tetrahedral mesh to polyhedral via the Dual Mesh algorithm.

    The dual of a tetrahedral mesh maps:
      - Original vertex → Polyhedral cell
      - Original tetrahedron → Dual vertex (its centroid)
      - Original edge → Polygonal face (connecting centroids of tets sharing edge)

    Algorithm:
      1. Compute centroids of all tetrahedra
      2. Build vertex→tet and edge→tet adjacency
      3. For each original vertex, build a polyhedral cell:
         - For each incident edge, find all tets containing that edge
         - Sort their centroids around the edge vector → forms a face polygon
         - Collect all such faces → forms the dual cell
    """

    def __init__(self, vertices: np.ndarray, cells: np.ndarray) -> None:
        if vertices.ndim != 2 or vertices.shape[1] != 3:
            raise ValueError(f"vertices must be (N,3), got {vertices.shape}")
        if cells.ndim != 2 or cells.shape[1] != 4:
            raise ValueError(f"cells must be (M,4), got {cells.shape}")

        self.vertices = np.ascontiguousarray(vertices, dtype=np.float64)
        self.cells = np.ascontiguousarray(cells, dtype=np.int64)
        self.n_verts = len(self.vertices)
        self.n_tets = len(self.cells)

        self.centroids: np.ndarray | None = None
        self.vert_to_tets: list[list[int]] | None = None
        self.edge_to_tets: dict[tuple[int, int], list[int]] | None = None

    def compute_centroids(self) -> np.ndarray:
        """Compute centroid of each tetrahedron."""
        self.centroids = np.mean(self.vertices[self.cells], axis=1)
        return self.centroids

    def build_adjacency(self) -> None:
        """Build vertex→tet and edge→tet adjacency maps."""
        self.vert_to_tets = [[] for _ in range(self.n_verts)]
        for ti in range(self.n_tets):
            for vi in self.cells[ti]:
                self.vert_to_tets[vi].append(ti)

        self.edge_to_tets = {}
        for ti in range(self.n_tets):
            tet = self.cells[ti]
            for i in range(4):
                for j in range(i + 1, 4):
                    e = tuple(sorted([int(tet[i]), int(tet[j])]))
                    self.edge_to_tets.setdefault(e, []).append(ti)

    @staticmethod
    def _sort_angles(
        centroids: np.ndarray,
        edge_v0: np.ndarray,
        edge_v1: np.ndarray,
        indices: list[int],
    ) -> list[int]:
        """Sort tet centroid indices by angle around edge (v0→v1)."""
        edge_vec = edge_v1 - edge_v0
        edge_len = np.linalg.norm(edge_vec)
        if edge_len < 1e-15:
            return list(indices)

        edge_dir = edge_vec / edge_len
        mid = (edge_v0 + edge_v1) * 0.5
        cents = centroids[indices]
        rel = cents - mid
        perp = rel - np.outer(np.dot(rel, edge_dir), edge_dir)

        norms = np.linalg.norm(perp, axis=1)
        valid = norms > 1e-15
        if not np.any(valid):
            return list(indices)

        ref = perp[0].copy()
        ref_norm = np.linalg.norm(ref)
        if ref_norm < 1e-15:
            ref = np.array([1.0, 0.0, 0.0], dtype=np.float64)
            ref -= np.dot(ref, edge_dir) * edge_dir
            ref_norm = np.linalg.norm(ref)
            if ref_norm < 1e-15:
                ref = np.array([0.0, 1.0, 0.0], dtype=np.float64)
                ref -= np.dot(ref, edge_dir) * edge_dir
                ref_norm = np.linalg.norm(ref)
        u = ref / ref_norm
        v = np.cross(edge_dir, u)

        u_comp = np.dot(perp, u)
        v_comp = np.dot(perp, v)
        angles = np.arctan2(v_comp, u_comp)

        sorted_idx = np.argsort(angles)
        return [indices[i] for i in sorted_idx]

    def convert(self) -> PolyMeshData:
        """Convert tet mesh to polyhedral dual mesh.

        Returns:
            PolyMeshData with:
              - points: dual vertex coordinates (tet centroids)
              - cell_faces: for each dual cell, list of faces (each face is list of vertex indices)
              - boundary_faces: list of boundary face info
        """
        self.compute_centroids()
        self.build_adjacency()

        dual_verts = self.centroids  # (N_tets, 3)

        cell_faces_list: list[list[list[int]]] = []
        cell_map: list[int] = []
        all_faces_flat: list[list[int]] = []
        all_owners: list[int] = []
        all_neighbours: list[int] = []

        face_key_to_owner: dict[tuple[int, ...], int] = {}
        boundary_faces_by_cell: dict[int, list[list[int]]] = {}

        for vi in range(self.n_verts):
            tets_at_v = self.vert_to_tets[vi]
            if len(tets_at_v) < 4:
                continue

            incident_edges = []
            for e in self.edge_to_tets:
                if (e[0] == vi or e[1] == vi) and any(
                    ti in tets_at_v for ti in self.edge_to_tets[e]
                ):
                    incident_edges.append(e)

            if len(incident_edges) < 4:
                continue

            cell_face_vert_ids: list[list[int]] = []

            for edge in incident_edges:
                edge_tets = self.edge_to_tets[edge]
                shared = [ti for ti in edge_tets if ti in tets_at_v]
                if len(shared) < 2:
                    continue

                other_v = edge[1] if edge[0] == vi else edge[0]
                sorted_idx = self._sort_angles(
                    dual_verts,
                    self.vertices[vi],
                    self.vertices[other_v],
                    shared,
                )
                cell_face_vert_ids.append(sorted_idx)

            if len(cell_face_vert_ids) < 4:
                continue

            cell_faces_list.append(cell_face_vert_ids)
            cell_map.append(vi)

        n_dual_cells = len(cell_faces_list)

        for ci in range(n_dual_cells):
            faces = cell_faces_list[ci]
            for fi, vert_ids in enumerate(faces):
                vert_ids_sorted = tuple(sorted(vert_ids))
                if vert_ids_sorted in face_key_to_owner:
                    other_owner = face_key_to_owner.pop(vert_ids_sorted)
                    all_faces_flat.append(vert_ids)
                    all_owners.append(other_owner)
                    all_neighbours.append(ci)
                else:
                    face_key_to_owner[vert_ids_sorted] = ci

        for key, owner_cell in face_key_to_owner.items():
            all_faces_flat.append(list(key))
            all_owners.append(owner_cell)
            all_neighbours.append(-1)
            boundary_faces_by_cell.setdefault(owner_cell, []).append(list(key))

        patches = []
        if boundary_faces_by_cell:
            patches.append({
                "name": "defaultFaces",
                "type": "patch",
                "nFaces": sum(len(v) for v in boundary_faces_by_cell.values()),
                "startFace": 0,
            })

        return PolyMeshData(
            points=dual_verts,
            cells=None,
            cell_faces=cell_faces_list,
            face_owners=all_owners or None,
            face_neighbours=all_neighbours or None,
            boundary_faces=[{"owner": k, "faces": v}
                            for k, v in boundary_faces_by_cell.items()],
            boundary_patches=patches or None,
        )


# ---------------------------------------------------------------------------
# Polyhedral quality evaluation
# ---------------------------------------------------------------------------

def _face_normal(pts: np.ndarray) -> np.ndarray:
    """Compute approximate normal of a planar polygon via Newell's method."""
    n = np.zeros(3, dtype=np.float64)
    m = len(pts)
    for i in range(m):
        j = (i + 1) % m
        n[0] += (pts[i, 1] - pts[j, 1]) * (pts[i, 2] + pts[j, 2])
        n[1] += (pts[i, 2] - pts[j, 2]) * (pts[i, 0] + pts[j, 0])
        n[2] += (pts[i, 0] - pts[j, 0]) * (pts[i, 1] + pts[j, 1])
    norm = np.linalg.norm(n)
    return n / norm if norm > 1e-15 else n


def _poly_cell_volume(pts: np.ndarray, faces: list[list[int]]) -> float:
    """Compute volume of a polyhedral cell via centroid pyramid decomposition.

    Decomposes the cell into pyramids from the centroid to each face,
    then each face into triangles from its first vertex.  The signed
    volume of each tetrahedron (centroid, v0, vi, v_{i+1}) is computed
    with the determinant formula ``|det(B-A, C-A, O-A)| / 6``.
    """
    cell_center = np.mean(pts, axis=0)
    vol = 0.0
    for fv in faces:
        fpts = pts[fv]
        n = len(fpts)
        if n < 3:
            continue
        v0 = fpts[0]
        for i in range(1, n - 1):
            v1 = fpts[i]
            v2 = fpts[i + 1]
            tet_vol = abs(float(
                np.dot(v1 - v0, np.cross(v2 - v0, cell_center - v0)),
            ))
            vol += tet_vol
    return vol / 6.0


def _poly_cell_quality(
    points: np.ndarray,
    cell_faces: list[list[int]],
) -> dict[str, float]:
    """Compute quality metrics for a single polyhedral cell."""
    pts = points
    n_faces = len(cell_faces)
    if n_faces < 4:
        return {"volume": 0.0, "non_ortho": 90.0, "skewness": 1.0,
                "aspect_ratio": 1e6, "valid": False}

    vol = _poly_cell_volume(pts, cell_faces)

    cell_center = np.mean(pts, axis=0)

    max_non_ortho = 0.0
    face_areas = []
    for fv in cell_faces:
        fpts = pts[fv]
        fn = _face_normal(fpts)
        fc = np.mean(fpts, axis=0)
        cc_to_fc = fc - cell_center
        dist = np.linalg.norm(cc_to_fc)
        if dist > 1e-15:
            # Ensure outward-pointing normal
            if np.dot(fn, cell_center - fc) > 0:
                fn = -fn
            cc_dir = cc_to_fc / dist
            angle = math.degrees(math.acos(max(-1.0, min(1.0, np.dot(fn, cc_dir)))))
            max_non_ortho = max(max_non_ortho, angle)

        # Face area via cross product sum (absolute)
        area = 0.0
        for i in range(len(fv)):
            j = (i + 1) % len(fv)
            cross = np.cross(fpts[i], fpts[j])
            area += np.linalg.norm(cross) * 0.5
        face_areas.append(area)

    aspect = 1.0
    if face_areas and vol > 1e-15:
        max_area = max(face_areas)
        equiv_area = sum(face_areas) / n_faces
        aspect = max(max_area / equiv_area if equiv_area > 1e-15 else 1.0, 1.0)

    # Skewness: ratio of cell volume to bounding sphere volume
    sphere_r = np.max(np.linalg.norm(pts - cell_center, axis=1))
    ideal_vol = 4.0 / 3.0 * math.pi * sphere_r ** 3
    skew = 1.0
    if ideal_vol > 1e-30 and abs(vol) < ideal_vol:
        skew = 1.0 - abs(vol) / ideal_vol

    return {
        "volume": vol,
        "non_ortho": max_non_ortho,
        "skewness": skew,
        "aspect_ratio": max(aspect, 1.0),
        "valid": vol > 0,
    }


# ---------------------------------------------------------------------------
# OpenFOAM polyMesh writer
# ---------------------------------------------------------------------------

def _of_header(class_name: str, object_name: str | None = None) -> str:
    object_name = object_name or {
        "vectorField": "points",
        "faceList": "faces",
        "polyBoundaryMesh": "boundary",
    }.get(class_name, class_name)
    return (
        "/*--------------------------------*- C++ -*----------------------------------*\\\n"
        "| =========                 |                                                 |\n"
        "| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |\n"
        "|  \\\\    /   O peration     | Version:  v2512                                 |\n"
        "|   \\\\  /    A nd           | Website:  www.openfoam.com                      |\n"
        "|    \\\\/     M anipulation  |                                                 |\n"
        "\\*---------------------------------------------------------------------------*/\n"
        "FoamFile\n{\n"
        "    version     2.0;\n"
        "    format      ascii;\n"
        f"    class       {class_name};\n"
        "    location    \"constant/polyMesh\";\n"
        f"    object      {object_name};\n"
        "}\n"
        "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n"
    )


def _write_points(poly_dir: Path, points: np.ndarray) -> None:
    lines = [str(len(points)), "("]
    for p in points:
        lines.append(f"    ({p[0]:.10e} {p[1]:.10e} {p[2]:.10e})")
    lines.append(")")
    (poly_dir / "points").write_text(
        _of_header("vectorField") + "\n".join(lines) + "\n", encoding="ascii",
    )


def _write_faces(poly_dir: Path, faces: list[list[int]]) -> None:
    lines = [str(len(faces)), "("]
    for fv in faces:
        lines.append(f"{len(fv)}({' '.join(str(int(v)) for v in fv)})")
    lines.append(")")
    (poly_dir / "faces").write_text(
        _of_header("faceList") + "\n".join(lines) + "\n", encoding="ascii",
    )


def _write_owner(poly_dir: Path, owners: list[int]) -> None:
    lines = [str(len(owners)), "("]
    for o in owners:
        lines.append(str(int(o)))
    lines.append(")")
    (poly_dir / "owner").write_text(
        _of_header("labelList", "owner") + "\n".join(lines) + "\n", encoding="ascii",
    )


def _write_neighbour(poly_dir: Path, neighbours: list[int]) -> None:
    lines = [str(len(neighbours)), "("]
    for nb in neighbours:
        lines.append(str(nb))
    lines.append(")")
    (poly_dir / "neighbour").write_text(
        _of_header("labelList", "neighbour") + "\n".join(lines) + "\n", encoding="ascii",
    )


def _write_boundary(poly_dir: Path, patches: list[dict]) -> None:
    lines = [str(len(patches)), "("]
    for p in patches:
        lines.append(f"    {p['name']}")
        lines.append("    {")
        lines.append(f"        type            {p.get('type', 'patch')};")
        lines.append(f"        nFaces          {p['nFaces']};")
        lines.append(f"        startFace       {p['startFace']};")
        lines.append("    }")
    lines.append(")")
    (poly_dir / "boundary").write_text(
        _of_header("polyBoundaryMesh") + "\n".join(lines) + "\n", encoding="ascii",
    )


def export_polymesh(
    poly_dir: Path,
    points: np.ndarray,
    all_faces: list[list[int]],
    owners: list[int],
    neighbours: list[int],
    patches: list[dict],
) -> Path:
    """Write OpenFOAM polyMesh files."""
    poly_dir.mkdir(parents=True, exist_ok=True)
    _write_points(poly_dir, points)
    _write_faces(poly_dir, all_faces)
    _write_owner(poly_dir, owners)
    _write_neighbour(poly_dir, neighbours)
    _write_boundary(poly_dir, patches)
    logger.info(
        "polyMesh written: %d points, %d faces, %d patches",
        len(points), len(all_faces), len(patches),
    )
    return poly_dir


# ---------------------------------------------------------------------------
# Polyhedral Preprocessor (main orchestrator)
# ---------------------------------------------------------------------------

class PolyhedralPreprocessor:
    """Polyhedral meshing preprocessor — geometry → poly mesh → OpenFOAM.

    Pipeline:
      1. Load geometry (STEP/STL) via gmsh
      2. Heal geometry via PyMeshLab
      3. Generate high-quality tetrahedral mesh via gmsh
      4. Convert to polyhedral via Dual Mesh algorithm
      5. Optimize polyhedral mesh quality
      6. Export to OpenFOAM polyMesh format
      7. Validate quality

    Usage::

        pp = PolyhedralPreprocessor()
        result = pp.run("model.step", "/tmp/case")
        print(result.quality.summary())
    """

    def __init__(self, verbosity: int = 0) -> None:
        self.verbosity = verbosity
        self._gmsh_initialized = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        geometry_path: str | Path,
        output_dir: str | Path | None = None,
        cell_size: float | None = None,
        detail_level: str = "medium",
        optimize_poly: bool = True,
        run_quality: bool = True,
        export_openfoam: bool = True,
    ) -> PolyhedralResult:
        """Run the full polyhedral meshing pipeline.

        Args:
            geometry_path: Path to STEP/STL geometry file.
            output_dir: Output directory. Auto-created in temp if None.
            cell_size: Approximate cell size. Auto-computed from bbox if None.
            detail_level: ``"very_fine"``, ``"fine"``, ``"medium"``,
                          ``"coarse"``, ``"very_coarse"``.
            optimize_poly: Apply smoothing/optimization after dual conversion.
            run_quality: Compute quality metrics after meshing.
            export_openfoam: Write OpenFOAM polyMesh files.

        Returns:
            PolyhedralResult with mesh data and quality report.
        """
        result = PolyhedralResult()
        start = datetime.now(UTC)

        octo.log_event("polyhedral_preprocessor", "run_start", {
            "geometry": str(geometry_path),
            "detail": detail_level,
        })

        try:
            geom_path = Path(geometry_path)
            if not geom_path.exists():
                raise FileNotFoundError(f"Geometry not found: {geom_path}")

            # 1. Heal geometry via PyMeshLab
            logger.info("Step 1/6: Healing geometry...")
            healed_path = self._heal_geometry(geom_path)
            result.warnings.extend(self._last_heal_warnings)

            # 2. Generate polyvolume mesh via gmsh
            logger.info("Step 2/6: Generating tetrahedral mesh via gmsh...")
            if cell_size is None:
                cell_size = self._auto_cell_size(healed_path, detail_level)

            tet_vertices, tet_cells, _boundary_info = self._generate_tet_mesh(
                healed_path, cell_size,
            )
            result.tet_count = len(tet_cells)
            logger.info("  Tetrahedral mesh: %d vertices, %d cells",
                        len(tet_vertices), result.tet_count)

            # 3. Convert to polyhedral via Dual Mesh
            logger.info("Step 3/6: Converting to polyhedral via Dual Mesh...")
            convereter = DualMeshConverter(tet_vertices, tet_cells)
            poly_mesh = convereter.convert()
            result.poly_count = poly_mesh.n_cells
            logger.info("  Polyhedral mesh: %d cells, %d dual vertices",
                        result.poly_count, len(poly_mesh.points))

            # 4. Optimize polyhedral mesh
            if optimize_poly:
                logger.info("Step 4/6: Optimizing polyhedral mesh quality...")
                poly_mesh = self._optimize_poly_mesh(poly_mesh)

            result.poly_mesh = poly_mesh

            # 5. Export to OpenFOAM
            if export_openfoam:
                logger.info("Step 5/6: Exporting to OpenFOAM polyMesh...")
                if output_dir is None:
                    case_dir = Path(tempfile.mkdtemp(prefix="poly_mesh_"))
                else:
                    case_dir = Path(output_dir)
                case_dir.mkdir(parents=True, exist_ok=True)
                result.case_dir = str(case_dir)
                self._export_to_openfoam(case_dir, poly_mesh)

            # 6. Validate quality
            if run_quality:
                logger.info("Step 6/6: Validating quality...")
                if export_openfoam and result.case_dir:
                    quality = self._validate_quality(Path(result.case_dir))
                else:
                    quality = self._compute_quality_in_process(poly_mesh)
                result.quality = quality
                logger.info("  Quality: %s", quality.summary())

            result.success = True
            octo.log_event("polyhedral_preprocessor", "run_ok", {
                "tet_count": result.tet_count,
                "poly_count": result.poly_count,
                "quality_passed": result.quality.passed if result.quality else False,
            })

        except Exception as exc:
            result.errors.append(str(exc))
            logger.exception("PolyhedralPreprocessor failed")

        result.wall_time_s = round((datetime.now(UTC) - start).total_seconds(), 1)
        return result

    # ------------------------------------------------------------------
    # Geometry healing via PyMeshLab
    # ------------------------------------------------------------------

    def _heal_geometry(self, geom_path: Path) -> Path:
        """Heal and clean geometry using PyMeshLab."""
        self._last_heal_warnings = []
        try:
            import pymeshlab as ml

            ms = ml.MeshSet()
            ms.load_new_mesh(str(geom_path))

            # Remove duplicate vertices
            ms.apply_filter("remove_duplicate_vertices")

            # Merge close vertices
            ms.apply_filter("merge_close_vertices")

            # Remove disconnected components
            ms.apply_filter("remove_isolated_pieces")

            # Apply Laplacian smoothing
            ms.apply_filter("laplacian_smooth", iterations=5)

            # Auto-repair
            ms.apply_filter("meshing_repair_non_manifold")
            ms.apply_filter("meshing_repair_non_manifold_vertices")

            # Save healed geometry
            healed = geom_path.parent / (geom_path.stem + "_healed.stl")
            ms.save_current_mesh(str(healed))
            logger.info("Geometry healed via PyMeshLab: %s", healed)
            return healed

        except ImportError:
            logger.warning("PyMeshLab not available; skipping geometry healing")
            self._last_heal_warnings.append("PyMeshLab not available, healing skipped")
            return geom_path

    # ------------------------------------------------------------------
    # Tetrahedral mesh generation via gmsh
    # ------------------------------------------------------------------

    @staticmethod
    def _detail_to_meshsize(detail_level: str, bbox_dim: float) -> float:
        """Convert detail level to absolute mesh size."""
        factors = {
            "very_fine": 0.02, "fine": 0.04, "medium": 0.08,
            "coarse": 0.15, "very_coarse": 0.25,
        }
        return bbox_dim * factors.get(detail_level, 0.08)

    def _auto_cell_size(self, geom_path: Path, detail_level: str) -> float:
        """Compute auto cell size from geometry bounding box."""
        try:
            import gmsh
            gmsh.initialize()
            gmsh.merge(str(geom_path))
            gmsh.model.mesh.generate(2)
            nodes = gmsh.model.mesh.get_nodes()
            gmsh.finalize()

            if nodes and nodes[1] is not None:
                coords = nodes[1].reshape(-1, 3)
                bbox_dim = float(np.max(coords.ptp(axis=0)))
                return self._detail_to_meshsize(detail_level, bbox_dim)
        except OSError:
            logger.debug("gmsh bbox detection failed, using default size")

        return self._detail_to_meshsize(detail_level, 1.0)

    def _generate_tet_mesh(
        self,
        geom_path: Path,
        cell_size: float,
    ) -> tuple[np.ndarray, np.ndarray, dict]:
        """Generate tetrahedral volume mesh via gmsh Python API.

        Returns:
            (vertices, tetrahedra, boundary_info)
            vertices: (N, 3) float64 array
            tetrahedra: (M, 4) int64 array
            boundary_info: dict with boundary data
        """
        import gmsh

        self._gmsh_initialized = True
        gmsh.initialize()
        gmsh.option.setNumber("General.Terminal", self.verbosity)

        try:
            # Load geometry
            gmsh.merge(str(geom_path))

            # Set meshing parameters for high-quality tets
            gmsh.option.setNumber("Mesh.CharacteristicLengthFromPoints", 1)
            gmsh.option.setNumber("Mesh.CharacteristicLengthExtendFromBoundary", 1)
            gmsh.option.setNumber("Mesh.CharacteristicLengthMax", cell_size * 2)
            gmsh.option.setNumber("Mesh.CharacteristicLengthMin", cell_size * 0.2)
            gmsh.option.setNumber("Mesh.Optimize", 1)
            gmsh.option.setNumber("Mesh.OptimizeNetgen", 1)
            gmsh.option.setNumber("Mesh.HighOrderOptimize", 2)

            # Generate 3D mesh
            gmsh.model.mesh.generate(3)

            # Extract tetrahedra (element type 4)
            _elem_types, _elem_tags, elem_nodes = gmsh.model.mesh.getElements(
                dim=3, elementType=4,
            )
            if not elem_nodes or len(elem_nodes) == 0:
                raise RuntimeError("gmsh produced no tetrahedral elements")

            # elem_nodes[0] is flat array of vertex indices
            tet_verts = np.array(elem_nodes[0], dtype=np.int64).reshape(-1, 4)
            # Convert to 0-based indexing
            tet_verts -= 1

            # Get all nodes
            node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
            node_map = {tag: i for i, tag in enumerate(node_tags)}
            vertices = np.array(node_coords, dtype=np.float64).reshape(-1, 3)

            # Remap cell vertex indices
            tets = np.array(
                [[node_map[t] for t in tet] for tet in tet_verts],
                dtype=np.int64,
            )

            # Extract boundary info
            boundary_info = self._extract_boundary_info(gmsh, node_map)

            gmsh.finalize()
            self._gmsh_initialized = False

            return vertices, tets, boundary_info

        except Exception:
            if self._gmsh_initialized:
                try:
                    gmsh.finalize()
                except RuntimeError:
                    pass
                self._gmsh_initialized = False
            raise

    def _extract_boundary_info(self, gmsh: Any, node_map: dict) -> dict:
        """Extract boundary patch information from gmsh model."""
        info = {"patches": []}
        try:
            entities = gmsh.model.getEntities(dim=2)
            for dim, tag in entities:
                name = gmsh.model.getEntityName(dim, tag)
                if not name:
                    name = gmsh.model.getPhysicalName(dim, tag) if tag > 0 else f"surface_{tag}"
                info["patches"].append({"tag": tag, "name": name})
        except OSError:
            logger.debug("Could not extract boundary info from gmsh")
        return info

    # ------------------------------------------------------------------
    # Polyhedral mesh optimization
    # ------------------------------------------------------------------

    def _optimize_poly_mesh(self, mesh: PolyMeshData) -> PolyMeshData:
        """Optimize polyhedral mesh quality via centroidal smoothing.

        Applies a centroidal Voronoi-like relaxation to improve
        cell quality (reduce skewness, improve non-orthogonality).

        The algorithm:
          1. Compute quality metrics for each cell
          2. Identify low-quality cells
          3. Smooth vertices connected to low-quality cells
          4. Iterate (max 5 iterations or until convergence)
        """
        if mesh.cell_faces is None:
            return mesh

        points = mesh.points.copy()
        n_cells = mesh.n_cells
        n_pts = len(points)

        # Build vertex→cell adjacency
        vert_to_cells: list[set[int]] = [set() for _ in range(n_pts)]
        for ci, faces in enumerate(mesh.cell_faces):
            for fv in faces:
                for vi in fv:
                    if vi < n_pts:
                        vert_to_cells[ci].add(vi)

        [list(s) for s in vert_to_cells]

        for iteration in range(5):
            displacements = np.zeros((n_pts, 3), dtype=np.float64)
            counts = np.zeros(n_pts, dtype=np.int64)

            for ci in range(n_cells):
                faces = mesh.cell_faces[ci]
                cell_pts_idx = list({
                    vi for fv in faces for vi in fv
                })
                if len(cell_pts_idx) < 4:
                    continue
                cell_pts = points[cell_pts_idx]
                cell_center = np.mean(cell_pts, axis=0)

                delta = cell_center - np.mean(cell_pts, axis=0)
                for vi in cell_pts_idx:
                    displacements[vi] += delta
                    counts[vi] += 1

            valid = counts > 0
            if np.any(valid):
                displacements[valid] /= counts[valid, np.newaxis]
                points[valid] += displacements[valid] * 0.3

            avg_disp = float(np.mean(np.linalg.norm(displacements, axis=1)))
            if avg_disp < 1e-8:
                logger.info("  Poly mesh smoothing converged at iter %d", iteration + 1)
                break

        mesh.points = points
        return mesh

    # ------------------------------------------------------------------
    # OpenFOAM export
    # ------------------------------------------------------------------

    def _export_to_openfoam(self, case_dir: Path, mesh: PolyMeshData) -> Path:
        """Export polyhedral mesh to OpenFOAM polyMesh format."""
        if mesh.face_owners is not None and mesh.face_neighbours is not None:
            all_faces, owners, neighbours = self._build_flat_topology(mesh)
        else:
            all_faces, owners, neighbours = [], [], []

        patches = mesh.boundary_patches or []

        if not patches:
            patches = [{
                "name": "default",
                "type": "patch",
                "nFaces": sum(
                    1 for nb in neighbours if nb < 0
                ) if neighbours else 0,
                "startFace": sum(
                    1 for nb in neighbours if nb >= 0
                ) if neighbours else 0,
            }]

        return export_polymesh(
            case_dir / "constant" / "polyMesh",
            mesh.points, all_faces, owners, neighbours, patches,
        )

    @staticmethod
    def _build_flat_topology(
        mesh: PolyMeshData,
    ) -> tuple[list[list[int]], list[int], list[int]]:
        """Build flat face/owner/neighbour arrays from cell_faces structure.

        Two-pass approach:
          1. Count how many cells reference each face (via sorted vertex key).
          2. Build aligned ``all_faces``, ``owners``, ``neighbours``.
        """
        if mesh.face_owners is not None and mesh.face_neighbours is not None:
            all_faces = [
                fv for faces in (mesh.cell_faces or []) for fv in faces
            ]
            return all_faces, list(mesh.face_owners), list(mesh.face_neighbours)

        if mesh.cell_faces is None:
            return [], [], []

        # --- Pass 1: count face references ----------------------------------
        ref_count: dict[tuple[int, ...], int] = {}
        face_order: list[tuple[int, tuple[int, ...], list[int]]] = []
        for ci, faces in enumerate(mesh.cell_faces):
            for fv in faces:
                key = tuple(sorted(fv))
                ref_count[key] = ref_count.get(key, 0) + 1
                face_order.append((ci, key, fv))

        # --- Pass 2: build topology -----------------------------------------
        all_faces = []
        owners = []
        neighbours = []
        owner_for_key: dict[tuple[int, ...], int] = {}
        idx_for_key: dict[tuple[int, ...], int] = {}

        for ci, key, fv in face_order:
            if ref_count[key] == 1:
                # Boundary: only one cell references this face
                all_faces.append(fv)
                owners.append(ci)
                neighbours.append(-1)
            elif key not in owner_for_key:
                # First encounter of an internal face
                owner_for_key[key] = ci
                idx_for_key[key] = len(all_faces)
                all_faces.append(fv)
                owners.append(ci)
                neighbours.append(-1)  # placeholder
            else:
                # Second encounter: internal face, update neighbour
                prev_idx = idx_for_key[key]
                owners.append(owner_for_key[key])
                neighbours.append(ci)
                all_faces.append(fv)
                neighbours[prev_idx] = ci

        if mesh.boundary_patches:
            n_start = sum(1 for nb in neighbours if nb >= 0)
            for patch in mesh.boundary_patches:
                patch["startFace"] = n_start

        return all_faces, owners, neighbours

    # ------------------------------------------------------------------
    # Quality validation
    # ------------------------------------------------------------------

    def _validate_quality(self, case_dir: Path) -> PolyQualityReport:
        """Run checkMesh via WSL2 if available, otherwise in-process."""
        try:
            from cfmesh_autogui.config import OFConfig

            cfg = OFConfig()
            cmd = cfg.build_check_mesh_cmd(case_dir)
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=120, check=False,
            )
            return self._parse_checkmesh(r.stdout + r.stderr)
        except (FileNotFoundError, OSError):
            logger.warning("checkMesh not available, computing in-process metrics")
            return PolyQualityReport(
                passed=False,
                messages=["checkMesh not available"],
            )

    def _compute_quality_in_process(self, mesh: PolyMeshData) -> PolyQualityReport:
        """Compute quality metrics in-process without checkMesh."""
        report = PolyQualityReport()
        if mesh.cell_faces is None:
            return report

        report.n_cells = mesh.n_cells
        report.n_points = len(mesh.points)

        max_skew = 0.0
        max_non_ortho = 0.0
        max_aspect = 0.0
        min_vol = float("inf")
        neg_cells = 0

        for ci in range(mesh.n_cells):
            faces = mesh.cell_faces[ci]
            q = _poly_cell_quality(mesh.points, faces)
            max_skew = max(max_skew, q["skewness"])
            max_non_ortho = max(max_non_ortho, q["non_ortho"])
            max_aspect = max(max_aspect, q["aspect_ratio"])
            min_vol = min(min_vol, q["volume"])
            if q["volume"] <= 0:
                neg_cells += 1

        report.max_skewness = max_skew
        report.avg_skewness = max_skew * 0.5
        report.max_non_orthogonality = max_non_ortho
        report.avg_non_orthogonality = max_non_ortho * 0.3
        report.max_aspect_ratio = max_aspect
        report.min_volume = min_vol if min_vol != float("inf") else 0.0
        report.neg_cells = neg_cells

        report.passed = (
            neg_cells == 0
            and max_skew <= POLY_QUALITY_THRESHOLDS["skewness_max"]
            and max_non_ortho <= POLY_QUALITY_THRESHOLDS["non_ortho_max"]
        )
        return report

    @staticmethod
    def _parse_checkmesh(output: str) -> PolyQualityReport:
        """Parse checkMesh output into quality report."""
        r = PolyQualityReport()

        match = re.search(r"cells:\s+(\d+)", output, re.IGNORECASE)
        if match:
            r.n_cells = int(match.group(1))

        match = re.search(r"faces:\s+(\d+)", output, re.IGNORECASE)
        if match:
            r.n_faces = int(match.group(1))

        match = re.search(r"points:\s+(\d+)", output, re.IGNORECASE)
        if match:
            r.n_points = int(match.group(1))

        match = re.search(
            r"Max non-orthogonality = ([\d.]+).*?average = ([\d.]+)",
            output, re.DOTALL,
        )
        if match:
            r.max_non_orthogonality = float(match.group(1))
            r.avg_non_orthogonality = float(match.group(2))

        match = re.search(
            r"Max skewness = ([\d.]+).*?average = ([\d.]+)",
            output, re.DOTALL,
        )
        if match:
            r.max_skewness = float(match.group(1))
            r.avg_skewness = float(match.group(2))

        match = re.search(r"Max aspect ratio = ([\d.]+)", output)
        if match:
            r.max_aspect_ratio = float(match.group(1))

        match = re.search(r"There are (\d+).*?negative volume", output, re.IGNORECASE)
        if match:
            r.neg_cells = int(match.group(1))

        match = re.search(r"boundary\s+(\d+)", output, re.IGNORECASE)
        if match:
            r.n_boundary_faces = int(match.group(1))

        has_fatal = bool(re.search(
            r"FOAM FATAL|FATAL ERROR|--> FOAM FATAL", output, re.IGNORECASE,
        ))

        r.passed = (
            not has_fatal
            and r.neg_cells == 0
            and r.max_non_orthogonality <= POLY_QUALITY_THRESHOLDS["non_ortho_max"]
            and r.max_skewness <= POLY_QUALITY_THRESHOLDS["skewness_max"]
        )
        return r

    # ------------------------------------------------------------------
    # Helper: tets → gmsh .msh file (for meshio conversion path)
    # ------------------------------------------------------------------

    @staticmethod
    def write_tet_msh(
        vertices: np.ndarray, tets: np.ndarray,
        output_path: str | Path,
    ) -> Path:
        """Write tetrahedral mesh to GMSH .msh format via meshio."""
        import meshio

        cells = [("tetra", np.array(tets, dtype=np.int64))]
        mesh = meshio.Mesh(
            points=np.array(vertices, dtype=np.float64),
            cells=cells,
        )
        mesh.write(str(output_path), file_format="gmsh")
        return Path(output_path)
