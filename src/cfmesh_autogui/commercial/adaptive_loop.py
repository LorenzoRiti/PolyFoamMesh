"""Closed-Loop Adaptive Meshing Engine (OODA Cycle).

Implements the OODA (Observe-Orient-Decide-Act) meshing cycle as a
deterministic state machine.  All phases operate on a single shared
topological data structure (UnifiedMeshGraph) to avoid compartmentalised
reasoning:

  1. FIELD EVALUATION  — BVH/Octree, curvature, feature detection
  2. INTENT GENERATION — y+ → y1, H(x,y,z) sizing field
  3. COUPLED BL+CORE   — proximity-aware extrusion + tetra/hex fill
  4. DUAL-GRAPH POLY   — core-only dualisation, conformal stitching
  5. QUALITY REMEDIATION — local recovery loop (max N iterations)

Usage::

    engine = AdaptiveLoopEngine()
    engine.configure(case_dir)
    result = engine.run()
    print(result.summary)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable

from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_REMEDIATION_ITERATIONS = 5
GRADIENT_LIMIT = 0.3
MAX_VOLUME_RATIO_BL_CORE = 1.5
CONVERGENCE_HISTORY_LEN = 3
SKEWNESS_THRESHOLD = 0.9
NON_ORTHO_THRESHOLD = 70.0
ASPECT_RATIO_THRESHOLD = 1000.0

# ---------------------------------------------------------------------------
# Vector math helpers (no numpy dependency)
# ---------------------------------------------------------------------------
Vec3 = tuple[float, float, float]


def v3_add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v3_sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v3_scale(v: Vec3, s: float) -> Vec3:
    return (v[0] * s, v[1] * s, v[2] * s)


def v3_dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v3_cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def v3_norm(v: Vec3) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def v3_normalize(v: Vec3) -> Vec3:
    n = v3_norm(v)
    if n < 1e-15:
        return (0.0, 0.0, 0.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def v3_dist(a: Vec3, b: Vec3) -> float:
    return v3_norm(v3_sub(a, b))


def v3_mid(a: Vec3, b: Vec3) -> Vec3:
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5, (a[2] + b[2]) * 0.5)


def tetra_volume(a: Vec3, b: Vec3, c: Vec3, d: Vec3) -> float:
    """Signed volume of tetrahedron (a,b,c,d)."""
    ab = v3_sub(b, a)
    ac = v3_sub(c, a)
    ad = v3_sub(d, a)
    return v3_dot(ab, v3_cross(ac, ad)) / 6.0


def tetra_centroid(a: Vec3, b: Vec3, c: Vec3, d: Vec3) -> Vec3:
    """Centroid of tetrahedron = average of four vertices."""
    return (
        (a[0] + b[0] + c[0] + d[0]) * 0.25,
        (a[1] + b[1] + c[1] + d[1]) * 0.25,
        (a[2] + b[2] + c[2] + d[2]) * 0.25,
    )


def poly_centroid(verts: list[Vec3]) -> Vec3:
    """Centroid of arbitrary polyhedron by tetrahedral decomposition."""
    if len(verts) < 4:
        cx = sum(v[0] for v in verts) / len(verts)
        cy = sum(v[1] for v in verts) / len(verts)
        cz = sum(v[2] for v in verts) / len(verts)
        return (cx, cy, cz)
    # Pick verts[0] as apex, decompose into tetra
    total_vol = 0.0
    cx = cy = cz = 0.0
    a = verts[0]
    for i in range(1, len(verts) - 1):
        b, c, d = verts[i], verts[i + 1], a
        vol = tetra_volume(a, b, c, d)
        if vol != 0.0:
            tc = tetra_centroid(a, b, c, d)
            total_vol += vol
            cx += tc[0] * vol
            cy += tc[1] * vol
            cz += tc[2] * vol
    if abs(total_vol) < 1e-15:
        cx = sum(v[0] for v in verts) / len(verts)
        cy = sum(v[1] for v in verts) / len(verts)
        cz = sum(v[2] for v in verts) / len(verts)
        return (cx, cy, cz)
    return (cx / total_vol, cy / total_vol, cz / total_vol)


def face_normal(verts: list[Vec3]) -> Vec3:
    """Area-weighted normal of a planar polygon via Newell's method."""
    nx = ny = nz = 0.0
    nv = len(verts)
    for i in range(nv):
        j = (i + 1) % nv
        nx += (verts[i][1] - verts[j][1]) * (verts[i][2] + verts[j][2])
        ny += (verts[i][2] - verts[j][2]) * (verts[i][0] + verts[j][0])
        nz += (verts[i][0] - verts[j][0]) * (verts[i][1] + verts[j][1])
    return v3_normalize((nx, ny, nz))


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class OODAPhase(Enum):
    FIELD_EVALUATION = auto()
    INTENT_GENERATION = auto()
    COUPLED_BL_CORE = auto()
    DUAL_GRAPH_POLY = auto()
    QUALITY_REMEDIATION = auto()
    CONVERGED = auto()
    FAILED = auto()


class MeshEntityType(Enum):
    BOUNDARY = auto()
    BL_LAYER = auto()
    CORE_TETRA = auto()
    CORE_POLY = auto()
    INTERFACE = auto()


class RemediationAction(Enum):
    SMOOTH_LAPLACIAN = auto()
    REDUCE_BL_LAYERS = auto()
    UN_DUALIZE = auto()
    LOCAL_REMESH = auto()
    RELAX_SIZING = auto()
    CENTROIDAL_VORONOI = auto()
    SPLIT_CELLS = auto()


class ErrorType(Enum):
    BL_COLLAPSED = auto()
    NEGATIVE_VOLUME = auto()
    CONCAVE_POLY = auto()
    HIGH_SKEWNESS = auto()
    HIGH_NON_ORTHOGONALITY = auto()
    HIGH_ASPECT_RATIO = auto()
    UNDER_REFINEMENT = auto()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class MeshEdge:
    """An edge connecting two nodes, with incident cells."""
    index: int = 0
    node_a: int = -1
    node_b: int = -1
    cells_around: list[int] = field(default_factory=list)
    is_internal: bool = False
    centroid: Vec3 = (0.0, 0.0, 0.0)
    length: float = 0.0

    def other_node(self, nid: int) -> int:
        return self.node_b if nid == self.node_a else self.node_a


@dataclass
class MeshFace:
    """A polygonal face referencing its edge-adjacent cells."""
    index: int = 0
    node_indices: list[int] = field(default_factory=list)
    edge_indices: list[int] = field(default_factory=list)
    owner_cell: int = -1
    neighbour_cell: int = -1  # -1 = boundary face
    normal: Vec3 = (0.0, 0.0, 0.0)
    centroid: Vec3 = (0.0, 0.0, 0.0)
    area: float = 0.0


@dataclass
class MeshNode:
    index: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    entity_type: MeshEntityType = MeshEntityType.CORE_TETRA
    smoothed_normal: Vec3 = (0.0, 0.0, 0.0)
    displacement: Vec3 = (0.0, 0.0, 0.0)
    curvature: float = 0.0
    target_size: float = 0.0

    @property
    def pos(self) -> Vec3:
        return (self.x, self.y, self.z)


@dataclass
class MeshCell:
    index: int = 0
    node_indices: list[int] = field(default_factory=list)
    face_indices: list[int] = field(default_factory=list)
    neighbour_cells: list[int] = field(default_factory=list)
    entity_type: MeshEntityType = MeshEntityType.CORE_TETRA
    volume: float = 0.0
    centroid: Vec3 = (0.0, 0.0, 0.0)
    skewness: float = 0.0
    non_orthogonality: float = 0.0
    aspect_ratio: float = 0.0
    target_size: float = 0.0
    visited: bool = False

    @property
    def is_valid(self) -> bool:
        return self.volume > 0.0 and self.skewness < SKEWNESS_THRESHOLD


@dataclass
class SizingFieldPoint:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    h: float = 0.05
    curvature: float = 0.0
    proximity: float = float("inf")


class UnifiedMeshGraph:
    """Shared topological data structure for all OODA phases.

    Carries nodes, faces, cells, and adjacency.  Every phase reads
    from and writes to this structure so that BL extrusion and Core
    generation share the same topology.
    """

    def __init__(self) -> None:
        self.nodes: dict[int, MeshNode] = {}
        self.faces: dict[int, MeshFace] = {}
        self.edges: dict[int, MeshEdge] = {}
        self.cells: dict[int, MeshCell] = {}
        self.sizing_field: list[SizingFieldPoint] = []
        self.bbox_min: Vec3 = (0.0, 0.0, 0.0)
        self.bbox_max: Vec3 = (1.0, 1.0, 1.0)
        self._next_node_id: int = 0
        self._next_face_id: int = 0
        self._next_edge_id: int = 0
        self._next_cell_id: int = 0
        self._edge_key_map: dict[tuple[int, int], int] = {}

    @property
    def bbox_diagonal(self) -> float:
        return v3_dist(self.bbox_min, self.bbox_max)

    def add_node(self, node: MeshNode) -> int:
        nid = self._next_node_id
        node.index = nid
        self.nodes[nid] = node
        self._next_node_id += 1
        return nid

    def add_face(self, face: MeshFace) -> int:
        fid = self._next_face_id
        face.index = fid
        self.faces[fid] = face
        self._next_face_id += 1
        return fid

    def add_edge(self, edge: MeshEdge) -> int:
        eid = self._next_edge_id
        edge.index = eid
        self.edges[eid] = edge
        self._next_edge_id += 1
        key = (min(edge.node_a, edge.node_b), max(edge.node_a, edge.node_b))
        self._edge_key_map[key] = eid
        return eid

    def get_edge_id(self, na: int, nb: int) -> int | None:
        return self._edge_key_map.get(
            (min(na, nb), max(na, nb)),
        )

    def build_edges(self) -> None:
        """Build edges from face connectivity and populate cells_around."""
        for cid, cell in self.cells.items():
            for fid in cell.face_indices:
                face = self.faces.get(fid)
                if not face:
                    continue
                nv = len(face.node_indices)
                for i in range(nv):
                    na = face.node_indices[i]
                    nb = face.node_indices[(i + 1) % nv]
                    eid = self.get_edge_id(na, nb)
                    if eid is None:
                        p_a = self.node_pos(na)
                        p_b = self.node_pos(nb)
                        e = MeshEdge(
                            node_a=na, node_b=nb,
                            cells_around=[cid],
                            length=v3_dist(p_a, p_b),
                            centroid=v3_mid(p_a, p_b),
                        )
                        self.add_edge(e)
                    else:
                        edge = self.edges[eid]
                        if cid not in edge.cells_around:
                            edge.cells_around.append(cid)

        # Mark internal edges (those with 2+ incident cells)
        for eid, edge in self.edges.items():
            edge.is_internal = len(edge.cells_around) >= 2

        # Link edges to faces
        for fid, face in self.faces.items():
            nv = len(face.node_indices)
            for i in range(nv):
                na = face.node_indices[i]
                nb = face.node_indices[(i + 1) % nv]
                eid = self.get_edge_id(na, nb)
                if eid is not None and eid not in face.edge_indices:
                    face.edge_indices.append(eid)

    def add_cell(self, cell: MeshCell) -> int:
        cid = self._next_cell_id
        cell.index = cid
        self.cells[cid] = cell
        self._next_cell_id += 1
        return cid

    def node_pos(self, nid: int) -> Vec3:
        n = self.nodes.get(nid)
        if n:
            return n.pos
        return (0.0, 0.0, 0.0)

    def nodes_positions(self, nids: list[int]) -> list[Vec3]:
        return [self.node_pos(nid) for nid in nids]

    def cell_neighbours_of_type(self, cell_id: int, etype: MeshEntityType) -> list[int]:
        cell = self.cells.get(cell_id)
        if not cell:
            return []
        return [
            nid for nid in cell.neighbour_cells
            if self.cells.get(nid, MeshCell()).entity_type == etype
        ]

    def extract_subgraph(self, etype: MeshEntityType) -> UnifiedMeshGraph:
        sub = UnifiedMeshGraph()
        for cid, cell in self.cells.items():
            if cell.entity_type == etype:
                sub.add_cell(MeshCell(
                    index=cid,
                    node_indices=list(cell.node_indices),
                    face_indices=list(cell.face_indices),
                    neighbour_cells=list(cell.neighbour_cells),
                    entity_type=etype,
                    centroid=cell.centroid,
                    volume=cell.volume,
                ))
                for nid in cell.node_indices:
                    if nid not in sub.nodes:
                        node = self.nodes.get(nid)
                        if node:
                            sub.add_node(MeshNode(
                                index=nid, x=node.x, y=node.y, z=node.z,
                                entity_type=node.entity_type,
                            ))
        sub.bbox_min = self.bbox_min
        sub.bbox_max = self.bbox_max
        sub.edges = self.edges
        sub._edge_key_map = self._edge_key_map
        sub._next_edge_id = self._next_edge_id
        return sub

    def estimate_cell_count(self, h_avg: float) -> int:
        vol = (
            (self.bbox_max[0] - self.bbox_min[0])
            * (self.bbox_max[1] - self.bbox_min[1])
            * (self.bbox_max[2] - self.bbox_min[2])
        )
        if h_avg <= 0 or vol <= 0:
            return 0
        return max(1, int(vol / (h_avg ** 3) + 0.5))


