"""GMSH Python API wrapper — CAD analysis, surface mesh, volume mesh.

GMSH runs natively on Windows (no WSL). It uses OpenCASCADE to read
STEP files directly, compute curvature and proximity fields, and
generate high-quality surface and volume meshes.

Integration with cfMesh:
  GMSH_HYBRID  → GMSH analyses CAD + generates surface STL with
                  curvature-aware refinement → cfMesh fills volume
  GMSH_DIRECT  → GMSH generates tetrahedral mesh + BL → meshio
                  converts to OpenFOAM polyMesh

Requirements:
  pip install gmsh meshio
"""
from __future__ import annotations

import logging
import math
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_GMSH_INITIALIZED = False
_GMSH_LOCK = threading.Lock()  # ✅ F-014

# Issue 2 fix: single canonical detail definition used by all three
# functions (compute_sizing, generate_surface_stl, generate_volume_mesh).
# Representative computed sizes assume lc_user ≈ max_extent × 0.02 (1 m bbox → 0.02).
# Actual runtime values = lc_user × min_mult / max_mult.
_GMSH_DETAIL = {
    # very_coarse/very_fine extend the same progression to match the 5-level
    # GUI slider ("Molto Grossolana".."Molto Fine") — without these, the two
    # extreme slider positions fell back silently to "medium" (the dict
    # lookup default), making them look broken/no-op in the UI.
    # cells_across: how many cells to put across the domain's CROSS-SECTION
    # (the bounding box's median dimension). This is the physically meaningful
    # resolution knob for CFD and the one that decides whether a mesh is usable
    # at all — see the "cross-section resolution" block in
    # _configure_adaptive_sizing for why max_mult alone was not enough.
    # Rules of thumb: <10 across a passage cannot resolve a developing profile;
    # ~20 is a usable RANS bulk; 30-50 is what a hand-built production mesh
    # uses. Boundary layers and curvature refinement are layered on top of this.
    "very_coarse": {"curv_angle": 45, "min_mult": 0.08,  "max_mult": 2.5,  "vol_mult": 0.03,
                     "min_size": 0.002, "max_size": 0.06, "cells_across": 8},
    "coarse": {"curv_angle": 30, "min_mult": 0.05,  "max_mult": 2.0,  "vol_mult": 0.02,
               "min_size": 0.001, "max_size": 0.04, "cells_across": 13},
    "medium": {"curv_angle": 18, "min_mult": 0.02,  "max_mult": 1.5,  "vol_mult": 0.015,
               "min_size": 0.0004, "max_size": 0.03, "cells_across": 20},
    "fine":   {"curv_angle": 10, "min_mult": 0.01,  "max_mult": 1.0,  "vol_mult": 0.01,
               "min_size": 0.0002, "max_size": 0.02, "cells_across": 32},
    "very_fine": {"curv_angle": 6, "min_mult": 0.005, "max_mult": 0.75, "vol_mult": 0.007,
                  "min_size": 0.0001, "max_size": 0.015, "cells_across": 48},
}


def _fmt_bytes(n: int) -> str:
    """Human-readable byte count (e.g. "14.2 GB"), best-effort."""
    try:
        val = float(n)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if val < 1024 or unit == "TB":
                return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
            val /= 1024
    except Exception:
        logger.debug("gmsh_wrapper.fmt_bytes: cannot format %r, returning raw", n)
    return str(n)


def _available_ram_bytes() -> int:
    """Free physical RAM, best-effort. No third-party dependency (ctypes
    is stdlib) so this works the same in a frozen/PyInstaller build."""
    if sys.platform == "win32":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            if stat.ullAvailPhys:
                return int(stat.ullAvailPhys)
        except Exception:
            logger.debug("gmsh_wrapper: Windows RAM probe failed", exc_info=True)
    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size)
    except Exception:
        logger.debug("gmsh_wrapper: sysconf probe failed", exc_info=True)
    return 4 * 1024**3  # unknown platform/failure: assume 4 GB free, conservative


