from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SharpEdge:
    point_a: tuple[float, float, float]
    point_b: tuple[float, float, float]
    angle: float
    length: float


@dataclass
class GapRegion:
    center: tuple[float, float, float]
    gap_width: float
    normal: tuple[float, float, float]


@dataclass
class FeatureMap:
    sharp_edges: list[SharpEdge] = field(default_factory=list)
    gap_regions: list[GapRegion] = field(default_factory=list)
    curvature_radius: float = 1.0
    suggested_min_cell: float = 0.01
    suggested_max_cell: float = 0.1


class FeatureDetector:
    _cache: dict[str, FeatureMap] = {}

    def __init__(self):
        self._gmsh_initialized = False

    def analyze_step(
        self, filepath: Path | str, detail: str = "medium"
    ) -> FeatureMap:
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"STEP file not found: {filepath}")

        # Check cache: keyed by (resolved path + mtime + detail)
        cache_key = f"{filepath.resolve()}::{filepath.stat().st_mtime}::{detail}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        import gmsh

        if not self._gmsh_initialized:
            gmsh.initialize()
            self._gmsh_initialized = True

        gmsh.clear()
        gmsh.model.add("feature_analysis")
        gmsh.open(str(filepath))

        feature_map = FeatureMap()

        # Curvature analysis
        try:
            curvature = gmsh.model.getCurvature(-1, [])
            if curvature:
                feature_map.curvature_radius = max(
                    1.0 / max(abs(c) for c in curvature if abs(c) > 1e-12), 1e-6
                )
        except Exception as e:
            logger.debug("Curvature analysis skipped: %s", e)

        # Sharp edge detection via dihedral angles (mesh-based)
        try:
            gmsh.model.mesh.generate(2)

            # Get all node coordinates
            node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
            node_map = {}
            for k in range(len(node_tags)):
                node_map[int(node_tags[k])] = node_coords[k*3:(k+1)*3]

            # Get 2D elements and compute face normals
            face_types, face_elem_tags, face_elem_nodes = gmsh.model.mesh.getElements(2)
            face_normal_map = {}
            for etype, etags, enodes in zip(face_types, face_elem_tags, face_elem_nodes):
                npe = 3 if etype == 2 else 4
                enodes = enodes.reshape(-1, npe)
                for k in range(len(etags)):
                    n0 = node_map.get(int(enodes[k][0]))
                    n1 = node_map.get(int(enodes[k][1]))
                    n2 = node_map.get(int(enodes[k][2]))
                    if n0 is not None and n1 is not None and n2 is not None:
                        v1 = n1 - n0
                        v2 = n2 - n0
                        n = np.cross(v1, v2)
                        norm_n = np.linalg.norm(n)
                        if norm_n > 1e-12:
                            n = n / norm_n
                        face_normal_map[int(etags[k])] = n

            # Build face-to-edge mapping
            face_edge_map = {}
            for etype, etags, enodes in zip(face_types, face_elem_tags, face_elem_nodes):
                try:
                    _, elem_edges = gmsh.model.mesh.getElementEdges(etype, etags)
                except Exception:
                    continue
                if elem_edges.ndim < 2:
                    continue
                for k, ftag in enumerate(etags):
                    face_edge_map[int(ftag)] = [int(elem_edges[k][j]) for j in range(elem_edges.shape[1])]

            # Reverse: edge -> adjacent face elements
            edge_face_map = {}
            for ftag, e_list in face_edge_map.items():
                for etag in e_list:
                    edge_face_map.setdefault(etag, []).append(ftag)

            # Edge node pairs for position/length
            edge_tags, edge_nodes = gmsh.model.mesh.getEdges()
            edge_nodes = edge_nodes.reshape(-1, 2)
            edge_node_map = {int(edge_tags[k]): (int(edge_nodes[k][0]), int(edge_nodes[k][1])) for k in range(len(edge_tags))}

            for etag, ftags in edge_face_map.items():
                if len(ftags) < 2:
                    continue
                n1 = face_normal_map.get(ftags[0])
                n2 = face_normal_map.get(ftags[1])
                if n1 is None or n2 is None:
                    continue

                cos_angle = float(np.dot(n1, n2))
                cos_angle = max(-1.0, min(1.0, cos_angle))
                angle = math.degrees(math.acos(cos_angle))

                en = edge_node_map.get(etag)
                if en is None:
                    continue
                p1 = node_map.get(en[0])
                p2 = node_map.get(en[1])
                if p1 is None or p2 is None:
                    continue
                length = math.sqrt(np.sum((p2 - p1) ** 2))

                if angle > 30.0 and length > 0.001:
                    feature_map.sharp_edges.append(
                        SharpEdge(
                            point_a=tuple(p1),
                            point_b=tuple(p2),
                            angle=angle,
                            length=length,
                        )
                    )
        except Exception as e:
            logger.debug("Sharp edge detection skipped: %s", e)

        # Gap detection: placeholder — real implementation requires
        # proximity queries via GMSH's `mesh.getClosestPoint` on each
        # surface pair. For now an empty list avoids corrupting cell
        # sizes with dummy data (each dummy GapRegion with width 0.001
        # would force minCell ≤ 0.0002 regardless of actual geometry).
        try:
            gmsh.model.mesh.clear()
        except Exception:
            pass

        # getBoundingBox(dim, tag) needs both positional args (missing the
        # second one crashed every call: "missing 1 required positional
        # argument: 'tag'", confirmed live) and returns one flat 6-tuple
        # (xmin, ymin, zmin, xmax, ymax, zmax), not two 3-tuples.
        bbox = gmsh.model.getBoundingBox(-1, -1)
        bbox_dim = max(bbox[i + 3] - bbox[i] for i in range(3))
        feature_map.suggested_min_cell, feature_map.suggested_max_cell = (
            self.suggest_cell_sizes(feature_map, bbox_dim)
        )

        logger.info(
            "Feature analysis complete: %d sharp edges, %d gaps, "
            "curvature=%.4f, cells: %.4f/%.4f",
            len(feature_map.sharp_edges),
            len(feature_map.gap_regions),
            feature_map.curvature_radius,
            feature_map.suggested_min_cell,
            feature_map.suggested_max_cell,
        )

        # Cache the result
        cache_key = f"{filepath.resolve()}::{filepath.stat().st_mtime}::{detail}"
        self._cache[cache_key] = feature_map
        # Limit cache size
        if len(self._cache) > 20:
            for k in list(self._cache.keys())[:-10]:
                del self._cache[k]

        return feature_map

    def suggest_cell_sizes(
        self, feature_map: FeatureMap, bbox_dim: float
    ) -> tuple[float, float]:
        base_min = bbox_dim * 0.005
        base_max = bbox_dim * 0.05

        if feature_map.sharp_edges:
            min_edge_len = min(e.length for e in feature_map.sharp_edges)
            base_min = min(base_min, min_edge_len * 0.3)

        if feature_map.gap_regions:
            min_gap = min(g.gap_width for g in feature_map.gap_regions)
            base_min = min(base_min, min_gap * 0.2)

        if feature_map.curvature_radius < bbox_dim * 0.1:
            curvature_cell = feature_map.curvature_radius * 0.1
            base_min = min(base_min, curvature_cell)

        base_min = max(base_min, 1e-6)
        base_max = max(base_max, base_min * 3)

        return base_min, base_max

    def shutdown(self):
        if self._gmsh_initialized:
            try:
                import gmsh
                gmsh.finalize()
            except Exception:
                pass
            self._gmsh_initialized = False