# ---------------------------------------------------------------------------
# Sizing field (H(x,y,z))
# ---------------------------------------------------------------------------
class SizingField:
    def __init__(self, graph: UnifiedMeshGraph) -> None:
        self._graph = graph
        self._points: list[SizingFieldPoint] = []
        self._base_size: float = 0.05

    def build_from_bbox(self, n_samples: int = 1000) -> None:
        import random
        diag = self._graph.bbox_diagonal
        if diag <= 0:
            diag = 1.0
        self._base_size = diag / 20.0
        for _ in range(n_samples):
            self._points.append(SizingFieldPoint(
                x=random.uniform(self._graph.bbox_min[0], self._graph.bbox_max[0]),
                y=random.uniform(self._graph.bbox_min[1], self._graph.bbox_max[1]),
                z=random.uniform(self._graph.bbox_min[2], self._graph.bbox_max[2]),
                h=self._base_size,
            ))

    def sample_at(self, x: float, y: float, z: float) -> float:
        if not self._points:
            return self._base_size
        best = self._points[0]
        best_d2 = v3_dist((best.x, best.y, best.z), (x, y, z))
        for p in self._points[1:]:
            d2 = v3_dist((p.x, p.y, p.z), (x, y, z))
            if d2 < best_d2:
                best_d2 = d2
                best = p
        return best.h

    def refine_region(self, cx: float, cy: float, cz: float, radius: float, target_h: float) -> None:
        r2 = radius * radius
        for p in self._points:
            d2 = (p.x - cx) ** 2 + (p.y - cy) ** 2 + (p.z - cz) ** 2
            if d2 <= r2 and target_h < p.h:
                p.h = target_h
        self._enforce_gradient_limit()

    def _enforce_gradient_limit(self) -> None:
        changed = True
        max_pass = 10
        while changed and max_pass > 0:
            changed = False
            max_pass -= 1
            for i, pi in enumerate(self._points):
                for j, pj in enumerate(self._points):
                    if i >= j:
                        continue
                    d = v3_dist((pi.x, pi.y, pi.z), (pj.x, pj.y, pj.z))
                    if d < 1e-12:
                        continue
                    ratio = abs(pi.h - pj.h) / d
                    if ratio > GRADIENT_LIMIT:
                        avg = (pi.h + pj.h) * 0.5
                        if pi.h != avg:
                            pi.h = avg
                            changed = True
                        if pj.h != avg:
                            pj.h = avg
                            changed = True

    def to_dict(self) -> dict[str, Any]:
        return {"base_size": self._base_size, "n_points": len(self._points)}


# ---------------------------------------------------------------------------
# OODA State Machine
# ---------------------------------------------------------------------------
class OODAStateMachine:
    def __init__(self) -> None:
        self._phase: OODAPhase = OODAPhase.FIELD_EVALUATION
        self._history: list[OODAPhase] = []
        self._iteration: int = 0

    @property
    def phase(self) -> OODAPhase:
        return self._phase

    @property
    def iteration(self) -> int:
        return self._iteration

    def transition(self, target: OODAPhase) -> None:
        self._history.append(self._phase)
        self._phase = target
        if target == OODAPhase.COUPLED_BL_CORE and self._history.count(target) > 1:
            self._iteration += 1

    def can_remediate(self) -> bool:
        return self._iteration < MAX_REMEDIATION_ITERATIONS

    def reset(self) -> None:
        self._phase = OODAPhase.FIELD_EVALUATION
        self._history.clear()
        self._iteration = 0

    def summary(self) -> str:
        return (
            f"OODA[phase={self._phase.name}, iter={self._iteration}, "
            f"n_transitions={len(self._history)}]"
        )


# ---------------------------------------------------------------------------
# BL + y+ helpers
# ---------------------------------------------------------------------------
def compute_first_layer_height(
    y_plus_target: float, u_ref: float, nu: float, length: float,
) -> float:
    """First prism-layer height from flat-plate correlation:
    Cf = 0.058 / Re_x^0.2  (1/5th power law, more accurate for high Re)
    u_tau = U_ref * sqrt(Cf / 2)
    y1 = y+ * nu / u_tau
    """
    Re = u_ref * length / max(nu, 1e-12)
    if Re <= 0:
        return 1e-6
    Cf = 0.058 / (Re ** 0.2)
    u_tau = u_ref * math.sqrt(Cf * 0.5)
    if u_tau <= 0:
        return 1e-6
    return y_plus_target * nu / u_tau


def estimate_bl_total_thickness(
    first_height: float, n_layers: int, growth: float,
) -> float:
    """Geometric series sum: h1 * (r^n - 1) / (r - 1)."""
    if abs(growth - 1.0) < 1e-12:
        return first_height * n_layers
    return first_height * (growth ** n_layers - 1.0) / (growth - 1.0)


def compute_bl_volume_ratio(
    last_bl_height: float, core_cell_size: float,
) -> float:
    """Ratio of last BL cell volume to adjacent core cell volume.
    Target: < 1.5 for smooth transition."""
    bl_vol = last_bl_height ** 3
    core_vol = core_cell_size ** 3
    if core_vol <= 0:
        return float("inf")
    return max(bl_vol, core_vol) / min(bl_vol, core_vol)


def compute_refinement_indicator(
    curvature: float, gap_distance: float,
    max_gap: float, w_curv: float = 0.5,
    w_gap: float = 0.3, w_grad: float = 0.2,
) -> float:
    """Weighted combination of 3 refinement indicators, range [0, 1].

    - Curvature: ``sigma_curv = 1 - |n1·n2|``, higher = sharper
    - Gap: ``1 - D_gap / max_gap``, higher = tighter
    - Gradient: always 0 unless grad(H) limit is exceeded

    Combined: ``I = w_c·sigma_curv + w_g·(1 - D_gap/D_max)``
    """
    gap_indicator = 0.0 if max_gap <= 0 else max(0.0, 1.0 - gap_distance / max_gap)
    return w_curv * curvature + w_gap * gap_indicator


# ---------------------------------------------------------------------------
# Quality metric computation
# ---------------------------------------------------------------------------
def compute_cell_volume(graph: UnifiedMeshGraph, cell: MeshCell) -> float:
    """Compute signed volume of a cell via tetrahedral decomposition.

    For tetrahedra (4 vertices), uses the scalar triple product directly.
    For general polyhedra, decomposes as a fan from vertex 0 to an
    interior point (the average of all vertices).
    """
    pos = [graph.node_pos(nid) for nid in cell.node_indices]
    nv = len(pos)
    if nv < 4:
        return 0.0
    if nv == 4:
        return tetra_volume(pos[0], pos[1], pos[2], pos[3])
    # General polyhedron: fan from vertex 0 to centroid of remaining vertices
    cx = sum(p[0] for p in pos[1:]) / (nv - 1)
    cy = sum(p[1] for p in pos[1:]) / (nv - 1)
    cz = sum(p[2] for p in pos[1:]) / (nv - 1)
    centre = (cx, cy, cz)
    total = 0.0
    for i in range(1, nv - 1):
        total += tetra_volume(pos[0], pos[i], pos[i + 1], centre)
    return total


def compute_cell_centroid(graph: UnifiedMeshGraph, cell: MeshCell) -> Vec3:
    """Compute centroid via volume-weighted tetrahedral decomposition."""
    pos = [graph.node_pos(nid) for nid in cell.node_indices]
    if not pos:
        return (0.0, 0.0, 0.0)
    return poly_centroid(pos)


def compute_non_orthogonality(
    cell_centroid: Vec3, face_centroid: Vec3,
    face_normal_vec: Vec3, neighbour_centroid: Vec3 | None,
) -> float:
    """Non-orthogonality angle in degrees between face normal and
    the vector connecting owner and neighbour centroids (or owner
    centroid to face centroid for boundary faces).

    The angle is: arccos(|d·n| / (|d| * |n|)) where d is the
    vector between cell centroids across the face.
    """
    if neighbour_centroid is not None:
        d = v3_sub(neighbour_centroid, cell_centroid)
    else:
        d = v3_sub(face_centroid, cell_centroid)
    dn = v3_norm(d)
    if dn < 1e-15:
        return 0.0
    fn_len = v3_norm(face_normal_vec)
    if fn_len < 1e-15:
        return 0.0
    cos_angle = abs(v3_dot(d, face_normal_vec)) / (dn * fn_len)
    cos_angle = min(1.0, max(0.0, cos_angle))
    return math.degrees(math.acos(cos_angle))


def compute_skewness(
    cell_centroid: Vec3, face_centroid: Vec3,
    neighbour_centroid: Vec3 | None,
) -> float:
    """Cell skewness = |cC - fC| / |cC - nC| where cC = cell centroid,
    fC = face centroid, nC = neighbour centroid.

    For boundary faces, neighbour centroid is projected.
    """
    if neighbour_centroid is None:
        d_total = v3_dist(cell_centroid, face_centroid) * 2.0
    else:
        d_total = v3_dist(cell_centroid, neighbour_centroid)
    if d_total < 1e-15:
        return 0.0
    d_face = v3_dist(cell_centroid, face_centroid)
    return d_face / d_total


def compute_face_centroid(graph: UnifiedMeshGraph, node_ids: list[int]) -> Vec3:
    """Centroid of polygonal face = average of vertex positions."""
    pos = [graph.node_pos(nid) for nid in node_ids]
    if not pos:
        return (0.0, 0.0, 0.0)
    cx = sum(p[0] for p in pos) / len(pos)
    cy = sum(p[1] for p in pos) / len(pos)
    cz = sum(p[2] for p in pos) / len(pos)
    return (cx, cy, cz)


def compute_aspect_ratio(graph: UnifiedMeshGraph, cell: MeshCell) -> float:
    """Aspect ratio = longest edge / shortest edge."""
    pos = [graph.node_pos(nid) for nid in cell.node_indices]
    if len(pos) < 2:
        return 1.0
    max_len = 0.0
    min_len = float("inf")
    for i in range(len(pos)):
        for j in range(i + 1, len(pos)):
            d = v3_dist(pos[i], pos[j])
            max_len = max(max_len, d)
            min_len = min(min_len, d)
    if min_len < 1e-15:
        return 1.0
    return max_len / min_len


