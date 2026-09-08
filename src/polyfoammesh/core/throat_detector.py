from __future__ import annotations

import logging
import numpy as np
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RefinementZone:
    centre: tuple[float, float, float]
    radius: float
    cell_size: float
    local_thickness: float
    significance: float
    n_samples_in_cluster: int


def _sample_thickness_field(
    meshes: list,
    n_samples_total: int = 500,
    seed: int = 0xC0FFEE,
    bbox_max: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Sample local thickness across ALL mesh surfaces.

    Returns (points, thicknesses):
      - points: (N, 3) array of sample point coordinates
      - thicknesses: (N,) array of local thickness at each point

    Delegates to ``core.geometry.sample_thickness_field`` (per mesh,
    proportioned by area) instead of this module's own former
    from-scratch ray-cast. That reimplementation cast up to 19 rays per
    sample point in a "cone" of directions around the surface normal and
    kept the SHORTEST hit across all of them — which, unlike a plain
    inward-normal ray-cast, can and did latch onto the nearest bit of
    nearby curved surface in an off-axis direction rather than the true
    opposite-wall passage width. Measured live on a venturi (wide 1.0 m
    ends, throat 0.30 m at the middle): it reported a "narrow passage" at
    the wide end near a cap edge and never found the real throat at all.
    ``geometry.sample_thickness_field`` is the SAME function this
    session's passage-thickness field for GMSH/STL and the local-feature
    densification already use, and is verified accurate on this exact
    venturi shape (~1.99 at the wide ends, ~0.34 at the throat).
    """
    from polyfoammesh.core.geometry import sample_thickness_field

    if not meshes:
        return np.empty((0, 3), dtype=np.float64), np.empty(0, dtype=np.float64)

    areas = np.asarray([max(m.area, 1e-12) for m in meshes])
    total_area = float(areas.sum())

    all_pts: list[np.ndarray] = []
    all_thick: list[np.ndarray] = []
    for mesh, area in zip(meshes, areas):
        n_local = max(int(n_samples_total * area / total_area), 16)
        pts, thick = sample_thickness_field(mesh, n_local, bbox_max, seed=seed)
        if len(pts) == 0:
            continue
        all_pts.append(pts)
        all_thick.append(thick)

    if not all_pts:
        return np.empty((0, 3), dtype=np.float64), np.empty(0, dtype=np.float64)

    return np.vstack(all_pts), np.concatenate(all_thick)


def _find_significant_minima_3d(
    points: np.ndarray,
    thicknesses: np.ndarray,
    n_neighbors: int = 30,
    significance_threshold: float = 0.65,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Find REGIONS of locally small thickness (not strict minima).

    Uses KD-tree to find n_neighbors nearest neighbors in 3D space.
    A point is flagged if its thickness is <= the median neighbor
    thickness, AND the ratio of its thickness to the median neighbor
    thickness is below significance_threshold.

    This detects "thin regions" rather than isolated local minima,
    which enables robust clustering into refinement zones.

    Returns (thin_points, thin_thicknesses, scores) where:
      - thin_points: (M, 3) array of thin point coordinates
      - thin_thicknesses: (M,) array of thickness at those points
      - scores: (M,) array of significance scores (0-1, larger = more significant)
    """
    if len(points) < n_neighbors + 1:
        return np.empty((0, 3), dtype=np.float64), np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)

    from scipy.spatial import KDTree
    tree = KDTree(points)
    _, nn_idx = tree.query(points, k=min(n_neighbors + 1, len(points)))

    is_thin = np.ones(len(points), dtype=bool)
    scores = np.zeros(len(points), dtype=np.float64)

    for i in range(len(points)):
        neighbor_thick = thicknesses[nn_idx[i, 1:]]  # exclude self
        t_i = thicknesses[i]
        if len(neighbor_thick) == 0 or t_i <= 0:
            is_thin[i] = False
            continue
        median_neighbor = float(np.median(neighbor_thick))
        if median_neighbor <= 0:
            is_thin[i] = False
            continue
        # Flag as thin if thickness is at or below the median neighbor
        if t_i > median_neighbor:
            is_thin[i] = False
            continue
        ratio = t_i / median_neighbor
        if ratio > significance_threshold:
            is_thin[i] = False
            continue
        # Also require that this point is in a genuine thin region:
        # at least 3 neighbors must also be thin (below median)
        n_neighbor_thin = int((neighbor_thick <= median_neighbor).sum())
        if n_neighbor_thin < 3:
            is_thin[i] = False
            continue
        scores[i] = 1.0 - ratio

    mask = is_thin
    return points[mask], thicknesses[mask], scores[mask]