def _total_ram_bytes() -> int:
    """Total physical RAM, best-effort.

    Used as a floor under the cell budget so a machine that merely happens to
    be busy right now (browser open, previous mesh still in the viewer) doesn't
    silently produce a much coarser mesh than the same machine would when idle.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            class _MEMSTAT(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMSTAT()
            stat.dwLength = ctypes.sizeof(_MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            if stat.ullTotalPhys:
                return int(stat.ullTotalPhys)
        except Exception:
            logger.debug("gmsh_wrapper: Windows RAM probe failed", exc_info=True)
    try:
        return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except Exception:
        logger.debug("gmsh_wrapper: sysconf probe failed", exc_info=True)
    return 8 * 1024**3


def _hardware_budget(max_cells_override: int | None = None) -> dict:
    """CPU/RAM snapshot used to keep automatic mesh refinement bounded
    on the machine actually running it, instead of a single global
    'Detail Level' the user has to guess a safe value for by hand.
    max_cells_override lets the GUI's "Max cells target" field cap this
    directly instead of relying on the hardware estimate."""
    cpu_count = os.cpu_count() or 4
    ram_bytes = _available_ram_bytes()
    is_explicit_target = bool(max_cells_override and max_cells_override > 0)
    if is_explicit_target:
        max_cells = max_cells_override
    else:
        # Bytes per cell. The old 2.5 KB/cell was far too pessimistic and was
        # the second reason automatic meshes came out too coarse for CFD (the
        # first being the cross-section sizing — see _configure_adaptive_sizing).
        # A tet mesh costs roughly: nodes ~ N/5.5 at 24 B, plus 4 int32 per tet
        # = ~20 B/cell in GMSH's own storage. Even allowing generously for
        # meshio's arrays and the OpenFOAM polyMesh written afterwards, 1 KB per
        # cell is already several times the real peak — and those stages run
        # SEQUENTIALLY, not all at once, which the old "copies that exist at
        # once" reasoning assumed. At 2.5 KB/cell a 32 GB machine with 16 GB
        # free was capped at 2.5M cells, when the same part needs ~9M to be a
        # usable CFD mesh.
        #
        # Also floor the estimate against TOTAL RAM, not just what happens to
        # be free right now: with a browser open, "available" can halve, and
        # the mesh budget should not silently depend on that.
        by_available = (ram_bytes * 0.5) / 1000
        by_total = (_total_ram_bytes() * 0.25) / 1000
        max_cells = max(200_000, int(max(by_available, by_total)))
    # The explicit target ("Max cells target" / Mesh Fineness slider) must
    # never exceed what the machine can actually hold: a "very fine" 20M
    # request on a low-RAM box would otherwise make GMSH run away and
    # crash/OOM. Cap it at the hardware floor the auto path would have
    # chosen; below that the user's explicit target is respected.
    hw_floor = max(200_000, int(max((ram_bytes * 0.5) / 1000, (_total_ram_bytes() * 0.25) / 1000)))
    if max_cells > hw_floor:
        logger.warning(
            "Cell target %d exceeds hardware budget (%d) — recomputing sizing "
            "to %d cells to avoid an out-of-memory run.",
            max_cells, hw_floor, hw_floor,
        )
        max_cells = hw_floor
    return {
        "cpu_count": cpu_count, "ram_available_bytes": ram_bytes,
        "max_cells": max_cells, "is_explicit_target": is_explicit_target,
    }


def _scan_feature_sizes(gmsh_mod, max_extent: float) -> dict:
    """Measure the geometry's own small-feature scale instead of
    assuming one is a fixed fraction of the overall bounding box — that
    assumption breaks down once a model spans a large size range (a 3 m
    valve body with ~0.25 mm fillets is a >10000:1 ratio; a uniform
    floor tight enough for the fillets would try to mesh the whole 3 m
    body that fine, and one loose enough for the body leaves the
    fillets under-resolved into degenerate elements — this was the
    actual cause of the 600+ incorrectly-oriented dual-mesh faces found
    testing the real Parte4 valve part).

    Returns the shortest curve length, a cutoff separating "small"
    curves from the rest (their 25th percentile by length), and the
    tags of those small curves — candidates for local refinement.
    """
    curves = gmsh_mod.model.getEntities(1)
    curve_lengths: list[tuple[int, float]] = []
    for dim, tag in curves:
        length = None
        try:
            length = gmsh_mod.model.occ.getMass(dim, tag)
        except Exception:
            # occ.getMass only works on OCC-kernel entities (STEP input) —
            # fall back to the bbox-based length below
            logger.debug("gmsh_wrapper: occ.getMass failed for dim=%d tag=%d", dim, tag, exc_info=True)
        if not length or length <= 1e-9:
            # occ.getMass only works on OCC-kernel entities (STEP input).
            # STL input goes through classifySurfaces()/createGeometry(),
            # which builds discrete/"geo"-kernel curves instead — fall
            # back to the curve's own bounding-box diagonal as a length
            # proxy (exact for a straight edge, a reasonable estimate for
            # a curved one, good enough to rank "small" vs "large").
            try:
                bbox = gmsh_mod.model.getBoundingBox(dim, tag)
                dx, dy, dz = bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]
                length = math.sqrt(dx * dx + dy * dy + dz * dz)
            except Exception:
                # degenerate entity — skip it, keep measuring the rest
                logger.debug("gmsh_wrapper: getBoundingBox failed dim=%d tag=%d", dim, tag, exc_info=True)
                continue
        if length and length > 1e-9:
            curve_lengths.append((tag, length))

    if not curve_lengths:
        fallback = max_extent * 0.01
        return {"min_feature": fallback, "small_cutoff": fallback, "small_curve_tags": []}

    lens_sorted = sorted(length for _, length in curve_lengths)
    min_feature = lens_sorted[0]
    idx = max(0, len(lens_sorted) // 4)
    small_cutoff = lens_sorted[idx]
    # The quartile above is RELATIVE to the other curves on this same part —
    # on a geometry with no genuine two-tier "big body / tiny fillet"
    # structure (a plain pipe, a simple duct: every curve roughly the same
    # size), the bottom quartile still gets flagged and locally refined for
    # no reason, purely because *something* has to be the shortest quarter.
    # A "small feature" only means something relative to the WHOLE part
    # (max_extent) — clamp the cutoff so a curve must also be small on that
    # absolute scale, not merely small among its neighbours. Never binds on
    # the documented reference case (a 3m valve with ~0.25mm fillets: the
    # quartile cutoff there is already far below 8% of max_extent), only
    # suppresses spurious flagging when nothing on the part is actually small.
    small_cutoff = min(small_cutoff, max_extent * 0.08)
    small_curve_tags = [tag for tag, length in curve_lengths if length <= small_cutoff]
    return {
        "min_feature": min_feature,
        "small_cutoff": small_cutoff,
        "small_curve_tags": small_curve_tags,
    }


def _detect_surface_gaps(gmsh_mod, max_extent: float, max_pairs: int = 4000) -> list[dict]:
    """Find pairs of surfaces close to each other — narrow internal
    channels/passages (a valve's internal bore near its seat, say) that
    curve-length scanning alone misses entirely: a gap can be tight with
    no short edge anywhere near it. GMSH's own automatic gap-aware field
    (AutomaticMeshSizeField, medial-axis based) would be the mature
    solution here, but the installed GMSH build lacks the P4EST it
    requires ("Gmsh has to be compiled with HXT and P4EST" — confirmed
    by trying it directly). This is the same pragmatic fallback a proven
    reference tool for this exact geometry (an external snappyHexMesh
    geometry analyzer script) uses: bounding-box-to-bounding-box
    distance as a cheap proxy for true surface-to-surface distance, not
    exact but good enough to flag WHERE a gap is probably tight.

    O(n^2) over surface pairs — fine for realistic part complexity
    (dozens to low hundreds of surfaces); max_pairs guards against
    pathological cases without silently hanging.
    """
    surfaces = gmsh_mod.model.getEntities(2)
    n = len(surfaces)
    if n < 2 or n * (n - 1) // 2 > max_pairs:
        return []

    boxes = [(tag, gmsh_mod.model.getBoundingBox(dim, tag)) for dim, tag in surfaces]

    def bbox_distance(b1, b2):
        d2 = 0.0
        for i in range(3):
            lo1, hi1, lo2, hi2 = b1[i], b1[i + 3], b2[i], b2[i + 3]
            if hi1 < lo2:
                d2 += (lo2 - hi1) ** 2
            elif hi2 < lo1:
                d2 += (lo1 - hi2) ** 2
        return d2 ** 0.5

    # Only genuinely narrow gaps are worth dedicated refinement — a
    # threshold relative to overall domain size, not an absolute value,
    # so this works the same on a 3m valve or a 3cm fitting.
    gap_threshold = max_extent * 0.02
    gaps = []
    for i in range(n):
        tag_a, box_a = boxes[i]
        for j in range(i + 1, n):
            tag_b, box_b = boxes[j]
            dist = bbox_distance(box_a, box_b)
            if 1e-9 < dist < gap_threshold:
                cx = ((box_a[0] + box_a[3]) / 2 + (box_b[0] + box_b[3]) / 2) / 2
                cy = ((box_a[1] + box_a[4]) / 2 + (box_b[1] + box_b[4]) / 2) / 2
                cz = ((box_a[2] + box_a[5]) / 2 + (box_b[2] + box_b[5]) / 2) / 2
                gaps.append({"distance": dist, "center": (cx, cy, cz)})

    gaps.sort(key=lambda g: g["distance"])
    return gaps[:50]  # cap field count — each becomes a Ball field below


def _sample_curvature_size_field(
    gmsh_mod, h_min: float, h_max: float, angle_sensitivity_rad: float,
    max_samples: int = 20000,
) -> tuple:
    """A-priori curvature sampling: for every curve and surface, read the
    local discrete curvature radius directly from GMSH's own OpenCASCADE
    kernel (gmsh.model.getCurvature — exact for analytic CAD geometry,
    unlike estimating curvature from a triangulated tessellation) and
    convert it to a target element size via the standard chord-angle
    relation: an element spanning a curvature radius R by an angular
    tolerance ANGOLO_SENSIBILITA_RAD subtends a chord of length
    ~= R * angle (the same relation GMSH's own MinimumElementsPerTwoPi
    option encodes implicitly — made explicit and directly tunable here).

    Returns (points[N,3], target_size[N]), NOT yet growth-rate limited —
    see _relax_size_field_growth_rate.
    """
    import numpy as np

    entities = gmsh_mod.model.getEntities(1) + gmsh_mod.model.getEntities(2)
    if not entities:
        return np.empty((0, 3)), np.empty((0,))

    # Split the sample budget across all entities so a complex part
    # (hundreds of curves/surfaces) stays bounded, with a floor so short
    # curves still get resolved.
    per_entity = max(4, max_samples // max(1, len(entities)))

    points: list = []
    sizes: list = []
    for dim, tag in entities:
        try:
            lo, hi = gmsh_mod.model.getParametrizationBounds(dim, tag)
        except Exception:
            # entity has no parametrization — skip it, keep sampling the rest
            logger.debug("gmsh_wrapper: getParametrizationBounds failed dim=%d tag=%d", dim, tag, exc_info=True)
            continue
        if dim == 1:
            us = np.linspace(lo[0], hi[0], per_entity)
            param_coord = [float(u) for u in us]
            try:
                curvs = gmsh_mod.model.getCurvature(dim, tag, param_coord)
                coords = gmsh_mod.model.getValue(dim, tag, param_coord)
            except Exception:
                # curvature probe failed for this entity — skip it
                logger.debug("gmsh_wrapper: getCurvature failed dim=%d tag=%d", dim, tag, exc_info=True)
                continue
            for i in range(len(us)):
                c = curvs[i] if i < len(curvs) else 0.0
                radius = (1.0 / c) if c > 1e-9 else math.inf
                target = min(h_max, max(h_min, radius * angle_sensitivity_rad))
                points.append(coords[3 * i:3 * i + 3])
                sizes.append(target)
        else:
            n_side = max(2, int(math.sqrt(per_entity)))
            us = np.linspace(lo[0], hi[0], n_side)
            vs = np.linspace(lo[1], hi[1], n_side)
            param_coord = []
            for u in us:
                for v in vs:
                    param_coord.extend([float(u), float(v)])
            try:
                curvs = gmsh_mod.model.getCurvature(dim, tag, param_coord)
                coords = gmsh_mod.model.getValue(dim, tag, param_coord)
            except Exception:
                # curvature probe failed for this entity — skip it
                logger.debug("gmsh_wrapper: getCurvature failed dim=%d tag=%d", dim, tag, exc_info=True)
                continue
            n_pts = len(param_coord) // 2
            for i in range(n_pts):
                c = curvs[i] if i < len(curvs) else 0.0
                radius = (1.0 / c) if c > 1e-9 else math.inf
                target = min(h_max, max(h_min, radius * angle_sensitivity_rad))
                points.append(coords[3 * i:3 * i + 3])
                sizes.append(target)

    if not points:
        return np.empty((0, 3)), np.empty((0,))
    return np.asarray(points, dtype=np.float64), np.asarray(sizes, dtype=np.float64)


def _relax_size_field_growth_rate(points, sizes, growth_rate: float, k_neighbors: int = 10):
    """Limit the spatial gradient of a size field so it never grows
    faster than GROWTH_RATE — a size field that's locally correct (fine
    at a fillet, coarse a few cm away with nothing in between) still
    produces poor-quality elements at the transition without this,
    since nothing tells the mesher how fast it's allowed to grow.

    Simplified Fast Marching: relaxed(p) is propagated outward from
    every sample point via Dijkstra over a k-nearest-neighbor graph
    (built with a KDTree), processing points in ascending size order
    (a min-heap) and relaxing each neighbor to
    min(current, size[p] + slope * dist(p, neighbor)), where
    slope = growth_rate - 1 — the discrete form of the eikonal gradient
    constraint |grad size| <= slope. growth_rate follows the usual CFD
    meshing convention (1.2 = at most 20% size increase per unit
    distance travelled); 1.0 means "no growth allowed" (uniform size).
    """
    import heapq

    import numpy as np
    from scipy.spatial import cKDTree

    n = len(points)
    if n == 0:
        return sizes
    relaxed = np.asarray(sizes, dtype=np.float64).copy()
    slope = max(growth_rate - 1.0, 1e-6)
    k = min(k_neighbors + 1, n)  # +1: query() always returns the point itself
    tree = cKDTree(points)
    _, neighbor_idx = tree.query(points, k=k)
    if k == 1:
        return relaxed
    neighbor_idx = np.atleast_2d(neighbor_idx)

    heap = [(float(relaxed[i]), i) for i in range(n)]
    heapq.heapify(heap)
    visited = np.zeros(n, dtype=bool)
    while heap:
        d, i = heapq.heappop(heap)
        if visited[i] or d > relaxed[i] + 1e-12:
            continue
        visited[i] = True
        for j in neighbor_idx[i]:
            j = int(j)
            if j == i or visited[j]:
                continue
            dist = float(np.linalg.norm(points[i] - points[j]))
            candidate = relaxed[i] + slope * dist
            if candidate < relaxed[j]:
                relaxed[j] = candidate
                heapq.heappush(heap, (candidate, j))
    return relaxed


def _advancing_front_1d(
    gmsh_mod, dim: int, tag: int, size_tree, relaxed_sizes,
    h_min: float, h_max: float, max_points: int = 500,
) -> list:
    """1D Advancing Front curve seeding: march along one curve's
    arclength, querying the (growth-rate-limited) size field at each
    step via KDTree nearest-neighbor lookup for the next step length.
    Used only to seed embedded points GMSH's own curve mesher is told
    to honor — it does not replace GMSH's node placement, which still
    owns the final discretization.

    Returns the parametric coordinates of the interior seed points
    (curve endpoints excluded).
    """
    lo, hi = gmsh_mod.model.getParametrizationBounds(dim, tag)
    u_lo, u_hi = lo[0], hi[0]
    try:
        length = gmsh_mod.model.occ.getMass(dim, tag)
    except Exception:
        length = None
    if not length or length <= 1e-12:
        return []

    def size_at(u: float) -> float:
        xyz = gmsh_mod.model.getValue(dim, tag, [u])
        _, idx = size_tree.query(xyz)
        return float(min(h_max, max(h_min, relaxed_sizes[idx])))

    params: list = []
    u = u_lo
    for _ in range(max_points):
        h = size_at(u)
        du = h / length * (u_hi - u_lo)
        if du <= 0:
            break
        u_next = u + du
        if u_next >= u_hi:
            break
        params.append(u_next)
        u = u_next
    return params


def _extract_boundary_trimesh(gmsh_mod, probe_size: float | None = None):
    """A COARSE discrete boundary surface (all 2D entities combined into
    one triangle soup) as a ``trimesh.Trimesh`` — the thing
    ``_sample_passage_thickness_field`` ray-casts against.

    ``probe_size`` (when given) caps the element size for this throwaway
    probe mesh. This matters for two independent reasons, both measured:

    - COST: the probe is a second full 2D meshing pass on top of the real
      one (the caller clears it afterwards precisely so the real pass is
      not skipped), so at full resolution it roughly doubles 2D meshing
      time for information — "how wide is the passage here" — that needs
      no resolution at all.
    - ROBUSTNESS: 2D meshing is exactly where GMSH's curve-recovery loop
      breaks down on degenerate periodic CAD surfaces. A coarse probe
      spends far less time in that loop, and is far less likely to
      trigger it, than a full-resolution one.

    Node tags are used directly as the shared-vertex key across surfaces
    (``process=False``): GMSH's own mesh is already conforming at surface
    junctions — two adjacent surfaces reference the SAME node tags along
    their shared boundary curve — so this reproduces that shared-vertex
    topology exactly, without trimesh's coordinate-based vertex merging
    and its tolerance pitfalls.
    """
    import numpy as np
    import trimesh

    saved_max = None
    if probe_size and probe_size > 0:
        try:
            saved_max = gmsh_mod.option.getNumber("Mesh.CharacteristicLengthMax")
            gmsh_mod.option.setNumber("Mesh.CharacteristicLengthMax", probe_size)
        except Exception:
            saved_max = None
    try:
        gmsh_mod.model.mesh.generate(2)
    finally:
        if saved_max is not None:
            try:
                gmsh_mod.option.setNumber("Mesh.CharacteristicLengthMax", saved_max)
            except Exception:
                logger.exception("Probe mesh: restoring CharacteristicLengthMax failed")
    node_tags, node_coords, _ = gmsh_mod.model.mesh.getNodes()
    if len(node_tags) == 0:
        return None
    coords = np.asarray(node_coords, dtype=np.float64).reshape(-1, 3)
    tag_to_idx = {int(t): i for i, t in enumerate(node_tags)}

    all_tris: list[np.ndarray] = []
    for dim, tag in gmsh_mod.model.getEntities(2):
        elem_types, _elem_tags, elem_node_tags = gmsh_mod.model.mesh.getElements(dim, tag)
        for et, ent_nodes in zip(elem_types, elem_node_tags):
            n_nodes_per_elem = gmsh_mod.model.mesh.getElementProperties(et)[3]
            if n_nodes_per_elem != 3 or len(ent_nodes) == 0:
                continue  # only true triangles (2nd-order/quad elements skipped)
            arr = np.asarray(ent_nodes, dtype=np.int64).reshape(-1, 3)
            idx = np.vectorize(tag_to_idx.get)(arr)
            all_tris.append(idx)
    if not all_tris:
        return None
    faces = np.vstack(all_tris)
    return trimesh.Trimesh(vertices=coords, faces=faces, process=False)


def _sample_passage_thickness_field(
    gmsh_mod, h_min: float, h_max: float,
    cells_across: int = 8, max_samples: int = 6000, seed: int = 0xC0FFEE,
    boundary_mesh=None,
) -> tuple:
    """A-priori sizing from local passage width — the geometric quantity
    (distance to the nearest opposite wall) that BOTH a narrow gap and a
    small feature actually are, measured directly by ray-casting the
    already-tessellated boundary instead of the two proxy heuristics this
    replaces:

    - the old ``_detect_surface_gaps`` used bounding-box-to-bounding-box
      distance between surface PAIRS as a proxy for "how close are they"
      — cheap, but not the true surface distance, and blind to a narrow
      passage formed by more than two surfaces (a filleted corner
      pinching a channel, say);
    - ``_scan_feature_sizes``'s "bottom quartile of curve length" has no
      notion of surface separation at all — it is a proxy for a DIFFERENT
      thing (short edges), which is why it never caught passage width.

    Method: cast a ray from each surface sample point along its INWARD
    normal; the first hit distance is the local width of the volume the
    boundary encloses at that point (for a CFD case, the fluid passage
    width — verified end-to-end on a single connected watertight venturi:
    ~1.99 at the wide ends vs ~0.34 at the throat, true diameters 2.0 and
    ~0.30 — a genuinely LOCAL field, not a per-surface-pair estimate).
    Falls back to the outward direction when the inward ray misses (can
    happen right at a convex corner). See
    ``core.geometry.sample_thickness_field`` for the vectorised
    ray-casting itself (shared with the existing global-percentile
    thickness analysis used by "Auto-Suggest Cell Sizes").

    Returns ``(points, target_sizes)`` — target_sizes[i] = clamp(
    thickness[i] / cells_across, h_min, h_max), NOT YET growth-rate
    relaxed (the caller combines this with the curvature field's points
    before relaxing, so the transition between the two is smooth too).
    Empty arrays (never an exception) when the boundary can't be
    extracted or ray-casting finds nothing — the existing curvature field
    and detail-level bounds still govern sizing either way.

    ``boundary_mesh``: when the caller already HAS a boundary surface as
    a ``trimesh.Trimesh`` (an STL input, notably — see the ``is_stl_input``
    branch in ``generate_volume_mesh``), pass it here to ray-cast against
    it directly instead of extracting one from GMSH via
    ``_extract_boundary_trimesh``. That extraction runs a throwaway GMSH
    2D meshing pass, which is exactly where GMSH's curve-recovery loop
    hangs on the reparametrized discrete surfaces an STL produces (see
    ``generate_volume_mesh``'s ``is_stl_input`` comment) — an STL's
    triangulation already IS the boundary mesh, so there is nothing to
    extract and no GMSH call is needed at all for this field.
    """
    import numpy as np

    from cfmesh_autogui.core.geometry import sample_thickness_field

    if boundary_mesh is not None:
        mesh = boundary_mesh
    else:
        try:
            # Probe at the COARSE end of the requested range: passage width is
            # a smooth, low-frequency quantity (how far to the opposite wall),
            # so a coarse tessellation measures it just as well while costing
            # a fraction of the time and touching GMSH's fragile curve-recovery
            # path far less. See _extract_boundary_trimesh for the details.
            mesh = _extract_boundary_trimesh(gmsh_mod, probe_size=h_max)
        except Exception:
            logger.exception("Passage thickness field: boundary extraction failed")
            mesh = None
        finally:
            # The probe mesh built by _extract_boundary_trimesh MUST be thrown
            # away: GMSH REUSES an existing 2D mesh when generate(3) runs later
            # (verified directly — a unit box meshed at lc=0.5 and then
            # generated at lc=0.08 kept all 540 of its original triangles), so
            # leaving it in place would freeze the final surface mesh at
            # whatever sizing existed BEFORE any size field was installed,
            # silently discarding the passage/curvature/small-curve sizing this
            # very function exists to compute. `mesh` already holds its own copy
            # of the coordinates, so clearing costs nothing here.
            try:
                gmsh_mod.model.mesh.clear()
            except Exception:
                logger.exception("Passage thickness field: probe-mesh clear failed")
    if mesh is None or len(mesh.faces) == 0:
        return np.zeros((0, 3)), np.zeros(0)

    bbox_max = float(max(mesh.extents)) if len(mesh.vertices) else 1.0
    try:
        pts, thickness = sample_thickness_field(mesh, max_samples, bbox_max, seed)
    except Exception:
        logger.exception("Passage thickness field: ray-casting failed")
        return np.zeros((0, 3)), np.zeros(0)
    if len(pts) == 0:
        return np.zeros((0, 3)), np.zeros(0)

    target = np.clip(thickness / max(cells_across, 1), h_min, h_max)
    return pts, target


def _configure_curvature_size_field(
    gmsh_mod,
    h_min: float,
    h_max: float,
    angle_sensitivity_rad: float = math.radians(15.0),
    growth_rate: float = 1.2,
    max_samples: int = 20000,
    seed_curves: bool = True,
    passage_points=None,
    passage_target_sizes=None,
    skip_curvature: bool = False,
) -> dict:
    """A-priori, geometry-based mesh adaptation: build an explicit local
    Size Field from the CAD model's own discrete curvature (H_MIN/H_MAX/
    ANGOLO_SENSIBILITA_RAD/GROWTH_RATE all configurable), gradient-limited
    so transitions between fine and coarse regions stay smooth, and feed
    it to GMSH via setSizeCallback — GMSH's own mesher still places every
    node; this only tells it how big each element is allowed to be, at
    every query point it makes across curves, surfaces, and the volume.

    passage_points / passage_target_sizes
        Additional (point, target size) samples to fold into the SAME
        field before growth-rate relaxation — currently the passage-
        thickness field from ``_sample_passage_thickness_field``. Merging
        before relaxation (rather than as a second, independent
        min-combined field) means the transition FROM a narrow passage
        TO the surrounding curvature-driven sizing is itself growth-rate
        limited, not a hard step.

    skip_curvature
        Skip ``_sample_curvature_size_field`` (which needs the CAD
        kernel's analytic curvature and is unstable on the reparametrized
        discrete surfaces an STL produces) and build the field from
        ``passage_points``/``passage_target_sizes`` alone — still gets
        the growth-rate relaxation and curve seeding below, just without
        a curvature contribution.

    Failure here (an unusual CAD kernel entity, an empty model) degrades
    to a no-op rather than blocking meshing — the existing Distance/
    Threshold background field in _configure_adaptive_sizing keeps
    working unchanged either way.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    if skip_curvature:
        points, raw_sizes = np.zeros((0, 3)), np.zeros(0)
    else:
        points, raw_sizes = _sample_curvature_size_field(
            gmsh_mod, h_min, h_max, angle_sensitivity_rad, max_samples
        )
    if passage_points is not None and len(passage_points):
        if len(points):
            points = np.vstack([points, passage_points])
            raw_sizes = np.concatenate([raw_sizes, passage_target_sizes])
        else:
            points = np.asarray(passage_points, dtype=np.float64)
            raw_sizes = np.asarray(passage_target_sizes, dtype=np.float64)
    if len(points) == 0:
        logger.warning("Curvature size field: no sample points found, skipping")
        return {"n_samples": 0}

    relaxed = _relax_size_field_growth_rate(points, raw_sizes, growth_rate)

    # Advancing-front curve seeding densifies the sample set used by the
    # KDTree lookup below — it does NOT touch the CAD model (no new OCC
    # points/embeds). Mutating geometry mid-pipeline is exactly what
    # introduced new defects earlier on the real valve part (native GMSH
    # defeature/healShapes both did); this stays purely data-side, so a
    # short curve just gets denser size-field coverage near it, sampled
    # by 1D arclength marching instead of a uniform parametric grid.
    n_seeded = 0
    if seed_curves:
        seed_tree = cKDTree(points)
        extra_points: list = []
        extra_sizes: list = []
        for dim, tag in gmsh_mod.model.getEntities(1):
            try:
                params = _advancing_front_1d(
                    gmsh_mod, dim, tag, seed_tree, relaxed, h_min, h_max
                )
                for u in params:
                    xyz = gmsh_mod.model.getValue(dim, tag, [u])
                    _, idx = seed_tree.query(xyz)
                    extra_points.append(xyz)
                    extra_sizes.append(float(relaxed[idx]))
            except Exception:
                # best-effort seeding; the size callback alone still applies
                logger.debug("gmsh_wrapper: seed tree query failed", exc_info=True)
                continue
        if extra_points:
            points = np.vstack([points, np.asarray(extra_points, dtype=np.float64)])
            relaxed = np.concatenate([relaxed, np.asarray(extra_sizes, dtype=np.float64)])
            n_seeded = len(extra_points)

    tree = cKDTree(points)

    def _callback(dim, tag, x, y, z, lc):
        _, idx = tree.query([x, y, z])
        target = float(relaxed[idx])
        return min(lc, target) if lc > 0 else target

    gmsh_mod.model.mesh.setSizeCallback(_callback)

    logger.info(
        "Curvature size field: %d samples, %d curve seed points, size range "
        "[%.6f, %.6f] (H_MIN=%.6f H_MAX=%.6f angle=%.3frad growth_rate=%.2f)",
        len(points), n_seeded, float(relaxed.min()), float(relaxed.max()),
        h_min, h_max, angle_sensitivity_rad, growth_rate,
    )
    return {
        "n_samples": len(points),
        "n_seeded": n_seeded,
        "size_min": float(relaxed.min()),
        "size_max": float(relaxed.max()),
    }