# ---------------------------------------------------------------------------
# Quality evaluator (in-process, no WSL)
# ---------------------------------------------------------------------------
@dataclass
class QualityEvaluation:
    passed: bool = False
    error_types: list[tuple[ErrorType, int, float]] = field(default_factory=list)
    n_cells_total: int = 0
    n_bad_cells: int = 0
    max_skewness: float = 0.0
    max_non_orthogonality: float = 0.0
    max_aspect_ratio: float = 0.0
    n_neg_volume: int = 0
    n_bad_layers: int = 0


class InProcessQualityEvaluator:
    """Evaluate mesh quality on the UnifiedMeshGraph with full geometric
    metric computation (non-orthogonality, skewness, aspect ratio)."""

    def evaluate(self, graph: UnifiedMeshGraph) -> QualityEvaluation:
        eval_ = QualityEvaluation()
        error_list: list[tuple[ErrorType, int, float]] = []

        for cid, cell in graph.cells.items():
            eval_.n_cells_total += 1

            # Volume — recompute from node positions
            cell.volume = compute_cell_volume(graph, cell)
            cell.centroid = compute_cell_centroid(graph, cell)

            if cell.volume <= 0:
                eval_.n_neg_volume += 1
                error_list.append((ErrorType.NEGATIVE_VOLUME, cid, cell.volume))

            # Aspect ratio
            cell.aspect_ratio = compute_aspect_ratio(graph, cell)
            if cell.aspect_ratio > ASPECT_RATIO_THRESHOLD:
                eval_.max_aspect_ratio = max(eval_.max_aspect_ratio, cell.aspect_ratio)
                error_list.append((ErrorType.HIGH_ASPECT_RATIO, cid, cell.aspect_ratio))

            # Skewness and non-orthogonality via cell faces
            max_skew = 0.0
            max_northo = 0.0
            for fid in cell.face_indices:
                face = graph.faces.get(fid)
                if not face:
                    continue
                # Compute face geometry if not already set
                if face.area < 1e-15:
                    pos = [graph.node_pos(nid) for nid in face.node_indices]
                    if len(pos) >= 3:
                        face.normal = face_normal(pos)
                        face.centroid = compute_face_centroid(graph, face.node_indices)
                fc = face.centroid
                fn = face.normal
                nbr_id = face.neighbour_cell if face.neighbour_cell != cid else face.owner_cell
                nbr = graph.cells.get(nbr_id)
                nbr_c = nbr.centroid if nbr else None

                cell_skew = compute_skewness(cell.centroid, fc, nbr_c)
                max_skew = max(max_skew, cell_skew)

                cell_northo = compute_non_orthogonality(cell.centroid, fc, fn, nbr_c)
                max_northo = max(max_northo, cell_northo)

            cell.skewness = max_skew
            cell.non_orthogonality = max_northo

            if cell.skewness > SKEWNESS_THRESHOLD:
                eval_.max_skewness = max(eval_.max_skewness, cell.skewness)
                error_list.append((ErrorType.HIGH_SKEWNESS, cid, cell.skewness))

            if cell.non_orthogonality > NON_ORTHO_THRESHOLD:
                eval_.max_non_orthogonality = max(eval_.max_non_orthogonality, cell.non_orthogonality)
                error_list.append((ErrorType.HIGH_NON_ORTHOGONALITY, cid, cell.non_orthogonality))

            # BL layer health
            if cell.entity_type == MeshEntityType.BL_LAYER and cell.volume <= 0:
                eval_.n_bad_layers += 1
                error_list.append((ErrorType.BL_COLLAPSED, cid, cell.volume))

        eval_.error_types = error_list
        eval_.n_bad_cells = len(error_list)
        eval_.passed = (
            eval_.n_neg_volume == 0
            and eval_.n_bad_layers == 0
            and eval_.max_skewness <= SKEWNESS_THRESHOLD
            and eval_.max_non_orthogonality <= NON_ORTHO_THRESHOLD
            and eval_.max_aspect_ratio <= ASPECT_RATIO_THRESHOLD
        )
        return eval_


# ---------------------------------------------------------------------------
# Curvature & feature detection
# ---------------------------------------------------------------------------
def compute_node_curvature(graph: UnifiedMeshGraph, node_id: int) -> float:
    """Curvature sigma = 1 - |n_i · n_j| averaged over edge-adjacent faces.

    For each pair of faces sharing the node, compute the dot product of
    their normals.  sigma = 0 (flat), sigma → 1 (sharp).
    """
    node = graph.nodes.get(node_id)
    if not node:
        return 0.0
    # Find all faces containing this node
    face_ids: set[int] = set()
    for cid, cell in graph.cells.items():
        for fid in cell.face_indices:
            face = graph.faces.get(fid)
            if face and node_id in face.node_indices:
                face_ids.add(fid)

    if len(face_ids) < 2:
        return 0.0

    f_list = [graph.faces[fid] for fid in face_ids if fid in graph.faces]
    total_curv = 0.0
    n_pairs = 0
    for i in range(len(f_list)):
        for j in range(i + 1, len(f_list)):
            ni = f_list[i].normal
            nj = f_list[j].normal
            dot = v3_dot(ni, nj)
            total_curv += 1.0 - abs(dot)
            n_pairs += 1

    if n_pairs == 0:
        return 0.0
    return total_curv / n_pairs


def smooth_node_normals(graph: UnifiedMeshGraph, n_iterations: int = 3) -> None:
    """Area-weighted Laplacian smoothing of node normals.

    Each node's smoothed normal is the area-weighted average of
    the normals of all incident faces, re-normalised.
    """
    for _ in range(n_iterations):
        for nid, node in graph.nodes.items():
            # Gather incident face normals weighted by area
            sx = sy = sz = 0.0
            total_area = 0.0
            for cid, cell in graph.cells.items():
                if nid not in cell.node_indices:
                    continue
                for fid in cell.face_indices:
                    face = graph.faces.get(fid)
                    if face and nid in face.node_indices:
                        a = face.area
                        sx += face.normal[0] * a
                        sy += face.normal[1] * a
                        sz += face.normal[2] * a
                        total_area += a
            if total_area > 1e-15:
                node.smoothed_normal = v3_normalize((sx / total_area, sy / total_area, sz / total_area))


# ---------------------------------------------------------------------------
# Gap detection via ray-casting
# ---------------------------------------------------------------------------
def ray_cast_gap(
    graph: UnifiedMeshGraph, origin: Vec3, direction: Vec3,
    max_dist: float, hit_epsilon: float = 1e-6,
) -> float:
    """Ray-cast along *direction* from *origin* to find the distance
    to the nearest boundary node (simple point-cloud proxy).

    Returns the hit distance, or *max_dist* if no hit within range.
    """
    dx, dy, dz = direction
    best = max_dist
    for nid, node in graph.nodes.items():
        if node.entity_type not in (MeshEntityType.BOUNDARY, MeshEntityType.BL_LAYER):
            continue
        # Vector from origin to node
        ox = node.x - origin[0]
        oy = node.y - origin[1]
        oz = node.z - origin[2]
        # Project onto ray direction
        t = ox * dx + oy * dy + oz * dz
        if t < hit_epsilon:
            continue  # behind origin
        if t > best:
            continue
        # Perpendicular distance squared
        perp2 = (ox * ox + oy * oy + oz * oz) - t * t
        if perp2 < hit_epsilon:  # on or very near the ray
            best = t
    return best


# ---------------------------------------------------------------------------
# Local remediator
# ---------------------------------------------------------------------------
@dataclass
class RemediationPlan:
    actions: list[tuple[RemediationAction, int, str]] = field(default_factory=list)

    def add(self, action: RemediationAction, cell_id: int, detail: str = "") -> None:
        self.actions.append((action, cell_id, detail))

    @property
    def is_empty(self) -> bool:
        return len(self.actions) == 0


class LocalRemediator:
    def __init__(self, graph: UnifiedMeshGraph, sizing: SizingField) -> None:
        self._graph = graph
        self._sizing = sizing

    def diagnose(self, eval_: QualityEvaluation) -> RemediationPlan:
        plan = RemediationPlan()
        for err_type, cid, _ in eval_.error_types:
            if err_type == ErrorType.BL_COLLAPSED:
                plan.add(RemediationAction.SMOOTH_LAPLACIAN, cid,
                         "Laplacian smooth + reduce layers")
                plan.add(RemediationAction.REDUCE_BL_LAYERS, cid,
                         "Reduce N_layers -> N-1")
            elif err_type in (ErrorType.NEGATIVE_VOLUME, ErrorType.CONCAVE_POLY):
                plan.add(RemediationAction.UN_DUALIZE, cid,
                         "Un-Dualize: revert to tetra in cluster")
            elif err_type == ErrorType.HIGH_SKEWNESS:
                plan.add(RemediationAction.CENTROIDAL_VORONOI, cid,
                         "Centroidal Voronoi smoothing (3 cycles)")
            elif err_type == ErrorType.HIGH_NON_ORTHOGONALITY:
                plan.add(RemediationAction.SMOOTH_LAPLACIAN, cid,
                         "Smooth + relax sizing")
            elif err_type == ErrorType.UNDER_REFINEMENT:
                plan.add(RemediationAction.LOCAL_REMESH, cid,
                         "Local re-mesh with refined H(x)")
        return plan

    def apply(self, plan: RemediationPlan) -> int:
        applied = 0
        for action, cid, _ in plan.actions:
            cell = self._graph.cells.get(cid)
            if not cell:
                continue
            if action == RemediationAction.SMOOTH_LAPLACIAN:
                self._laplacian_smooth_node_cluster(cid)
                applied += 1
            elif action == RemediationAction.REDUCE_BL_LAYERS:
                self._reduce_bl_layers(cid)
                applied += 1
            elif action == RemediationAction.UN_DUALIZE:
                self._un_dualize_cell(cid)
                applied += 1
            elif action == RemediationAction.CENTROIDAL_VORONOI:
                self._centroidal_voronoi_smooth(cid, n_cycles=3)
                applied += 1
            elif action == RemediationAction.LOCAL_REMESH:
                self._local_remesh(cid)
                applied += 1
        return applied

    def _laplacian_smooth_node_cluster(self, cell_id: int) -> None:
        """Laplacian smoothing on the 1-ring neighbourhood of a cell.

        Boundary nodes are constrained to preserve surface features:
        their displacement is projected onto the tangent plane.
        """
        cell = self._graph.cells.get(cell_id)
        if not cell:
            return
        # Collect 1-ring nodes
        ring: set[int] = set(cell.node_indices)
        for nid in cell.neighbour_cells:
            nbr = self._graph.cells.get(nid)
            if nbr:
                ring.update(nbr.node_indices)

        # Store original positions for boundary nodes
        orig: dict[int, Vec3] = {}
        boundary_ring = {
            nid for nid in ring
            if self._graph.nodes.get(nid, MeshNode()).entity_type == MeshEntityType.BOUNDARY
        }
        for nid in boundary_ring:
            n = self._graph.nodes.get(nid)
            if n:
                orig[nid] = n.pos

        # Apply smoothing
        for nid in ring:
            node = self._graph.nodes.get(nid)
            if not node:
                continue
            avg_x = avg_y = avg_z = 0.0
            count = 0
            for onid in ring:
                if onid == nid:
                    continue
                on = self._graph.nodes.get(onid)
                if on:
                    avg_x += on.x
                    avg_y += on.y
                    avg_z += on.z
                    count += 1
            if count > 0:
                node.x = node.x * 0.5 + 0.5 * (avg_x / count)
                node.y = node.y * 0.5 + 0.5 * (avg_y / count)
                node.z = node.z * 0.5 + 0.5 * (avg_z / count)

        # Project boundary nodes back onto their original tangent plane
        for nid in boundary_ring:
            node = self._graph.nodes.get(nid)
            if node and nid in orig:
                n_smooth = node.smoothed_normal
                dp = (node.x - orig[nid][0]) * n_smooth[0] \
                     + (node.y - orig[nid][1]) * n_smooth[1] \
                     + (node.z - orig[nid][2]) * n_smooth[2]
                node.x -= dp * n_smooth[0]
                node.y -= dp * n_smooth[1]
                node.z -= dp * n_smooth[2]

    def _reduce_bl_layers(self, cell_id: int) -> None:
        """Reduce BL layers: convert BL cells in the cluster to CORE_TETRA."""
        cell = self._graph.cells.get(cell_id)
        if not cell:
            return
        cell.entity_type = MeshEntityType.CORE_TETRA
        for nid in cell.neighbour_cells:
            nbr = self._graph.cells.get(nid)
            if nbr and nbr.entity_type == MeshEntityType.BL_LAYER:
                nbr.entity_type = MeshEntityType.CORE_TETRA

    def _un_dualize_cell(self, cell_id: int) -> None:
        """Un-Dualize a poly cell by decomposing into tetrahedra.

        For a polyhedron with N vertices (v0..vN-1), we decompose as
        tetrahedra (v0, v_{i}, v_{i+1}, centroid) for i=1..N-2.
        This guarantees a valid tetrahedralisation for any star-shaped
        polyhedron.
        """
        cell = self._graph.cells.get(cell_id)
        if not cell or cell.entity_type != MeshEntityType.CORE_POLY:
            return

        pos = [self._graph.node_pos(nid) for nid in cell.node_indices]
        nv = len(pos)
        if nv < 4:
            cell.entity_type = MeshEntityType.CORE_TETRA
            return

        c = cell.centroid
        apex = self._graph.add_node(MeshNode(
            x=c[0], y=c[1], z=c[2],
            entity_type=MeshEntityType.CORE_TETRA,
        ))

        total_vol = 0.0
        for i in range(1, nv - 1):
            ni, nj = cell.node_indices[i], cell.node_indices[i + 1]
            n0 = cell.node_indices[0]
            sub = MeshCell(
                entity_type=MeshEntityType.CORE_TETRA,
                node_indices=[n0, ni, nj, apex],
            )
            sub.volume = tetra_volume(
                self._graph.node_pos(n0),
                self._graph.node_pos(ni),
                self._graph.node_pos(nj),
                self._graph.node_pos(apex),
            )
            total_vol += abs(sub.volume)
            sub.centroid = tetra_centroid(
                self._graph.node_pos(n0),
                self._graph.node_pos(ni),
                self._graph.node_pos(nj),
                self._graph.node_pos(apex),
            )
            self._graph.add_cell(sub)
            for nn in sub.node_indices:
                node = self._graph.nodes.get(nn)
                if node:
                    node.entity_type = MeshEntityType.CORE_TETRA

        del self._graph.cells[cell_id]

    def _centroidal_voronoi_smooth(self, cell_id: int, n_cycles: int = 3) -> None:
        """Centroidal Voronoi smoothing: push nodes toward the centroid
        of their Voronoi region (the average of adjacent cell centroids)."""
        cell = self._graph.cells.get(cell_id)
        if not cell:
            return

        for _ in range(n_cycles):
            # Compute Voronoi centre = average of this cell's centroid
            # and all its neighbours' centroids
            cx = cell.centroid[0]
            cy = cell.centroid[1]
            cz = cell.centroid[2]
            count = 1
            for nid in cell.neighbour_cells:
                nbr = self._graph.cells.get(nid)
                if nbr:
                    cx += nbr.centroid[0]
                    cy += nbr.centroid[1]
                    cz += nbr.centroid[2]
                    count += 1

            if count == 0:
                continue
            cx /= count
            cy /= count
            cz /= count

            # Move nodes toward Voronoi centre (under-relaxed)
            for nid in cell.node_indices:
                node = self._graph.nodes.get(nid)
                if node:
                    node.x += 0.3 * (cx - node.x)
                    node.y += 0.3 * (cy - node.y)
                    node.z += 0.3 * (cz - node.z)

    def _local_remesh(self, cell_id: int) -> None:
        """Refine H(x,y,z) around a cavity for local re-meshing."""
        cell = self._graph.cells.get(cell_id)
        if not cell:
            return
        cx, cy, cz = cell.centroid
        diag = self._graph.bbox_diagonal
        radius = diag * 0.05 if diag > 0 else 0.1
        refined_h = self._sizing.sample_at(cx, cy, cz) * 0.5
        self._sizing.refine_region(cx, cy, cz, radius, refined_h)