def _cluster_minima(
    points: np.ndarray,
    thicknesses: np.ndarray,
    scores: np.ndarray,
    cluster_radius_factor: float = 3.0,
    bbox_max: float = 1.0,
) -> list[dict]:
    """Cluster nearby minima into refinement zones.

    Uses greedy clustering: sort by significance, take the most significant
    point as a cluster seed, absorb all points within cluster_radius of it,
    repeat until no unclustered points remain.

    Returns list of dicts with keys:
      centre, radius, cell_size, local_thickness, significance, n_samples
    """
    if len(points) == 0:
        return []

    order = np.argsort(-scores)  # most significant first
    clustered = np.zeros(len(points), dtype=bool)
    zones: list[dict] = []

    from scipy.spatial import KDTree
    tree = KDTree(points)

    for idx in order:
        if clustered[idx]:
            continue
        t_i = thicknesses[idx]
        # Clustering radius based on local neighborhood median thickness
        # to handle varying scales (wide tubes vs narrow throats)
        nn_dist, nn_idx = tree.query(points[idx:idx+1], k=min(40, len(points)))
        neighbor_thick = thicknesses[nn_idx[0, 1:]]
        local_scale = max(float(np.median(neighbor_thick)), t_i)
        radius = local_scale * 1.5
        radius = max(radius, t_i * cluster_radius_factor, 1e-3)

        dists = np.linalg.norm(points - points[idx], axis=1)
        in_cluster = (~clustered) & (dists < radius)
        if not in_cluster.any():
            continue
        in_cluster_idx = np.where(in_cluster)[0]
        clustered[in_cluster_idx] = True

        # Skip very small clusters (likely noise from edges or grazing rays)
        if len(in_cluster_idx) < 2:
            continue

        cluster_pts = points[in_cluster_idx]
        centroid = cluster_pts.mean(axis=0)
        cluster_thicknesses = thicknesses[in_cluster_idx]
        min_thick = float(cluster_thicknesses.min())
        avg_thick = float(cluster_thicknesses.mean())
        max_score = float(scores[in_cluster_idx].max())

        zone_radius = max(min_thick * 1.5, avg_thick * 0.8, 1e-3)
        cell_size = max(min_thick / 6.0, zone_radius / 8.0, 0.0005)

        zones.append({
            "centre": (float(centroid[0]), float(centroid[1]), float(centroid[2])),
            "radius": float(zone_radius),
            "cell_size": float(cell_size),
            "local_thickness": float(min_thick),
            "significance": float(max_score),
            "n_samples": int(len(in_cluster_idx)),
        })

    return zones


