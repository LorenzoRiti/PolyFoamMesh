# -*- coding: utf-8 -*-
"""Measured BL fidelity: h1 cap reasons and y+ from the achieved layer.

W10.1 separates three facts that the old report mixed:

- the requested first-layer height;
- the h1 the dual support/edge geometry actually allows;
- the h1 and y+ the written mesh actually has.

The functions are pure and never write a mesh.  ``bl_poly`` uses them to put
the numbers on ``PolyBoundaryLayerResult``; ``openfoam_runner`` uses the
same math for the post-build report/GUI log.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

#: Fluid kinematic viscosity at 20 C, m^2/s.  Same values as the W7 GUI and
#: ``solver_calibration``; kept here so the mesh report does not import Qt or
#: the commercial package.
FLUID_NU = {"air": 1.5e-5, "water": 1.0e-6}
FLUID_RHO = {"air": 1.2, "water": 998.0}


def height_cap_stats(
    pre: dict[str, Any], first_height: float, n_layers: int,
    growth: float = 1.2,
) -> dict[str, Any]:
    """Count which term binds the per-vertex stack budget.

    ``_build`` uses ``budget = angle_fade * min(clamp*d, 0.5*edge_cap)``.
    The fallback loop's clamp is 0.5 at the requested scale, which is the
    value that matters for the h1 cap.  This function reports the raw
    support/edge/fade split, not a guessed cause.
    """
    support_n = edge_n = fade_n = 0
    below_h1 = below_full = 0
    budgets: list[float] = []
    h1 = float(first_height)
    if h1 <= 0:
        return {
            "n_wall_vertices": 0, "support_bound": 0, "edge_bound": 0,
            "fade_bound": 0, "below_requested_h1": 0,
            "below_full_stack": 0, "max_budget_over_full_stack": 0.0,
            "budget_percentiles_over_full_stack": [],
        }
    from polyfoammesh.core.bl_poly import _stack_depths

    full = float(
        _stack_depths(int(n_layers), h1, max(float(growth), 1.0))[-1]
    )
    for w in pre.get("wall_verts", ()):
        d = pre["support_d"].get(w)
        if d is None:
            budgets.append(0.0)
            below_h1 += 1
            below_full += 1
            support_n += 1
            continue
        support = 0.5 * float(d)
        edge = 0.5 * float(pre["edge_cap"][w])
        fade = float(pre["angle_fade"][w])
        budget = fade * min(support, edge)
        budgets.append(budget)
        if budget < h1:
            below_h1 += 1
            if support < edge:
                support_n += 1
            else:
                edge_n += 1
            if fade < 1.0:
                fade_n += 1
        if budget < full:
            below_full += 1
    arr = np.asarray(budgets, dtype=np.float64) if budgets else np.zeros(0)
    return {
        "n_wall_vertices": int(arr.size),
        "support_bound": int(support_n),
        "edge_bound": int(edge_n),
        "fade_bound": int(fade_n),
        "below_requested_h1": int(below_h1),
        "below_full_stack": int(below_full),
        "max_budget_over_full_stack": (
            float(arr.max() / full) if arr.size and full > 0 else 0.0
        ),
        "budget_percentiles_over_full_stack": (
            [float(v / full) for v in np.percentile(arr, [0, 10, 50, 90, 100])]
            if arr.size and full > 0 else []
        ),
    }


def cap_reason(stats: dict[str, Any]) -> str:
    """One short reason code for the report/log."""
    support = int(stats.get("support_bound", 0))
    edge = int(stats.get("edge_bound", 0))
    fade = int(stats.get("fade_bound", 0))
    total = max(support + edge + fade, 1)
    parts = []
    if support:
        parts.append(f"support={100.0 * support / total:.0f}%")
    if edge:
        parts.append(f"edge={100.0 * edge / total:.0f}%")
    if fade:
        parts.append(f"fade={100.0 * fade / total:.0f}%")
    return "h1 cap: " + (", ".join(parts) if parts else "no support/edge cap")


def effective_h1_stats(
    built: dict[str, Any], requested_h1: float,
) -> dict[str, Any]:
    """h1 per layered wall face from the built mesh (before writing).

    The first prism owner is ``prism_start + prism_off[bi]``; its centroid
    is half a first-layer height away from the wall face centre.  The
    returned values are the same physical quantity as
    :func:`boundary_layer_report`'s ``h1_median``.
    """
    from polyfoammesh.core.bl_poly import _cell_centroids, _face_geometry

    pts = np.asarray(built["points"], dtype=np.float64)
    faces = built["faces"]
    owner = np.asarray(built["owner"], dtype=np.int64)
    neigh = np.asarray(built["neigh"], dtype=np.int64)
    n_int = int(built["n_int"])
    n_cells = int(max(owner.max(), neigh.max() if neigh.size else -1)) + 1
    centres, _vols = _cell_centroids(
        pts, faces, owner, neigh, n_int, n_cells,
    )
    _sf, cf = _face_geometry(pts, faces)
    wall_faces = built.get("wall_faces") or []
    wall_centres = built.get("wall_face_centres")
    if wall_centres is None:
        wall_centres = cf[np.asarray(wall_faces, dtype=np.int64)]
    off = built.get("prism_off") or {}
    nf = built.get("prism_nf") or {}
    start = int(built.get("prism_start", 0))
    vals: list[float] = []
    for bi, _fi in enumerate(wall_faces):
        if int(nf.get(bi, 0)) <= 0:
            continue
        cell = start + int(off[bi])
        vals.append(
            2.0 * float(np.linalg.norm(centres[cell] - wall_centres[bi]))
        )
    arr = np.asarray(vals, dtype=np.float64) if vals else np.zeros(0)
    request = float(requested_h1 or 0.0)
    ratio = (
        float(np.median(arr) / request) if arr.size and request > 0 else 0.0
    )
    bins = [0.0, 0.05e-3, 0.10e-3, 0.20e-3, 0.50e-3, 1.0e-3, 2.0e-3, 5.0e-3, 1.0]
    hist, _edges = np.histogram(arr, bins=bins) if arr.size else (
        np.zeros(len(bins) - 1, dtype=np.int64), None,
    )
    return {
        "n_layered_faces": int(arr.size),
        "h1_median": float(np.median(arr)) if arr.size else None,
        "h1_p10": float(np.percentile(arr, 10)) if arr.size else None,
        "h1_max": float(arr.max()) if arr.size else None,
        "h1_ratio": ratio,
        "h1_histogram": {
            f"{bins[i] * 1e3:g}-{bins[i + 1] * 1e3:g} mm": int(hist[i])
            for i in range(len(hist))
        },
    }


def fluid_nu(fluid: str) -> float:
    return FLUID_NU["water" if "water" in str(fluid).lower() else "air"]


def fluid_rho(fluid: str) -> float:
    return FLUID_RHO["water" if "water" in str(fluid).lower() else "air"]


def estimate_u_tau(
    velocity: float, reference_length: float, fluid: str = "air",
    flow_type: str = "flat_plate",
) -> tuple[float, float, str]:
    """Correlation estimate ``(u_tau, nu, method)``, not a solver value.

    Reuses the W7 GUI/BLEngine formulas:
    flat plate ``Cf = 0.027 / Re^(1/7)``; pipe ``Cf = 0.079 / Re^0.25``
    below Re 1e5, ``0.046 / Re^0.2`` above.  ``u_tau = U sqrt(Cf/2)``.
    Returns ``(0, nu, "unavailable")`` when inputs are not positive.
    """
    nu = fluid_nu(fluid)
    u = float(velocity or 0.0)
    length = float(reference_length or 0.0)
    if u <= 0 or length <= 0 or nu <= 0:
        return 0.0, nu, "unavailable"
    re = u * length / nu
    if re <= 0:
        return 0.0, nu, "unavailable"
    if str(flow_type).lower() in ("pipe", "internal", "channel"):
        cf = 0.079 / (re ** 0.25) if re < 1.0e5 else 0.046 / (re ** 0.2)
        method = "pipe correlation (Blasius/1/5-power), estimate only"
    else:
        cf = 0.027 / (re ** (1.0 / 7.0))
        method = "flat-plate 1/7-power correlation, estimate only"
    return u * math.sqrt(cf / 2.0), nu, method


def y_plus_for_h1(h1: float, u_tau: float, nu: float) -> float | None:
    if h1 is None or u_tau is None or nu is None:
        return None
    if float(h1) <= 0 or float(u_tau) <= 0 or float(nu) <= 0:
        return None
    return float(h1) * float(u_tau) / float(nu)