def _configure_adaptive_sizing(
    gmsh_mod, detail: str, max_extent: float, feat: dict, hw: dict,
    cross_scale: float | None = None, domain_volume: float | None = None,
    growth_rate: float = 1.2, use_curvature_size_field: bool = True,
    passage_boundary_mesh=None,
) -> dict:
    """Local/adaptive sizing: fine near small curves (a Distance +
    Threshold background field) and near curved surfaces (GMSH's
    existing curvature-based auto-sizing, left on — the two combine via
    GMSH's own min-of-all-active-sources rule, no extra field needed
    for that part), with the bulk/coarse size driven by the cell budget
    rather than a single fixed detail-level multiplier.

    passage_boundary_mesh: a pre-built ``trimesh.Trimesh`` boundary
    surface (an STL input's own triangulation) to ray-cast the
    passage-thickness field against directly, instead of extracting one
    via a throwaway GMSH 2D probe mesh — see
    ``_sample_passage_thickness_field``'s ``boundary_mesh`` param. Lets
    STL input get passage-thickness sizing even with
    ``use_curvature_size_field=False`` (curvature itself still needs the
    CAD kernel and stays off for STL).

    Earlier version used a fixed coarse_max from detail level alone —
    on a geometry with few/no small features (a plain pipe), that meant
    a "Max cells target" override changed almost nothing: the floor
    only governs refinement NEAR small features, so with none present
    the whole mesh sat at the detail-level ceiling regardless of budget
    (observed: requesting a 1M-cell target on a simple pipe still only
    produced ~2K nodes). Now coarse_max is pulled down toward whatever
    uniform size would use the full cell budget across the domain
    volume — detail level still acts as the upper bound (never coarser
    than what the user picked), the budget can only make it finer.
    """
    df = _GMSH_DETAIL.get(detail, _GMSH_DETAIL["medium"])
    lc_user = max_extent * 0.02
    coarse_max_detail = lc_user * df["max_mult"]

    # --- cross-section resolution -------------------------------------------
    # coarse_max_detail above is a fixed fraction of the LARGEST bounding-box
    # dimension. On an elongated part that is the length, which has nothing to
    # do with the passage the flow actually goes through, and the bulk mesh
    # comes out unusable for CFD: the 3.0 x 0.255 x 0.255 m valve at "medium"
    # got 3.0 * 0.02 * 1.5 = 9 cm cells against a 25.5 cm passage — fewer than
    # 3 cells across the bore. That is the long-standing "automatic mode gives
    # too few cells" problem; a commercial tool needed ~9M cells with MANUAL
    # refinement on the same part, and auto mode here was landing at 750k-2.2M
    # almost entirely from curvature/small-feature refinement, with a bulk that
    # resolved nothing.
    #
    # Size the bulk off the CROSS-SECTION (bounding box median dimension)
    # instead, asking for cells_across cells through it. Take whichever of the
    # two is finer, so this can only ever improve resolution, never coarsen a
    # geometry the old rule already handled well (on a roughly cubic domain the
    # two agree closely and nothing changes).
    cells_across = df.get("cells_across", 20)
    coarse_max_cross = cross_scale / max(cells_across, 1)
    if coarse_max_cross < coarse_max_detail:
        logger.info(
            "Cross-section sizing binds: %.5g (=%.4g/%d across) is finer than "
            "the extent-based %.5g — bulk mesh sized for the passage, not the "
            "overall length.",
            coarse_max_cross, cross_scale, cells_across, coarse_max_detail,
        )
    coarse_max_detail = min(coarse_max_detail, coarse_max_cross)

    # Hardware floor: keeps the finest allowed size bounded by the cell
    # budget, so a machine with little free RAM backs off automatically
    # rather than grinding for minutes/hours or exhausting memory — the
    # "fine everywhere" uniform pass tried earlier on this same valve
    # part never converged in a reasonable time; this bound is what a
    # uniform detail level has no way to express.
    #
    # Scaled off cross_scale (the bounding box's MEDIAN dimension), not
    # max_extent (its LARGEST). On an elongated domain — a long pipe —
    # max_extent is the length, not the diameter; a floor sized off the
    # length was far coarser than the cross-section actually needed,
    # producing stretched/flattened cells there (observed: aspect ratio
    # 933 on a pipe test).
    #
    # CORRECTION: that pipe fix turned out to be the geom_min clamp
    # below, not this — hw_floor was never the dominant term there
    # (0.00013, negligible next to geom_min's 0.01). Using cross_scale
    # here too was wrong: it also shrinks the reference on OTHER
    # elongated geometries (the valve is 3m x 0.255m x 0.255m, just as
    # elongated as the pipe test), making hw_floor ~12x finer than
    # intended there and dragging the whole mesh into a multi-minute
    # stall. hw_floor is meant purely as a hardware SAFETY backstop
    # (only matters when it's the larger/coarser of the two floors) —
    # max_extent is the right scale for that, not the cross-section.
    hw_floor = max_extent / (hw["max_cells"] ** (1 / 3) * 15)

    # Budget-driven coarse size: the uniform edge length that would use
    # roughly the full cell budget across the domain volume (0.118 is a
    # regular tetrahedron's volume-to-edge-length^3 ratio, so V/(0.118*a^3)
    # ~= N cells of edge a). Computed BEFORE the small-feature floor
    # below, and independent of it, so an explicit "Max cells target"
    # can genuinely drive the whole mesh finer — not just the area near
    # small features — capped only by the detail level's own ceiling
    # (never coarser than what the user picked) and by hw_floor (never
    # finer than the hardware can reasonably handle).
    # Only pulled finer when the user set an EXPLICIT "Max cells target".
    # Without one, hw["max_cells"] is an auto-estimate from free RAM (an
    # upper SAFETY bound only) — several million on a machine with a lot
    # of RAM. Treating that as an active target by default turned every
    # adaptive run into one striving to use the whole estimate, not just
    # what the chosen Detail Level asked for: on the 3m valve body this
    # meant a uniformly much finer bulk mesh than "medium" detail should
    # give, and the generation stalled. Only an explicit target should
    # pull resolution beyond the detail level's own default ceiling.
    if hw.get("is_explicit_target"):
        vol = domain_volume if domain_volume and domain_volume > 0 else max_extent ** 3
        budget_uniform_size = (vol / (0.118 * hw["max_cells"])) ** (1 / 3)
        coarse_max = min(coarse_max_detail, max(budget_uniform_size, hw_floor * 2))
    else:
        coarse_max = coarse_max_detail

    # Absolute sanity floor, independent of domain scale: coarse_max_detail
    # is `max_extent * 0.02 * max_mult` — proportional to the domain, which
    # is right for CFD-scale parts (the 3m valve gets ~9cm bulk cells, a
    # sensible "medium") but breaks down on a genuinely small domain: a 2mm
    # test cylinder gets the SAME relative treatment and lands at 0.06mm —
    # confirmed directly to produce 1,071,874 tets in 49s for "medium" on a
    # part the size of a grain of rice, with nothing about it actually
    # needing that resolution. df["min_size"] is this detail level's own
    # declared finest-intended absolute size (already defined per level,
    # just never wired to anything before this) — using it as a floor here
    # means the bulk mesh is never finer than what "medium" itself considers
    # its own upper resolution limit, regardless of how small the domain is.
    # Never engages on a normal CFD-scale part (there coarse_max_detail is
    # already well above this floor), so the valve's tuning is unaffected.
    coarse_max = max(coarse_max, df["min_size"])

    # geom_min is only meaningful when the geometry genuinely HAS a small
    # feature relative to everything else (the valve's 0.25mm fillets on
    # a 3m body). On a geometry with no small features — every curve
    # roughly the same size, like a plain pipe — "the shortest curve"
    # isn't small at all, and using it unclamped produced min_size >
    # coarse_max (observed directly: min=0.07 vs max=0.02 on the pipe
    # test), which GMSH can't reconcile and which collapsed the whole
    # mesh down to ~2K nodes regardless of any cell budget requested.
    # Clamped against coarse_max (computed above, already budget-aware)
    # rather than against the fixed detail ceiling, so it can't undercut
    # a genuinely higher-resolution budget request either.
    geom_min = min(
        max(feat["min_feature"] * 0.35, max_extent * 1e-5),
        coarse_max * 0.5,
    )
    min_size = max(geom_min, hw_floor)

    n_small = len(feat["small_curve_tags"])
    active_fields = []
    if n_small:
        f_dist = gmsh_mod.model.mesh.field.add("Distance")
        gmsh_mod.model.mesh.field.setNumbers(
            f_dist, "CurvesList", [float(t) for t in feat["small_curve_tags"]]
        )
        f_thresh = gmsh_mod.model.mesh.field.add("Threshold")
        gmsh_mod.model.mesh.field.setNumber(f_thresh, "InField", f_dist)
        gmsh_mod.model.mesh.field.setNumber(f_thresh, "SizeMin", min_size)
        gmsh_mod.model.mesh.field.setNumber(f_thresh, "SizeMax", coarse_max)
        dist_min = max(feat["small_cutoff"] * 2, min_size * 4)
        # DistMax is the radius over which the field ramps from SizeMin back
        # up to SizeMax — i.e. how far the "fine near this small feature"
        # zone reaches before the mesh is allowed to go coarse again. Left
        # as a bare multiple of small_cutoff (small_cutoff * 20), it grows
        # with small_cutoff itself, and small_cutoff is capped only at
        # max_extent * 0.08 (see _scan_feature_sizes) — so DistMax could
        # reach 1.6x the whole part's extent, meaning the "local" fine zone
        # around one small edge (a fold line, say) never actually finishes
        # ramping back to coarse ANYWHERE on the part. The result: a mesh
        # that looks uniformly fine everywhere instead of graded (fine only
        # near the feature, visibly coarser away from it) — even though the
        # total cell count matches the budget, because coarse_max is never
        # actually reached. Capped here at a genuinely LOCAL neighbourhood
        # (15% of max_extent) so the transition always completes within the
        # part; never binds on the reference case (a 3m valve with 0.25mm
        # fillets: DistMax there is already far below 0.15*3m=0.45m).
        dist_max = max(feat["small_cutoff"] * 20, min_size * 40)
        dist_max = min(dist_max, max_extent * 0.15)
        # dist_min must stay strictly below dist_max (a Threshold field with
        # DistMin >= DistMax has no ramp — GMSH's own interpretation of that
        # is undefined territory, not just "less graded"). dist_min can
        # reach small_cutoff * 2 = up to max_extent * 0.16 at the small_cutoff
        # ceiling above, which is BIGGER than the 0.15 * max_extent dist_max
        # cap just applied — clamp it below dist_max explicitly rather than
        # relying on the two independent formulas happening to stay ordered.
        dist_min = min(dist_min, dist_max * 0.5)
        gmsh_mod.model.mesh.field.setNumber(f_thresh, "DistMin", dist_min)
        gmsh_mod.model.mesh.field.setNumber(f_thresh, "DistMax", dist_max)
        active_fields.append(f_thresh)

    # Gap-aware refinement, superseded here: the old approach placed one
    # Ball field per (surface-pair, bounding-box distance) — a proxy that
    # misses passages formed by more than two surfaces and gets the
    # location/size only approximately right. Replaced below by
    # ray-casting the actual local passage width (see
    # _sample_passage_thickness_field), which is merged into the
    # curvature field's own KDTree/callback rather than added as separate
    # Ball fields — one continuous field, one growth-rate-limited
    # transition, instead of N independent bubbles that can overlap or
    # leave gaps between them. _detect_surface_gaps itself is unchanged
    # and still used elsewhere (the polyDualMesh risk heuristic below).
    import numpy as np

    # Passage-thickness sizing runs whenever EITHER the CAD curvature path
    # is on (the original condition) OR the caller handed us a boundary
    # mesh directly (the STL path — see generate_volume_mesh's
    # is_stl_input branch). The two used to share one gate
    # (use_curvature_size_field) because both relied on
    # _extract_boundary_trimesh's GMSH probe, which is exactly what hangs
    # on STL's reparametrized discrete surfaces; passage_boundary_mesh
    # sidesteps that entirely (the STL's own triangulation IS the
    # boundary mesh, ray-cast directly, zero extra GMSH calls), so it no
    # longer needs curvature sizing to be safe to run.
    passage_points, passage_target = np.zeros((0, 3)), np.zeros(0)
    if use_curvature_size_field or passage_boundary_mesh is not None:
        try:
            passage_points, passage_target = _sample_passage_thickness_field(
                gmsh_mod, min_size, coarse_max,
                # "min_thick_cells" is core/geometry.py's own detail-level
                # dict, a DIFFERENT dict from this module's _GMSH_DETAIL
                # (which has no such key — df.get("min_thick_cells", 8)
                # would silently fall back to 8 for every detail level,
                # ignoring the slider). Reuse cells_across instead: the
                # same "how many cells across the passage" concept, already
                # tuned per level here (8/13/20/32/48).
                cells_across=df.get("cells_across", 8),
                boundary_mesh=passage_boundary_mesh,
            )
        except Exception:
            logger.exception("Passage thickness field failed; continuing without it")

    bg_field: int | None = None
    if len(active_fields) == 1:
        bg_field = active_fields[0]
        gmsh_mod.model.mesh.field.setAsBackgroundMesh(bg_field)
    elif len(active_fields) > 1:
        bg_field = gmsh_mod.model.mesh.field.add("Min")
        gmsh_mod.model.mesh.field.setNumbers(bg_field, "FieldsList", active_fields)
        gmsh_mod.model.mesh.field.setAsBackgroundMesh(bg_field)

    gmsh_mod.option.setNumber("Mesh.CharacteristicLengthMin", min_size)
    gmsh_mod.option.setNumber("Mesh.CharacteristicLengthMax", coarse_max)
    # Sizing here is fully FIELD-driven: the Distance/Threshold (small
    # curves) and Ball (gaps) fields set up above, min-combined into the
    # background mesh, plus the bounded a-priori curvature field installed
    # below (_configure_curvature_size_field, already clamped to
    # [min_size, coarse_max] and growth-rate limited). GMSH's own manual
    # says to turn its BLANKET curvature/point/boundary-extend options OFF
    # in exactly this situation ("When mesh element sizes are fully
    # specified by a mesh size field, it is often desirable to set
    # MeshSizeFromPoints = 0; MeshSizeFromCurvature = 0;
    # MeshSizeExtendFromBoundary = 0 to prevent over-refinement").
    #
    # Left ON (as this code did until now), it is what caused the reported
    # "refines at random" behaviour: MeshSizeFromCurvature has NO notion of
    # which curved features matter for CFD — it just asks for N elements
    # around every circle/fillet on the part, bounded only by the GLOBAL
    # CharacteristicLengthMin. One genuinely small feature ANYWHERE on the
    # geometry (a 0.25mm manufacturing fillet on a 3m valve, say) pulls
    # min_size down for the whole model, and this blanket option then
    # chased that SAME fine size on every other curved surface too,
    # completely uncorrelated with the small-feature/gap detection above.
    # Disabling it leaves sizing entirely to the two purpose-built,
    # bounded sources: features/gaps that were actually DETECTED, and the
    # curvature field's own explicit floor/ceiling.
    gmsh_mod.option.setNumber("Mesh.CharacteristicLengthFromCurvature", 0)
    gmsh_mod.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    gmsh_mod.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh_mod.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    for opt in (
        "General.NumThreads", "Mesh.MaxNumThreads1D",
        "Mesh.MaxNumThreads2D", "Mesh.MaxNumThreads3D",
    ):
        try:
            gmsh_mod.option.setNumber(opt, _gmsh_thread_count())
        except Exception:
            # option name varies across GMSH versions; NumThreads alone still helps
            logger.debug("gmsh_wrapper: thread option %s not accepted", opt, exc_info=True)

    # A-priori curvature size field: an explicit, growth-rate-limited
    # size field built from the CAD kernel's own local curvature radius
    # at each point (see _configure_curvature_size_field), layered on
    # top of the Distance/Threshold/Ball fields above via GMSH's size
    # callback mechanism (min-of-all-sources, so it can only make a
    # point finer than what those fields already ask for, never coarser).
    # angle_sensitivity defaults to the detail level's own curv_angle so
    # it stays consistent with "Detail Level" instead of introducing an
    # unrelated knob; best-effort — a failure here just means the
    # existing background fields alone govern sizing, as before.
    # Register the (growth-rate-relaxed, KDTree-backed) size callback
    # whenever there is EITHER curvature sampling OR a passage-thickness
    # field to install — an STL input has no curvature contribution
    # (skip_curvature=True) but its passage points still need this same
    # callback/relaxation/curve-seeding machinery to actually reach GMSH.
    curvature_field_info: dict = {"n_samples": 0}
    if use_curvature_size_field or len(passage_points):
        try:
            curvature_field_info = _configure_curvature_size_field(
                gmsh_mod, min_size, coarse_max,
                angle_sensitivity_rad=math.radians(df["curv_angle"]),
                growth_rate=growth_rate,
                passage_points=passage_points if len(passage_points) else None,
                passage_target_sizes=passage_target if len(passage_target) else None,
                skip_curvature=not use_curvature_size_field,
            )
        except Exception:
            logger.exception("Curvature size field setup failed; continuing without it")

    # polyDualMesh risk heuristic: on the real valve part investigated
    # today (89 surfaces, 50 detected gaps — the detector's cap, i.e.
    # "at least 50") polyDualMesh reliably produced 600-1000
    # incorrectly-oriented faces regardless of tet mesh quality —
    # confirmed across curvature-based, gap-aware, budget-driven sizing
    # and both HXT and classic Delaunay tet algorithms, so it's a
    # limitation of polyDualMesh's dual construction on that surface
    # topology, not something this pipeline's tet generation can fix.
    # Simple/moderate parts tested (a tube, a block with one hole, an
    # aero body with a fin — 5 to 11 surfaces, 0 gaps) converted cleanly
    # every time. Surface count and gap count are the two measurements
    # available before ever running polyDualMesh; flagging high values
    # lets the caller warn instead of silently shipping a defective
    # poly mesh — investigated foamyHexMesh as a structurally different
    # alternative (no dual-conversion step at all) but the installed
    # OpenFOAM build's binary itself crashes (SIGFPE inside its own
    # CGAL-based conformalVoronoiMesh library, reproducible, unrelated
    # to any dictionary setting tried), so no reliable alternative
    # exists for this geometry class yet.
    # Gap count for the polyDualMesh risk heuristic below (a SEPARATE use
    # from sizing — this bbox-distance proxy is not accurate enough to
    # size a field on, see the note above, but "how many close surface
    # pairs exist" is still a fine coarse risk signal on its own).
    gaps = _detect_surface_gaps(gmsh_mod, max_extent)
    n_surfaces = len(gmsh_mod.model.getEntities(2))
    poly_dual_risk = n_surfaces > 30 or len(gaps) >= 20

    logger.info(
        "Adaptive sizing: min=%.5f max=%.5f (geom_min=%.5f hw_floor=%.5f) "
        "small_curves=%d/%d gaps=%d passage_samples=%d surfaces=%d "
        "poly_dual_risk=%s cpu=%d max_cells_budget=%d",
        min_size, coarse_max, geom_min, hw_floor, n_small,
        len(gmsh_mod.model.getEntities(1)), len(gaps), len(passage_points),
        n_surfaces, poly_dual_risk, hw["cpu_count"], hw["max_cells"],
    )
    return {
        "min_size": min_size, "coarse_max": coarse_max,
        # Tag of the background size field this function installed (None if
        # it installed none). Returned so a caller layering an additional
        # size field on top — the solution-adaptive one, see
        # apply_solution_size_field — can MIN-combine with it instead of
        # calling setAsBackgroundMesh() again and silently discarding all
        # the geometry-based sizing computed here.
        "bg_field": bg_field,
        "n_small_curves": n_small, "n_gaps": len(gaps),
        "n_passage_samples": len(passage_points),
        "n_surfaces": n_surfaces, "poly_dual_risk": poly_dual_risk,
        "curvature_field_samples": curvature_field_info.get("n_samples", 0),
        "curvature_field_seeded": curvature_field_info.get("n_seeded", 0),
    }