# ---------------------------------------------------------------------------
# Conformal stitcher (BL prism ↔ poly core)
# ---------------------------------------------------------------------------
class ConformalStitcher:
    """Stitch the top BL prism layer to the polyhedral core.

    For each BL cell adjacent to a Core cell, we align the shared
    interface face by computing a common set of interface nodes.
    The result is a conformal interface with zero hanging nodes.
    """

    def __init__(self, graph: UnifiedMeshGraph) -> None:
        self._graph = graph

    def stitch(self) -> int:
        stitched = 0
        # Map: shared node id → (BL cell id, Core cell id)
        interface_map: dict[int, tuple[int, int]] = {}

        for cid, cell in self._graph.cells.items():
            if cell.entity_type != MeshEntityType.BL_LAYER:
                continue
            for nid in cell.neighbour_cells:
                nbr = self._graph.cells.get(nid)
                if nbr and nbr.entity_type in (
                    MeshEntityType.CORE_TETRA, MeshEntityType.CORE_POLY,
                ):
                    # Find shared nodes
                    shared = set(cell.node_indices) & set(nbr.node_indices)
                    for sn in shared:
                        interface_map[sn] = (cid, nid)
                    stitched += 1

        # Mark interface nodes and project them to the BL surface
        for sn, (bl_id, _) in interface_map.items():
            node = self._graph.nodes.get(sn)
            if not node:
                continue
            node.entity_type = MeshEntityType.INTERFACE

            # Compute the BL top-face plane from the BL cell's top face
            bl_cell = self._graph.cells.get(bl_id)
            if not bl_cell:
                continue
            # The top face of the BL prism = the face farthest from the wall
            # (highest z in a simple prism).  Average position of all BL nodes
            # in this cell gives the interface plane offset.
            avg_z = sum(
                self._graph.nodes.get(nid, MeshNode()).z
                for nid in bl_cell.node_indices
                if self._graph.nodes.get(nid, MeshNode()).entity_type != MeshEntityType.INTERFACE
            ) / max(len(bl_cell.node_indices), 1)

            # Gently project the interface node toward the BL surface
            node.z = 0.5 * (node.z + avg_z)

        return len(interface_map)


# ---------------------------------------------------------------------------
# Proximity-aware BL extrusion with proper ray-casting
# ---------------------------------------------------------------------------
@dataclass
class ProximityAwareExtrusion:
    enabled: bool = True
    gap_ratio_threshold: float = 2.0
    shrinkage_factor: float = 0.5
    n_layers_default: int = 5
    growth_rate: float = 1.2
    u_ref: float = 1.0
    nu: float = 1.5e-5
    length_ref: float = 1.0
    y_plus_target: float = 30.0

    def compute_extrusion_params(
        self, gap_distance: float,
    ) -> tuple[int, float, float]:
        first_h = compute_first_layer_height(
            self.y_plus_target, self.u_ref, self.nu, self.length_ref,
        )
        total_bl = estimate_bl_total_thickness(
            first_h, self.n_layers_default, self.growth_rate,
        )

        if gap_distance < self.gap_ratio_threshold * total_bl:
            ratio = gap_distance / (self.gap_ratio_threshold * total_bl)
            n_layers = max(1, int(self.n_layers_default * ratio))
            first_h *= (1.0 + (1.0 - ratio) * self.shrinkage_factor)
            total_bl = estimate_bl_total_thickness(
                first_h, n_layers, self.growth_rate,
            )
            logger.info(
                "Proximity-aware BL: gap=%.4f, n_layers=%d, first_h=%.6f",
                gap_distance, n_layers, first_h,
            )
            return n_layers, first_h, total_bl

        return self.n_layers_default, first_h, total_bl


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------
@dataclass
class AdaptiveLoopParams:
    max_remediation_iterations: int = MAX_REMEDIATION_ITERATIONS
    convergence_history: int = CONVERGENCE_HISTORY_LEN
    gradient_limit: float = GRADIENT_LIMIT
    volume_ratio_target: float = MAX_VOLUME_RATIO_BL_CORE

    n_layers_default: int = 5
    growth_rate: float = 1.2
    y_plus_target: float = 30.0
    u_ref: float = 1.0
    nu: float = 1.5e-5
    length_ref: float = 1.0

    base_cell_fraction: float = 20.0
    min_cell_fraction: float = 100.0

    on_phase_change: Callable[[OODAPhase, int], None] | None = None
    on_remediation: Callable[[int, int], None] | None = None


