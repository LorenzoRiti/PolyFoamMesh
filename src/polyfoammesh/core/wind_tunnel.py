"""Virtual wind tunnel for external aerodynamics.

The user loads the OBJECT (car, wing, building); the fluid domain is the box
around it minus the object.  Box size follows the usual external-flow
practice, every number editable:

* upstream   = ``upstream_L``  x L   (default 3 L)
* downstream = ``downstream_L`` x L  (default 8 L)
* each side  = ``side_D`` x D        (default 4 D, D = max(H, W))
* top        = ``top_H`` x H         (default 5 H)
* floor      = the object's lowest point when ``on_ground`` (car, building),
  otherwise the same clearance as the top (free flight: wing, projectile)

with L, H, W the object's extents along the flow, up and cross axes.  The
frontal blockage (object frontal box area / tunnel section) is then capped
at ``max_blockage`` (default 3 %) by widening sides and top - a too-small
tunnel accelerates the flow around the object and biases forces.

The domain is built on TRIANGULATED surfaces, not CAD booleans: the box
faces (``inlet``, ``outlet``, ``farfield``, ``ground``) plus the object's
own patches with flipped normals.  fTetWild meshes exactly the space
between them (winding-number filtering), so this works even when the CAD is
a set of surfaces rather than a closed solid.

Two refinement boxes come with it (the GUI's refinement-box schema): one
hugging the object and one covering the near wake.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

_AXES = "xyz"


@dataclass
class TunnelSpec:
    flow_axis: int = 0          # 0 = x, 1 = y, 2 = z
    flow_sign: int = 1          # +1: flow towards +axis
    up_axis: int = 2
    on_ground: bool = False
    upstream_L: float = 3.0
    downstream_L: float = 8.0
    side_D: float = 4.0
    top_H: float = 5.0
    max_blockage: float = 0.03

    def validate(self) -> None:
        if self.flow_axis not in (0, 1, 2) or self.up_axis not in (0, 1, 2):
            raise ValueError("axes must be 0, 1 or 2")
        if self.flow_axis == self.up_axis:
            raise ValueError("flow axis and up axis must differ")
        if self.flow_sign not in (1, -1):
            raise ValueError("flow_sign must be +1 or -1")
        for name in ("upstream_L", "downstream_L", "side_D", "top_H"):
            if getattr(self, name) <= 0.0:
                raise ValueError(f"{name} must be > 0")
        if not 0.0 < self.max_blockage < 0.5:
            raise ValueError("max_blockage must be in (0, 0.5)")


@dataclass
class TunnelBox:
    lo: tuple
    hi: tuple
    L: float
    H: float
    W: float
    blockage: float
    widened: bool
    spec: TunnelSpec

    def summary(self) -> str:
        ext = [self.hi[i] - self.lo[i] for i in range(3)]
        return (
            f"wind tunnel {ext[0]:.4g} x {ext[1]:.4g} x {ext[2]:.4g} m "
            f"(object L {self.L:.4g}, H {self.H:.4g}, W {self.W:.4g} m; "
            f"blockage {100 * self.blockage:.2f} %"
            + (", widened to meet the blockage limit" if self.widened else "")
            + ")"
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["spec"] = asdict(self.spec)
        return d


def compute_tunnel(obj_lo, obj_hi, spec: TunnelSpec | None = None) -> TunnelBox:
    """Tunnel bounds around an object bounding box (metres)."""
    spec = spec or TunnelSpec()
    spec.validate()
    lo = np.asarray(obj_lo, dtype=float)
    hi = np.asarray(obj_hi, dtype=float)
    ext = hi - lo
    f, u = spec.flow_axis, spec.up_axis
    c = 3 - f - u
    L, H, W = float(ext[f]), float(ext[u]), float(ext[c])
    if min(L, H, W) <= 0.0:
        raise ValueError("object bounding box is degenerate")
    D = max(H, W)
    t_lo = lo.copy()
    t_hi = hi.copy()
    # flow direction
    if spec.flow_sign > 0:
        t_lo[f] -= spec.upstream_L * L
        t_hi[f] += spec.downstream_L * L
    else:
        t_hi[f] += spec.upstream_L * L
        t_lo[f] -= spec.downstream_L * L
    side = spec.side_D * D
    top = spec.top_H * H
    floor = 0.0 if spec.on_ground else top

    def _section(side_, top_, floor_):
        return (W + 2 * side_) * (H + top_ + floor_)

    frontal = W * H
    widened = False
    if frontal / _section(side, top, floor) > spec.max_blockage:
        # scale the clearances together until the limit holds
        k_lo, k_hi = 1.0, 2.0
        while frontal / _section(side * k_hi, top * k_hi,
                                 floor * k_hi) > spec.max_blockage:
            k_hi *= 2.0
        for _ in range(60):
            k = 0.5 * (k_lo + k_hi)
            if frontal / _section(side * k, top * k, floor * k) > spec.max_blockage:
                k_lo = k
            else:
                k_hi = k
        side, top, floor = side * k_hi, top * k_hi, floor * k_hi
        widened = True
    t_lo[c] -= side
    t_hi[c] += side
    t_hi[u] += top
    t_lo[u] -= floor
    blockage = frontal / _section(side, top, floor)
    return TunnelBox(tuple(t_lo), tuple(t_hi), L, H, W, blockage, widened, spec)


def refinement_boxes(obj_lo, obj_hi, tunnel: TunnelBox, base_cell: float) -> list[dict]:
    """Object box (level 3) and near-wake box (level 2), GUI schema."""
    from polyfoammesh.core.refinement_boxes import box_cell_size

    spec = tunnel.spec
    lo = np.asarray(obj_lo, dtype=float)
    hi = np.asarray(obj_hi, dtype=float)
    f, u = spec.flow_axis, spec.up_axis
    c = 3 - f - u
    L, D = tunnel.L, max(tunnel.H, tunnel.W)
    t_lo = np.asarray(tunnel.lo)
    t_hi = np.asarray(tunnel.hi)

    def _box(b_lo, b_hi, level):
        b_lo = np.maximum(b_lo, t_lo)
        b_hi = np.minimum(b_hi, t_hi)
        out = {"type": "box", "level": int(level),
               "cell_size": box_cell_size(level, base_cell)}
        for i, a in enumerate(_AXES):
            out[f"{a}min"] = float(b_lo[i])
            out[f"{a}max"] = float(b_hi[i])
        return out

    pad = 0.3 * D
    near_lo, near_hi = lo - pad, hi + pad
    wake_lo, wake_hi = lo - 0.5 * pad, hi + 0.5 * pad
    if spec.flow_sign > 0:
        near_lo[f] = lo[f] - 0.3 * L
        near_hi[f] = hi[f] + 1.0 * L
        wake_lo[f] = hi[f]
        wake_hi[f] = hi[f] + 3.0 * L
    else:
        near_hi[f] = hi[f] + 0.3 * L
        near_lo[f] = lo[f] - 1.0 * L
        wake_hi[f] = lo[f]
        wake_lo[f] = lo[f] - 3.0 * L
    if spec.on_ground:
        near_lo[u] = t_lo[u]
        wake_lo[u] = t_lo[u]
    wake_lo[c] = min(wake_lo[c], lo[c] - pad)
    wake_hi[c] = max(wake_hi[c], hi[c] + pad)
    return [_box(near_lo, near_hi, 3), _box(wake_lo, wake_hi, 2)]


def _box_face_mesh(lo, hi, axis: int, side: int, n: int):
    """Two-triangle-per-cell grid on one box face, outward normal."""
    import trimesh

    a1, a2 = [i for i in range(3) if i != axis]
    s = np.linspace(0.0, 1.0, n + 1)
    g1 = lo[a1] + s * (hi[a1] - lo[a1])
    g2 = lo[a2] + s * (hi[a2] - lo[a2])
    P = np.zeros(((n + 1) ** 2, 3))
    P[:, a1] = np.repeat(g1, n + 1)
    P[:, a2] = np.tile(g2, n + 1)
    P[:, axis] = hi[axis] if side > 0 else lo[axis]
    idx = np.arange((n + 1) ** 2).reshape(n + 1, n + 1)
    a = idx[:-1, :-1].ravel()
    b = idx[1:, :-1].ravel()
    cc = idx[1:, 1:].ravel()
    d = idx[:-1, 1:].ravel()
    F = np.vstack([np.column_stack([a, b, cc]), np.column_stack([a, cc, d])])
    out_n = np.zeros(3)
    out_n[axis] = 1.0 if side > 0 else -1.0
    nrm = np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]])
    if float(nrm.sum(axis=0) @ out_n) < 0.0:
        F = F[:, ::-1]
    # built with the final winding (trimesh's invert() leaves a stale
    # face_normals cache behind)
    return trimesh.Trimesh(P, F, process=False)


def tunnel_patches(obj_meshes, tunnel: TunnelBox, face_cells: int = 8):
    """Fluid-domain surface patches: box faces + inward-facing object.

    Returns a list of named ``trimesh.Trimesh`` (``metadata['name']``): the
    object patches keep their names (walls) with normals flipped to point
    into the object - i.e. out of the fluid - and the six box faces become
    ``inlet``, ``outlet``, ``ground`` (floor when on_ground) and
    ``farfield`` (merged).  ``face_cells`` is the coarse box tessellation
    (the mesher resizes it).
    """
    import trimesh

    spec = tunnel.spec
    lo = np.asarray(tunnel.lo, dtype=float)
    hi = np.asarray(tunnel.hi, dtype=float)
    f, u = spec.flow_axis, spec.up_axis
    names: dict[tuple[int, int], str] = {}
    names[(f, -spec.flow_sign)] = "inlet"
    names[(f, spec.flow_sign)] = "outlet"
    if spec.on_ground:
        names[(u, -1)] = "ground"
    out = []
    far = []
    for axis in range(3):
        for side in (-1, 1):
            m = _box_face_mesh(lo, hi, axis, side, face_cells)
            name = names.get((axis, side), "farfield")
            if name == "farfield":
                far.append(m)
            else:
                m.metadata["name"] = name
                out.append(m)
    farm = trimesh.util.concatenate(far)
    farm.metadata["name"] = "farfield"
    out.append(farm)
    for m in obj_meshes:
        # normals out of the fluid = into the object: reversed winding
        w = trimesh.Trimesh(np.asarray(m.vertices, dtype=float),
                            np.asarray(m.faces)[:, ::-1], process=False)
        w.metadata["name"] = m.metadata.get("name", "wall")
        out.append(w)
    return out


# --------------------------------------------------------------------------
# cell budget
# --------------------------------------------------------------------------

#: levels of the two refinement boxes (see :func:`refinement_boxes`)
_NEAR_LEVEL, _WAKE_LEVEL = 3, 2
#: cells across the object's thinnest extent below which we warn
_MIN_CELLS_ACROSS_OBJECT = 10


def _box_volume(b: dict) -> float:
    return float(np.prod([b[f"{a}max"] - b[f"{a}min"] for a in _AXES]))


def estimate_cells(tunnel: TunnelBox, boxes: list[dict], base: float,
                   obj_volume: float = 0.0) -> float:
    """Hex-equivalent cell count (volume / cell^3) for a base cell size.

    Far field at ``base``, the object box at ``base / 2**(level-1)`` and the
    wake box likewise.  The two boxes may overlap near the trailing edge, so
    this is a slight over-estimate, which is the safe side for a budget.
    The same measure as the load-time "bulk estimate" of the GUI (tets are
    ~8.5x this number).
    """
    v_t = float(np.prod(np.asarray(tunnel.hi) - np.asarray(tunnel.lo)))
    v_boxes = sum(_box_volume(b) for b in boxes)
    cells = max(v_t - v_boxes - obj_volume, 0.0) / base ** 3
    for b in boxes:
        cells += _box_volume(b) / (base / 2 ** (b["level"] - 1)) ** 3
    return cells


def suggest_base_cell(obj_lo, obj_hi, tunnel: TunnelBox, obj_volume: float = 0.0,
                      target_cells: int = 100_000) -> dict:
    """Base (far-field) cell size that meets a cell budget.

    Bisection on :func:`estimate_cells` (monotone in the cell size).  The
    object surface then gets ``base / 2**(NEAR_LEVEL - 1)``; if that leaves
    fewer than ``_MIN_CELLS_ACROSS_OBJECT`` cells across the object's
    thinnest extent the result carries a warning: the budget is too small
    for the object, raise it rather than trust the result.
    """
    if target_cells < 1000:
        raise ValueError("target_cells must be >= 1000")
    ext = np.asarray(obj_hi, dtype=float) - np.asarray(obj_lo, dtype=float)

    def cells(base: float) -> float:
        boxes = refinement_boxes(obj_lo, obj_hi, tunnel, base)
        return estimate_cells(tunnel, boxes, base, obj_volume)

    lo, hi = 1e-9, float(max(np.asarray(tunnel.hi) - np.asarray(tunnel.lo)))
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if cells(mid) > target_cells:
            lo = mid
        else:
            hi = mid
    base = hi
    surface = base / 2 ** (_NEAR_LEVEL - 1)
    across = float(ext.min()) / surface
    out = {
        "base_cell": base, "surface_cell": surface,
        "estimated_cells": cells(base), "cells_across_object": across,
        "warning": "",
    }
    if across < _MIN_CELLS_ACROSS_OBJECT:
        out["warning"] = (
            f"with a budget of {target_cells:,} cells the object's thinnest "
            f"side gets only {across:.1f} cells (surface cell "
            f"{surface * 1e3:.3g} mm): raise the budget or accept a coarse "
            "object surface."
        )
    return out


def open_edge_count(meshes) -> int:
    """Edges used by exactly ONE triangle once the patches are merged.

    0 means no gaps in the surface (an edge shared by 3+ triangles - e.g.
    a hole the healer filled twice - is tolerated: the winding-number
    inside/outside test of the tet mesher does not need it manifold).  A
    positive count on the OBJECT means fTetWild may fill it instead of
    carving it out of the fluid.
    """
    import trimesh
    import trimesh.grouping as grouping

    if not meshes:
        return 0
    c = trimesh.util.concatenate(list(meshes))
    c.merge_vertices()
    return int(len(grouping.group_rows(c.edges_sorted, require_count=1)))