@dataclass
class GmshSizing:
    """Structured sizing information computed by GMSH for a CAD geometry."""
    min_curvature_radius: float = 1.0
    min_proximity: float = 1.0
    max_extent: float = 1.0
    suggested_surface_size: float = 0.05
    suggested_volume_size: float = 0.1
    # List of (patch_name, suggested_size) for each physical surface
    patch_sizes: dict[str, float] = field(default_factory=dict)
    n_physical_groups: int = 0


def _ensure_gmsh():
    """Start GMSH if not already running (thread-safe with double-checked locking)."""
    global _GMSH_INITIALIZED
    if not _GMSH_INITIALIZED:
        with _GMSH_LOCK:
            if not _GMSH_INITIALIZED:  # double-checked locking
                import gmsh
                # interruptible=True (gmsh's default) calls signal.signal(),
                # which only works on the main thread — this function is
                # explicitly documented/locked for multi-threaded callers,
                # and the same call crashed feature_detector.py's worker
                # thread with "signal only works in main thread of the main
                # interpreter" when it ran off the main thread (confirmed
                # live). Skip the signal-handler install entirely.
                gmsh.initialize(interruptible=False)
                # Was 0 (silenced): GMSH's own progress output ("Meshing
                # curve/surface/volume N", "Info: Done meshing 3D (Wall
                # Xs)", element-splitting retry messages, etc.) is real,
                # specific progress — with it off, a long run showed the
                # user nothing but this app's own synthetic "still
                # running, Ns elapsed" heartbeat, no way to tell genuine
                # multi-minute work on a big/complex mesh apart from a
                # stuck one. _stream_subprocess already reads and forwards
                # every stdout line as it's produced, so turning this back
                # on costs nothing extra to wire up — it just stops
                # throwing away output that was already flowing through
                # the same pipe. The CLI's own JSON result is still always
                # the LAST non-empty line (see generate_volume_mesh's CLI
                # wrapper below), so callers parsing "last line as JSON"
                # are unaffected by GMSH's own chatter appearing before it.
                gmsh.option.setNumber("General.Terminal", 1)
                # Without this, GMSH's OCC STEP/IGES importer ignores the
                # unit declared in the file (its LENGTH_UNIT entity, e.g.
                # SI_UNIT(.MILLI.,.METRE.)) and keeps the raw numeric
                # coordinate values as-is — confirmed live: a STEP file
                # explicitly declaring millimetres (cadquery's default
                # export, and most mechanical CAD tools' default too) came
                # through gmsh.open() with getBoundingBox() reporting the
                # RAW file numbers unconverted, i.e. a real 2mm part read
                # as a 2-METRE part internally — a 1000x scale mismatch
                # against every other part of this app, which treats the
                # same file as millimetres (see geometry.py's mm->m
                # conversion). "M" tells GMSH to convert TO metres FROM
                # whatever the file declares, rather than passing values
                # through unconverted (the default, empty-string behavior).
                gmsh.option.setString("Geometry.OCCTargetUnit", "M")
                _GMSH_INITIALIZED = True
    import gmsh
    return gmsh


