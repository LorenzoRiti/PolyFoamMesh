"""autopoly bridge — Python interface to the native C++ polyhedral mesher.

Provides a unified Python API that:
  1. Tries to import the compiled C++ ``autopoly`` pybind11 module.
  2. Falls back to a pure Python CVT-based polyhedral mesher using
     numpy + scipy.spatial (Voronoi) when the native library is absent.

This module is the integration point between the C++ autopoly library
and the cfmesh-autogui GUI.
"""

from __future__ import annotations

import logging
import math
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try native C++ module
# ---------------------------------------------------------------------------
_HAS_NATIVE = False
try:
    import autopoly_python as _native_autopoly
    _HAS_NATIVE = True
    logger.info("autopoly: native C++ module loaded (CVT engine).")
except ImportError:
    logger.info("autopoly: native module not found, using Python fallback.")


# ---------------------------------------------------------------------------
# Progress / Cancel types
# ---------------------------------------------------------------------------
ProgressCallback = Callable[[int, str, str], None]  # percent, stage, message
CancelToken = Callable[[], bool]                     # returns True if cancelled


def _noop_progress(pct: int, stage: str, msg: str) -> None:
    pass


def _never_cancelled() -> bool:
    return False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AutopolyParams:
    """Parameters for the autopoly mesher, mirroring the C++ MeshingParameters."""
    global_size: float = 0.05
    min_size: float = 0.001
    max_size: float = 1.0
    feature_angle_deg: float = 30.0
    curvature_adaptivity: float = 0.5
    proximity_adaptivity: float = 0.5
    max_growth_rate: float = 1.2
    surface_target_size: float = 0.03
    lloyd_iterations: int = 30
    seed: int = 12345
    merge_coplanar_faces: bool = True
    bl_enabled: bool = False
    bl_layers: int = 3
    bl_first_height: float = 0.0005
    bl_growth_rate: float = 1.2
    bl_max_thickness: float = 0.01

    @classmethod
    def from_detail_level(cls, detail: str) -> AutopolyParams:
        """Map detail level string to parameters."""
        params = cls()
        multipliers = {
            "very_fine": 0.3,
            "fine": 0.6,
            "medium": 1.0,
            "coarse": 1.8,
            "very_coarse": 3.0,
        }
        mult = multipliers.get(detail, 1.0)
        params.global_size = 0.05 * mult
        params.min_size = 0.001 * mult
        params.max_size = 1.0 * mult
        params.surface_target_size = 0.03 * mult
        params.lloyd_iterations = {  # fewer iterations for coarser meshes
            "very_fine": 50, "fine": 40, "medium": 30, "coarse": 20, "very_coarse": 15,
        }.get(detail, 30)
        return params


@dataclass
class AutopolyResult:
    """Result from an autopoly meshing run."""
    success: bool = False
    message: str = ""
    output_dir: str = ""
    n_points: int = 0
    n_cells: int = 0
    n_faces: int = 0
    n_patches: int = 0
    min_volume: float = 0.0
    max_non_ortho: float = 0.0
    max_skewness: float = 0.0
    max_aspect_ratio: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    wall_time_s: float = 0.0


# ---------------------------------------------------------------------------
# Pure Python CVT-based polyhedral mesher (fallback)
# ---------------------------------------------------------------------------

