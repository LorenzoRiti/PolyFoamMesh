from __future__ import annotations

import logging
import math
import re
from typing import TypedDict
import numpy as np
import cadquery as cq
import trimesh
from pathlib import Path

logger = logging.getLogger(__name__)


class AnalysisResult(TypedDict):  # ✅ F-016
    p1: float
    p5: float
    p10: float
    p25: float
    p50: float
    p75: float
    n_samples: int
    min_thickness: float
    bbox: tuple[float, float, float]


# ------------------------------------------------------------------
# Geometry creation / loading
# ------------------------------------------------------------------
def create_test_cylinder(radius: float = 1.0, height: float = 2.0) -> cq.Workplane:
    return cq.Workplane("XY").circle(radius).extrude(height)


def load_step(filepath: Path | str) -> cq.Shape:
    filepath = Path(filepath)
    try:
        shape = cq.importers.importStep(str(filepath))
    except Exception as exc:
        raise RuntimeError(
            f"Failed to import STEP file '{filepath}'. "
            "The file may be corrupted or in an unsupported format."
        ) from exc
    if isinstance(shape, cq.Workplane):
        return shape.val()
    return shape


# Bug 1 fix: STL loader with multi-solid support.
_SOLID_RE = re.compile(r"\bsolid\s+(\S+)", re.IGNORECASE)
_ENDSOLID_RE = re.compile(r"\bendsolid\b", re.IGNORECASE)


def _stl_solid_names(text: str) -> list[str]:
    """Extract solid names from an ASCII STL file.

    Falls back to a single ['unnamed'] entry if the file is binary STL
    (trimesh will handle that path).
    """
    names = _SOLID_RE.findall(text)
    if names:
        return names
    return ["unnamed"]