def gmsh_shutdown():
    """Finalize GMSH. Call once at app exit."""
    global _GMSH_INITIALIZED
    if _GMSH_INITIALIZED:
        import gmsh
        try:
            gmsh.finalize()
        except Exception:
            # best-effort teardown
            logger.debug("gmsh_wrapper: gmsh.finalize failed", exc_info=True)
        _GMSH_INITIALIZED = False


def from_step(filepath: Path | str) -> list[str]:
    """Open a STEP file in GMSH, return physical surface names.

    Uses OpenCASCADE kernel. Returns the list of patch names found
    (physical groups), ready to be mapped to inlet/outlet/wall.
    """
    gmsh = _ensure_gmsh()
    gmsh.clear()
    filepath = str(Path(filepath).resolve())
    gmsh.open(filepath)

    # Get physical groups (these are the user-defined patch names from
    # the STEP file; if none exist, GMSH auto-assigns dim tags).
    groups = gmsh.model.getPhysicalGroups()
    names: list[str] = []
    for dim, tag in groups:
        name = gmsh.model.getPhysicalName(dim, tag)
        if not name:
            name = f"surface_{dim}_{tag}"
        names.append(name)

    # If no physical groups, fall back to surface entities
    if not names:
        entities = gmsh.model.getEntities(2)
        names = [f"surface_{tag}" for _, tag in entities]
    return names


def compute_sizing(
    filepath: Path | str,
    detail: str = "medium",
    user_lc: float | None = None,
) -> GmshSizing:
    """Open a STEP/STP file in GMSH and compute curvature + proximity.

    Args:
        filepath: Path to STEP file.
        detail: 'coarse' | 'medium' | 'fine' — controls how aggressively
            small features are resolved.
        user_lc: Optional override for the global element size.

    Returns:
        GmshSizing with min curvature radius, min proximity, and
        suggested per-patch surface/volume sizes.
    """
    gmsh = _ensure_gmsh()
    gmsh.clear()
    filepath_str = str(Path(filepath).resolve())
    gmsh.open(filepath_str)

    # Get bounding box
    bbox = gmsh.model.getBoundingBox(-1, -1)
    dx, dy, dz = bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]
    max_extent = max(dx, dy, dz)

    # Default element size
    lc_user = user_lc or max_extent * 0.02

    # Detail multipliers
    df = _GMSH_DETAIL.get(detail, _GMSH_DETAIL["medium"])

    # Set mesh options for optimal surface extraction
    gmsh.option.setNumber("Mesh.CharacteristicLengthMin", lc_user * 0.01)
    gmsh.option.setNumber("Mesh.CharacteristicLengthMax", lc_user * 2)
    gmsh.option.setNumber("Mesh.CharacteristicLengthFromPoints", 0)
    gmsh.option.setNumber("Mesh.CharacteristicLengthFromCurvature", 1)
    gmsh.option.setNumber("Mesh.CharacteristicLengthExtendFromBoundary", 1)
    gmsh.option.setNumber("Mesh.MinimumCirclePoints", df["curv_angle"])

    # Compute min element size from curvature
    gmsh.option.setNumber("Mesh.CharacteristicLengthFromCurvature", 1)
    gmsh.option.setNumber("Mesh.MinimumElementsPerTwoPi", int(360 / df["curv_angle"]))

    # Get physical groups (patch names)
    groups = gmsh.model.getPhysicalGroups()
    physical_names: dict[int, str] = {}
    for dim, tag in groups:
        name = gmsh.model.getPhysicalName(dim, tag)
        if not name:
            name = f"surface_{dim}_{tag}"
        physical_names[tag] = name

    # Compute per-surface min curvature radius (approximation)
    from math import sqrt
    patch_sizes: dict[str, float] = {}
    min_curvature = max_extent
    min_proximity = max_extent

    surfaces = gmsh.model.getEntities(2)
    for _, tag in surfaces:
        # Get bounding box of this surface
        sbox = gmsh.model.getBoundingBox(2, tag)
        sx, sy, sz = sbox[3] - sbox[0], sbox[4] - sbox[1], sbox[5] - sbox[2]
        surf_diag = sqrt(sx*sx + sy*sy + sz*sz)

        # Approximate curvature from surface diagonal
        # (small diagonal = likely curved; large diagonal = likely flat)
        curv_est = max(0.001, surf_diag * df["min_mult"])
        if curv_est < min_curvature:
            min_curvature = curv_est

        # Surface element size
        surf_size = max(curv_est, lc_user * 0.1)
        name = physical_names.get(tag, f"surface_{tag}")
        patch_sizes[name] = round(surf_size, 6)

    # Volume element size (slightly larger than surface -> smooth growth)
    vol_size = max(min_curvature * 2.0, lc_user * 0.5)

    return GmshSizing(
        min_curvature_radius=round(min_curvature, 6),
        min_proximity=round(min_proximity, 6),
        max_extent=round(max_extent, 6),
        suggested_surface_size=round(min_curvature, 6),
        suggested_volume_size=round(max(vol_size, lc_user * 0.5), 6),
        patch_sizes=patch_sizes,
        n_physical_groups=len(groups),
    )


def generate_surface_stl(
    filepath: Path | str,
    output_stl: Path | str,
    detail: str = "medium",
    user_lc: float | None = None,
) -> list[str]:
    """Generate a curvature-adapted surface STL using GMSH.

    This is the *hybrid* flow:
      1. GMSH opens CAD (STEP)
      2. Computes curvature-aware sizing field
      3. Generates 2D surface mesh (triangles)
      4. Exports as ASCII STL → can be read by cfMesh for volume fill

    Returns:
        List of patch names found in the STL (suitable for inlet/outlet/wall mapping).
    """
    gmsh = _ensure_gmsh()
    gmsh.clear()
    filepath_str = str(Path(filepath).resolve())
    output_stl = str(Path(output_stl).resolve())
    gmsh.open(filepath_str)

    try:
        bbox = gmsh.model.getBoundingBox(-1, -1)
        dx, dy, dz = abs(bbox[3] - bbox[0]), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2])
        max_extent = max(dx, dy, dz)
        cross_scale = sorted([dx, dy, dz])[1]
        surf_vol = max(dx * dy * dz, 1e-12)
    except Exception as e:
        raise RuntimeError(f"Failed to read geometry bounding box: {e}") from e

    if user_lc:
        df = _GMSH_DETAIL.get(detail, _GMSH_DETAIL["medium"])
        # Same domain-scale safety clamp as generate_volume_mesh's manual
        # path — see its comment for the confirmed failure mode (the
        # built-in test cylinder, 2mm real-world scale after mm->m
        # conversion, hit a 600s non-convergent GMSH run against the GUI's
        # untouched 5cm/1cm defaults).
        if user_lc > max_extent * 0.5:
            logger.warning(
                "Max Cell Size %.5g doesn't fit domain (max_extent=%.5g) — "
                "clamping to %.5g to avoid a non-convergent GMSH run.",
                user_lc, max_extent, max_extent * 0.5,
            )
            user_lc = max_extent * 0.5
        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", user_lc * 0.01)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", user_lc * df["max_mult"])
        gmsh.option.setNumber("Mesh.CharacteristicLengthFromCurvature", 1)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 1)
        gmsh.option.setNumber("Mesh.MinimumCirclePoints", df["curv_angle"])
        gmsh.option.setNumber("Mesh.MinimumElementsPerTwoPi", int(360 / df["curv_angle"]))
    else:
        hw = _hardware_budget()
        feat = _scan_feature_sizes(gmsh, max_extent)
        sizing_info = _configure_adaptive_sizing(
            gmsh, detail, max_extent, feat, hw, cross_scale, domain_volume=surf_vol,
        )
        logger.info("generate_surface_stl adaptive sizing: %s", sizing_info)

    gmsh.option.setNumber("Mesh.Algorithm3D", 1)  # Delaunay
    gmsh.option.setNumber("Mesh.Algorithm", 6)  # Frontal (good for surfaces)

    # Generate 2D mesh (surface)
    try:
        gmsh.model.mesh.generate(2)
    except Exception as e:
        raise RuntimeError(
            f"GMSH surface meshing failed: {e}. "
            "Try reducing max cell size or repairing the CAD geometry."
        ) from e

    # Get physical groups for patch names
    groups = gmsh.model.getPhysicalGroups()
    names: list[str] = []
    for dim, tag in groups:
        name = gmsh.model.getPhysicalName(dim, tag)
        if not name:
            name = f"surface_{dim}_{tag}"
        names.append(name)

    # If no physical groups, create named groups from surface entities
    if not names:
        entities = gmsh.model.getEntities(2)
        for dim, tag in entities:
            gmsh.model.addPhysicalGroup(2, [tag], tag)
            gmsh.model.setPhysicalName(2, tag, f"surface_{tag}")
            names.append(f"surface_{tag}")

    # Write STL (ASCII, multi-solid/named)
    stl_path = Path(output_stl)
    stl_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        gmsh.write(str(stl_path))
    except Exception as e:
        raise RuntimeError(f"GMSH STL export failed: {e}") from e

    # Verify STL output
    if not stl_path.exists() or stl_path.stat().st_size == 0:
        raise RuntimeError(
            f"GMSH STL export produced empty or missing file: {stl_path}. "
            "Check disk space and write permissions."
        )

    logger.info(
        "GMSH surface: %s  detail=%s patches=%s",
        stl_path, detail, names,
    )
    return names