def detect_refinement_regions(
    meshes: list,
    detail: str = "medium",
    global_max_cell: float = 0.05,
) -> list[RefinementZone]:
    """Detect narrow passages anywhere on the geometry surface.

    Samples local thickness (passage width) across the entire surface,
    finds significant local minima using KD-tree neighborhood analysis,
    clusters nearby minima into refinement zones.

    Works on ANY geometry: ducts, manifolds, external aerodynamics,
    multi-branch pipes — no flow axis assumption.

    Returns empty list for geometries with no significant narrow passages.
    """
    if not meshes:
        return []

    all_verts = np.vstack([m.vertices for m in meshes])
    bbox = all_verts.max(axis=0) - all_verts.min(axis=0)
    bbox_max = float(max(bbox))
    if bbox_max < 1e-12:
        return []

    n_samples_map = {"very_coarse": 200, "coarse": 350, "medium": 500, "fine": 800, "very_fine": 1500}
    n_samples = n_samples_map.get(detail, 500)

    points, thicknesses = _sample_thickness_field(meshes, n_samples_total=n_samples, bbox_max=bbox_max)
    if len(points) < 20:
        return []

    # Scale n_neighbors with complexity
    n_neighbors = min(50, max(15, len(points) // 15))
    minima_pts, minima_thick, scores = _find_significant_minima_3d(
        points, thicknesses, n_neighbors=n_neighbors,
    )
    if len(minima_pts) == 0:
        return []

    cluster_radius_factor = 2.5
    zones = _cluster_minima(minima_pts, minima_thick, scores, cluster_radius_factor, bbox_max)

    # Curvature overlap: check if any zone is near high curvature
    # (no curvature data here, but we reduce cell_size if there's
    # a strong minimum signal — curvature combination happens downstream)
    result: list[RefinementZone] = []
    min_sensible_thickness = bbox_max * 0.02
    for z in zones:
        # Filter clusters whose thickness is below 2% of bbox
        # (these are typically edge/grazing artifacts, not real passages)
        if z["local_thickness"] < min_sensible_thickness:
            continue
        cs = min(z["cell_size"], global_max_cell / 2.0, z["radius"] / 4.0)
        cs = max(cs, 0.0005)
        result.append(RefinementZone(
            centre=z["centre"],
            radius=z["radius"],
            cell_size=cs,
            local_thickness=z["local_thickness"],
            significance=z["significance"],
            n_samples_in_cluster=z["n_samples"],
        ))
    return result


def check_bl_throat_compatibility(
    bl_params: dict | None,
    zones: list[RefinementZone],
) -> tuple[list[str], dict]:
    """Check if boundary layer parameters fit in the narrowest zone.

    If the total BL thickness exceeds 50% of the available space (half the
    narrowest passage/gap), auto-reduces nLayers and/or firstLayerThickness
    so the mesh stays valid.  Never lets cfMesh crash or produce degenerate
    cells — reducing layers is safer than a failed run.

    Returns:
        (warnings, adjusted_bl_params):
            warnings: list of human-readable messages (empty if all OK).
            adjusted_bl_params: copy of bl_params with reduced nLayers and/or
                firstLayerThickness, or the original dict if no reduction needed.
    """
    if not bl_params or not zones:
        return [], dict(bl_params) if bl_params else {}

    result = dict(bl_params)
    n_layers = int(result.get("nLayers", 3))
    growth = float(result.get("thicknessRatio") or result.get("expansionRatio", 1.2))
    first_layer = float(result.get("firstLayerThickness", 0.001))

    if growth <= 1.0:
        growth = 1.2

    # Total BL thickness = geometric series
    def _bl_total(fl: float, gr: float, nl: int) -> float:
        if gr == 1.0:
            return fl * nl
        return fl * (1.0 - gr ** nl) / (1.0 - gr)

    total = _bl_total(first_layer, growth, n_layers)
    min_available = min((z.local_thickness / 2.0 for z in zones if z.local_thickness > 0), default=float("inf"))

    warnings: list[str] = []
    if min_available <= 0 or min_available == float("inf"):
        return warnings, result

    if total <= min_available * 0.50:
        return warnings, result

    # Reduce: first try reducing nLayers, then firstLayerThickness
    ratio = total / min_available
    reduced = False

    # Strategy 1: halve nLayers
    if n_layers > 2:
        new_n = max(n_layers // 2, 1)
        new_total = _bl_total(first_layer, growth, new_n)
        if new_total <= min_available * 0.50:
            result["nLayers"] = new_n
            warnings.append(
                f"BL reduced: nLayers={n_layers} -> {new_n} — total BL {total:.5f}m "
                f"exceeded 50% of available throat space ({min_available:.5f}m, "
                f"ratio={ratio:.0%})."
            )
            reduced = True

    # Strategy 2: reduce firstLayerThickness (if nLayers reduction wasn't enough)
    if not reduced and first_layer > 1e-6:
        target_ratio = 0.45  # aim for 45% fill, below the 50% threshold
        max_fl = min_available * target_ratio * (1.0 - growth) / (1.0 - growth ** n_layers) if growth != 1.0 else min_available * target_ratio / n_layers
        if max_fl > 0:
            capped_fl = min(first_layer, max_fl)
            result["firstLayerThickness"] = round(capped_fl, 8)
            warnings.append(
                f"BL firstLayerThickness reduced: {first_layer:.8f} -> {capped_fl:.8f} — "
                f"total BL exceeded 50% of throat space ({min_available:.5f}m, ratio={ratio:.0%})."
            )
            reduced = True

    if not reduced:
        warnings.append(
            f"BL may still be tight for narrow passage (d={min_available*2:.4f}m): "
            f"{result.get('nLayers', n_layers)} layers x {growth:.2f} growth = "
            f"{_bl_total(float(result.get('firstLayerThickness', first_layer)), growth, int(result.get('nLayers', n_layers))):.5f}m "
            f"({_bl_total(float(result.get('firstLayerThickness', first_layer)), growth, int(result.get('nLayers', n_layers))) / min_available:.0%} of passage radius)."
        )

    return warnings, result