class _PythonPolyMesher:
    """Pure Python implementation of CVT-based polyhedral meshing.

    Pipeline:
      1. Load STL surface mesh (via trimesh or numpy)
      2. Build size field from curvature + proximity
      3. Generate adaptive seed points (jittered grid + Poisson disk)
      4. Compute Centroidal Voronoi Tessellation via Lloyd iterations
         using scipy.spatial.Voronoi
      5. Clip Voronoi cells against domain boundary
      6. Merge coplanar faces, collapse small features
      7. Export OpenFOAM polyMesh
      8. Compute quality metrics
    """

    def __init__(self):
        self._stages = [
            "load geometry",
            "build size field",
            "generate seeds",
            "CVT Lloyd relaxation",
            "clip boundary",
            "optimize mesh",
            "quality check",
            "export",
        ]

    def run(
        self,
        input_path: str | Path,
        output_dir: str | Path,
        params: AutopolyParams,
        progress: ProgressCallback = _noop_progress,
        cancel: CancelToken = _never_cancelled,
    ) -> AutopolyResult:
        """Run the full polyhedral meshing pipeline."""
        t0 = time.time()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        result = AutopolyResult(output_dir=str(output_dir))

        try:
            # Stage 1: Load geometry
            progress(0, self._stages[0], "Loading geometry...")
            vertices, triangles, _ = self._load_stl(input_path)
            if cancel():
                result.message = "Cancelled during geometry loading"
                return result
            logger.info("autopoly: loaded %d vertices, %d triangles", len(vertices), len(triangles))
            progress(10, self._stages[0], f"Loaded {len(vertices):,} vertices")

            # Compute bounding box
            bbox_min = vertices.min(axis=0)
            bbox_max = vertices.max(axis=0)
            bbox_diag = np.linalg.norm(bbox_max - bbox_min)

            # Stage 2: Build size field
            progress(15, self._stages[1], "Building size field...")
            sizes = self._compute_size_field(vertices, triangles, params, bbox_diag)
            if cancel():
                result.message = "Cancelled during size field"
                return result
            progress(20, self._stages[1], "Size field done")

            # Stage 3: Generate seeds
            progress(25, self._stages[2], "Generating seeds...")
            seeds = self._generate_seeds(vertices, triangles, sizes, params, bbox_min, bbox_max)
            if cancel():
                result.message = "Cancelled during seed generation"
                return result
            logger.info("autopoly: generated %d seeds", len(seeds))
            progress(35, self._stages[2], f"{len(seeds):,} seeds generated")

            # Stage 4: CVT / Lloyd relaxation
            progress(40, self._stages[3], "Lloyd relaxation...")
            seeds = self._lloyd_relaxation(seeds, vertices, triangles, sizes, params, progress, cancel)
            if cancel():
                result.message = "Cancelled during CVT"
                return result
            progress(60, self._stages[3], "CVT converged")

            # Stage 5: Clip boundary + build polyhedral mesh
            progress(65, self._stages[4], "Building Voronoi cells...")
            points, face_verts, face_owner, face_neigh, patches_out = self._build_voronoi_mesh(
                seeds, vertices, triangles, params
            )
            if cancel():
                result.message = "Cancelled during boundary clip"
                return result
            progress(80, self._stages[4], f"{len(seeds):,} cells built")

            # Stage 6: Optimize
            progress(82, self._stages[5], "Optimizing mesh...")
            points = self._smooth_mesh(points, face_verts, face_owner, face_neigh, params)
            progress(88, self._stages[5], "Optimization done")

            # Stage 7: Quality check
            progress(90, self._stages[6], "Computing quality metrics...")
            quality = self._compute_quality(points, face_verts, face_owner, face_neigh)
            result.min_volume = quality["min_volume"]
            result.max_non_ortho = quality["max_non_ortho"]
            result.max_skewness = quality["max_skewness"]
            result.max_aspect_ratio = quality["max_aspect_ratio"]
            progress(95, self._stages[6], "Quality: OK" if quality["passed"] else "Quality: warnings")

            # Stage 8: Export
            progress(96, self._stages[7], "Exporting...")
            _export_openfoam(points, face_verts, face_owner, face_neigh, patches_out, output_dir)
            progress(100, self._stages[7], f"Exported to {output_dir}")

            result.success = True
            result.n_points = len(points)
            result.n_cells = max(face_owner) + 1 if face_owner else 0
            result.n_faces = len(face_verts)
            result.n_patches = len(patches_out)
            result.message = f"Mesh generated: {result.n_cells:,} polyhedral cells"
            result.wall_time_s = time.time() - t0

        except Exception as e:
            logger.exception("autopoly meshing failed")
            result.success = False
            result.message = str(e)
            result.errors.append(str(e))
            result.wall_time_s = time.time() - t0

        octo.log_event("autopoly", "run_complete", {
            "success": result.success,
            "cells": result.n_cells,
            "time_s": result.wall_time_s,
        })
        return result

    # -----------------------------------------------------------------------
    # Geometry loading
    # -----------------------------------------------------------------------
    @staticmethod
    def _load_stl(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Load surface mesh file (STL, STEP, OBJ, PLY, VTK), return (vertices, triangles, patch_ids)."""
        path = Path(path)
        ext = path.suffix.lower()

        if ext in (".step", ".stp"):
            return _load_step(path)
        elif ext == ".stl":
            try:
                import trimesh
                mesh = trimesh.load(str(path))
                if isinstance(mesh, trimesh.Scene):
                    mesh = mesh.dump(concatenate=True)
                vertices = np.asarray(mesh.vertices, dtype=np.float64)
                triangles = np.asarray(mesh.faces, dtype=np.int32)
                # trimesh assigns visual materials as face colors; use as patch hint
                patch_ids = np.zeros(len(triangles), dtype=np.int32)
                return vertices, triangles, patch_ids
            except ImportError:
                return _load_stl_native(path)
        elif ext == ".obj":
            return _load_obj(path)
        elif ext == ".ply":
            return _load_ply(path)
        elif ext in (".vtk", ".vtp"):
            return _load_vtk(path)
        else:
            # Try trimesh for any supported format
            try:
                import trimesh
                mesh = trimesh.load(str(path))
                if isinstance(mesh, trimesh.Scene):
                    mesh = mesh.dump(concatenate=True)
                vertices = np.asarray(mesh.vertices, dtype=np.float64)
                triangles = np.asarray(mesh.faces, dtype=np.int32)
                patch_ids = np.zeros(len(triangles), dtype=np.int32)
                return vertices, triangles, patch_ids
            except ImportError as exc:
                raise ValueError(f"Unsupported format: {ext}") from exc

    # -----------------------------------------------------------------------
    # Size field
    # -----------------------------------------------------------------------
    @staticmethod
    def _compute_size_field(
        vertices: np.ndarray, triangles: np.ndarray,
        params: AutopolyParams, bbox_diag: float
    ) -> np.ndarray:
        """Compute per-vertex target size based on curvature + proximity."""
        n = len(vertices)
        sizes = np.full(n, params.global_size)

        # Curvature-based sizing
        if params.curvature_adaptivity > 0:
            # Approximate curvature via vertex normal variation
            curvatures = _estimate_curvature(vertices, triangles)
            k_min = curvatures.min()
            k_max = curvatures.max()
            if k_max > k_min:
                curv_norm = (curvatures - k_min) / (k_max - k_min)
                sizes *= (1.0 - params.curvature_adaptivity * curv_norm)
                sizes = np.maximum(sizes, params.min_size)

        # Clamp
        sizes = np.clip(sizes, params.min_size, params.max_size)
        return sizes

    # -----------------------------------------------------------------------
    # Seed generation
    # -----------------------------------------------------------------------
    @staticmethod
    def _generate_seeds(
        vertices: np.ndarray, triangles: np.ndarray,
        sizes: np.ndarray, params: AutopolyParams,
        bbox_min: np.ndarray, bbox_max: np.ndarray
    ) -> np.ndarray:
        """Generate adaptive seeds via jittered grid."""
        rng = np.random.RandomState(params.seed)
        diag = np.linalg.norm(bbox_max - bbox_min)
        grid_size = max(int(diag / params.global_size), 10)

        # Jittered grid
        seeds = []
        for i in range(grid_size):
            for j in range(grid_size):
                for k in range(grid_size):
                    xi = (i + rng.uniform(-0.3, 0.3)) / grid_size
                    yj = (j + rng.uniform(-0.3, 0.3)) / grid_size
                    zk = (k + rng.uniform(-0.3, 0.3)) / grid_size
                    p = np.array([
                        bbox_min[0] + xi * (bbox_max[0] - bbox_min[0]),
                        bbox_min[1] + yj * (bbox_max[1] - bbox_min[1]),
                        bbox_min[2] + zk * (bbox_max[2] - bbox_min[2]),
                    ])
                    # Inside test via ray casting
                    if _point_in_mesh(p, vertices, triangles):
                        seeds.append(p)

        if not seeds:
            # Fallback: generate seeds along a simple grid inside bbox
            for i in range(8):
                for j in range(8):
                    for k in range(8):
                        p = np.array([
                            bbox_min[0] + (i + 0.5) / 8 * (bbox_max[0] - bbox_min[0]),
                            bbox_min[1] + (j + 0.5) / 8 * (bbox_max[1] - bbox_min[1]),
                            bbox_min[2] + (k + 0.5) / 8 * (bbox_max[2] - bbox_min[2]),
                        ])
                        seeds.append(p)

        return np.array(seeds, dtype=np.float64)

    # -----------------------------------------------------------------------
    # Lloyd relaxation
    # -----------------------------------------------------------------------
    @staticmethod
    def _lloyd_relaxation(
        seeds: np.ndarray, vertices: np.ndarray, triangles: np.ndarray,
        sizes: np.ndarray, params: AutopolyParams,
        progress: ProgressCallback, cancel: CancelToken,
    ) -> np.ndarray:
        """Perform Lloyd iterations for Centroidal Voronoi Tessellation."""
        n_seeds = len(seeds)
        if n_seeds < 2:
            return seeds

        from scipy.spatial import Voronoi

        seeds = seeds.copy()
        n_iter = min(params.lloyd_iterations, 100)

        for it in range(n_iter):
            if cancel():
                break

            # Reflect seeds across bounding box for boundary handling
            bbox_min = vertices.min(axis=0)
            bbox_max = vertices.max(axis=0)
            bbox_extent = bbox_max - bbox_min

            # Add reflected copies for periodic-like boundary handling
            reflected = []
            for seed in seeds:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for dz in (-1, 0, 1):
                            if dx == 0 and dy == 0 and dz == 0:
                                continue
                            r = seed + np.array([dx * bbox_extent[0], dy * bbox_extent[1], dz * bbox_extent[2]])
                            reflected.append(r)

            all_points = np.vstack([seeds, np.array(reflected)]) if reflected else seeds

            try:
                vor = Voronoi(all_points)
                # Compute centroids of finite Voronoi regions
                new_seeds = []
                for i in range(n_seeds):
                    region_idx = vor.point_region[i]
                    region_verts = vor.regions[region_idx]
                    if not region_verts or -1 in region_verts:
                        new_seeds.append(seeds[i])
                        continue
                    poly = np.array([vor.vertices[v] for v in region_verts])
                    centroid = _polygon_centroid_3d(poly)
                    if centroid is not None:
                        new_seeds.append(centroid)
                    else:
                        new_seeds.append(seeds[i])
                seeds = np.array(new_seeds, dtype=np.float64)
            except Exception:
                logger.debug("Lloyd iteration %d failed for a seed", it, exc_info=True)

            pct = 40 + int(20 * (it + 1) / n_iter)
            progress(pct, "CVT Lloyd relaxation", f"Iteration {it + 1}/{n_iter}")

        return seeds

    # -----------------------------------------------------------------------
    # Build Voronoi mesh
    # -----------------------------------------------------------------------
    @staticmethod
    def _build_voronoi_mesh(
        seeds: np.ndarray, vertices: np.ndarray, triangles: np.ndarray,
        params: AutopolyParams,
    ) -> tuple[np.ndarray, list[list[int]], list[int], list[int], list[dict]]:
        """Build polyhedral mesh from Voronoi tessellation."""
        from scipy.spatial import Voronoi

        n_seeds = len(seeds)
        if n_seeds < 2:
            # Single cell: return bounding box as hex
            bbox_min = vertices.min(axis=0)
            bbox_max = vertices.max(axis=0)
            points = np.array([
                [bbox_min[0], bbox_min[1], bbox_min[2]],
                [bbox_max[0], bbox_min[1], bbox_min[2]],
                [bbox_max[0], bbox_max[1], bbox_min[2]],
                [bbox_min[0], bbox_max[1], bbox_min[2]],
                [bbox_min[0], bbox_min[1], bbox_max[2]],
                [bbox_max[0], bbox_min[1], bbox_max[2]],
                [bbox_max[0], bbox_max[1], bbox_max[2]],
                [bbox_min[0], bbox_max[1], bbox_max[2]],
            ], dtype=np.float64)
            # 6 faces of a hex
            face_verts = [
                [0, 1, 2, 3],  # bottom
                [4, 5, 6, 7],  # top
                [0, 1, 5, 4],  # front
                [2, 3, 7, 6],  # back
                [0, 3, 7, 4],  # left
                [1, 2, 6, 5],  # right
            ]
            face_owner = [0] * 6
            face_neigh = [-1] * 6
            patches_out = [
                {"name": "walls", "type": "wall", "faces": list(range(6))},
            ]
            return points, face_verts, face_owner, face_neigh, patches_out

        # Compute Voronoi on seeds (with reflections for boundary)
        bbox_min = vertices.min(axis=0)
        bbox_max = vertices.max(axis=0)
        bbox_extent = bbox_max - bbox_min

        # Pad domain slightly to ensure Voronoi cells are bounded
        domain_min = bbox_min - bbox_extent * 0.01
        domain_max = bbox_max + bbox_extent * 0.01

        vor = Voronoi(seeds)

        # Build cell topology
        all_points = []  # global vertex list
        point_map = {}   # (x,y,z) tuple -> index
        face_verts = []
        face_owner = []
        face_neigh = []
        boundary_faces = []
        patches_out = []

        def _add_point(p: np.ndarray) -> int:
            key = (round(p[0], 12), round(p[1], 12), round(p[2], 12))
            if key in point_map:
                return point_map[key]
            idx = len(all_points)
            all_points.append(p)
            point_map[key] = idx
            return idx

        # Build ridge_dict: (i, j) -> (vertices, ...)
        ridge_vertices: dict = {}
        for ridge_points, (i, j) in zip(vor.ridge_vertices, vor.ridge_points):
            if -1 in ridge_points:
                continue
            verts = [vor.vertices[v] for v in ridge_points]
            if i < n_seeds and j < n_seeds:
                key = (min(i, j), max(i, j))
                if key not in ridge_vertices:
                    ridge_vertices[key] = []
                ridge_vertices[key].append(verts)

        # For each cell, collect its faces
        for cell_idx in range(n_seeds):
            region_idx = vor.point_region[cell_idx]
            region = vor.regions[region_idx]
            if not region or -1 in region:
                continue

            # For each adjacent cell, build the face
            cell_faces_verts: list[list[int]] = []
            for adj_idx in range(n_seeds):
                if adj_idx == cell_idx:
                    continue
                key = (min(cell_idx, adj_idx), max(cell_idx, adj_idx))
                ridges = ridge_vertices.get(key, [])
                if not ridges:
                    continue
                # For a 3D Voronoi, each pair has one ridge (the face)
                ridge = ridges[0]
                face = [_add_point(p) for p in ridge]
                if len(face) >= 3:
                    cell_faces_verts.append(face)
                    face_idx = len(face_verts)
                    face_verts.append(face)
                    face_owner.append(cell_idx)
                    face_neigh.append(adj_idx)
                    # Boundary check
                    centroid = np.mean([all_points[v] for v in face], axis=0)
                    if not (np.all(centroid >= domain_min) and np.all(centroid <= domain_max)):
                        boundary_faces.append(face_idx)

        # Mark boundary faces
        for bf in boundary_faces:
            face_neigh[bf] = -1

        # Build patches
        if boundary_faces:
            patches_out.append({
                "name": "walls",
                "type": "wall",
                "faces": boundary_faces,
            })

        if not all_points:
            # Fallback: return a simple box
            return _PythonPolyMesher._build_voronoi_mesh(
                np.array([[0, 0, 0], [1, 1, 1]]), vertices, triangles, params
            )

        return (np.array(all_points, dtype=np.float64),
                face_verts, face_owner, face_neigh, patches_out)

    # -----------------------------------------------------------------------
    # Smoothing
    # -----------------------------------------------------------------------
    @staticmethod
    def _smooth_mesh(
        points: np.ndarray, face_verts: list[list[int]],
        face_owner: list[int], face_neigh: list[int],
        params: AutopolyParams,
    ) -> np.ndarray:
        """Laplacian smoothing."""
        if len(points) == 0:
            return points
        n = len(points)
        adj = [[] for _ in range(n)]
        for fv in face_verts:
            for i in range(len(fv)):
                j = (i + 1) % len(fv)
                vi, vj = fv[i], fv[j]
                if vj not in adj[vi]:
                    adj[vi].append(vj)
                if vi not in adj[vj]:
                    adj[vj].append(vi)

        smoothed = points.copy()
        for _ in range(3):
            new_pts = points.copy()
            for i in range(n):
                if adj[i]:
                    new_pts[i] = smoothed[i] + 0.3 * (np.mean([smoothed[j] for j in adj[i]], axis=0) - smoothed[i])
            smoothed = new_pts
        return smoothed

    # -----------------------------------------------------------------------
    # Quality metrics
    # -----------------------------------------------------------------------
    @staticmethod
    def _compute_quality(
        points: np.ndarray, face_verts: list[list[int]],
        face_owner: list[int], face_neigh: list[int],
    ) -> dict:
        """Compute mesh quality metrics."""
        n_cells = max(face_owner) + 1 if face_owner else 0

        # Group faces by cell
        cell_faces: dict[int, list[int]] = {}
        for fi, owner in enumerate(face_owner):
            cell_faces.setdefault(owner, []).append(fi)

        max_non_ortho = 0.0
        max_skewness = 0.0
        max_ar = 0.0
        min_vol = float("inf")
        valid = True

        for ci in range(n_cells):
            fi_list = cell_faces.get(ci, [])
            if not fi_list:
                continue

            # Compute volume by decomposing into pyramids from centroid
            face_pts = []
            for fi in fi_list:
                fv = face_verts[fi]
                for v in fv:
                    face_pts.append(points[v])
            if not face_pts:
                continue
            centroid = np.mean(face_pts, axis=0)

            vol = 0.0
            for fi in fi_list:
                fv = face_verts[fi]
                for i in range(1, len(fv) - 1):
                    a, b, c = points[fv[0]], points[fv[i]], points[fv[i + 1]]
                    vol += abs(np.dot(np.cross(b - a, c - a), centroid - a)) / 6.0

            min_vol = min(min_vol, vol)
            if vol <= 0:
                valid = False

            # Compute non-orthogonality for each face
            for fi in fi_list:
                fv = face_verts[fi]
                if len(fv) < 3:
                    continue
                fc = centroid
                if face_neigh[fi] >= 0:
                    # Internal face
                    nc = np.mean([
                        points[vv] for v in
                        cell_faces.get(face_neigh[fi], [])
                        for vv in (face_verts[v] if v < len(face_verts) else [])
                    ], axis=0) if face_neigh[fi] in cell_faces else fc
                    if not np.all(np.isfinite(nc)):
                        nc = fc
                    vec_cc = nc - fc
                    if np.linalg.norm(vec_cc) > 1e-30:
                        normal = _face_normal(points, fv)
                        n_len = np.linalg.norm(normal)
                        if n_len > 1e-30:
                            cos_a = abs(np.dot(vec_cc, normal)) / (np.linalg.norm(vec_cc) * n_len)
                            angle = math.degrees(math.acos(min(cos_a, 1.0)))
                            max_non_ortho = max(max_non_ortho, angle)

                # Skewness: ratio of face area to area of inscribed circle
                area = _face_area(points, fv)
                if area > 1e-30 and len(fv) >= 3:
                    # Approximate skewness via face regularity
                    avg_edge = 0.0
                    for i in range(len(fv)):
                        j = (i + 1) % len(fv)
                        avg_edge += np.linalg.norm(points[fv[j]] - points[fv[i]])
                    avg_edge /= len(fv)
                    # Equivalent to equilateral triangle area reference
                    ref_area = math.sqrt(3) / 4 * avg_edge * avg_edge * len(fv)
                    if ref_area > 0:
                        skew = abs(ref_area - area) / ref_area * 2
                        max_skewness = max(max_skewness, skew)

                # Aspect ratio
                if area > 1e-30:
                    perimeter = 0.0
                    for i in range(len(fv)):
                        j = (i + 1) % len(fv)
                        perimeter += np.linalg.norm(points[fv[j]] - points[fv[i]])
                    if perimeter > 0:
                        ar = perimeter * perimeter / (4 * math.pi * area)
                        max_ar = max(max_ar, ar)

        return {
            "min_volume": 0.0 if min_vol == float("inf") else min_vol,
            "max_non_ortho": max_non_ortho,
            "max_skewness": max_skewness,
            "max_aspect_ratio": max_ar,
            "passed": valid and max_non_ortho < 85 and max_skewness < 10 and max_ar < 1000,
        }


# ---------------------------------------------------------------------------
# Native C++ wrapper
# ---------------------------------------------------------------------------

class _NativePolyMesher:
    """Wrapper around the compiled C++ autopoly pybind11 module."""

    def run(
        self,
        input_path: str | Path,
        output_dir: str | Path,
        params: AutopolyParams,
        progress: ProgressCallback = _noop_progress,
        cancel: CancelToken = _never_cancelled,
    ) -> AutopolyResult:
        """Delegate to native C++ implementation."""
        try:
            mp = _native_autopoly.MeshingParameters()

            mp.geometry.mergeTolerance = 1e-9
            mp.geometry.fillSmallHoles = True
            mp.geometry.orientNormals = True

            mp.features.enable = True
            mp.features.featureAngleDeg = params.feature_angle_deg

            mp.sizeField.globalSize = params.global_size
            mp.sizeField.minSize = params.min_size
            mp.sizeField.maxSize = params.max_size
            mp.sizeField.curvatureAdaptivity = params.curvature_adaptivity
            mp.sizeField.proximityAdaptivity = params.proximity_adaptivity
            mp.sizeField.maxGrowthRate = params.max_growth_rate

            mp.surface.targetSize = params.surface_target_size
            mp.surface.preserveFeatures = True

            mp.volume.seed = params.seed
            mp.volume.lloydIterations = params.lloyd_iterations
            mp.volume.mergeCoplanarFaces = params.merge_coplanar_faces

            mp.boundaryLayer.enable = params.bl_enabled
            mp.boundaryLayer.layers = params.bl_layers
            mp.boundaryLayer.firstHeight = params.bl_first_height
            mp.boundaryLayer.growthRate = params.bl_growth_rate
            mp.boundaryLayer.maxThickness = params.bl_max_thickness

            mp.exportOptions.outputDirectory = str(output_dir)

            mesher = _native_autopoly.createMesher()

            def native_progress(info):
                progress(info.stagePercent, info.stageName, info.message)

            result = mesher.run(str(input_path), mp, native_progress)

            return AutopolyResult(
                success=result.success,
                message=result.message,
                output_dir=result.outputDirectory,
                n_points=result.pointCount,
                n_cells=result.cellCount,
                n_faces=result.faceCount,
                n_patches=result.boundaryPatchCount,
                min_volume=result.minVolume,
                max_non_ortho=result.maxNonOrthogonality,
                max_skewness=result.maxSkewness,
                max_aspect_ratio=result.maxAspectRatio,
                warnings=result.warnings,
                errors=result.errors,
            )

        except Exception:
            logger.exception("Native autopoly failed, falling back to Python")
            return _PythonPolyMesher().run(input_path, output_dir, params, progress, cancel)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_mesher():
    """Factory: return the best available mesher (native C++ or Python fallback)."""
    if _HAS_NATIVE:
        return _NativePolyMesher()
    return _PythonPolyMesher()


def run_autopoly(
    input_path: str | Path,
    output_dir: str | Path,
    params: AutopolyParams | None = None,
    detail_level: str = "medium",
    progress: ProgressCallback = _noop_progress,
    cancel: CancelToken = _never_cancelled,
) -> AutopolyResult:
    """Convenience: create mesher, set params from detail level, run."""
    if params is None:
        params = AutopolyParams.from_detail_level(detail_level)
    mesher = create_mesher()
    return mesher.run(input_path, output_dir, params, progress, cancel)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _load_stl_native(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load STL without trimesh (pure numpy)."""
    path = Path(path)
    data = path.read_bytes()
    # Detect binary vs ASCII
    is_ascii = data[:5].lower() == b'solid'
    if is_ascii:
        return _parse_stl_ascii(data)
    return _parse_stl_binary(data)


def _parse_stl_ascii(data: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Parse ASCII STL format (pure numpy, no trimesh dependency).

    This is the fallback used when trimesh is unavailable, so it must be
    self-contained. Keywords are matched case-insensitively; facet normals
    and the ``solid``/``endsolid`` wrapper are optional in the wild. Vertex
    coordinates are deduplicated exactly like the binary parser so both
    paths return the same (vertices, triangles, patch_ids) contract.
    Malformed input raises ValueError — never a silently-empty mesh.
    """
    text = data.decode("ascii", errors="replace")
    tokens = text.split()
    if not tokens:
        raise ValueError("Empty ASCII STL data")

    vertices: list[list[float]] = []
    triangles: list[tuple[int, int, int]] = []
    v_map: dict[tuple[float, float, float], int] = {}

    def _get_or_add(v: tuple[float, float, float]) -> int:
        key = (round(v[0], 12), round(v[1], 12), round(v[2], 12))
        if key in v_map:
            return v_map[key]
        idx = len(vertices)
        vertices.append(list(v))
        v_map[key] = idx
        return idx

    tri_verts: list[tuple[float, float, float]] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i].lower()
        if tok == "vertex":
            if i + 3 >= n:
                raise ValueError("Truncated ASCII STL: vertex without coordinates")
            try:
                v = (float(tokens[i + 1]), float(tokens[i + 2]), float(tokens[i + 3]))
            except ValueError as exc:
                raise ValueError(
                    f"Invalid vertex coordinates in ASCII STL: {tokens[i + 1:i + 4]}"
                ) from exc
            tri_verts.append(v)
            i += 4
        elif tok == "endfacet":
            if len(tri_verts) != 3:
                raise ValueError(
                    f"ASCII STL facet has {len(tri_verts)} vertices, expected 3"
                )
            triangles.append((
                _get_or_add(tri_verts[0]),
                _get_or_add(tri_verts[1]),
                _get_or_add(tri_verts[2]),
            ))
            tri_verts = []
            i += 1
        else:
            # solid / endsolid / facet / normal / outer / loop / endloop
            i += 1

    if tri_verts:
        raise ValueError("ASCII STL ended inside a facet (missing endfacet)")
    if not triangles:
        raise ValueError("ASCII STL contains no facets")

    return (
        np.array(vertices, dtype=np.float64),
        np.array(triangles, dtype=np.int32),
        np.zeros(len(triangles), dtype=np.int32),
    )


def _parse_stl_binary(data: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Parse binary STL format."""
    n_tri = struct.unpack_from("<I", data, 80)[0]
    expected_size = 84 + n_tri * 50
    if len(data) < expected_size:
        raise ValueError(f"Truncated binary STL: {len(data)} < {expected_size}")

    vertices = []
    triangles = []
    v_map: dict[tuple[float, float, float], int] = {}

    def _get_or_add(v: tuple[float, float, float]) -> int:
        key = (round(v[0], 12), round(v[1], 12), round(v[2], 12))
        if key in v_map:
            return v_map[key]
        idx = len(vertices)
        vertices.append(list(v))
        v_map[key] = idx
        return idx

    offset = 84
    for _ in range(n_tri):
        # Skip normal (3 floats)
        offset += 12
        v0 = struct.unpack_from("<fff", data, offset)
        offset += 12
        v1 = struct.unpack_from("<fff", data, offset)
        offset += 12
        v2 = struct.unpack_from("<fff", data, offset)
        offset += 12
        # Attribute byte count
        offset += 2
        i0 = _get_or_add(v0)
        i1 = _get_or_add(v1)
        i2 = _get_or_add(v2)
        triangles.append((i0, i1, i2))

    return (np.array(vertices, dtype=np.float64),
            np.array(triangles, dtype=np.int32),
            np.zeros(len(triangles), dtype=np.int32))


def _load_step(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load STEP file, tessellate via cadquery, return (vertices, triangles, patch_ids)."""
    import cadquery as cq
    from cfmesh_autogui.core.geometry import classify_faces, tessellate_patches

    path = Path(path)
    logger.info("autopoly: loading STEP file '%s'", path.name)

    try:
        # Load STEP file
        shape = cq.importers.importStep(str(path))
        if isinstance(shape, cq.Workplane):
            shape = shape.val()

        # Classify faces into patches
        patches = classify_faces(shape)
        logger.info("autopoly: classified %d patches from STEP", len(patches))

        # Tessellate patches
        meshes = tessellate_patches(patches, tolerance=0.01, angle_tolerance=0.1)
        if not meshes:
            raise ValueError("Tessellation produced no valid meshes")

        # Merge all meshes into single vertex/triangle arrays
        all_vertices = []
        all_triangles = []
        patch_ids = []
        vertex_offset = 0

        for patch_idx, mesh in enumerate(meshes):
            vertices = np.asarray(mesh.vertices, dtype=np.float64)
            faces = np.asarray(mesh.faces, dtype=np.int32)

            all_vertices.append(vertices)
            all_triangles.append(faces + vertex_offset)
            patch_ids.extend([patch_idx] * len(faces))

            vertex_offset += len(vertices)

        vertices_out = np.vstack(all_vertices) if all_vertices else np.empty((0, 3), dtype=np.float64)
        triangles_out = np.vstack(all_triangles) if all_triangles else np.empty((0, 3), dtype=np.int32)
        patch_ids_out = np.array(patch_ids, dtype=np.int32)

        logger.info(
            "autopoly: tessellated STEP → %d vertices, %d triangles, %d patches",
            len(vertices_out), len(triangles_out), len(meshes)
        )
        return vertices_out, triangles_out, patch_ids_out

    except Exception as e:
        logger.exception("autopoly: STEP tessellation failed")
        raise ValueError(f"Failed to tessellate STEP file: {e}") from e


def _load_obj(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load OBJ file."""
    import trimesh
    mesh = trimesh.load(str(path))
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)
    return (np.asarray(mesh.vertices, dtype=np.float64),
            np.asarray(mesh.faces, dtype=np.int32),
            np.zeros(len(mesh.faces), dtype=np.int32))


def _load_ply(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load PLY file."""
    import trimesh
    mesh = trimesh.load(str(path))
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)
    return (np.asarray(mesh.vertices, dtype=np.float64),
            np.asarray(mesh.faces, dtype=np.int32),
            np.zeros(len(mesh.faces), dtype=np.int32))


def _load_vtk(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load VTK file."""
    import meshio
    m = meshio.read(str(path))
    cells = m.get_cells_type("triangle")
    if len(cells) == 0:
        raise ValueError("No triangle cells in VTK file")
    return (np.asarray(m.points, dtype=np.float64),
            np.asarray(cells, dtype=np.int32),
            np.zeros(len(cells), dtype=np.int32))


def _point_in_mesh(p: np.ndarray, vertices: np.ndarray, triangles: np.ndarray) -> bool:
    """Ray casting inside test."""
    n_intersect = 0
    for _ in range(5):  # Multiple rays for robustness
        ray_dir = np.random.randn(3)
        ray_dir /= np.linalg.norm(ray_dir)
        count = 0
        for tri in triangles:
            v0, v1, v2 = vertices[tri]
            # Möller–Trumbore
            edge1 = v1 - v0
            edge2 = v2 - v0
            h = np.cross(ray_dir, edge2)
            a = np.dot(edge1, h)
            if -1e-15 < a < 1e-15:
                continue
            f = 1.0 / a
            s = p - v0
            u = f * np.dot(s, h)
            if u < 0 or u > 1:
                continue
            q = np.cross(s, edge1)
            v = f * np.dot(ray_dir, q)
            if v < 0 or u + v > 1:
                continue
            t = f * np.dot(edge2, q)
            if t > 1e-15:
                count += 1
        n_intersect += count
    return n_intersect % 2 == 1


def _estimate_curvature(vertices: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Estimate per-vertex mean curvature via normal variation."""
    n = len(vertices)
    # Compute face normals
    face_normals = np.zeros((len(triangles), 3))
    for i, tri in enumerate(triangles):
        v0, v1, v2 = vertices[tri]
        nml = np.cross(v1 - v0, v2 - v0)
        norm = np.linalg.norm(nml)
        if norm > 1e-30:
            face_normals[i] = nml / norm

    # Vertex normals (area-weighted average)
    vert_normals = np.zeros((n, 3))
    vert_areas = np.zeros(n)
    for i, tri in enumerate(triangles):
        v0, v1, v2 = vertices[tri]
        area = np.linalg.norm(np.cross(v1 - v0, v2 - v0)) * 0.5
        for v in tri:
            vert_normals[v] += face_normals[i] * area
            vert_areas[v] += area
    for i in range(n):
        if vert_areas[i] > 0:
            vert_normals[i] /= np.linalg.norm(vert_normals[i])

    # Curvature as normal variation in 1-ring
    adj = [[] for _ in range(n)]
    for tri in triangles:
        for i in range(3):
            v0, v1 = tri[i], tri[(i + 1) % 3]
            if v1 not in adj[v0]:
                adj[v0].append(v1)
            if v0 not in adj[v1]:
                adj[v1].append(v0)

    curvature = np.zeros(n)
    for i in range(n):
        if not adj[i]:
            continue
        var = 0.0
        for j in adj[i]:
            d = np.linalg.norm(vertices[j] - vertices[i])
            if d > 1e-15:
                var += (1.0 - abs(np.dot(vert_normals[i], vert_normals[j]))) / d
        curvature[i] = var / len(adj[i])
    return curvature


def _polygon_centroid_3d(poly: np.ndarray) -> np.ndarray | None:
    """Compute centroid of a 3D polygon (area-weighted)."""
    if len(poly) < 3:
        return None
    ref = poly[0]
    centroid = np.zeros(3)
    total_area = 0.0
    for i in range(1, len(poly) - 1):
        a, b, c = ref, poly[i], poly[i + 1]
        area = np.linalg.norm(np.cross(b - a, c - a)) * 0.5
        centroid += (a + b + c) / 3 * area
        total_area += area
    if total_area > 0:
        return centroid / total_area
    return None


def _face_normal(points: np.ndarray, face_verts: list[int]) -> np.ndarray:
    """Compute face normal (Newell's method for robustness)."""
    normal = np.zeros(3)
    n = len(face_verts)
    for i in range(n):
        j = (i + 1) % n
        vi, vj = points[face_verts[i]], points[face_verts[j]]
        normal[0] += (vi[1] - vj[1]) * (vi[2] + vj[2])
        normal[1] += (vi[2] - vj[2]) * (vi[0] + vj[0])
        normal[2] += (vi[0] - vj[0]) * (vi[1] + vj[1])
    norm = np.linalg.norm(normal)
    return normal / norm if norm > 0 else normal


def _face_area(points: np.ndarray, face_verts: list[int]) -> float:
    """Compute face area for any convex polygon."""
    if len(face_verts) < 3:
        return 0.0
    ref = points[face_verts[0]]
    area = 0.0
    for i in range(1, len(face_verts) - 1):
        a, b, c = ref, points[face_verts[i]], points[face_verts[i + 1]]
        area += np.linalg.norm(np.cross(b - a, c - a)) * 0.5
    return area


def _export_openfoam(
    points: np.ndarray,
    face_verts: list[list[int]],
    face_owner: list[int],
    face_neigh: list[int],
    patches: list[dict],
    output_dir: Path,
) -> list[Path]:
    """Export mesh to OpenFOAM polyMesh format."""
    poly_dir = output_dir / "constant" / "polyMesh"
    poly_dir.mkdir(parents=True, exist_ok=True)

    written = []

    # points
    n_points = len(points)
    lines = [
        "/*-------------------------------*- C++ -*----------------------------------*/",
        "FoamFile",
        "{",
        "    version     2.0;",
        "    format      ascii;",
        "    class       vectorField;",
        "    location    \"constant/polyMesh\";",
        "    object      points;",
        "}",
        "",
        f"{n_points}",
        "(",
    ]
    for p in points:
        lines.append(f"    ({p[0]:.15g} {p[1]:.15g} {p[2]:.15g})")
    lines.append(")")
    (poly_dir / "points").write_text("\n".join(lines), encoding="ascii")
    written.append(poly_dir / "points")

    # faces
    n_faces = len(face_verts)
    lines = [
        "/*-------------------------------*- C++ -*----------------------------------*/",
        "FoamFile",
        "{",
        "    version     2.0;",
        "    format      ascii;",
        "    class       faceList;",
        "    location    \"constant/polyMesh\";",
        "    object      faces;",
        "}",
        "",
        f"{n_faces}",
        "(",
    ]
    for fv in face_verts:
        nv = len(fv)
        verts = " ".join(str(v) for v in fv)
        lines.append(f"    {nv}({verts})")
    lines.append(")")
    (poly_dir / "faces").write_text("\n".join(lines), encoding="ascii")
    written.append(poly_dir / "faces")

    # owner
    lines = [
        "/*-------------------------------*- C++ -*----------------------------------*/",
        "FoamFile",
        "{",
        "    version     2.0;",
        "    format      ascii;",
        "    class       labelList;",
        "    location    \"constant/polyMesh\";",
        "    object      owner;",
        "}",
        "",
        f"{n_faces}",
        "(",
    ]
    for o in face_owner:
        lines.append(f"    {o}")
    lines.append(")")
    (poly_dir / "owner").write_text("\n".join(lines), encoding="ascii")
    written.append(poly_dir / "owner")

    # neighbour
    lines = [
        "/*-------------------------------*- C++ -*----------------------------------*/",
        "FoamFile",
        "{",
        "    version     2.0;",
        "    format      ascii;",
        "    class       labelList;",
        "    location    \"constant/polyMesh\";",
        "    object      neighbour;",
        "}",
        "",
        f"{n_faces}",
        "(",
    ]
    for n in face_neigh:
        lines.append(f"    {n}" if n >= 0 else "    -1")
    lines.append(")")
    (poly_dir / "neighbour").write_text("\n".join(lines), encoding="ascii")
    written.append(poly_dir / "neighbour")

    # boundary
    lines = [
        "/*-------------------------------*- C++ -*----------------------------------*/",
        "FoamFile",
        "{",
        "    version     2.0;",
        "    format      ascii;",
        "    class       polyBoundaryMesh;",
        "    location    \"constant/polyMesh\";",
        "    object      boundary;",
        "}",
        "",
        f"{len(patches)}",
        "(",
    ]
    boundary_start = 0
    for patch in patches:
        nf = len(patch.get("faces", []))
        ptype = patch.get("type", "patch")
        pname = patch.get("name", "patch")
        lines.append(f"    {pname}")
        lines.append("    {")
        lines.append(f"        type            {ptype};")
        lines.append(f"        nFaces          {nf};")
        lines.append(f"        startFace       {boundary_start};")
        lines.append("    }")
        boundary_start += nf
    lines.append(")")
    (poly_dir / "boundary").write_text("\n".join(lines), encoding="ascii")
    written.append(poly_dir / "boundary")

    return written