def prepare_foamy_hex_mesh(
    filepath: Path | str,
    case_dir: Path | str,
    detail: str = "medium",
) -> dict:
    """Set up a foamyHexMesh case: a curvature-aware STL surface (same
    generator as the hybrid GMSH+cfMesh flow) plus a self-contained
    foamyHexMeshDict, ready for `OFConfig.build_foamy_hex_mesh_cmd()` to
    run via WSL.

    foamyHexMesh is OpenFOAM's native conformal-Voronoi mesher — it
    builds genuine polyhedra directly from the surface geometry, with no
    tet-mesh + dual-conversion step at all. Investigated as the
    alternative to polyDualMesh's face-orientation defect after
    confirming that defect survives every tet-generation strategy tried
    (curvature/feature-size sizing, gap-aware refinement, budget-driven
    sizing, both HXT and classic Delaunay algorithms) — it's a
    limitation of polyDualMesh's dual construction itself on complex
    real-world CAD, not of input tet mesh quality, so no amount of tet
    tuning was ever going to fix it.

    `locationInMesh` — a point foamyHexMesh needs inside the volume to
    be meshed — is computed as the CAD volume's own center of mass
    (gmsh.model.occ.getCenterOfMass), not guessed or asked of the user:
    for the tube/duct-like flow volumes this app targets (the STEP
    geometry defines the fluid passage directly, confirmed by every
    watertight-volume check so far), center of mass reliably falls
    inside the solid.
    """
    case_dir = Path(case_dir).resolve()
    tri_surface_dir = case_dir / "constant" / "triSurface"
    tri_surface_dir.mkdir(parents=True, exist_ok=True)
    stl_path = tri_surface_dir / "geometry.stl"

    names = generate_surface_stl(filepath, stl_path, detail=detail)

    gmsh = _ensure_gmsh()
    vols = gmsh.model.getEntities(3)
    if vols:
        com = gmsh.model.occ.getCenterOfMass(*vols[0])
    else:
        bbox = gmsh.model.getBoundingBox(-1, -1)
        com = tuple((bbox[i] + bbox[i + 3]) / 2 for i in range(3))

    bbox = gmsh.model.getBoundingBox(-1, -1)
    max_extent = max(
        abs(bbox[3] - bbox[0]), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2])
    )
    df = _GMSH_DETAIL.get(detail, _GMSH_DETAIL["medium"])
    default_cell_size = max_extent * 0.02 * df["max_mult"]

    system_dir = case_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)
    stl_name = stl_path.name
    dict_text = f"""FoamFile
{{
    version 2.0;
    format ascii;
    class dictionary;
    object foamyHexMeshDict;
}}

// Pull in every required default (conformationControls, initialPoints,
// motionControl sub-settings, meshQualityControls, etc.) so this file
// only needs to override what's actually geometry-specific.
#includeEtc "caseDicts/foamyHexMeshDict"

geometry
{{
    {stl_name}
    {{
        type triSurfaceMesh;
        name geometry;
    }}
}}

surfaceConformation
{{
    locationInMesh ({com[0]:.6f} {com[1]:.6f} {com[2]:.6f});

    geometryToConformTo
    {{
        {stl_name}
        {{
            featureMethod extractFeatures;
            includedAngle 140;

            patchInfo
            {{
                type wall;
            }}
        }}
    }}
}}

initialPoints
{{
    initialPointsMethod autoDensity;

    // autoDensity::fillBox SIGFPE'd with the base default's
    // autoDensityCoeffs (no minCellSizeLimit) — the working simpleShapes
    // tutorial always sets this explicitly.
    autoDensityCoeffs
    {{
        minCellSizeLimit        {default_cell_size * 0.5:.6f};
        minLevels               4;
        maxSizeRatio            5.0;
        sampleResolution        3;
        surfaceSampleResolution 3;
    }}
}}

motionControl
{{
    defaultCellSize      {default_cell_size:.6f};
    minimumCellSizeCoeff 0;

    // Required — an empty shapeControlFunctions left foamyHexMesh's
    // internal cell-size background mesh completely uninitialized
    // ("Cell Size Mesh Bounds = (1e+150 ...) (-1e+150 ...)", 0 points
    // generated). Matches the real simpleShapes tutorial's pattern for
    // a plain enclosing surface with no internal objects.
    shapeControlFunctions
    {{
        geometry
        {{
            type                    searchableSurfaceControl;
            priority                1;
            mode                    bothSides;

            surfaceCellSizeFunction uniformValue;
            uniformValueCoeffs
            {{
                surfaceCellSizeCoeff 1;
            }}

            cellSizeFunction        uniform;
            uniformCoeffs
            {{}}
        }}
    }}
}}
"""
    (system_dir / "foamyHexMeshDict").write_text(dict_text, encoding="utf-8")

    logger.info(
        "prepare_foamy_hex_mesh: stl=%s locationInMesh=%s defaultCellSize=%.6f patches=%s",
        stl_path, com, default_cell_size, names,
    )
    return {
        "stl_path": str(stl_path),
        "location_in_mesh": com,
        "default_cell_size": default_cell_size,
        "patch_names": names,
    }


def apply_solution_size_field(
    gmsh_mod,
    size_field_file: Path | str,
    existing_bg_field: int | None = None,
) -> dict:
    """Install a solution-derived target-cell-size field as background sizing.

    *size_field_file* is a GMSH ``Structured`` field file (text format) written
    by ``core.solution_adaptive.write_structured_size_field`` — a regular
    lattice of target cell sizes sampled from a CFD solution's refinement
    indicator. Unlike the ``refinement_zones`` ball primitives, this is a
    genuinely spatially-continuous field: GMSH trilinearly interpolates it, so
    a refined region can follow the actual shape of a shear layer or a
    contraction instead of being approximated by axis-aligned spheres.

    Two things this deliberately does NOT do the obvious way:

    1. It MIN-combines with *existing_bg_field* rather than calling
       setAsBackgroundMesh() on its own. Calling it directly would replace the
       geometry-adaptive field (curvature/small-feature/gap sizing) outright —
       which is exactly what the older ``refinement_zones`` block does, and why
       zones silently coarsen everything the geometry sizing had refined.
       Min-combining means the solution field can only ever ASK FOR SMALLER
       cells than geometry already demanded, never larger, which is the correct
       semantics for a refinement indicator.

    2. It lowers ``Mesh.CharacteristicLengthMin`` to the field's own minimum.
       Verified directly against GMSH 4.15.2: that option is a hard floor
       applied AFTER the background field is evaluated — a probe box meshed
       with a background field asking for 0.02 produced 27,263 nodes with the
       floor at 0, and 443 nodes with the floor left at the field's coarse
       value 0.15. Without this line the whole refinement request is silently
       clamped away and the "refined" mesh comes back essentially unchanged.

    Returns a dict with the field's min/max target size and the tag installed.
    """
    path = Path(size_field_file)
    if not path.exists():
        raise RuntimeError(f"Solution size field file not found: {path}")

    # Parse the field's value range so the CharacteristicLengthMin floor can be
    # dropped to match it (see point 2 above). The first three lines are
    # origin / spacing / counts; everything after is the value lattice.
    tokens = path.read_text(encoding="ascii").split()
    if len(tokens) < 10:
        raise RuntimeError(f"Solution size field file is malformed: {path}")
    values = [float(t) for t in tokens[9:]]
    if not values:
        raise RuntimeError(f"Solution size field file has no values: {path}")
    f_min, f_max = min(values), max(values)

    f_struct = gmsh_mod.model.mesh.field.add("Structured")
    gmsh_mod.model.mesh.field.setString(f_struct, "FileName", str(path))
    gmsh_mod.model.mesh.field.setNumber(f_struct, "TextFormat", 1)
    # Outside the sampled lattice, return something enormous so the MIN below
    # always defers to the geometry field there rather than imposing a size.
    gmsh_mod.model.mesh.field.setNumber(f_struct, "SetOutsideValue", 1)
    gmsh_mod.model.mesh.field.setNumber(f_struct, "OutsideValue", 1e22)

    if existing_bg_field is not None:
        f_bg = gmsh_mod.model.mesh.field.add("Min")
        gmsh_mod.model.mesh.field.setNumbers(
            f_bg, "FieldsList", [f_struct, existing_bg_field]
        )
    else:
        f_bg = f_struct
    gmsh_mod.model.mesh.field.setAsBackgroundMesh(f_bg)

    try:
        current_floor = gmsh_mod.option.getNumber("Mesh.CharacteristicLengthMin")
    except Exception:
        current_floor = 0.0
    new_floor = min(current_floor, f_min) if current_floor > 0 else f_min
    gmsh_mod.option.setNumber("Mesh.CharacteristicLengthMin", new_floor)

    logger.info(
        "Solution size field applied: %s (target size %.6g..%.6g, %d samples), "
        "min-combined with bg_field=%s, CharacteristicLengthMin %.6g -> %.6g",
        path.name, f_min, f_max, len(values), existing_bg_field,
        current_floor, new_floor,
    )
    return {
        "field_tag": f_struct,
        "bg_field": f_bg,
        "min_size": f_min,
        "max_size": f_max,
        "n_samples": len(values),
        "length_min_floor": new_floor,
    }


def _resolve_algo3d() -> int | None:
    """3D meshing algorithm override via ``CFMESH_GMSH_ALGO3D``.

    Values: 1 = classic Delaunay, 6 = Frontal-Delaunay, 10 = HXT.
    Returns None when the env var is unset/invalid — the caller then keeps
    its current default behaviour (HXT). Used by tools/bench_hxt_ab.py to
    A/B the 3D algorithm without touching the default path.
    """
    env = os.environ.get("CFMESH_GMSH_ALGO3D", "").strip()
    if not env:
        return None
    try:
        return int(env)
    except ValueError:
        logger.debug("gmsh_wrapper: bad CFMESH_GMSH_ALGO3D value %r", env, exc_info=True)
        return None