def load_stl(filepath: Path | str) -> list[trimesh.Trimesh]:
    """Load an STL file as a list of trimesh meshes (one per solid).

    ASCII STL: split on `solid NAME` / `endsolid NAME` blocks so each
    named solid becomes its own patch (preserves multi-body geometry).
    Binary STL: trimesh loads it as a single mesh; we still wrap it as
    a one-element list with the patch name derived from the filename stem.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"STL file not found: {filepath}")

    # Sniff format: ASCII STL starts with "solid " on the first non-empty line
    # AND contains "facet normal" within the first 4 KB.
    try:
        head = filepath.read_text(encoding="ascii", errors="replace")[:4096].lower()
    except OSError as exc:
        raise RuntimeError(f"Failed to read STL '{filepath}': {exc}") from exc

    is_ascii = "facet normal" in head

    if not is_ascii:
        # Binary STL: trimesh handles it
        scene = trimesh.load_mesh(str(filepath), force="mesh")
        if isinstance(scene, trimesh.Scene):
            meshes = list(scene.geometry.values())
        else:
            meshes = [scene]
        stem = filepath.stem
        for m in meshes:
            m.metadata["name"] = stem
        return meshes

    # ASCII STL: read full text and split on solid blocks
    text = filepath.read_text(encoding="ascii", errors="replace")
    blocks: list[tuple[str, str]] = []
    current_name: str | None = None
    current_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        m_solid = _SOLID_RE.match(stripped)
        if m_solid:
            if current_name is not None and current_lines:
                blocks.append((current_name, "\n".join(current_lines)))
            current_name = m_solid.group(1)
            current_lines = [line]
        elif _ENDSOLID_RE.match(stripped):
            if current_lines:
                current_lines.append(line)
            if current_name is not None:
                blocks.append((current_name, "\n".join(current_lines or [])))
                current_name = None
                current_lines = []
        else:
            if current_name is not None:
                current_lines.append(line)

    if current_name is not None and current_lines:
        blocks.append((current_name, "\n".join(current_lines)))

    import tempfile
    import os

    meshes_out: list[trimesh.Trimesh] = []
    for name, body in blocks:
        # Write each solid to a temp .stl and load via trimesh.
        # This is the most reliable path: trimesh handles ASCII STL
        # with `solid NAME` headers via a real file context.
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".stl", prefix="cfm_stl_")
            try:
                with os.fdopen(fd, "w", encoding="ascii") as fh:
                    fh.write(body)
                mesh = trimesh.load_mesh(tmp_path, force="mesh")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        except Exception as exc:
            logger.warning("Failed to parse solid '%s' in %s: %s", name, filepath, exc)
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            continue
        if not isinstance(mesh, trimesh.Trimesh):
            continue
        if len(mesh.faces) == 0:
            logger.warning("Solid '%s' in %s is empty, skipping.", name, filepath)
            continue
        mesh.metadata["name"] = name
        meshes_out.append(mesh)

    if not meshes_out:
        raise RuntimeError(
            f"STL file '{filepath}' contains no valid geometry."
        )
    return meshes_out


# ------------------------------------------------------------------
# Bounding-box helpers
# ------------------------------------------------------------------
def compute_bbox_dim(meshes: list[trimesh.Trimesh]) -> float:
    """Return the maximum dimension of the geometry bounding box."""
    if not meshes:
        return 1.0
    bbox_min = np.array([np.inf, np.inf, np.inf])
    bbox_max = np.array([-np.inf, -np.inf, -np.inf])
    for mesh in meshes:
        bbox_min = np.minimum(bbox_min, mesh.vertices.min(axis=0))
        bbox_max = np.maximum(bbox_max, mesh.vertices.max(axis=0))
    dim = float(np.max(bbox_max - bbox_min))
    return max(dim, 0.001)


# V1.1: return full (dx, dy, dz) for UI domain display
def compute_bbox_full(meshes: list[trimesh.Trimesh]) -> tuple[float, float, float]:
    """Return (dx, dy, dz) of the geometry bounding box."""
    if not meshes:
        return 0.0, 0.0, 0.0
    bbox_min = np.array([np.inf, np.inf, np.inf])
    bbox_max = np.array([-np.inf, -np.inf, -np.inf])
    for mesh in meshes:
        bbox_min = np.minimum(bbox_min, mesh.vertices.min(axis=0))
        bbox_max = np.maximum(bbox_max, mesh.vertices.max(axis=0))
    return tuple(float(x) for x in bbox_max - bbox_min)


# V1.1 + post-review: feature-aware cell-size suggestion.
# Old algorithm used a single bbox-based rule (bbox/20, bbox/100) that failed
# for geometries with multi-scale features: long thin tubes got mesh cells
# that were larger than the tube diameter; sharp constrictions got cells
# larger than the constriction itself. The new algorithm samples the surface
# and measures the LOCAL thickness at N points, then derives cell sizes that
# respect both the small features and the overall domain scale.

_DETAIL_PRESETS = {
    "very_coarse": {"max_mult": 0.6,  "min_div": 2.0, "samples": 256,  "p_max": "p50", "p_min": "p10", "cells_per_curvature": 4,  "min_thick_cells": 3},
    "coarse":      {"max_mult": 0.4,  "min_div": 3.0, "samples": 384,  "p_max": "p50", "p_min": "p5",  "cells_per_curvature": 6,  "min_thick_cells": 5},
    "medium":      {"max_mult": 0.3,  "min_div": 4.0, "samples": 768,  "p_max": "p50", "p_min": "p1",  "cells_per_curvature": 10, "min_thick_cells": 8},
    "fine":        {"max_mult": 0.18, "min_div": 6.0, "samples": 1536, "p_max": "p50", "p_min": "p1",  "cells_per_curvature": 16, "min_thick_cells": 12},
    "very_fine":   {"max_mult": 0.05, "min_div": 12.0,"samples": 4096, "p_max": "p50", "p_min": "p1",  "cells_per_curvature": 32, "min_thick_cells": 30},
}


def analyze_local_thickness(
    meshes: list[trimesh.Trimesh],
    samples: int = 192,
    seed: int = 0xC0FFEE,
) -> AnalysisResult:  # ✅ F-016
    """Per-patch thickness sampling, then aggregated.

    Algorithm:
      1. For each patch, allocate samples proportional to area (with
         a floor so small patches are never starved).
      2. Sample thickness within each patch.
      3. Aggregate per-patch percentiles using a stratified rule:
           p_global[k] = sum over patches of (area_patch / area_total) * p_patch[k]
         This way, a small-area constriction (with small p10) actually
         pulls down the global p10 — even if its sample count is small.

    Why this matters: cfMesh real cases often have one big patch (e.g.
    a tube wall) and one small patch (e.g. a constriction). With a
    naive global percentiles, the constriction's small values get
    drowned in the tube's large values.
    """
    if not meshes:
        return _empty_analysis((0.0, 0.0, 0.0))

    all_verts = np.vstack([np.asarray(m.vertices) for m in meshes])
    bbox = all_verts.max(axis=0) - all_verts.min(axis=0)
    bbox_max = float(max(bbox))

    areas = np.asarray([float(m.area) for m in meshes])
    total_area = float(areas.sum())
    if total_area <= 0:
        return _empty_analysis(bbox)

    # Proportional allocation with a per-patch floor.
    min_per_patch = max(16, samples // 4)
    raw_alloc = areas / total_area * samples
    alloc = np.maximum(raw_alloc.astype(int), min_per_patch)

    # Per-patch percentiles
    per_patch_percentiles: list[tuple[np.ndarray, float]] = []  # (percentiles, weight)
    total_n = 0
    for mesh, n in zip(meshes, alloc):
        n = max(int(n), 16)
        thicknesses = _sample_thickness_one_mesh(mesh, n, bbox_max, seed)
        if not thicknesses:
            continue
        arr = np.asarray(thicknesses)
        pcts = np.percentile(arr, [1, 5, 10, 25, 50, 75])
        per_patch_percentiles.append((pcts, float(mesh.area)))
        total_n += len(thicknesses)

    if not per_patch_percentiles:
        return _empty_analysis(bbox)

    # Weighted average of per-patch percentiles (weight = patch area).
    # For percentile values that should be MINIMIZED (p5, p10) we take
    # the minimum across patches — so a single small patch with small
    # thickness wins. For p25/p50/p75 we take the weighted average.
    all_pcts = np.stack([p[0] for p in per_patch_percentiles], axis=0)  # (n_patches, 5)
    weights = np.asarray([p[1] for p in per_patch_percentiles])
    w = weights / weights.sum()

    # min-strategy for low percentiles (small features matter most)
    p1  = float(all_pcts[:, 0].min())
    p5  = float(all_pcts[:, 1].min())
    p10 = float(all_pcts[:, 2].min())
    # weighted average for the rest
    p25 = float((all_pcts[:, 3] * w).sum())
    p50 = float((all_pcts[:, 4] * w).sum())
    p75 = float((all_pcts[:, 5] * w).sum())

    return {
        "p1": p1,
        "p5": p5,
        "p10": p10,
        "p25": p25,
        "p50": p50,
        "p75": p75,
        "n_samples": total_n,
        "min_thickness": p1,
        "bbox": (float(bbox[0]), float(bbox[1]), float(bbox[2])),
    }


def _empty_analysis(bbox) -> AnalysisResult:  # ✅ F-016
    return {
        "p1": 0.0, "p5": 0.0, "p10": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0,
        "n_samples": 0,
        "min_thickness": 0.0,
        "bbox": (float(bbox[0]), float(bbox[1]), float(bbox[2])),
    }


def _sample_thickness_one_mesh(
    mesh: trimesh.Trimesh,
    n_samples: int,
    bbox_max: float,
    seed: int,
) -> list[float]:
    """Sample *n_samples* points on *mesh* and measure local thickness.

    Rays are queried in batches against trimesh's C++-backed ray tracer
    instead of one ``intersects_location`` call per sample (the old loop
    took minutes on the 'very_fine' preset with 4096 samples). Semantics
    are identical to the old per-sample loop: cast along -normal first,
    fall back to +normal for samples that miss; keep distances in
    (eps, bbox_max * 2); same seeded sample points.
    """
    if n_samples <= 0 or mesh.area <= 0 or len(mesh.faces) == 0:
        return []
    try:
        pts, face_idx = trimesh.sample.sample_surface(mesh, n_samples, seed=seed)
    except Exception:
        return []
    if len(pts) == 0:
        return []
    normals = np.asarray(mesh.face_normals[face_idx], dtype=np.float64)
    norms = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.where(norms > 0, norms, 1.0)

    # Ignore hits essentially at the ray origin (the face we started from).
    eps = max(bbox_max * 1e-6, 1e-12)
    _BATCH = 512

    def _nearest_hits(origins: np.ndarray, directions: np.ndarray) -> np.ndarray:
        """Closest hit distance > eps per ray; 0.0 for rays without one."""
        # Starts at +inf so np.minimum.at can take the per-ray minimum
        # (starting from 0.0 would pin every ray to 0).
        out = np.full(len(origins), np.inf, dtype=np.float64)
        if len(origins) == 0:
            return np.zeros(0, dtype=np.float64)
        for lo in range(0, len(origins), _BATCH):
            o = origins[lo:lo + _BATCH]
            d = directions[lo:lo + _BATCH]
            try:
                res = mesh.ray.intersects_location(ray_origins=o, ray_directions=d)
            except Exception:
                continue
            if not res or len(res[0]) == 0:
                continue
            locs, ray_ids = res[0], res[1]
            dist = np.linalg.norm(np.asarray(locs) - o[ray_ids], axis=1)
            valid = dist > eps
            if not valid.any():
                continue
            np.minimum.at(out, lo + ray_ids[valid], dist[valid])
        return np.where(np.isinf(out), 0.0, out)

    inward = _nearest_hits(pts, -normals)
    missing = inward <= 0.0
    outward = np.zeros(len(pts), dtype=np.float64)
    if missing.any():
        outward[missing] = _nearest_hits(pts[missing], normals[missing])
    t = np.where(inward > 0.0, inward, outward)
    t = t[(t > 0.0) & (t < bbox_max * 2)]
    return t.tolist()


def sample_thickness_field(
    mesh: trimesh.Trimesh,
    n_samples: int,
    bbox_max: float,
    seed: int = 0xC0FFEE,
) -> tuple[np.ndarray, np.ndarray]:
    """Like ``_sample_thickness_one_mesh``, but returns ``(points,
    thickness)`` pairs instead of a flat list of values.

    ``analyze_local_thickness`` only ever needed global percentiles (p1,
    p5, ...), so ``_sample_thickness_one_mesh`` discards WHERE each
    thickness was measured. A per-point sizing field (see
    ``gmsh_wrapper._configure_passage_thickness_field``) needs exactly
    that spatial correspondence — a passage is narrow at a specific
    location, not "somewhere on the part" — so this is a separate
    function rather than a change to the existing one, to avoid touching
    the already-used, already-correct percentile path.

    The measurement itself is the width of the volume ``mesh`` encloses
    at each sample point (cast a ray along the inward normal, first hit
    wins; falls back to the outward direction if the inward ray misses,
    e.g. a sample near a convex corner). For a CFD case this ``mesh`` is
    normally the fluid domain's boundary, so "thickness" here IS the
    local width of the flow passage — verified end-to-end on a single
    connected watertight venturi (wide-narrow-wide): reports ~1.99 at the
    wide ends (true diameter 2.0) and ~0.34 at the throat (true diameter
    ~0.30), a genuinely LOCAL field, not a per-body statistic.
    """
    if n_samples <= 0 or mesh.area <= 0 or len(mesh.faces) == 0:
        return np.zeros((0, 3)), np.zeros(0)

    eps = max(bbox_max * 1e-6, 1e-12)
    _BATCH = 512

    def _nearest_hits(origins: np.ndarray, directions: np.ndarray) -> np.ndarray:
        out = np.full(len(origins), np.inf, dtype=np.float64)
        if len(origins) == 0:
            return out
        for lo in range(0, len(origins), _BATCH):
            o = origins[lo:lo + _BATCH]
            d = directions[lo:lo + _BATCH]
            try:
                res = mesh.ray.intersects_location(ray_origins=o, ray_directions=d)
            except Exception:
                continue
            if not res or len(res[0]) == 0:
                continue
            locs, ray_ids = res[0], res[1]
            dist = np.linalg.norm(np.asarray(locs) - o[ray_ids], axis=1)
            valid = dist > eps
            if not valid.any():
                continue
            np.minimum.at(out, lo + ray_ids[valid], dist[valid])
        return out

    def _measure(pts: np.ndarray, face_idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if len(pts) == 0:
            return pts, np.zeros(0)
        normals = np.asarray(mesh.face_normals[face_idx], dtype=np.float64)
        norms = np.linalg.norm(normals, axis=1, keepdims=True)
        normals = normals / np.where(norms > 0, norms, 1.0)
        inward = _nearest_hits(pts, -normals)
        missing = ~np.isfinite(inward)
        outward = np.full(len(pts), np.inf, dtype=np.float64)
        if missing.any():
            outward[missing] = _nearest_hits(pts[missing], normals[missing])
        t = np.where(np.isfinite(inward), inward, outward)
        valid = np.isfinite(t) & (t > 0.0) & (t < bbox_max * 2)
        return pts[valid], t[valid]

    try:
        pts1, face_idx1 = trimesh.sample.sample_surface(mesh, n_samples, seed=seed)
    except Exception:
        return np.zeros((0, 3)), np.zeros(0)
    pts1, t1 = _measure(pts1, face_idx1)
    if len(pts1) == 0:
        return pts1, t1

    # Densification pass: a uniform-by-AREA sample (trimesh's default)
    # under-represents a narrow, low-area feature relative to the rest of
    # the part — a thin fin or a tight throat can end up with only a
    # handful of samples, so the true local minimum thickness there is
    # easily missed even though the feature itself was "seen". Take the
    # faces behind the thinnest quartile of pass 1 and resample THOSE
    # specifically (plus their immediate face-adjacency neighbours, so
    # the resample isn't confined to a single triangle), independent of
    # their share of total surface area. This refines the estimate where
    # it's already known to be thin; it does not find an entirely
    # unsampled thin region pass 1 missed outright — full-coverage
    # guarantees would need octree/medial-axis sampling (see the
    # `passage-thickness field only for STL` commit's near-term LFS plan).
    try:
        thin_cutoff = np.percentile(t1, 25)
        thin_faces = np.unique(face_idx1[t1 <= thin_cutoff])
        if len(thin_faces):
            adjacency = mesh.face_adjacency
            adj_mask = np.isin(adjacency[:, 0], thin_faces) | np.isin(adjacency[:, 1], thin_faces)
            neighbour_faces = adjacency[adj_mask].ravel()
            dense_faces = np.unique(np.concatenate([thin_faces, neighbour_faces]))
            n_extra = min(n_samples, max(len(dense_faces) * 8, 32))
            face_areas = mesh.area_faces[dense_faces]
            face_areas = np.where(face_areas > 0, face_areas, face_areas.mean() or 1.0)
            rng = np.random.default_rng(seed)
            picks = rng.choice(dense_faces, size=n_extra, p=face_areas / face_areas.sum())
            tri = mesh.triangles[picks]
            r1 = rng.random(n_extra)
            r2 = rng.random(n_extra)
            sqrt_r1 = np.sqrt(r1)
            bary = np.stack([1 - sqrt_r1, sqrt_r1 * (1 - r2), sqrt_r1 * r2], axis=1)
            pts2 = np.einsum("ij,ijk->ik", bary, tri)
            pts2, t2 = _measure(pts2, picks)
        else:
            pts2, t2 = np.zeros((0, 3)), np.zeros(0)
    except Exception:
        pts2, t2 = np.zeros((0, 3)), np.zeros(0)

    if len(pts2):
        return np.vstack([pts1, pts2]), np.concatenate([t1, t2])
    return pts1, t1


def check_watertight(meshes: list[trimesh.Trimesh]) -> tuple[bool, int, str]:
    """Pre-flight check: do the patches together bound a closed volume?

    cfMesh needs a closed domain. Handed an open surface it does not fail — it
    happily produces a small nonsense mesh that leaks into the surroundings, so
    the user only discovers the problem much later (or never). Catching it here
    turns a silent bad result into an early, actionable message.

    Returns (is_watertight, n_open_boundary_edges, human_readable_message).
    """
    if not meshes:
        return False, 0, "No geometry loaded."
    try:
        combined = trimesh.util.concatenate(meshes)
        combined.merge_vertices()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Watertight check could not run: %s", exc)
        return True, 0, "Watertight check skipped (could not combine patches)."

    if combined.is_watertight:
        return True, 0, "Watertight: geometry bounds a closed volume."

    n_open = 0
    try:
        import trimesh.grouping as _grouping

        n_open = len(
            combined.edges[_grouping.group_rows(combined.edges_sorted, require_count=1)]
        )
    except Exception:  # pragma: no cover - defensive
        pass

    return False, n_open, (
        f"Geometry is not watertight: {n_open} open boundary edges. "
        "cfMesh needs a fully closed domain — meshing it would leak and produce "
        "a meaningless mesh. Look for gaps between patches or missing faces."
    )


# ------------------------------------------------------------------
# Curvature-aware cell sizing
# ------------------------------------------------------------------
def compute_curvature_sizing(
    meshes: list[trimesh.Trimesh],
    cells_per_curvature: float = 8,
    bbox_max: float = 1.0,
) -> float | None:
    """Estimate cell size needed to resolve surface curvature.

    Uses face adjacency dihedral angles to detect curved regions (1-30°)
    and derives a cell size that puts ``cells_per_curvature`` cells across
    the minimum radius of curvature. Returns ``None`` when no curved
    surfaces are detected (shape is entirely flat-faceted).
    """
    if not meshes:
        return None

    min_curvature_cell = float("inf")
    has_curvature = False

    for mesh in meshes:
        if len(mesh.faces) == 0:
            continue
        adj_angles = np.asarray(mesh.face_adjacency_angles, dtype=np.float64)
        curved_mask = (adj_angles > np.radians(1)) & (adj_angles < np.radians(30))
        curved_angles = adj_angles[curved_mask]

        if len(curved_angles) == 0:
            continue

        has_curvature = True
        mean_angle = float(curved_angles.mean())

        edges = np.asarray(mesh.face_adjacency_edges, dtype=np.int64)[curved_mask]
        edge_verts = np.asarray(mesh.vertices, dtype=np.float64)[edges]
        edge_lengths = np.linalg.norm(edge_verts[:, 0, :] - edge_verts[:, 1, :], axis=1)
        mean_edge = float(edge_lengths.mean())

        if mean_angle > 1e-6:
            radius = mean_edge / (2.0 * np.sin(mean_angle / 2.0))
            curvature_cell = radius / cells_per_curvature
            min_curvature_cell = min(min_curvature_cell, curvature_cell)

    if not has_curvature:
        return None

    return min(min_curvature_cell, bbox_max / 4.0)


def compute_patch_cell_sizes(
    meshes: list[trimesh.Trimesh],
    detail: str = "medium",
    seed: int = 0xC0FFEE,
) -> tuple[dict[str, float], float | None, float | None]:
    """Compute per-patch cell sizes + boundary refinement parameters.

    For each mesh patch, samples its local thickness and derives a cell
    size that resolves it. Returns:
      - patch_cell_sizes: {name: cell_size} — only for patches whose
        suggested size is SMALLER than the global max.
      - boundary_cell_size: suggested cell size near walls (None if
        boundary refinement not needed).
      - boundary_refinement_thickness: distance (m) for boundary
        refinement (None if not needed).
    """
    if not meshes:
        return {}, None, None

    preset = _DETAIL_PRESETS.get(detail, _DETAIL_PRESETS["medium"])
    all_verts = np.vstack([np.asarray(m.vertices) for m in meshes])
    bbox = all_verts.max(axis=0) - all_verts.min(axis=0)
    bbox_max = float(max(bbox))

    patch_sizes: dict[str, float] = {}
    for mesh in meshes:
        name = mesh.metadata.get("name", "patch")
        n_samples = max(preset["samples"] // len(meshes), 8)
        thicknesses = _sample_thickness_one_mesh(mesh, n_samples, bbox_max, seed)
        if not thicknesses:
            continue
        p_local = np.percentile(np.asarray(thicknesses), [50, 10])
        p50 = float(p_local[0])
        p10 = float(p_local[1])
        if p50 <= 0:
            continue
        # patchCellSize applies to the WHOLE patch, so it must describe that
        # patch's typical scale (p50), not its thinnest spot. Sizing it from
        # p10 meant a single thin feature forced fine cells across the entire
        # patch — on a one-patch geometry that is the entire domain (a 1 mm gap
        # in a 0.1 m box drove the mesh to millions of cells). The thin feature
        # is handled by minCellSize instead.
        cell_size = min(p50 * preset["max_mult"], bbox_max / 8.0)
        cell_size = max(cell_size, 0.001)
        patch_sizes[name] = round(max(cell_size, 0.0005), 6)

    # Boundary refinement: the thinnest feature across all patches,
    # used to set boundaryCellSize and boundaryCellSizeRefinementThickness.
    all_thicknesses: list[float] = []
    for mesh in meshes:
        ts = _sample_thickness_one_mesh(
            mesh,
            n_samples=max(len(mesh.faces) // 20, 16),
            bbox_max=bbox_max,
            seed=seed,
        )
        all_thicknesses.extend(ts)
    if all_thicknesses:
        p10 = float(np.percentile(np.asarray(all_thicknesses), 10))
        bc_size = round(min(p10 / 2.0, bbox_max / 16.0), 6)
        bc_size = max(bc_size, 0.0005)
        # Boundary refinement thickness: use the larger of:
        # - 80% of local thickness (for thin features)
        # - 3x the maxCellSize (for smooth transition from walls to core)
        # The larger value ensures smooth spatial transition even for
        # geometries where thin features are absent.
        bc_thick = max(p10 * 0.8, bbox_max * 0.1)
        bc_thick = round(bc_thick, 6)
        boundary_cell_size = bc_size
        boundary_refinement_thickness = bc_thick if bc_thick > 0.001 else None
    else:
        boundary_cell_size = None
        boundary_refinement_thickness = None

    return patch_sizes, boundary_cell_size, boundary_refinement_thickness


def suggest_cell_sizes(
    meshes: list[trimesh.Trimesh] | None = None,
    detail: str = "medium",
    bbox_max_dim: float | None = None,
) -> tuple[float, float]:
    """Feature-aware cell size suggestion.

    Args:
        meshes: list of trimesh patches. If provided, used to sample local
            thickness and pick cell sizes that respect small features.
            If None, falls back to the old bbox-based algorithm.
        detail: 'coarse' | 'medium' | 'fine' — controls how aggressively
            the algorithm resolves small features.
        bbox_max_dim: explicit bbox max (required if meshes is None).

    Returns:
        (max_cell_size, min_cell_size) in metres.
    """
    preset = _DETAIL_PRESETS.get(detail, _DETAIL_PRESETS["medium"])

    if meshes is None:
        if bbox_max_dim is None or bbox_max_dim <= 0.0:
            return 0.05, 0.01
        s_max = round(bbox_max_dim / 20.0, 6)
        s_min = round(bbox_max_dim / 100.0, 6)
        return max(s_max, 0.001), max(s_min, 0.0001)

    analysis = analyze_local_thickness(meshes, samples=preset["samples"])
    bbox_max = max(analysis["bbox"]) if analysis["bbox"] else 0.0
    if bbox_max <= 0.0:
        return 0.05, 0.01

    # If sampling failed, fall back to bbox
    if analysis["n_samples"] == 0:
        s_max = round(bbox_max / 20.0, 6)
        s_min = round(bbox_max / 100.0, 6)
        return max(s_max, 0.001), max(s_min, 0.0001)

    # Use the chosen percentile of the local-thickness distribution
    # for the "max cell" and a lower percentile for the "min cell".
    # Multipliers from the preset scale how aggressive the suggestion is.
    p_max = analysis[preset["p_max"]]
    p_min = analysis[preset["p_min"]]

    s_max = p_max * preset["max_mult"]
    s_min = p_min / preset["min_div"]

    # Curvature-aware sizing: if the surface has tight curvature,
    # cap s_max to ensure enough cells per curvature radius.
    curvature_cell = compute_curvature_sizing(
        meshes, cells_per_curvature=preset.get("cells_per_curvature", 8),
        bbox_max=bbox_max,
    )
    if curvature_cell is not None:
        s_max = min(s_max, curvature_cell)

    # Clamp s_max: never larger than bbox/8 (so a single long domain
    # doesn't blow up the cell count). The percentile-based ratio above
    # keeps s_min < s_max for typical multi-scale geometries, but for
    # blocky/compact shapes (thickness ~ bbox size) the bbox/8 clamp can
    # pull s_max below the still-unclamped s_min — so s_min must be
    # re-clamped relative to the FINAL s_max, or the UI ends up with
    # min > max (e.g. a simple cube triggers this on every detail level).
    s_max = min(s_max, bbox_max / 8.0)
    s_max = max(s_max, 0.001)

    # min_thick_cells: guarantee at least N cells through the smallest
    # local thickness so thin features are always resolved regardless
    # of the detail preset (which was producing ~300-cell meshes before).
    min_thick = analysis.get("min_thickness", bbox_max)
    min_thick_cells = preset.get("min_thick_cells", 8)
    s_min_from_thick = min_thick / min_thick_cells if min_thick > 0 else s_min
    s_min = max(s_min, 0.0001)
    s_min = min(s_min, s_max / 2.0, s_min_from_thick)

    # Snap both sizes to the cfMesh octree level grid so they are not
    # silently rounded by cartesianMesh. The max cell snaps to a coarser
    # level (larger size), the min cell to a finer level (smaller size).
    s_max_snapped, _ = snap_to_octree_level(s_max, bbox_max)
    s_min_snapped, _ = snap_to_octree_level(s_min, bbox_max)
    s_min_snapped = min(s_min_snapped, s_max_snapped / 2.0)

    # Round to 4 decimals with a 49% (not 50%) margin, matching the cell-size
    # spinboxes' own precision — see validate_cell_sizes() for why: an exact
    # 50% ratio computed with more precision than the spinbox can display
    # rounds each side independently on setValue(), and can land the stored
    # pair just over 50%, hard-blocking meshing with no visible cause.
    s_max_final = round(max(s_max_snapped, 0.001), 4)
    s_min_final = round(max(min(s_min_snapped, s_max_final * 0.49), 0.0001), 4)
    return s_max_final, s_min_final


# V1.1: ------------------------------------------------------------------
# Unit conversion & scaling helpers for CAD import
# ------------------------------------------------------------------
_UNIT_SCALE = {
    "m": 1.0,
    "mm": 0.001,
    "cm": 0.01,
    "inch": 0.0254,
    "in": 0.0254,
    "ft": 0.3048,
}


def unit_to_scale(unit: str) -> float:
    """Convert a CAD unit string to a scale factor (CAD unit → metres)."""
    return _UNIT_SCALE.get(unit.lower(), 1.0)


def scale_meshes(meshes: list[trimesh.Trimesh], factor: float) -> list[trimesh.Trimesh]:
    """Multiply all vertex coordinates in-place by *factor*. Returns the input list."""
    if abs(factor - 1.0) < 1e-9:
        return meshes
    for mesh in meshes:
        mesh.vertices *= factor
    return meshes


def cfmesh_cell_budget(max_cells_override: int | None = None) -> dict:
    """RAM-aware cell-count ceiling for cfMesh/cartesianMesh — the
    equivalent of ``gmsh_wrapper._hardware_budget`` for the tet/GMSH
    path, which had NO counterpart on the cfMesh side (``validate_cell_sizes``
    only ever clamped against the bounding box, never against the machine's
    actual RAM). Without this, a user requesting a fine ``maxCellSize`` on a
    large domain can ask cfMesh for a mesh that doesn't fit in memory with no
    warning until the process is killed or thrashes the machine.

    Bytes-per-cell: cartesianMesh's own reference numbers put a real
    14.5M-cell mesh at ~11 GB peak, i.e. ~760 bytes/cell — used here
    directly rather than guessed, the same way GMSH's own bytes/cell was
    re-derived from first principles instead of an old, much too
    pessimistic constant (see ``_hardware_budget``'s comment).

    Same floor logic as the GMSH budget: the ceiling is
    max(50% of currently-free RAM, 25% of total RAM), so a machine that's
    merely busy right now (browser open, previous mesh still in the
    viewer) doesn't silently get a much coarser budget than the same
    idle machine would.

    cartesianMesh actually RUNS inside the WSL2 VM, not on the Windows
    host — and WSL2 defaults to a memory cap of its own (50% of host RAM,
    or whatever ``.wslconfig`` sets), independent of the host's real
    total. Capping only against the HOST'S RAM can let a request through
    that the host has room for but the WSL2 VM does not — measured live:
    a 32 GB host with WSL2 capped at ~15 GB total / ~9 GB available let
    an 8-11M cell request past a host-only budget, which then failed
    inside WSL2 during parallel decomposition/reconstruction. The
    smaller of the host-based and WSL-based budgets wins. WSL's own
    per-rank decomposition/reconstruction overhead on TOP of the base
    mesh size (each of N ranks holds a partition, then
    reconstructParMesh briefly needs the full mesh again) is NOT
    separately modeled here — this budget bounds the base mesh, not the
    transient peak a highly-parallel run adds on top of it.
    """
    from cfmesh_autogui.core.hardware_budget import (
        available_ram_bytes, total_ram_bytes, wsl_ram_bytes,
    )

    is_explicit_target = bool(max_cells_override and max_cells_override > 0)
    if is_explicit_target:
        return {"max_cells": int(max_cells_override), "explicit": True}

    bytes_per_cell = 760.0
    by_available = (available_ram_bytes() * 0.5) / bytes_per_cell
    by_total = (total_ram_bytes() * 0.25) / bytes_per_cell
    max_cells = max(200_000, int(max(by_available, by_total)))

    wsl_stats = wsl_ram_bytes()
    wsl_max_cells = None
    if wsl_stats is not None:
        wsl_total, wsl_available = wsl_stats
        wsl_by_available = (wsl_available * 0.5) / bytes_per_cell
        wsl_by_total = (wsl_total * 0.25) / bytes_per_cell
        wsl_max_cells = max(200_000, int(max(wsl_by_available, wsl_by_total)))
        max_cells = min(max_cells, wsl_max_cells)

    return {
        "max_cells": max_cells, "explicit": False,
        "host_max_cells": int(max(by_available, by_total)),
        "wsl_max_cells": wsl_max_cells,
    }


def coarsen_for_ram_budget(
    max_cell: float,
    domain_volume: float,
    max_cells_override: int | None = None,
) -> tuple[float, list[str]]:
    """Coarsen ``max_cell`` so ``domain_volume / max_cell**3`` stays within
    ``cfmesh_cell_budget()`` — the RAM safety net, standalone so callers
    that already have their own bbox clamp (or none at all, like
    ``meshdict_gen.write_meshdict`` reading a surface STL directly) can
    apply just this check without ``validate_cell_sizes``'s bbox/2 clamp
    running a second time on top of whatever the caller already did.

    Returns (safe_max, warnings) — ``safe_max`` is unchanged when the
    estimate is already within budget.
    """
    warnings: list[str] = []
    if domain_volume <= 0 or max_cell <= 0:
        return max_cell, warnings
    budget = cfmesh_cell_budget(max_cells_override)
    est_cells = domain_volume / (max_cell ** 3)
    if est_cells <= budget["max_cells"]:
        return max_cell, warnings
    from cfmesh_autogui.core.hardware_budget import available_ram_bytes, fmt_bytes

    new_max = (domain_volume / budget["max_cells"]) ** (1.0 / 3.0)
    warnings.append(
        f"maxCellSize {max_cell:.5f} would produce ~{est_cells:,.0f} "
        f"cells, exceeding the RAM-based budget of "
        f"{budget['max_cells']:,} cells (free RAM "
        f"{fmt_bytes(available_ram_bytes())}). Auto-coarsening to "
        f"{new_max:.5f} (~{domain_volume / (new_max ** 3):,.0f} cells)."
    )
    logger.warning(warnings[-1])
    return new_max, warnings


# FIX: safeguard — prevent cartesianMesh smoothing-loop by clamping cell sizes
def validate_cell_sizes(
    bbox_max_dim: float,
    max_cell: float,
    min_cell: float,
    domain_volume: float | None = None,
    max_cells_override: int | None = None,
) -> tuple[float, float, list[str]]:
    """Clamp cell sizes relative to the bounding box to avoid degenerate meshes.

    ``domain_volume`` (bbox dx*dy*dz, or the true enclosed volume if known):
    when given, additionally estimates the cell count cfMesh would produce
    (``domain_volume / max_cell**3``) and coarsens ``max_cell`` further if
    it would exceed ``cfmesh_cell_budget(max_cells_override)`` — the RAM
    safety net the bbox-only clamp below never provided. Skipped (no-op)
    when ``domain_volume`` is not supplied, so existing callers that don't
    pass it keep their exact previous behaviour.

    Returns (safe_max, safe_min, warnings).
    Raises ValueError if clamping produces non-positive values.
    """
    warnings: list[str] = []

    safe_max = max_cell
    if safe_max > bbox_max_dim / 2.0:
        safe_max = bbox_max_dim / 2.0
        warnings.append(
            f"maxCellSize clamped from {max_cell:.4f} to {safe_max:.4f} "
            f"(bbox max dim = {bbox_max_dim:.4f})"
        )
        logger.warning(warnings[-1])

    if domain_volume is not None and domain_volume > 0 and safe_max > 0:
        safe_max, ram_warnings = coarsen_for_ram_budget(
            safe_max, domain_volume, max_cells_override,
        )
        warnings.extend(ram_warnings)

    safe_min = min_cell
    if safe_min > safe_max / 2.0:
        safe_min = safe_max / 2.0
        warnings.append(
            f"minCellSize clamped from {min_cell:.4f} to {safe_min:.4f} "
            f"(max/2 = {safe_max / 2.0:.4f})"
        )
        logger.warning(warnings[-1])

    # Match the GUI precision while preserving micron/nanometre-scale meshes.
    # Older code always rounded to four decimals; on a millimetre part that
    # turned both derived values into 0.0000. Use four decimals for ordinary
    # metre-scale values and significant extra digits below 1e-4.
    smallest = max(min(abs(safe_max), abs(safe_min)), 1e-15)
    decimals = 4
    if smallest < 1e-4:
        decimals = min(12, max(7, int(math.ceil(-math.log10(smallest))) + 2))
    safe_max = round(safe_max, decimals)
    safe_min = round(min(safe_min, safe_max * 0.49), decimals)

    if safe_max > bbox_max_dim / 10.0:
        warnings.append(
            f"maxCellSize ({safe_max:.4f}) > bbox/10 ({bbox_max_dim / 10.0:.4f}). "
            "Cells are very coarse — small features or holes in the geometry may be degraded."
        )
        logger.warning(warnings[-1])

    if safe_min <= 0.0 or safe_max <= 0.0:
        raise ValueError(
            f"Invalid cell sizes after clamping: max={safe_max}, min={safe_min}. "
            "Check geometry scale."
        )

    return safe_max, safe_min, warnings


def snap_to_octree_level(
    cell_size: float,
    bbox_dim: float,
    ratio_max: float = 1.5,
) -> tuple[float, int]:
    """Snap a cell size to the nearest cfMesh octree level.

    cfMesh uses an octree where the root box covers the geometry with some
    padding.  Cell sizes are ``root_size / 2^k`` for integer k.  A size that
    is not exactly ``root_size / 2^k`` is silently rounded to the nearest
    valid level, so the requested size is not what the user gets.

    This function estimates the root box size as ``bbox_dim * ratio_max``
    (empirical default 1.5) and returns the closest valid size and its
    octree level.

    Args:
        cell_size: Desired cell size in metres.
        bbox_dim: Maximum bounding-box dimension of the geometry.
        ratio_max: Factor to estimate root box size (empirical, default 1.5
            based on cfMesh v2512 behaviour).

    Returns:
        ``(effective_size, level)`` where ``effective_size`` is the size
        that cfMesh actually uses, and ``level`` is the octree level (>= 1).
    """
    root_est = max(bbox_dim * ratio_max, 1e-6)
    level_f = np.log2(root_est / max(cell_size, 1e-12))
    level = max(int(round(level_f)), 1)
    snapped = root_est / (2 ** level)
    return round(snapped, 6), level


# V1.1: pre-mesh cell count estimation (F1)
def compute_volume(meshes: list[trimesh.Trimesh]) -> float:
    """Return the summed volume of all patch meshes.

    trimesh may emit warnings for non-watertight meshes; those are logged
    at debug level and skipped. The result is clamped to >= 0.
    """
    total = 0.0
    for mesh in meshes:
        try:
            total += float(mesh.volume)
        except Exception as exc:
            logger.debug("Volume computation failed for a patch: %s", exc)
    if not np.isfinite(total) or total < 0.0:
        logger.warning("Computed volume was non-finite (%r); falling back to 0.", total)
        return 0.0
    return total


def estimate_cell_count(
    volume: float, max_cell: float, min_cell: float,
) -> tuple[int, int, int]:
    """Estimate the final cell count before meshing.

    Returns (low, nominal, high) with a +/-40%% margin around the nominal
    estimate based on the average cell size. A floor of 100 is enforced
    on all three values.
    """
    avg_cell = (max_cell + min_cell) / 2.0
    if avg_cell <= 0.0:
        logger.warning(
            "estimate_cell_count: non-positive avg cell (%.6f); using floor.", avg_cell
        )
        return 100, 100, 100
    if volume <= 0.0:
        logger.info("estimate_cell_count: zero/negative volume; returning floor.")
        return 100, 100, 100
    n_est = max(100, int(volume / (avg_cell ** 3)))
    margin = int(n_est * 0.4)
    return max(100, n_est - margin), n_est, n_est + margin


# near-wall shell depth, in local-cell-size units, used by
# estimate_cell_count_geometric below (~ a few boundary-layer-ish cells).
_SHELL_DEPTH_CELLS = 4.0


def estimate_cell_count_geometric(
    meshes: list[trimesh.Trimesh],
    volume: float,
    core_cell: float,
    patch_sizes: dict[str, float] | None = None,
) -> tuple[int, int, int]:
    """Geometry-aware cell-count estimate: near-wall refinement + bulk core.

    ``estimate_cell_count`` divides the WHOLE volume by one average cell
    size, which is blind to local refinement: a small-area, finely-sized
    patch (e.g. a 0.04 m wall on a 3 m bbox) occupies a tiny fraction of the
    volume by geometry but a huge fraction of the CELLS, because cells there
    are tiny. That mismatch was the source of >10x estimate errors
    (documented at the call site in main_window.py: 19K estimated vs 2.3M
    actual on exactly this pattern).

    Two-zone model instead, using the real per-patch surface area (already
    computed from the loaded trimesh patches, no extra meshing pass needed):
      - near-wall shell per patch: a slab of thickness
        ``_SHELL_DEPTH_CELLS * local_cell_size``, volume = area * thickness,
        cells = shell_volume / local_cell_size**3
      - core: whatever volume is left over (>= 0), cells = core_volume /
        core_cell**3

    Falls back to the plain volume/core_cell**3 estimate when there is no
    per-patch sizing information (``patch_sizes`` empty/None) — same
    behaviour as before for geometries without local refinement.

    Returns (low, nominal, high) with a +/-20%% margin (tighter than the
    +/-40%% blind estimate, since this one is grounded in real surface
    area rather than a single global average).
    """
    if core_cell <= 0.0 or volume <= 0.0:
        return estimate_cell_count(volume, core_cell, core_cell)
    if not patch_sizes:
        return estimate_cell_count(volume, core_cell, core_cell)

    shell_volume = 0.0
    shell_cells = 0.0
    for mesh in meshes:
        name = mesh.metadata.get("name", "patch")
        local_size = patch_sizes.get(name)
        if not local_size or local_size <= 0.0:
            continue
        try:
            area = float(mesh.area)
        except Exception as exc:
            logger.debug("estimate_cell_count_geometric: area failed for a patch: %s", exc)
            continue
        if not np.isfinite(area) or area <= 0.0:
            continue
        v_shell = min(area * _SHELL_DEPTH_CELLS * local_size, volume - shell_volume)
        v_shell = max(v_shell, 0.0)
        shell_volume += v_shell
        shell_cells += v_shell / (local_size ** 3)

    core_volume = max(volume - shell_volume, 0.0)
    core_cells = core_volume / (core_cell ** 3)
    n_est = max(100, int(shell_cells + core_cells))
    margin = int(n_est * 0.2)
    return max(100, n_est - margin), n_est, n_est + margin


# ------------------------------------------------------------------
# Face classification
# ------------------------------------------------------------------
def _face_normal(face: cq.Face) -> np.ndarray:
    v = face.normalAt()
    return np.array([v.x, v.y, v.z])


def _axis_from_str(axis: str) -> int:
    return {"X": 0, "Y": 1, "Z": 2}[axis.upper()]


def _classify_by_axis(
    faces: list[cq.Face],
    axis_idx: int,
    tol: float,
    shape: cq.Shape,
) -> tuple[list[cq.Face], list[cq.Face], list[cq.Face]]:
    coords = [f.Center().toTuple()[axis_idx] for f in faces]
    c_min = min(coords)
    c_max = max(coords)
    h_scale = height_scale(shape)

    inlet_faces: list[cq.Face] = []
    outlet_faces: list[cq.Face] = []
    wall_faces: list[cq.Face] = []

    for f in faces:
        center = f.Center()
        center_arr = np.array([center.x, center.y, center.z])
        if f.geomType() == "PLANE":
            normal = _face_normal(f)
            normal_mag = np.linalg.norm(normal)
            if normal_mag < 1e-9:
                wall_faces.append(f)
                continue
            normal /= normal_mag
            component = normal[axis_idx]
            if abs(abs(component) - 1.0) < tol:
                if component < 0 and abs(center_arr[axis_idx] - c_min) < tol * h_scale:
                    inlet_faces.append(f)
                elif component > 0 and abs(center_arr[axis_idx] - c_max) < tol * h_scale:
                    outlet_faces.append(f)
                else:
                    wall_faces.append(f)
            else:
                wall_faces.append(f)
        else:
            wall_faces.append(f)

    return inlet_faces, outlet_faces, wall_faces


def classify_faces_auto(
    shape: cq.Shape,
    inlet_axis: str = "Z",
    tol: float = 0.01,
) -> list[tuple[str, list[cq.Face]]]:
    faces = list(shape.Faces())
    if len(faces) <= 1:
        return [("wall", faces)]

    axis_idx = _axis_from_str(inlet_axis)
    inlet_faces, outlet_faces, wall_faces = _classify_by_axis(faces, axis_idx, tol, shape)

    if not inlet_faces and not outlet_faces:
        for alt_axis in ("X", "Y", "Z"):
            if alt_axis.upper() == inlet_axis.upper():
                continue
            alt_idx = _axis_from_str(alt_axis)
            inlet_faces, outlet_faces, wall_faces = _classify_by_axis(faces, alt_idx, tol, shape)
            if inlet_faces or outlet_faces:
                break

    result: list[tuple[str, list[cq.Face]]] = []
    if inlet_faces:
        result.append(("inlet", inlet_faces))
    if outlet_faces:
        result.append(("outlet", outlet_faces))
    if wall_faces:
        result.append(("wall", wall_faces))
    if not result:
        result.append(("wall", faces))
    return result


def classify_faces(
    shape: cq.Shape,
    inlet_axis: str = "Z",
    strategy: str = "auto",
    custom_tags: dict[int, str] | None = None,
) -> list[tuple[str, list[cq.Face]]]:
    if strategy == "auto":
        return classify_faces_auto(shape, inlet_axis)
    if strategy == "custom" and custom_tags:
        result: dict[str, list[cq.Face]] = {}
        for idx, f in enumerate(shape.Faces()):
            name = custom_tags.get(idx, "wall")
            result.setdefault(name, []).append(f)
        return list(result.items())
    return [("wall", list(shape.Faces()))]


def height_scale(shape: cq.Shape) -> float:
    bbox = shape.BoundingBox()
    return max(bbox.zlen, 1.0)


# ------------------------------------------------------------------
# Tessellation
# ------------------------------------------------------------------
def tessellate_patches(
    patches: list[tuple[str, list[cq.Face]]],
    tolerance: float = 0.01,
    angle_tolerance: float = 0.1,
) -> list[trimesh.Trimesh]:
    meshes: list[trimesh.Trimesh] = []
    total = len(patches)
    failures = 0

    for name, faces in patches:
        if not faces:
            failures += 1
            logger.warning("Tessellation skipped for patch '%s': no faces assigned.", name)
            continue
        try:
            compound = cq.Compound.makeCompound(faces)
            verts, tris = compound.tessellate(tolerance, angle_tolerance)
            if len(tris) == 0:
                failures += 1
                logger.warning(
                    "Tessellation for patch '%s' produced 0 triangles (%d faces, tol=%.4f).",
                    name, len(faces), tolerance,
                )
                continue
            # CadQuery normalizes every imported STEP file to its own
            # internal working unit (millimetres) regardless of what unit
            # the source file itself declared — that normalization is
            # reliable (mature CAD kernel behaviour), so converting mm to
            # this app's internal metre convention here is always correct,
            # not a guess. Without it, a real CAD export authored in mm
            # (the normal case for mechanical parts — confirmed live: a
            # genuinely 3 m part came through as bbox=3000, then got
            # treated as 3000 m by every downstream consumer that assumes
            # metres, producing a "~45 billion cell" estimate and blocking
            # meshing entirely). Synthetic test geometry built directly
            # with cadquery calls never exposed this: dimensions typed
            # into cadquery ARE already in its native mm, so there was
            # never a real-world scale to be wrong against.
            verts_np = np.array([(v.x, v.y, v.z) for v in verts], dtype=np.float64) * 0.001
            tris_np = np.array(tris, dtype=np.int32)
            mesh = trimesh.Trimesh(vertices=verts_np, faces=tris_np, process=False)
            mesh.metadata["name"] = name
            meshes.append(mesh)
        except Exception as exc:
            failures += 1
            logger.warning(
                "Tessellation failed for patch '%s' (%d faces): %s",
                name, len(faces), exc,
            )

    if meshes and failures > 0:
        logger.warning(
            "Tessellation: %d/%d patches succeeded, %d failed.",
            len(meshes), total, failures,
        )

    if not meshes:
        raise RuntimeError(
            "Tessellation failed for all patches. "
            "The CAD geometry may be too complex or contain invalid faces."
        )

    if failures > total / 2:
        raise RuntimeError(
            f"Tessellation failed for {failures}/{total} patches (>50%%). "
            "The CAD geometry likely contains invalid or degenerate faces. "
            "Try simplifying the geometry or adjusting the tessellation tolerance."
        )

    return meshes


def load_and_tessellate(
    filepath: Path | str,
    tolerance: float = 0.01,
    angle_tolerance: float = 0.1,
) -> list[trimesh.Trimesh]:
    shape = load_step(filepath)
    patches = classify_faces(shape)
    return tessellate_patches(patches, tolerance, angle_tolerance)


# Bug 1 fix: single-entry-point geometry loader used by the GUI.
# Picks STEP or STL based on file extension, then returns trimesh meshes
# ready for tessellation / scaling / export.
def load_geometry(filepath: Path | str) -> list[trimesh.Trimesh]:
    """Load STEP or STL, returning a list of trimesh meshes (one per patch/solid).

    STEP → classify_faces → tessellate_patches (existing flow).
    STL  → load_stl (multi-solid aware, ASCII or binary).
    """
    filepath = Path(filepath)
    suffix = filepath.suffix.lower()
    if suffix in (".step", ".stp"):
        shape = load_step(filepath)
        patches = classify_faces(shape)
        return tessellate_patches(patches)
    if suffix == ".stl":
        return load_stl(filepath)
    raise ValueError(
        f"Unsupported geometry format: '{suffix}'. "
        "Supported: .step, .stp, .stl"
    )