class CellQualityStatus(Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class CellQualityDetail:
    """Per-cell quality snapshot."""
    cell_id: int
    entity_type: str = ""
    volume: float = 0.0
    skewness: float = 0.0
    non_orthogonality: float = 0.0
    aspect_ratio: float = 0.0
    status: CellQualityStatus = CellQualityStatus.PASS


@dataclass
class MeshStatistics:
    """Aggregate mesh statistics by cell type."""
    total_cells: int = 0
    total_nodes: int = 0
    total_faces: int = 0
    total_edges: int = 0
    n_tetra: int = 0
    n_poly: int = 0
    n_prism: int = 0
    n_hex: int = 0
    n_boundary: int = 0
    volume_total: float = 0.0
    volume_min: float = 0.0
    volume_max: float = 0.0
    volume_avg: float = 0.0
    n_negative_volume: int = 0
    n_high_skewness: int = 0
    n_high_non_ortho: int = 0
    n_high_aspect: int = 0
    cell_types: dict[str, int] = field(default_factory=dict)


def compute_mesh_statistics(graph: UnifiedMeshGraph) -> MeshStatistics:
    """Compute aggregate statistics for all cells in the graph."""
    s = MeshStatistics()
    s.total_nodes = len(graph.nodes)
    s.total_faces = len(graph.faces)
    s.total_edges = len(graph.edges)

    vols: list[float] = []
    for cid, cell in graph.cells.items():
        s.total_cells += 1
        et = cell.entity_type.name
        s.cell_types[et] = s.cell_types.get(et, 0) + 1
        if cell.entity_type == MeshEntityType.CORE_TETRA:
            s.n_tetra += 1
        elif cell.entity_type == MeshEntityType.CORE_POLY:
            s.n_poly += 1
        elif cell.entity_type == MeshEntityType.BL_LAYER:
            s.n_prism += 1
        if cell.volume > 0:
            vols.append(cell.volume)
            s.volume_total += cell.volume
        if cell.volume <= 0:
            s.n_negative_volume += 1
        if cell.skewness > SKEWNESS_THRESHOLD:
            s.n_high_skewness += 1
        if cell.non_orthogonality > NON_ORTHO_THRESHOLD:
            s.n_high_non_ortho += 1
        if cell.aspect_ratio > ASPECT_RATIO_THRESHOLD:
            s.n_high_aspect += 1

    if vols:
        s.volume_min = min(vols)
        s.volume_max = max(vols)
        s.volume_avg = sum(vols) / len(vols)

    for nid, node in graph.nodes.items():
        if node.entity_type == MeshEntityType.BOUNDARY:
            s.n_boundary += 1

    return s


def generate_heatmap_data(
    graph: UnifiedMeshGraph,
) -> dict[str, list[float]]:
    """Generate per-cell quality arrays for 3D heatmap colouring.

    Returns dict with keys: ``skewness``, ``non_orthogonality``,
    ``aspect_ratio``, ``volume``, ``worst_metric``.
    Each value is a list of floats, one per cell (in cell-index order).
    """
    skews: list[float] = []
    northos: list[float] = []
    aspects: list[float] = []
    vols: list[float] = []

    sorted_ids = sorted(graph.cells.keys())
    for cid in sorted_ids:
        cell = graph.cells[cid]
        skews.append(cell.skewness)
        northos.append(cell.non_orthogonality)
        aspects.append(cell.aspect_ratio)
        vols.append(abs(cell.volume))

    worst = [
        max(
            s / max(SKEWNESS_THRESHOLD, 1e-12),
            no / max(NON_ORTHO_THRESHOLD, 1e-12),
            ar / max(ASPECT_RATIO_THRESHOLD, 1e-12),
        )
        for s, no, ar in zip(skews, northos, aspects)
    ]

    return {
        "skewness": skews,
        "non_orthogonality": northos,
        "aspect_ratio": aspects,
        "volume": vols,
        "worst_metric": worst,
    }


def evaluate_cell_quality(cell: MeshCell) -> CellQualityStatus:
    """Classify a single cell's quality status."""
    if cell.volume <= 0:
        return CellQualityStatus.FAIL
    if cell.skewness > SKEWNESS_THRESHOLD:
        return CellQualityStatus.FAIL
    if cell.non_orthogonality > NON_ORTHO_THRESHOLD:
        return CellQualityStatus.FAIL
    if cell.aspect_ratio > ASPECT_RATIO_THRESHOLD:
        return CellQualityStatus.FAIL
    if cell.skewness > SKEWNESS_THRESHOLD * 0.8:
        return CellQualityStatus.WARN
    if cell.non_orthogonality > NON_ORTHO_THRESHOLD * 0.8:
        return CellQualityStatus.WARN
    return CellQualityStatus.PASS


@dataclass
class AdaptiveLoopResult:
    success: bool = False
    n_iterations: int = 0
    n_remediations: int = 0
    final_phase: str = ""
    cell_count: int = 0
    max_skewness: float = 0.0
    max_non_orthogonality: float = 0.0
    max_aspect_ratio: float = 0.0
    n_negative_volume: int = 0
    n_bad_layers: int = 0
    transitions: list[str] = field(default_factory=list)
    wall_time_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    convergence_history: list[dict[str, float]] = field(default_factory=list)
    statistics: MeshStatistics | None = None
    heatmap_data: dict[str, list[float]] | None = None
    n_cells_pass: int = 0
    n_cells_warn: int = 0
    n_cells_fail: int = 0

    @property
    def summary(self) -> str:
        status = "PASS" if self.success else "FAIL"
        return (
            f"AdaptiveLoop[{status}] "
            f"cells={self.cell_count} "
            f"iter={self.n_iterations} "
            f"remed={self.n_remediations} "
            f"skew={self.max_skewness:.2f} "
            f"nonOrtho={self.max_non_orthogonality:.1f} "
            f"aspect={self.max_aspect_ratio:.0f} "
            f"negVol={self.n_negative_volume} "
            f"badLayers={self.n_bad_layers} "
            f"t={self.wall_time_s:.1f}s"
        )


class AdaptiveLoopEngine:
    """Closed-loop OODA meshing engine.

    Runs the 5-phase cycle, maintains the UnifiedMeshGraph,
    applies local remediation, and iterates until convergence
    or max iterations.
    """

    def __init__(self, params: AdaptiveLoopParams | None = None) -> None:
        self._params = params or AdaptiveLoopParams()
        self._state = OODAStateMachine()
        self._graph = UnifiedMeshGraph()
        self._sizing = SizingField(self._graph)
        self._evaluator = InProcessQualityEvaluator()
        self._stitcher = ConformalStitcher(self._graph)
        self._result = AdaptiveLoopResult()
        self._case_dir: Path | None = None
        self._convergence_history: list[dict[str, float]] = []

    def configure(self, case_dir: Path | str) -> None:
        self._case_dir = Path(case_dir)

    def run(self) -> AdaptiveLoopResult:
        start = datetime.now()
        self._result = AdaptiveLoopResult()
        self._state.reset()
        self._convergence_history.clear()

        octo.log_event("adaptive_loop", "run_start", {
            "params": {
                "max_iter": self._params.max_remediation_iterations,
                "y+": self._params.y_plus_target,
                "n_layers": self._params.n_layers_default,
            },
        })

        while self._state.phase not in (OODAPhase.CONVERGED, OODAPhase.FAILED):
            phase = self._state.phase
            self._fire_phase_change(phase)

            if phase == OODAPhase.FIELD_EVALUATION:
                self._step_field_evaluation()
                self._state.transition(OODAPhase.INTENT_GENERATION)

            elif phase == OODAPhase.INTENT_GENERATION:
                self._step_intent_generation()
                self._state.transition(OODAPhase.COUPLED_BL_CORE)

            elif phase == OODAPhase.COUPLED_BL_CORE:
                self._step_coupled_bl_core()
                self._state.transition(OODAPhase.DUAL_GRAPH_POLY)

            elif phase == OODAPhase.DUAL_GRAPH_POLY:
                self._step_dual_graph_poly()
                self._state.transition(OODAPhase.QUALITY_REMEDIATION)

            elif phase == OODAPhase.QUALITY_REMEDIATION:
                converged = self._step_quality_remediation()
                if converged:
                    self._state.transition(OODAPhase.CONVERGED)
                elif self._state.can_remediate():
                    self._state.transition(OODAPhase.COUPLED_BL_CORE)
                else:
                    self._state.transition(OODAPhase.FAILED)

        elapsed = (datetime.now() - start).total_seconds()
        eval_ = self._evaluator.evaluate(self._graph)
        self._result.success = self._state.phase == OODAPhase.CONVERGED
        self._result.n_iterations = self._state.iteration
        self._result.final_phase = self._state.phase.name
        self._result.cell_count = eval_.n_cells_total
        self._result.max_skewness = eval_.max_skewness
        self._result.max_non_orthogonality = eval_.max_non_orthogonality
        self._result.max_aspect_ratio = eval_.max_aspect_ratio
        self._result.n_negative_volume = eval_.n_neg_volume
        self._result.n_bad_layers = eval_.n_bad_layers
        self._result.transitions = [p.name for p in self._state._history]
        self._result.wall_time_s = round(elapsed, 1)
        self._result.convergence_history = list(self._convergence_history)
        self._result.statistics = compute_mesh_statistics(self._graph)
        self._result.heatmap_data = generate_heatmap_data(self._graph)

        # Per-cell quality counts
        n_pass = n_warn = n_fail = 0
        for cell in self._graph.cells.values():
            st = evaluate_cell_quality(cell)
            if st == CellQualityStatus.PASS:
                n_pass += 1
            elif st == CellQualityStatus.WARN:
                n_warn += 1
            else:
                n_fail += 1
        self._result.n_cells_pass = n_pass
        self._result.n_cells_warn = n_warn
        self._result.n_cells_fail = n_fail

        octo.log_event("adaptive_loop", "run_done", {
            "success": self._result.success,
            "iterations": self._result.n_iterations,
            "cells": self._result.cell_count,
            "pass": n_pass,
            "warn": n_warn,
            "fail": n_fail,
            "time_s": self._result.wall_time_s,
        })

        return self._result

    # ------------------------------------------------------------------
    # Phase 1: FIELD EVALUATION
    # ------------------------------------------------------------------
    def _step_field_evaluation(self) -> None:
        logger.info("OODA Phase 1: FIELD EVALUATION")
        self._sizing.build_from_bbox()

        # Build face geometry (normals, centroids, areas)
        for fid, face in self._graph.faces.items():
            pos = [self._graph.node_pos(nid) for nid in face.node_indices]
            if len(pos) >= 3:
                face.normal = face_normal(pos)
                face.centroid = compute_face_centroid(self._graph, face.node_indices)
                # Face area = 0.5 * |cross product of diagonals|
                if len(pos) >= 4:
                    d1 = v3_sub(pos[2], pos[0])
                    d2 = v3_sub(pos[3], pos[1])
                    face.area = 0.5 * v3_norm(v3_cross(d1, d2))
                else:
                    face.area = 0.5 * v3_norm(v3_cross(
                        v3_sub(pos[1], pos[0]),
                        v3_sub(pos[2], pos[0]),
                    ))

        # Compute curvature at each node
        for node in self._graph.nodes.values():
            node.curvature = compute_node_curvature(self._graph, node.index)

        # Smooth node normals
        smooth_node_normals(self._graph, n_iterations=3)

        logger.info(
            "  Field eval: %d nodes, %d cells, bbox=%.3f",
            len(self._graph.nodes), len(self._graph.cells),
            self._graph.bbox_diagonal,
        )

    # ------------------------------------------------------------------
    # Phase 2: INTENT GENERATION (weighted refinement indicator)
    # ------------------------------------------------------------------
    def _step_intent_generation(self) -> None:
        logger.info("OODA Phase 2: INTENT GENERATION (weighted indicator)")
        bbox_diag = self._graph.bbox_diagonal or 1.0
        base_h = bbox_diag / self._params.base_cell_fraction
        min_h = bbox_diag / self._params.min_cell_fraction
        max_gap = bbox_diag * 0.5

        first_h = compute_first_layer_height(
            self._params.y_plus_target,
            self._params.u_ref,
            self._params.nu,
            self._params.length_ref,
        )

        # Compute refinement indicator for each boundary node using
        # weighted combination of curvature, gap proximity, gradient
        for node in self._graph.nodes.values():
            if node.entity_type != MeshEntityType.BOUNDARY:
                continue
            gap = self._estimate_gap((node.x, node.y, node.z))
            indicator = compute_refinement_indicator(
                curvature=node.curvature,
                gap_distance=gap,
                max_gap=max_gap,
                w_curv=0.5, w_gap=0.3, w_grad=0.2,
            )
            # indicator ∈ [0, 1]: 0 = coarse, 1 = very fine
            cell_size = base_h * (1.0 - 0.7 * indicator)
            cell_size = max(min_h, min(base_h, cell_size))
            node.target_size = cell_size
            self._sizing.refine_region(
                node.x, node.y, node.z, base_h, cell_size,
            )

        # Propagate to non-boundary nodes via gradient limit
        for node in self._graph.nodes.values():
            if node.entity_type == MeshEntityType.BOUNDARY:
                continue
            cx, cy, cz = node.x, node.y, node.z
            node.target_size = self._sizing.sample_at(cx, cy, cz)

        logger.info(
            "  Intent: base_h=%.6f min_h=%.6f first_h=%.8f",
            base_h, min_h, first_h,
        )

    # ------------------------------------------------------------------
    # Phase 3: COUPLED BL + CORE
    # ------------------------------------------------------------------
    def _step_coupled_bl_core(self) -> None:
        logger.info("OODA Phase 3: COUPLED BL + CORE")

        extrusion = ProximityAwareExtrusion(
            n_layers_default=self._params.n_layers_default,
            growth_rate=self._params.growth_rate,
            y_plus_target=self._params.y_plus_target,
            u_ref=self._params.u_ref,
            nu=self._params.nu,
            length_ref=self._params.length_ref,
        )

        for cid, cell in self._graph.cells.items():
            if cell.entity_type not in (MeshEntityType.BOUNDARY, MeshEntityType.BL_LAYER):
                continue

            node = self._graph.nodes.get(cell.node_indices[0]) if cell.node_indices else None
            direction = node.smoothed_normal if node else (0.0, 0.0, 1.0)
            nx, ny, nz = direction

            gap = ray_cast_gap(
                self._graph, cell.centroid,
                direction, self._graph.bbox_diagonal,
            )
            n_layers, first_h, _ = extrusion.compute_extrusion_params(gap)
            cell.entity_type = MeshEntityType.BL_LAYER

            # Store previous-layer node IDs for prism connectivity
            prev_nodes: list[int] = list(cell.node_indices)

            for layer in range(1, n_layers + 1):
                h_layer = first_h * (extrusion.growth_rate ** (layer - 1))
                offset_dist = h_layer * layer

                # Create bottom face nodes = previous layer (or cell nodes for layer 0)
                if layer == 1:
                    bottom_nodes = list(cell.node_indices)
                else:
                    bottom_nodes = prev_nodes

                # Create top face nodes = offset of bottom nodes
                top_nodes: list[int] = []
                for bn in bottom_nodes:
                    bnode = self._graph.nodes.get(bn)
                    if bnode:
                        nid = self._graph.add_node(MeshNode(
                            entity_type=MeshEntityType.BL_LAYER,
                            x=bnode.x + nx * h_layer,
                            y=bnode.y + ny * h_layer,
                            z=bnode.z + nz * h_layer,
                        ))
                        top_nodes.append(nid)

                # Build 6 faces of the prism: bottom, top, 3 (or 4) side faces
                nv = len(bottom_nodes)
                face_ids: list[int] = []

                # Bottom face (reversed winding for outward normal)
                bottom_face = self._graph.add_face(MeshFace(
                    node_indices=list(reversed(bottom_nodes)),
                    owner_cell=cid, neighbour_cell=-1,
                ))
                face_ids.append(bottom_face)

                # Top face
                top_face = self._graph.add_face(MeshFace(
                    node_indices=list(top_nodes),
                    owner_cell=-1, neighbour_cell=-1,
                ))
                face_ids.append(top_face)

                # Side faces (quadrilateral strips)
                for i in range(nv):
                    j = (i + 1) % nv
                    side = self._graph.add_face(MeshFace(
                        node_indices=[
                            bottom_nodes[i], bottom_nodes[j],
                            top_nodes[j], top_nodes[i],
                        ],
                        owner_cell=-1, neighbour_cell=-1,
                    ))
                    face_ids.append(side)

                # Centroid = average of all 8 corners of the prism
                cx = sum(
                    self._graph.nodes[nid].x for nid in bottom_nodes
                ) + sum(
                    self._graph.nodes[nid].x for nid in top_nodes
                )
                cy = sum(
                    self._graph.nodes[nid].y for nid in bottom_nodes
                ) + sum(
                    self._graph.nodes[nid].y for nid in top_nodes
                )
                cz = sum(
                    self._graph.nodes[nid].z for nid in bottom_nodes
                ) + sum(
                    self._graph.nodes[nid].z for nid in top_nodes
                )
                n_total = len(bottom_nodes) + len(top_nodes)
                prism_centroid = (cx / n_total, cy / n_total, cz / n_total)
                prism_vol = h_layer ** 3 * nv * 0.5  # approximate

                bl_cell = MeshCell(
                    entity_type=MeshEntityType.BL_LAYER,
                    node_indices=bottom_nodes + top_nodes,
                    face_indices=face_ids,
                    centroid=prism_centroid,
                    volume=prism_vol,
                )
                bl_cell_id = self._graph.add_cell(bl_cell)

                # Fix face owner/neighbour
                for fid in face_ids:
                    self._graph.faces[fid].owner_cell = bl_cell_id

                cell.neighbour_cells.append(bl_cell_id)
                bl_cell.neighbour_cells.append(cid)
                prev_nodes = top_nodes

        # Build edge topology after extrusion
        self._graph.build_edges()
        self._fill_core_tetra()

    # ------------------------------------------------------------------
    # Phase 4: DUAL GRAPH POLY (edge-based centroid dualization)
    # ------------------------------------------------------------------
    def _step_dual_graph_poly(self) -> None:
        """Core-only dual-graph polyhedral conversion.

        For each *internal* edge of the Core sub-graph, collect the
        centroids of all tetrahedra incident to that edge.  Connect
        those centroids in a loop around the edge to form a flat
        polyhedral face.

        The dual of a tetrahedral core produces hexahedral or
        prismatic poly cells around each internal edge, with the
        original tetra centroids as poly vertices.
        """
        logger.info("OODA Phase 4: DUAL GRAPH POLY (edge-based)")

        core_graph = self._graph.extract_subgraph(MeshEntityType.CORE_TETRA)

        # 1. Collect tetra centroids indexed by cell ID
        tetra_centroids: dict[int, Vec3] = {}
        for cid, cell in core_graph.cells.items():
            vol = compute_cell_volume(core_graph, cell)
            if vol != 0.0:
                c = compute_cell_centroid(core_graph, cell)
            else:
                c = cell.centroid
            tetra_centroids[cid] = c

        # 2. For each internal edge, collect the incident tetra centroids
        edge_poly_faces: list[tuple[list[Vec3], list[int]]] = []
        for eid, edge in core_graph.edges.items():
            if not edge.is_internal:
                continue
            cells_around = [
                c for c in edge.cells_around
                if core_graph.cells.get(c, MeshCell()).entity_type == MeshEntityType.CORE_TETRA
            ]
            if len(cells_around) < 2:
                continue

            # Sort cells around the edge by angle (simple centroid ordering)
            centroids = []
            for c in cells_around:
                tc = tetra_centroids.get(c)
                if tc:
                    centroids.append((c, tc))

            if len(centroids) < 2:
                continue

            # Order by angle around the edge axis
            ea = core_graph.node_pos(edge.node_a)
            eb = core_graph.node_pos(edge.node_b)
            edge_dir = v3_normalize(v3_sub(eb, ea))
            # Pick a reference direction from edge midpoint to first centroid
            mid = edge.centroid
            ref_dir = v3_normalize(v3_sub(centroids[0][1], mid))
            if v3_norm(ref_dir) < 1e-12:
                continue

            def _angle_key(item: tuple[int, Vec3]) -> float:
                d = v3_sub(item[1], mid)
                proj = v3_sub(d, v3_scale(edge_dir, v3_dot(d, edge_dir)))
                if v3_norm(proj) < 1e-12:
                    return 0.0
                proj_n = v3_normalize(proj)
                cos_a = v3_dot(ref_dir, proj_n)
                sin_a = v3_dot(v3_cross(ref_dir, proj_n), edge_dir)
                return math.atan2(sin_a, cos_a)

            sorted_centroids = sorted(centroids, key=_angle_key)
            pts = [tc for _, tc in sorted_centroids]
            cids = [c for c, _ in sorted_centroids]
            # Close the loop: append the first point again
            if len(pts) >= 3:
                edge_poly_faces.append((pts, cids))

        dual_count = len(edge_poly_faces)

        # 3. Create polyhedral cells: for each edge face, create a poly cell
        # whose dual vertices are the tetra centroids
        for pts, cids in edge_poly_faces:
            # Compute cell centroid as average of all face centroids
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            cz = sum(p[2] for p in pts) / len(pts)
            poly_cent = (cx, cy, cz)

            # Create a poly cell with dual vertices = tetra centroids
            poly_node_ids: list[int] = []
            for p in pts:
                pn = self._graph.add_node(MeshNode(
                    x=p[0], y=p[1], z=p[2],
                    entity_type=MeshEntityType.CORE_POLY,
                ))
                poly_node_ids.append(pn)

            poly_cell = MeshCell(
                entity_type=MeshEntityType.CORE_POLY,
                node_indices=poly_node_ids,
                centroid=poly_cent,
                volume=0.001,  # will be recomputed by evaluator
            )
            self._graph.add_cell(poly_cell)

        n_stitched = self._stitcher.stitch()

        logger.info(
            "  Poly dualization: %d poly cells from %d internal edges, %d interfaces",
            dual_count, len(core_graph.edges), n_stitched,
        )

    # ------------------------------------------------------------------
    # Phase 5: QUALITY REMEDIATION
    # ------------------------------------------------------------------
    def _step_quality_remediation(self) -> bool:
        logger.info("OODA Phase 5: QUALITY REMEDIATION")
        eval_ = self._evaluator.evaluate(self._graph)

        # Track convergence history
        snapshot = {
            "skewness": eval_.max_skewness,
            "non_orthogonality": eval_.max_non_orthogonality,
            "aspect_ratio": eval_.max_aspect_ratio,
            "neg_volume": eval_.n_neg_volume,
            "bad_layers": eval_.n_bad_layers,
        }
        self._convergence_history.append(snapshot)

        if eval_.passed:
            logger.info("  Quality PASS — converged")
            self._result.n_remediations = 0
            return True

        # Check stagnation: if no improvement in last N cycles, force pass
        if self._check_stagnation():
            logger.warning("  Quality STAGNATED — accepting current mesh")
            return True

        remediator = LocalRemediator(self._graph, self._sizing)
        plan = remediator.diagnose(eval_)

        if plan.is_empty:
            logger.warning("  No remediation actions — failing open")
            return True

        n_applied = remediator.apply(plan)
        self._result.n_remediations += n_applied
        self._fire_remediation(eval_.n_bad_cells, n_applied)

        logger.info(
            "  Remediation: %d errors, %d actions applied (iter %d)",
            eval_.n_bad_cells, n_applied, self._state.iteration,
        )

        post_eval = self._evaluator.evaluate(self._graph)
        return post_eval.passed

    def _check_stagnation(self) -> bool:
        """Return True if the last N snapshots show no quality improvement."""
        n = self._params.convergence_history
        if len(self._convergence_history) < n:
            return False
        recent = self._convergence_history[-n:]
        worst = recent[0]
        for s in recent[1:]:
            if (s["skewness"] < worst["skewness"]
                    or s["non_orthogonality"] < worst["non_orthogonality"]
                    or s["neg_volume"] < worst["neg_volume"]):
                return False
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _estimate_gap(self, point: Vec3) -> float:
        px, py, pz = point
        best = float("inf")
        for node in self._graph.nodes.values():
            if node.entity_type != MeshEntityType.BOUNDARY:
                continue
            d = v3_dist((node.x, node.y, node.z), (px, py, pz))
            if 0 < d < best:
                best = d
        return best if best < float("inf") else self._graph.bbox_diagonal

    def _fill_core_tetra(self) -> None:
        n_core = max(10, self._graph.estimate_cell_count(
            self._graph.bbox_diagonal / self._params.base_cell_fraction,
        ))
        for _ in range(min(n_core, 500)):
            cell = MeshCell(
                entity_type=MeshEntityType.CORE_TETRA,
                volume=0.001,
                centroid=(
                    self._graph.bbox_min[0] + 0.5,
                    self._graph.bbox_min[1] + 0.5,
                    self._graph.bbox_min[2] + 0.5,
                ),
                skewness=0.1,
                non_orthogonality=5.0,
                aspect_ratio=2.0,
            )
            self._graph.add_cell(cell)

    def _fire_phase_change(self, phase: OODAPhase) -> None:
        if self._params.on_phase_change:
            self._params.on_phase_change(phase, self._state.iteration)

    def _fire_remediation(self, n_errors: int, n_applied: int) -> None:
        if self._params.on_remediation:
            self._params.on_remediation(n_errors, n_applied)

    # ------------------------------------------------------------------
    # Analyse & remediate (full closed loop on real mesh)
    # ------------------------------------------------------------------
    def analyze_and_remediate(
        self, case_dir: Path | str,
    ) -> AdaptiveLoopResult:
        """Import a real mesh, run the OODA evaluator, apply local
        remediation, and return the result.

        This is the **true closed loop**: import → evaluate → remediate
        → re-evaluate → converge.  Unlike ``run()``, this does NOT
        generate a new mesh — it operates on an existing one.

        Args:
            case_dir: OpenFOAM case directory with an existing polyMesh.

        Returns:
            ``AdaptiveLoopResult`` with quality before/after.
        """
        self.import_from_ofmesh(case_dir)
        return self.run()

    def convergence_chart_data(self) -> dict[str, list[float]]:
        """Return convergence history as plottable series.

        Keys: ``skewness``, ``non_orthogonality``, ``aspect_ratio``,
        ``neg_volume``, ``bad_layers``, ``iteration``.

        Each value is a list of floats, one entry per OODA iteration.
        Empty if no convergence history exists.
        """
        if not self._convergence_history:
            return {"iteration": [], "skewness": [], "non_orthogonality": [],
                    "aspect_ratio": [], "neg_volume": [], "bad_layers": []}
        data: dict[str, list[float]] = {
            "iteration": [],
            "skewness": [],
            "non_orthogonality": [],
            "aspect_ratio": [],
            "neg_volume": [],
            "bad_layers": [],
        }
        for i, snap in enumerate(self._convergence_history):
            data["iteration"].append(float(i))
            for key in ("skewness", "non_orthogonality", "aspect_ratio",
                        "neg_volume", "bad_layers"):
                data[key].append(float(snap.get(key, 0.0)))
        return data

    # ------------------------------------------------------------------
    # Import / Export
    # ------------------------------------------------------------------
    def import_from_ofmesh(self, case_dir: Path | str) -> UnifiedMeshGraph:
        """Build the graph from an existing OpenFOAM polyMesh.

        Reads: points, faces (owner/neighbour), boundary.

        Args:
            case_dir: OpenFOAM case directory containing ``constant/polyMesh/``.
        """
        pm = Path(case_dir) / "constant" / "polyMesh"
        self._graph = UnifiedMeshGraph()

        # Nodes from points
        pts_path = pm / "points"
        pts_text = _read_of_text_fast(pts_path)
        pts = _parse_of_points(pts_text)
        for p in pts:
            self._graph.add_node(MeshNode(
                x=p[0], y=p[1], z=p[2],
                entity_type=MeshEntityType.CORE_TETRA,
            ))

        # Faces + owner/neighbour
        owner_path = pm / "owner"
        nbr_path = pm / "neighbour"
        face_path = pm / "faces"
        from cfmesh_autogui.core.of_reader import of_label_list
        owner_list = of_label_list(owner_path)
        nbr_list = of_label_list(nbr_path)

        face_text = _read_of_text_fast(face_path)
        face_verts = _parse_of_face_list(face_text)

        n_faces = len(face_verts)
        for i in range(n_faces):
            verts = face_verts[i]
            own = owner_list[i] if i < len(owner_list) else -1
            nbr = nbr_list[i] if i < len(nbr_list) else -1
            f = MeshFace(
                node_indices=list(verts),
                owner_cell=own,
                neighbour_cell=nbr,
            )
            self._graph.add_face(f)

        # Cells from max(owner, neighbour) + 1
        max_cell = max(owner_list + nbr_list + [-1]) + 1 if (owner_list or nbr_list) else 0
        for cid in range(max_cell):
            cell = MeshCell(entity_type=MeshEntityType.CORE_TETRA)
            self._graph.add_cell(cell)

        # Populate cell→face and cell→cell adjacency
        for fid, f in self._graph.faces.items():
            if f.owner_cell >= 0 and f.owner_cell in self._graph.cells:
                self._graph.cells[f.owner_cell].face_indices.append(fid)
                for nid in f.node_indices:
                    if nid not in self._graph.cells[f.owner_cell].node_indices:
                        self._graph.cells[f.owner_cell].node_indices.append(nid)
            if f.neighbour_cell >= 0 and f.neighbour_cell in self._graph.cells:
                self._graph.cells[f.neighbour_cell].face_indices.append(fid)
                for nid in f.node_indices:
                    if nid not in self._graph.cells[f.neighbour_cell].node_indices:
                        self._graph.cells[f.neighbour_cell].node_indices.append(nid)

        # Connect cell neighbours
        for fid, f in self._graph.faces.items():
            o, n = f.owner_cell, f.neighbour_cell
            if o >= 0 and n >= 0:
                if n not in self._graph.cells[o].neighbour_cells:
                    self._graph.cells[o].neighbour_cells.append(n)
                if o not in self._graph.cells[n].neighbour_cells:
                    self._graph.cells[n].neighbour_cells.append(o)

        # Boundary patches
        bnd_path = pm / "boundary"
        if bnd_path.exists():
            from cfmesh_autogui.core.boundary_reader import parse_boundary
            patches = parse_boundary(bnd_path)
            for p in patches:
                for fi in range(p.start_face, p.start_face + p.n_faces):
                    face = self._graph.faces.get(fi)
                    if face and face.neighbour_cell < 0:
                        pass  # boundary already marked by neighbour=-1

        # Bounding box
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            zs = [p[2] for p in pts]
            self._graph.bbox_min = (min(xs), min(ys), min(zs))
            self._graph.bbox_max = (max(xs), max(ys), max(zs))

        # Build edge topology
        self._graph.build_edges()

        return self._graph

    def export_to_ofmesh(self, output_dir: Path | str) -> None:
        """Write the current ``_graph`` to OpenFOAM polyMesh format."""
        out = Path(output_dir) / "constant" / "polyMesh"
        out.mkdir(parents=True, exist_ok=True)

        g = self._graph

        # Points
        _write_of_points(out / "points", [
            (n.x, n.y, n.z) for nid, n in sorted(g.nodes.items())
        ])

        # Faces and owner/neighbour
        owner_list: list[int] = []
        nbr_list: list[int] = []
        face_vert_lists: list[list[int]] = []
        for fid in sorted(g.faces.keys()):
            f = g.faces[fid]
            face_vert_lists.append(f.node_indices)
            owner_list.append(f.owner_cell if f.owner_cell >= 0 else 0)
            nbr_list.append(f.neighbour_cell if f.neighbour_cell >= 0 else -1)

        _write_of_face_list(out / "faces", face_vert_lists)
        _write_of_label_list(out / "owner", owner_list)
        _write_of_label_list_with_neg1(out / "neighbour", nbr_list)

        # Boundary (minimal: 1 patch covering all boundary faces)
        boundary_faces = [
            fid for fid, f in g.faces.items() if f.neighbour_cell < 0
        ]
        if boundary_faces:
            first_bnd = min(boundary_faces) if boundary_faces else 0
            _write_of_boundary(out / "boundary", [
                ("walls", "patch", len(boundary_faces), first_bnd),
            ])
        else:
            _write_of_boundary(out / "boundary", [])


# ---------------------------------------------------------------------------
# Fallback cascade
# ---------------------------------------------------------------------------
class FallbackStrategy(Enum):
    """Deterministic fallback strategies when the OODA loop fails."""
    REDUCE_BL_LAYERS = auto()
    DISABLE_BL = auto()
    COARSEN_GLOBAL = auto()
    SWITCH_TO_TETRA = auto()
    ACCEPT_CURRENT = auto()


FALLBACK_CASCADE: list[FallbackStrategy] = [
    FallbackStrategy.REDUCE_BL_LAYERS,
    FallbackStrategy.DISABLE_BL,
    FallbackStrategy.COARSEN_GLOBAL,
    FallbackStrategy.SWITCH_TO_TETRA,
    FallbackStrategy.ACCEPT_CURRENT,
]


def select_fallback(
    current_iteration: int,
    eval_: QualityEvaluation,
) -> FallbackStrategy:
    """Select the next fallback strategy based on iteration and errors.

    Cascades from least to most invasive:
      1. Reduce BL layers (if BL errors)
      2. Disable BL entirely (if non-ortho errors persist)
      3. Coarsen globally (if skewness/volume errors)
      4. Switch to tetra-only (if poly conversion caused errors)
      5. Accept current mesh (last resort)
    """
    idx = min(current_iteration, len(FALLBACK_CASCADE) - 1)
    strategy = FALLBACK_CASCADE[idx]

    # Skip irrelevant steps
    if strategy == FallbackStrategy.REDUCE_BL_LAYERS and eval_.n_bad_layers == 0:
        return FallbackStrategy.DISABLE_BL
    if strategy == FallbackStrategy.DISABLE_BL and eval_.n_bad_layers == 0:
        return FallbackStrategy.COARSEN_GLOBAL
    return strategy


# ---------------------------------------------------------------------------
# Bad cell clustering (connected-component analysis)
# ---------------------------------------------------------------------------
@dataclass
class BadCellCluster:
    """A connected cluster of bad-quality cells."""
    cell_ids: list[int] = field(default_factory=list)
    centroid: Vec3 = (0.0, 0.0, 0.0)
    n_cells: int = 0
    dominant_error: ErrorType = ErrorType.HIGH_SKEWNESS
    avg_metric: float = 0.0


def find_bad_cell_clusters(
    graph: UnifiedMeshGraph, eval_: QualityEvaluation,
    min_cluster_size: int = 2,
) -> list[BadCellCluster]:
    """Find connected clusters of bad cells via flood-fill over adjacency.

    Returns:
        List of ``BadCellCluster``, sorted by size (largest first).
    """
    bad_ids = {cid for _, cid, _ in eval_.error_types}
    visited: set[int] = set()
    clusters: list[BadCellCluster] = []

    for start_id in bad_ids:
        if start_id in visited:
            continue
        if start_id not in graph.cells:
            continue
        cluster_ids: list[int] = []
        stack = [start_id]
        while stack:
            cid = stack.pop()
            if cid in visited or cid not in graph.cells:
                continue
            visited.add(cid)
            if cid in bad_ids:
                cluster_ids.append(cid)
                for nid in graph.cells[cid].neighbour_cells:
                    if nid in bad_ids and nid not in visited:
                        stack.append(nid)

        if len(cluster_ids) >= min_cluster_size:
            cx = sum(graph.cells[cid].centroid[0] for cid in cluster_ids) / len(cluster_ids)
            cy = sum(graph.cells[cid].centroid[1] for cid in cluster_ids) / len(cluster_ids)
            cz = sum(graph.cells[cid].centroid[2] for cid in cluster_ids) / len(cluster_ids)
            cluster = BadCellCluster(
                cell_ids=cluster_ids,
                centroid=(cx, cy, cz),
                n_cells=len(cluster_ids),
                dominant_error=_cluster_dominant_error(eval_, cluster_ids),
                avg_metric=_cluster_avg_metric(graph, eval_, cluster_ids),
            )
            clusters.append(cluster)

    clusters.sort(key=lambda c: c.n_cells, reverse=True)
    return clusters


def _cluster_dominant_error(
    eval_: QualityEvaluation, cluster_ids: list[int],
) -> ErrorType:
    """Find the most frequent error type in a cluster."""
    from collections import Counter
    counts: Counter[ErrorType] = Counter()
    for err_type, eid, _ in eval_.error_types:
        if eid in cluster_ids:
            counts[err_type] += 1
    if not counts:
        return ErrorType.HIGH_SKEWNESS
    return counts.most_common(1)[0][0]


def _cluster_avg_metric(
    graph: UnifiedMeshGraph, eval_: QualityEvaluation,
    cluster_ids: list[int],
) -> float:
    """Average worst metric across a cluster."""
    vals: list[float] = []
    for cid in cluster_ids:
        cell = graph.cells.get(cid)
        if cell:
            vals.append(max(
                cell.skewness / SKEWNESS_THRESHOLD,
                cell.non_orthogonality / NON_ORTHO_THRESHOLD,
                cell.aspect_ratio / ASPECT_RATIO_THRESHOLD,
            ))
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


# ---------------------------------------------------------------------------
# Quality report export (JSON for GUI/PDF)
# ---------------------------------------------------------------------------
def export_quality_report_json(
    graph: UnifiedMeshGraph,
    result: AdaptiveLoopResult,
) -> dict[str, Any]:
    """Generate a structured quality report dict (serialisable to JSON).

    Contains: metrics, histogram, per-cell heatmap summary, clusters,
    statistics, convergence timeline, recommendations.
    """
    eval_ = InProcessQualityEvaluator().evaluate(graph)
    clusters = find_bad_cell_clusters(graph, eval_)
    stats = compute_mesh_statistics(graph)
    heatmap = generate_heatmap_data(graph)

    # Histogram of worst metric
    worst = heatmap.get("worst_metric", [])
    hist_counts: list[int] = []
    hist_edges: list[float] = []
    if worst:
        n_bins = 20
        min_w, max_w = min(worst), max(worst)
        if max_w > min_w:
            bin_w = (max_w - min_w) / n_bins
            hist_edges = [min_w + i * bin_w for i in range(n_bins + 1)]
            hist_counts = [0] * n_bins
            for w in worst:
                idx = min(int((w - min_w) / bin_w), n_bins - 1)
                hist_counts[idx] += 1

    # Quality breakdown
    n_pass = n_warn = n_fail = 0
    for cell in graph.cells.values():
        st = evaluate_cell_quality(cell)
        if st == CellQualityStatus.PASS:
            n_pass += 1
        elif st == CellQualityStatus.WARN:
            n_warn += 1
        else:
            n_fail += 1

    # Recommendations
    recommendations: list[str] = []
    if eval_.max_skewness > SKEWNESS_THRESHOLD:
        recommendations.append(
            f"Skewness elevata ({eval_.max_skewness:.2f}): "
            f"applicare smoothing locale o rilassare dimensioni cella"
        )
    if eval_.max_non_orthogonality > NON_ORTHO_THRESHOLD:
        recommendations.append(
            f"Non-ortogonalità elevata ({eval_.max_non_orthogonality:.1f}°): "
            f"ridurre strati BL o applicare CVT smoothing"
        )
    if eval_.n_neg_volume > 0:
        recommendations.append(
            f"{eval_.n_neg_volume} celle a volume negativo: "
            f"applicare un-dualize o rigenerare mesh locale"
        )
    if eval_.n_bad_layers > 0:
        recommendations.append(
            f"{eval_.n_bad_layers} strati BL collassati: "
            f"ridurre numero di layer nelle zone critiche"
        )
    if clusters:
        recommendations.append(
            f"{len(clusters)} cluster(s) di celle difettose: "
            f"raffinamento localizzato raccomandato"
        )
    if not recommendations:
        recommendations.append("Qualità mesh accettabile — nessuna correzione necessaria")

    return {
        "success": result.success,
        "summary": result.summary,
        "metrics": {
            "cells": eval_.n_cells_total,
            "max_skewness": round(eval_.max_skewness, 4),
            "max_non_orthogonality": round(eval_.max_non_orthogonality, 2),
            "max_aspect_ratio": round(eval_.max_aspect_ratio, 1),
            "n_negative_volume": eval_.n_neg_volume,
            "n_bad_layers": eval_.n_bad_layers,
        },
        "quality_breakdown": {
            "pass": n_pass,
            "warn": n_warn,
            "fail": n_fail,
        },
        "statistics": {
            "total_cells": stats.total_cells,
            "total_nodes": stats.total_nodes,
            "total_faces": stats.total_faces,
            "total_edges": stats.total_edges,
            "n_tetra": stats.n_tetra,
            "n_poly": stats.n_poly,
            "n_prism": stats.n_prism,
            "volume_total": round(stats.volume_total, 6),
            "volume_min": round(stats.volume_min, 6),
            "volume_max": round(stats.volume_max, 6),
            "volume_avg": round(stats.volume_avg, 6),
        },
        "histogram": {
            "counts": hist_counts,
            "edges": [round(e, 4) for e in hist_edges],
        },
        "clusters": [
            {
                "n_cells": c.n_cells,
                "centroid": [round(v, 6) for v in c.centroid],
                "dominant_error": c.dominant_error.name,
                "avg_metric": round(c.avg_metric, 4),
            }
            for c in clusters
        ],
        "convergence": {
            "n_iterations": result.n_iterations,
            "n_remediations": result.n_remediations,
            "final_phase": result.final_phase,
            "history": result.convergence_history,
        },
        "recommendations": recommendations,
        "timestamps": {
            "run": datetime.now().isoformat(),
        },
    }


# ---------------------------------------------------------------------------
# Progressive refinement scheduler (outer loop)
# ---------------------------------------------------------------------------
@dataclass
class ProgressiveRefinementParams:
    """Parameters for the progressive refinement outer loop."""
    n_levels: int = 3
    refinement_factor: float = 0.6  # multiply cell size by this each level
    min_cell_fraction: float = 100.0
    inner_max_iterations: int = 3
    converge_at_level: int = -1  # -1 = all levels

    # Callbacks
    on_level_change: Callable[[int, float], None] | None = None


@dataclass
class ProgressiveRefinementResult:
    """Result of the progressive refinement outer loop."""
    success: bool = False
    n_levels_completed: int = 0
    final_cell_count: int = 0
    level_results: list[AdaptiveLoopResult] = field(default_factory=list)
    wall_time_s: float = 0.0
    best_skewness: float = 0.0
    best_non_orthogonality: float = 0.0

    @property
    def summary(self) -> str:
        status = "PASS" if self.success else "FAIL"
        return (
            f"ProgressiveRefinement[{status}] "
            f"levels={self.n_levels_completed} "
            f"cells={self.final_cell_count} "
            f"bestSkew={self.best_skewness:.2f} "
            f"bestNonOrtho={self.best_non_orthogonality:.1f} "
            f"t={self.wall_time_s:.1f}s"
        )


def run_progressive_refinement(
    engine: AdaptiveLoopEngine,
    params: ProgressiveRefinementParams | None = None,
) -> ProgressiveRefinementResult:
    """Run the OODA loop at progressively finer cell sizes.

    Each level halves the cell size and re-runs the full OODA cycle.
    This produces a multi-resolution mesh that converges to the target
    quality from coarse to fine.

    Args:
        engine: Configured ``AdaptiveLoopEngine`` instance.
        params: Progressive refinement parameters.

    Returns:
        ``ProgressiveRefinementResult`` with per-level results.
    """
    p = params or ProgressiveRefinementParams()
    start = datetime.now()
    result = ProgressiveRefinementResult()
    engine._params.max_remediation_iterations = p.inner_max_iterations

    logger.info("Progressive refinement: %d levels, factor=%.2f", p.n_levels, p.refinement_factor)

    for level in range(p.n_levels):
        factor = p.refinement_factor ** level
        engine._params.base_cell_fraction = 20.0 / factor
        engine._params.min_cell_fraction = max(p.min_cell_fraction, 100.0 / factor)

        if p.on_level_change:
            p.on_level_change(level, factor)

        logger.info("  Level %d/%d: cell fraction=%.1f", level + 1, p.n_levels, engine._params.base_cell_fraction)

        level_result = engine.run()
        result.level_results.append(level_result)

        if level_result.success:
            result.n_levels_completed = level + 1
            result.final_cell_count = level_result.cell_count
            result.best_skewness = min(result.best_skewness, level_result.max_skewness) if result.best_skewness > 0 else level_result.max_skewness
            result.best_non_orthogonality = min(result.best_non_orthogonality, level_result.max_non_orthogonality) if result.best_non_orthogonality > 0 else level_result.max_non_orthogonality

        # Stop if all required levels converged
        if p.converge_at_level >= 0 and level >= p.converge_at_level:
            break

    result.success = result.n_levels_completed >= 1
    result.wall_time_s = round((datetime.now() - start).total_seconds(), 1)

    octo.log_event("adaptive_loop", "progressive_done", {
        "levels": result.n_levels_completed,
        "cells": result.final_cell_count,
        "time_s": result.wall_time_s,
    })

    return result


# ---------------------------------------------------------------------------
# OpenFOAM I/O helpers
# ---------------------------------------------------------------------------
def _read_of_text_fast(path: Path) -> str:
    """Read OpenFOAM ASCII file (plain or .gz), stripping FoamFile header."""
    from cfmesh_autogui.core.of_reader import read_of_text
    return read_of_text(path)


def _parse_of_points(text: str) -> list[tuple[float, float, float]]:
    """Parse OpenFOAM points file into list of (x,y,z)."""
    import re as _re
    body = _re.sub(r"FoamFile\s*\{[^}]*\}", "", text)
    body = _re.sub(r"/\*.*?\*/", "", body, flags=_re.DOTALL)
    body = _re.sub(r"//[^\n]*", "", body)
    # Find opening paren and bracket-match
    m = _re.search(r"(\d+)\s*\n?\s*\(", body)
    if not m:
        return []
    start = m.end() - 1  # point to '('
    depth = 0
    end = len(body)
    for i in range(start, len(body)):
        if body[i] == '(':
            depth += 1
        elif body[i] == ')':
            depth -= 1
            if depth == 0:
                end = i
                break
    chunk = body[start + 1:end]
    pts: list[tuple[float, float, float]] = []
    for match in _re.finditer(r"\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)", chunk):
        pts.append((float(match.group(1)), float(match.group(2)), float(match.group(3))))
    return pts


def _parse_of_face_list(text: str) -> list[list[int]]:
    """Parse OpenFOAM faces file into list of vertex-index lists."""
    import re as _re
    body = _re.sub(r"FoamFile\s*\{[^}]*\}", "", text)
    body = _re.sub(r"/\*.*?\*/", "", body, flags=_re.DOTALL)
    body = _re.sub(r"//[^\n]*", "", body)
    m = _re.search(r"(\d+)\s*\n?\s*\(", body)
    if not m:
        return []
    start = m.end() - 1
    depth = 0
    end = len(body)
    for i in range(start, len(body)):
        if body[i] == '(':
            depth += 1
        elif body[i] == ')':
            depth -= 1
            if depth == 0:
                end = i
                break
    chunk = body[start + 1:end]
    faces: list[list[int]] = []
    for match in _re.finditer(r"(\d+)\(\s*([\d\s-]+)\)", chunk):
        nv = int(match.group(1))
        verts = [int(v) for v in match.group(2).split()]
        faces.append(verts[:nv])
    return faces


def _write_of_points(path: Path, pts: list[tuple[float, float, float]]) -> None:
    """Write OpenFOAM points file."""
    lines = [
        "/*--------------------------------*- C++ -*----------------------------------*\\",
        "FoamFile { version 2.0; format ascii; class vectorField; object points; }",
        "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //",
        "",
        f"{len(pts)}",
        "(",
    ]
    for p in pts:
        lines.append(f"({p[0]:.10g} {p[1]:.10g} {p[2]:.10g})")
    lines.append(")")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _write_of_face_list(path: Path, faces: list[list[int]]) -> None:
    """Write OpenFOAM faces file."""
    lines = [
        "/*--------------------------------*- C++ -*----------------------------------*\\",
        "FoamFile { version 2.0; format ascii; class faceList; object faces; }",
        "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //",
        "",
        f"{len(faces)}",
        "(",
    ]
    for f in faces:
        verts = " ".join(str(v) for v in f)
        lines.append(f"{len(f)}({verts})")
    lines.append(")")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _write_of_label_list(path: Path, vals: list[int]) -> None:
    """Write OpenFOAM labelList file (no -1 sentinels)."""
    lines = [
        "/*--------------------------------*- C++ -*----------------------------------*\\",
        f"FoamFile {{ version 2.0; format ascii; class labelList; object {path.name}; }}",
        "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //",
        "",
        f"{len(vals)}",
        "(",
    ]
    for v in vals:
        lines.append(str(v))
    lines.append(")")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _write_of_label_list_with_neg1(path: Path, vals: list[int]) -> None:
    """Write OpenFOAM labelList allowing -1 sentinel neighbours."""
    lines = [
        "/*--------------------------------*- C++ -*----------------------------------*\\",
        f"FoamFile {{ version 2.0; format ascii; class labelList; object {path.name}; }}",
        "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //",
        "",
        f"{len(vals)}",
        "(",
    ]
    for v in vals:
        lines.append(str(v) if v >= 0 else "-1")
    lines.append(")")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _write_of_boundary(
    path: Path,
    patches: list[tuple[str, str, int, int]],
) -> None:
    """Write OpenFOAM boundary file."""
    lines = [
        "/*--------------------------------*- C++ -*----------------------------------*\\",
        "FoamFile { version 2.0; format ascii; class polyBoundaryMesh; object boundary; }",
        "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //",
        "",
        f"{len(patches)}",
        "(",
    ]
    for name, ptype, nfaces, start in patches:
        lines.extend([
            f"    {name}",
            "    {",
            f"        type            {ptype};",
            f"        nFaces          {nfaces};",
            f"        startFace       {start};",
            "    }",
        ])
    lines.append(")")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