def _gmsh_thread_count() -> int:
    """Number of threads GMSH should use for meshing.

    Honours an explicit ``GMSH_NUM_THREADS`` override; otherwise falls back
    to a conservative cap instead of every logical core.  GMSH threading
    gives essentially no wall-time benefit (measured on the reference valve:
    268 s single-thread vs 274 s with 8 threads — the SAMR path already
    forces 1 for the same reason), but it DOES saturate every core while it
    runs, which froze the whole system during a long STL/CAD volume mesh
    (the user-visible "mesh di gmsh impalla il sistema" symptom).  Leaving
    at least half the machine free keeps the OS and GUI responsive.
    """
    env = os.environ.get("GMSH_NUM_THREADS", "").strip()
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            logger.debug("gmsh_wrapper: bad thread env value %r", env, exc_info=True)
    cpus = os.cpu_count() or 4
    base = max(1, min(cpus // 2, 8))
    # RAM-aware backoff: GMSH's 3D mesher keeps a per-thread chunk of the
    # working mesh in memory, so on a memory-tight machine (WSL2/OpenFOAM
    # plus the GUI already competing for RAM) the default thread count can
    # itself push the system into thrashing — the "mesh di gmsh impalla il
    # sistema" symptom observed with 8 threads. Back off when free RAM is
    # scarce; the wall-time cost is small (GMSH threading gains ~2% on the
    # volume mesh) and a responsive OS is worth far more.
    free_gb = _available_ram_bytes() / (1024 ** 3)
    if free_gb < 6:
        return max(1, min(base, 2))
    if free_gb < 12:
        return max(1, min(base, 4))
    return base


def _apply_gmsh_thread_env(gmsh_mod) -> None:
    """Set General.NumThreads from GMSH_NUM_THREADS (or a capped default).

    GMSH defaults to a single thread; on an 89-surface valve part the volume
    mesh takes minutes. The meshing subprocess is passed the env var so
    large remeshes can use multiple cores without changing the Python API.
    When the env var is absent the capped default keeps the system
    responsive instead of pinning every logical core.
    """
    try:
        gmsh_mod.option.setNumber("General.NumThreads", _gmsh_thread_count())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not set GMSH threads: %s", exc)


def _add_refinement_zone_fields(gmsh_mod, zones: list) -> list:
    """Turn refinement-zone dicts into GMSH size fields, returning their tags.

    Supports two zone shapes (the caller MIN-combines the returned fields with
    the base adaptive sizing):

    * ``{"type": "box", xmin, xmax, ymin, ymax, zmin, zmax, cell_size}`` —
      a true axis-aligned box (GMSH ``Box`` field). Used by the GUI's manual
      3D refinement-box editor.
    * ``{"centre": (cx,cy,cz), "radius": r, "cell_size": cs}`` — a sphere
      (GMSH ``Ball`` field); the legacy throat-detector / picker zones.

    All fields set ``VOut = -1`` so they only shrink size inside the zone and
    never coarsen what surrounds it (a refinement zone must be refinement-only).
    """
    zone_fields = []
    for i, z in enumerate(zones):
        if z.get("type") == "box":
            try:
                bf = gmsh_mod.model.mesh.field.add("Box")
                for k, v in (
                    ("XMin", z.get("xmin")), ("XMax", z.get("xmax")),
                    ("YMin", z.get("ymin")), ("YMax", z.get("ymax")),
                    ("ZMin", z.get("zmin")), ("ZMax", z.get("zmax")),
                    ("VIn", z.get("cell_size")),
                ):
                    if v is None:
                        raise ValueError(f"box zone {i} missing {k}")
                    gmsh_mod.model.mesh.field.setNumber(bf, k, float(v))
                gmsh_mod.model.mesh.field.setNumber(bf, "VOut", -1)
                zone_fields.append(bf)
                logger.info(
                    "GMSH refinement box %d: x[%.4g,%.4g] y[%.4g,%.4g] "
                    "z[%.4g,%.4g] cell_size=%.5g",
                    i, z["xmin"], z["xmax"], z["ymin"], z["ymax"],
                    z["zmin"], z["zmax"], z["cell_size"],
                )
            except Exception as exc:
                logger.warning("Failed to add GMSH refinement box %d: %s", i, exc)
            continue
        cx, cy, cz = z.get("centre", (0, 0, 0))
        r = z.get("radius", 0.1)
        cs = z.get("cell_size", 0.01)
        try:
            ball_field = gmsh_mod.model.mesh.field.add("Ball")
            gmsh_mod.model.mesh.field.setNumber(ball_field, "VIn", cs)
            gmsh_mod.model.mesh.field.setNumber(ball_field, "VOut", -1)
            gmsh_mod.model.mesh.field.setNumber(ball_field, "Radius", r)
            # Centre keys are XCenter/YCenter/ZCenter — the bare X/Y/Z names
            # are unknown options, which silently made every manually-placed
            # sphere zone fail to build (found by extracting this into the
            # _add_refinement_zone_fields helper and unit-testing it).
            gmsh_mod.model.mesh.field.setNumber(ball_field, "XCenter", cx)
            gmsh_mod.model.mesh.field.setNumber(ball_field, "YCenter", cy)
            gmsh_mod.model.mesh.field.setNumber(ball_field, "ZCenter", cz)
            zone_fields.append(ball_field)
            logger.info(
                "GMSH refinement zone %d: centre=(%.4f,%.4f,%.4f) "
                "radius=%.4f cell_size=%.5f",
                i, cx, cy, cz, r, cs,
            )
        except Exception as exc:
            logger.warning("Failed to add GMSH refinement zone %d: %s", i, exc)
    return zone_fields


def generate_volume_mesh(
    filepath: Path | str,
    output_msh: Path | str,
    detail: str = "medium",
    user_lc: float | None = None,
    n_layers: int = 0,
    bl_thickness: float | None = None,
    bl_expansion: float = 1.2,
    refinement_zones: list[dict] | None = None,
    min_cell_size: float | None = None,
    max_cells_override: int | None = None,
    size_field_file: Path | str | None = None,
) -> tuple[Path, list[str]]:
    """Generate a full tetrahedral volume mesh using GMSH (direct flow).

    This is the *direct* flow — no cfMesh needed. The mesh includes
    boundary layers if requested.

    Steps:
      1. Open CAD (STEP)
      2. Compute size field
      3. Generate 3D tetrahedral mesh with optional BL
      4. Export as MSH (.msh)
      5. Convert to OpenFOAM polyMesh via meshio

    Args:
        size_field_file: optional GMSH ``Structured`` field file holding a
            solution-derived target cell size lattice (see
            ``apply_solution_size_field``). Min-combined with whatever
            geometry-based sizing this function set up, so it can only refine
            further, never coarsen. This is the re-meshing hook for
            solution-adaptive refinement.

    Returns:
        (path_to_msh, list_of_patch_names)
    """
    gmsh = _ensure_gmsh()
    gmsh.clear()
    _apply_gmsh_thread_env(gmsh)
    filepath_str = str(Path(filepath).resolve())
    output_msh = str(Path(output_msh).resolve())
    gmsh.open(filepath_str)

    if filepath_str.lower().endswith(".stl"):
        # A raw STL has no B-Rep topology — gmsh.open() only imports the
        # discrete surface triangles, so mesh.generate(3) below has no
        # volume to fill and silently produces 0 tetrahedra (surface
        # triangles pass through untouched). Reconstruct a real volume:
        # classify the discrete triangles into surface patches by feature
        # angle, give them a parametrization, then close a surface loop
        # into a volume — the standard GMSH STL-to-volume workflow (see
        # GMSH tutorial t13).
        gmsh.model.mesh.classifySurfaces(
            angle=40 * math.pi / 180,
            boundary=True,
            # forReparametrization=True unblocks "Invalid exterior boundary
            # mesh for parametrization" on closed discrete surfaces (e.g. a
            # sphere STL); the old default (False) fails there.
            forReparametrization=True,
            curveAngle=180 * math.pi / 180,
        )
        gmsh.model.mesh.createGeometry()
        surfaces = gmsh.model.getEntities(2)
        if not surfaces:
            raise RuntimeError(
                f"GMSH found no surfaces after reconstructing STL topology: {filepath_str}"
            )
        surface_loop = gmsh.model.geo.addSurfaceLoop([tag for _, tag in surfaces])
        gmsh.model.geo.addVolume([surface_loop])
        gmsh.model.geo.synchronize()

    try:
        bbox = gmsh.model.getBoundingBox(-1, -1)
    except Exception as e:
        raise RuntimeError(f"Failed to read geometry bounding box: {e}") from e
    dx, dy, dz = abs(bbox[3] - bbox[0]), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2])
    max_extent = max(dx, dy, dz)
    cross_scale = sorted([dx, dy, dz])[1]  # median dimension — see _configure_adaptive_sizing
    vol = max(dx * dy * dz, 1e-12)

    # SAFETY: estimate cell count and auto-coarsen the explicit-size path
    # if it would run away. The adaptive path has its own hardware-budget
    # clamp (_hardware_budget/_configure_adaptive_sizing) that does the
    # equivalent job with a better size estimate, so it doesn't need a
    # separate pre-check here.
    if user_lc and user_lc > 0:
        # Support production-scale direct meshes.  This is a safety ceiling,
        # not a normal target; the GUI target can request anything up to 20M.
        MAX_GMSH_CELLS = 20_000_000
        est_cells = vol / (user_lc ** 3)
        # The ceiling must also respect the machine's actual RAM, or a
        # legitimately in-budget request (e.g. 15M cells on a box with 32 GB
        # but WSL2/OpenFOAM already holding 16 GB) lets GMSH balloon past
        # physical memory: the system starts thrashing, the GUI main thread
        # stops being scheduled, Windows reports an App Hang and kills the
        # whole app — reproduced repeatedly in a real session (CFD Poly runs
        # all produced 0.7-1.1 GB .msh files, then the GUI froze and was
        # killed by the OS). _hardware_budget's max_cells already encodes a
        # "50% of free RAM / 25% of total RAM" floor that the adaptive path
        # uses; the explicit path now uses the same one instead of assuming
        # 20M is always safe.
        ram_cap = _hardware_budget(None)["max_cells"]
        cell_cap = min(MAX_GMSH_CELLS, ram_cap)
        if est_cells > cell_cap:
            new_lc = (vol / cell_cap) ** (1.0 / 3.0)
            logger.warning(
                "Cell size %.5f would produce ~%.0f cells, exceeding the "
                "hardware budget of %.0f cells (free RAM %s). Auto-coarsening "
                "to %.5f (~%.0f cells).",
                user_lc, est_cells, cell_cap,
                _fmt_bytes(_available_ram_bytes()), new_lc,
                vol / (new_lc ** 3),
            )
            user_lc = new_lc
            if min_cell_size and min_cell_size > 0:
                min_cell_size = min(min_cell_size, user_lc * 0.1)

    # Tag of the background size field currently installed, threaded through
    # the sizing branches below so a solution-adaptive size field can be
    # min-combined with it rather than replacing it (see
    # apply_solution_size_field).
    bg_field_tag: int | None = None

    # Meshing algorithms are chosen HERE, before any sizing configuration —
    # NOT after it (where they used to be). The adaptive sizing path calls
    # _sample_passage_thickness_field -> _extract_boundary_trimesh, which
    # runs mesh.generate(2) to get the tessellated boundary to ray-cast
    # against. That 2D pass is exactly where the Frontal-Delaunay
    # curve-recovery crash happens on periodic-surface CAD, so setting the
    # algorithm afterwards was too late to have any effect: measured on the
    # real valve part, the log showed 314 Frontal-Delaunay surface meshings
    # against 32 MeshAdapt (all of the latter being GMSH's own internal
    # fallback), i.e. the requested algorithm never applied to the pass
    # that matters.
    #
    # HXT (10) for 3D, not classic Delaunay (1) — Delaunay fails with
    # "Invalid boundary mesh (overlapping facets)" on the discrete/
    # reparametrized surfaces produced by STL reconstruction (long curved
    # patches parametrize badly); HXT meshes directly off the discrete
    # boundary triangulation and doesn't hit this.
    #
    # CFMESH_GMSH_ALGO3D lets an A/B benchmark (tools/bench_hxt_ab.py) pick
    # the 3D algorithm (1=Delaunay, 6=Frontal, 10=HXT). Unset = current
    # default behaviour (HXT). When HXT is explicitly requested we ALSO set
    # Mesh.MaxNumThreads3D: HXT only parallelises when that option is set —
    # General.NumThreads alone leaves it at 1 thread, which is why past
    # "already tried HXT, doesn't work" tests saw no speedup.
    algo3d = _resolve_algo3d()
    if algo3d is None:
        gmsh.option.setNumber("Mesh.Algorithm3D", 10)  # HXT (current default)
    else:
        gmsh.option.setNumber("Mesh.Algorithm3D", algo3d)
        if algo3d == 10:
            try:
                gmsh.option.setNumber("Mesh.MaxNumThreads3D", _gmsh_thread_count())
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not set GMSH MaxNumThreads3D: %s", exc)
    # MeshAdapt (1) for 2D, not Frontal-Delaunay (6). Measured over
    # repeated runs on a real CAD part (a valve with periodic Cone/
    # Cylinder/BSpline surfaces whose OCC seam curve is legitimately
    # traversed twice in its own wire): Frontal-Delaunay's curve-
    # intersection recovery loop never converges there and the process
    # SEGFAULTS — a crash, not a catchable Python exception, so the app
    # cannot detect or recover from it. MeshAdapt hits the same surfaces
    # but recovers in one iteration in most runs, and where it still
    # fails it raises a normal catchable exception. Never measured worse
    # on any test case (cylinder, venturi, box).
    gmsh.option.setNumber("Mesh.Algorithm", 1)  # MeshAdapt
    gmsh.option.setNumber("Mesh.Optimize", 1)
    gmsh.option.setNumber("Mesh.OptimizeNetgen", 1)

    if user_lc:
        # Explicit user override: use user's cell sizes directly
        df = _GMSH_DETAIL.get(detail, _GMSH_DETAIL["medium"])
        # User's min cell size takes priority over detail-level min_mult
        lc_min = min_cell_size if min_cell_size and min_cell_size > 0 else user_lc * df["min_mult"]

        # Domain-scale safety clamp. Max/Min Cell Size are free-typed
        # fields with fixed GUI defaults (5cm / 1cm) tuned for metre-scale
        # CFD parts, and nothing forces them to reflect the geometry
        # actually loaded ("Auto-Suggest Cell Sizes" is a manual button,
        # never run automatically) — confirmed live: the built-in 2mm test
        # cylinder (create_test_cylinder, cadquery's native mm units) with
        # those untouched defaults asks GMSH for a minimum cell 5x and a
        # maximum cell 25x larger than the entire object. The est_cells
        # check above only guards the opposite failure (user_lc so SMALL it
        # would produce >20M cells) — a user_lc/lc_min that doesn't fit in
        # the domain at all sails through it (est_cells comes out «1, not
        # «20M) and instead sends GMSH into a real, unbounded "splitting
        # those edges and trying again" retry loop that never converges —
        # reproduced live as a 600s timeout with zero progress. Cap both
        # against max_extent so a size request always fits inside the part,
        # regardless of what's sitting in the GUI fields.
        if user_lc > max_extent * 0.5:
            logger.warning(
                "Max Cell Size %.5g doesn't fit domain (max_extent=%.5g) — "
                "clamping to %.5g to avoid a non-convergent GMSH run.",
                user_lc, max_extent, max_extent * 0.5,
            )
            user_lc = max_extent * 0.5
        if lc_min >= user_lc:
            lc_min = user_lc * 0.2

        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", lc_min)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", user_lc)
        gmsh.option.setNumber("Mesh.CharacteristicLengthFromCurvature", 1)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 1)
        gmsh.option.setNumber("Mesh.MinimumCirclePoints", int(360 / df["curv_angle"]))
        gmsh.option.setNumber("Mesh.MinimumElementsPerTwoPi", int(360 / df["curv_angle"]))
        logger.info(
            "GMSH user cell sizes: max=%.5f min=%.5f (detail=%s)",
            user_lc, lc_min, detail,
        )
    else:
        hw = _hardware_budget(max_cells_override)
        feat = _scan_feature_sizes(gmsh, max_extent)
        # STL input: the adaptive path's curvature size field runs an extra
        # throwaway 2D probe mesh that is exactly where GMSH's curve-
        # recovery loop breaks down on the discrete surfaces an STL
        # produces (measured: pipe.stl and a 12-facet duct both hang in
        # "splitting those edges and trying again" / fail with "unable to
        # find ... Try reducing max cell size" only when that probe runs;
        # with it skipped the same STLs mesh 2D+3D in seconds). A discrete
        # STL has no analytic CAD kernel, so curvature sizing genuinely
        # adds nothing the surface triangles don't already encode — it
        # stays off. Passage-thickness sizing is a DIFFERENT story: it
        # only needs a boundary surface to ray-cast against, and for an
        # STL that surface is the file itself — load it directly with
        # trimesh (no GMSH call at all, so the probe-hang above can't
        # happen) and pass it through so narrow passages/thin features
        # still get local refinement instead of falling back to the
        # coarser Distance/Threshold-only sizing. See
        # _sample_passage_thickness_field/_extract_boundary_trimesh.
        is_stl_input = filepath_str.lower().endswith(".stl")
        passage_boundary_mesh = None
        if is_stl_input:
            try:
                import trimesh as _trimesh
                passage_boundary_mesh = _trimesh.load(
                    filepath_str, force="mesh", process=False,
                )
                if len(passage_boundary_mesh.faces) == 0:
                    passage_boundary_mesh = None
            except Exception:
                logger.exception(
                    "STL passage-thickness field: loading %s for ray-casting "
                    "failed; continuing without it", filepath_str,
                )
                passage_boundary_mesh = None
        sizing_info = _configure_adaptive_sizing(
            gmsh, detail, max_extent, feat, hw, cross_scale, domain_volume=vol,
            use_curvature_size_field=not is_stl_input,
            passage_boundary_mesh=passage_boundary_mesh,
        )
        bg_field_tag = sizing_info.get("bg_field")
        logger.info(
            "generate_volume_mesh adaptive sizing: %s (feature scan: min=%.5f "
            "small_curves=%d, curvature_field=%s)",
            sizing_info, feat["min_feature"], sizing_info["n_small_curves"],
            not is_stl_input,
        )

    # (Meshing algorithms are set near the top of this function, BEFORE the
    # sizing configuration — see the comment there for why the order
    # matters.)

    # Boundary layers
    if n_layers > 0 and bl_thickness is not None:
        surfaces = gmsh.model.getEntities(2)
        surface_tags = [tag for _, tag in surfaces]
        if surface_tags:
            # Auto-assigned tag (not hardcoded 1) — the adaptive sizing
            # fields above already claim their own auto-assigned tags
            # starting at 1, and a hardcoded collision here would either
            # error or silently overwrite one of them.
            bl_field = gmsh.model.mesh.field.add("BoundaryLayer")
            gmsh.model.mesh.field.setNumbers(bl_field, "CurvesList", [])
            gmsh.model.mesh.field.setNumbers(bl_field, "PointsList", [])
            gmsh.model.mesh.field.setNumber(bl_field, "hwall_n", bl_thickness)
            gmsh.model.mesh.field.setNumber(bl_field, "thickness", bl_thickness * n_layers)
            gmsh.model.mesh.field.setNumber(bl_field, "ratio", bl_expansion)
            gmsh.model.mesh.field.setNumber(bl_field, "Quads", 0)
            # A "BoundaryLayer" field only takes effect once activated via
            # setAsBoundaryLayer() — without this call the field is fully
            # configured but silently never applied, so BL settings the
            # user enables in the UI (n_layers/thickness/expansion) have
            # no effect on the generated volume mesh.
            gmsh.model.mesh.field.setAsBoundaryLayer(bl_field)

    # Refinement zones: add Distance + Threshold fields to shrink cell size
    # near narrow passages detected by the throat detector.
    # Each zone creates a field that maps distance-from-centre → cell size,
    # then a Min field combines them all with the base adaptive sizing.
    if refinement_zones:
        zone_fields = _add_refinement_zone_fields(gmsh, refinement_zones)
        # Combine all zone fields with Min (smallest cell size wins)
        if len(zone_fields) == 1:
            bg_field_tag = zone_fields[0]
            gmsh.model.mesh.field.setAsBackgroundMesh(bg_field_tag)
        elif zone_fields:
            bg_field_tag = gmsh.model.mesh.field.add("Min")
            gmsh.model.mesh.field.setNumbers(bg_field_tag, "FieldsList", zone_fields)
            gmsh.model.mesh.field.setAsBackgroundMesh(bg_field_tag)

    # Solution-adaptive sizing: a target-cell-size lattice computed from a CFD
    # solution's refinement indicator. Applied last so it layers on top of
    # every geometry-based source above, and min-combined so it can only
    # refine further. This is the re-mesh half of the AMR loop in
    # core.solution_adaptive.
    if size_field_file:
        sf_info = apply_solution_size_field(gmsh, size_field_file, bg_field_tag)
        bg_field_tag = sf_info["bg_field"]

    # Generate 3D mesh
    try:
        gmsh.model.mesh.generate(3)
    except Exception as e:
        raise RuntimeError(
            f"GMSH volume meshing failed: {e}. "
            "Try reducing max cell size or simplifying the CAD geometry."
        ) from e

    # Get patch names
    groups = gmsh.model.getPhysicalGroups()
    names: list[str] = []
    for dim, tag in groups:
        name = gmsh.model.getPhysicalName(dim, tag)
        if not name:
            name = f"surface_{dim}_{tag}"
        names.append(name)
    if not names:
        for dim, tag in gmsh.model.getEntities(2):
            gmsh.model.addPhysicalGroup(2, [tag], tag)
            gmsh.model.setPhysicalName(2, tag, f"surface_{tag}")
            names.append(f"surface_{tag}")

    # GMSH only writes mesh elements belonging to a defined Physical Group
    # by default. Only surfaces ever got one (above) — the volume never
    # did — so every tetrahedron mesh.generate(3) just produced was
    # silently dropped on write: the .msh always came out with only the
    # boundary triangles, 0 volume cells, no error anywhere. This was the
    # actual root cause of "GMSH direct" always producing 0 tets, for BOTH
    # STEP and STL input — independent of the STL topology reconstruction
    # above (which is still needed for STL specifically, since a raw STL
    # has no volume entity to mesh in the first place).
    #
    # Fixed by tagging the volume(s) too, rather than blanket
    # Mesh.SaveAll=1 — SaveAll also dumps untagged leftover element
    # blocks (stray points/edges from the STL reconstruction, etc.),
    # and downstream msh_to_of_polymesh()'s meshio.read() rejects a
    # mismatched count of cell blocks vs. physical-tag arrays.
    for dim, tag in gmsh.model.getEntities(3):
        gmsh.model.addPhysicalGroup(3, [tag], tag)

    # gmshToFoam (OpenFOAM's own converter) segfaults reading MSH 4.x
    # output on geometries with many adjoining surfaces (e.g. a real
    # multi-face valve part): duplicate-node removal during meshing
    # leaves gaps in the node tag sequence (e.g. 7099 nodes but max tag
    # 7494), and gmshToFoam's MSH4 reader isn't robust to that — it
    # crashes mid-parse ("Starting to read points...", then SIGSEGV) on
    # geometries simple enough to never hit a gap (a plain tube, a block
    # with one hole) never triggered this. Renumbering to a dense 1..N
    # sequence and writing the older MSH 2.2 format — which gmshToFoam
    # handles reliably — avoids both the gaps and the fragile MSH4 parser.
    gmsh.model.mesh.renumberNodes()
    gmsh.model.mesh.renumberElements()
    gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)

    msh_path = Path(output_msh)
    msh_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        gmsh.write(str(msh_path))
    except Exception as e:
        raise RuntimeError(f"GMSH MSH export failed: {e}") from e

    if not msh_path.exists() or msh_path.stat().st_size == 0:
        raise RuntimeError(
            f"GMSH volume export produced empty or missing file: {msh_path}"
        )

    logger.info(
        "GMSH volume: %s  detail=%s nLayers=%d thk=%s patches=%s",
        msh_path, detail, n_layers, bl_thickness, names,
    )
    return msh_path, names


def _cli_main(argv):
    """CLI entry point for the gmsh_wrapper subcommands (surface / volume /
    convert_to_foam).  Kept as a plain function so the PyInstaller-frozen
    exe can call it directly from app.py's subprocess dispatch (runpy on
    the archive path does not work — the module lives inside the PYZ).
    """
    import json
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "surface":
        geom_path = argv[2]
        stl_out = Path(argv[3])
        detail = argv[4] if len(argv) > 4 else "medium"
        stl_out.parent.mkdir(parents=True, exist_ok=True)
        try:
            names = generate_surface_stl(geom_path, stl_out, detail=detail)
            sizing = compute_sizing(geom_path, detail=detail)
            print(json.dumps({
                "success": True,
                "names": names,
                "suggested_volume_size": sizing.suggested_volume_size,
                "suggested_surface_size": sizing.suggested_surface_size,
                "min_curvature_radius": sizing.min_curvature_radius,
                "patch_sizes": sizing.patch_sizes,
            }))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))
            sys.exit(1)
    elif cmd == "volume":
        # Fase 3 P3.4: keyword-args contract. Positional slots are still
        # accepted for external callers, but named --key=value flags override
        # them and are the contract GmshVolumeWorker/run_gmsh_volume use —
        # a 10-deep positional list was unreadable and one more option would
        # have been the 11th.
        flags: dict[str, str] = {}
        rest: list[str] = []
        for a in argv[2:]:
            if a.startswith("--") and "=" in a:
                k, v = a[2:].split("=", 1)
                flags[k] = v
            else:
                rest.append(a)

        def _arg(idx: int, name: str, cast, default):
            if name in flags:
                return cast(flags[name])
            if len(rest) > idx:
                return cast(rest[idx])
            return default

        step_path = _arg(0, "step", str, "")
        msh_path = Path(_arg(1, "msh", str, ""))
        detail = _arg(2, "detail", str, "medium")
        n_layers = _arg(3, "n-layers", int, 0)
        bl_thickness = _arg(4, "bl-thickness", float, None)
        bl_expansion = _arg(5, "bl-expansion", float, 1.2)
        max_cell_size = _arg(6, "max-cell", float, 0)
        min_cell_size = _arg(7, "min-cell", float, 0)
        max_cells_target = _arg(8, "max-cells-target", int, 0)
        # Read refinement zones from environment variable (set by GmshVolumeWorker)
        refinement_zones = None
        zones_json = os.environ.get("GMSH_REFINEMENT_ZONES", "")
        if zones_json:
            try:
                refinement_zones = json.loads(zones_json)
            except json.JSONDecodeError:
                refinement_zones = None
        # Solution-adaptive size field, passed the same way (an env var rather
        # than yet another positional argv slot in an already 10-deep list).
        size_field_file = os.environ.get("GMSH_SOLUTION_SIZE_FIELD", "") or None
        msh_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result_path, names = generate_volume_mesh(
                step_path, msh_path, detail=detail,
                size_field_file=size_field_file,
                n_layers=n_layers, bl_thickness=bl_thickness,
                bl_expansion=bl_expansion,
                refinement_zones=refinement_zones,
                user_lc=max_cell_size if max_cell_size > 0 else None,
                min_cell_size=min_cell_size if min_cell_size > 0 else None,
                max_cells_override=max_cells_target if max_cells_target > 0 else None,
            )
            # Cheap post-hoc check (reuses the still-open GMSH model, a
            # few ms) so the GUI can warn about the known polyDualMesh
            # limitation on complex geometries — see the poly_dual_risk
            # comment in _configure_adaptive_sizing for the evidence.
            poly_dual_risk, n_surfaces, n_gaps = False, 0, 0
            try:
                g = _ensure_gmsh()
                bbox = g.model.getBoundingBox(-1, -1)
                me = max(abs(bbox[3] - bbox[0]), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2]))
                n_surfaces = len(g.model.getEntities(2))
                n_gaps = len(_detect_surface_gaps(g, me))
                poly_dual_risk = n_surfaces > 30 or n_gaps >= 20
            except Exception:
                # risk heuristic is best-effort; default (no risk) is safe
                logger.debug("gmsh_wrapper: poly_dual_risk probe failed", exc_info=True)
            print(json.dumps({
                "success": True, "path": str(result_path), "names": names,
                "poly_dual_risk": poly_dual_risk, "n_surfaces": n_surfaces, "n_gaps": n_gaps,
            }))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))
            sys.exit(1)
    elif cmd == "convert_to_foam":
        # Runs gmshToFoam via a WSL subprocess.run() call, same as
        # OFConfig.build_gmsh_to_foam_cmd always did — but from inside a
        # fresh, short-lived Python process (spawned by the GUI exactly
        # like the "volume" subcommand above), not from within the
        # long-lived GUI process itself. That call reproducibly hung
        # (in a real user session, and independently reproduced with a
        # genuine app.exec() loop) when launched directly from
        # MainWindow, regardless of whether it went through QProcess or
        # a plain threading.Thread — but the exact same command ran
        # fine, fast, every time from a plain script. The most likely
        # explanation is Windows handle/state accumulated by the
        # long-lived GUI process (many prior subprocess/QThread/QProcess
        # calls) leaking into this specific subprocess creation; a
        # fresh process sidesteps that regardless of the precise cause.
        import subprocess
        case_dir_arg = Path(argv[2])
        msh_filename = argv[3]
        try:
            from cfmesh_autogui.config import OFConfig
            cfg = OFConfig()
            wsl_cmd = cfg.build_gmsh_to_foam_cmd(case_dir_arg, msh_filename)
            r = subprocess.run(wsl_cmd, capture_output=True, text=True, timeout=900)
            print(json.dumps({
                "success": r.returncode == 0,
                "returncode": r.returncode,
                "stdout": r.stdout[-4000:],
                "stderr": r.stderr[-2000:],
            }))
            if r.returncode != 0:
                sys.exit(1)
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))
            sys.exit(1)

if __name__ == "__main__":
    _cli_main(sys.argv)
