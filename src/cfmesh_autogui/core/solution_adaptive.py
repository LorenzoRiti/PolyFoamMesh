"""Solution-adaptive mesh refinement — refine where the flow demands it.

This is error-driven remeshing: run a CFD solve, measure where the computed
flow is under-resolved, translate that into a spatially-varying target cell
size, and regenerate the mesh from the original CAD with that size field. It is
the "after a first CFD solution" half of the refinement design in
``docs/poly_workflow_part2.md``.

It is NOT any of the three adaptive-sounding things that already exist here,
and deliberately composes with rather than replaces them:

* ``gmsh_wrapper._configure_adaptive_sizing`` refines from CAD *geometry*
  (curvature, small features, gaps) before any mesh or solve exists. This
  module's size field is MIN-combined with it, so geometry-driven refinement is
  never coarsened away — see ``gmsh_wrapper.apply_solution_size_field``.
* ``commercial.adaptive_loop`` (the OODA engine) remediates bad *mesh quality*
  cells. Its "gradient" language is about size-field grading, not flow.
* ``commercial.amr`` refines an existing case in place with OpenFOAM's
  ``refineMesh``. That utility applies hex-topology cutting and cannot coarsen;
  the design doc rules that family out for this app's tet/poly meshes. Here we
  remesh from CAD instead, so cell size can move in both directions and the
  result is a clean conforming tet mesh with no hanging nodes.


Why h·|grad U| is the refinement indicator
------------------------------------------

The indicator is the cell-local first-difference of velocity, normalised by a
reference speed:

    eta_c = h_c * ||grad U||_F / U_ref

where h_c = V_c^(1/3) is the cell's length scale and ||.||_F the Frobenius norm
of the 3x3 velocity gradient tensor.

The reasoning is the Taylor remainder of the discretisation. A finite-volume
scheme reconstructs the solution across a cell from cell-centred values; the
leading term it cannot represent scales with how much the solution actually
changes over one cell width, i.e. h·|grad U|. Dividing by U_ref makes it the
dimensionless fraction of the flow's own velocity scale that is being lost
inside a single cell. eta_c = 0.5 means velocity changes by half the reference
speed across one cell — the cell is resolving nothing. eta_c = 0.01 means the
field is locally almost linear at cell scale and further refinement buys
little. That is precisely "is this region under-resolved for the flow", and it
peaks exactly where the physics is interesting: contractions (by continuity,
acceleration is largest where area changes fastest), shear layers, separation
and wakes.

Refinement then targets *equidistribution*: drive eta towards a uniform target
eta_t. Since eta is linear in h, the target size follows directly:

    h_new = h_old * (eta_t / eta_c),  clamped

Cells already below eta_t are left alone (the field never asks to coarsen —
that job belongs to the geometry sizing it is min-combined with).

A Hessian- or adjoint-based indicator is more powerful (the adjoint one can
target a specific engineering output like pressure drop) but both need machinery
this pipeline does not have yet, and the simple one is what can be *verified* to
put cells in the physically-obvious place on a venturi. That verification comes
first; see ``tools/venturi_amr_validation.py``.
"""

from __future__ import annotations

import logging
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from cfmesh_autogui.config import OFConfig

logger = logging.getLogger(__name__)

__all__ = [
    "AdaptiveParams",
    "CycleReport",
    "AdaptiveResult",
    "SolutionAdaptiveRefiner",
    "compute_indicator",
    "target_size_field",
    "write_structured_size_field",
    "run_solver",
    "load_solution",
    "pressure_drop_qoi",
    "latest_time_dir",
]

LogFn = Callable[[str], None]


def _noop_log(_msg: str) -> None:
    pass


# ----------------------------------------------------------------------------
# Parameters
# ----------------------------------------------------------------------------

@dataclass
class AdaptiveParams:
    """Knobs for the solve -> indicate -> remesh loop."""

    # --- flow setup -------------------------------------------------------
    inlet_velocity: tuple[float, float, float] = (1.0, 0.0, 0.0)
    turbulence_model: str = "kOmegaSST"

    # --- solver budget ----------------------------------------------------
    # Intermediate cycles use a deliberately short, under-converged solve. The
    # indicator only needs the *spatial structure* of grad(U) — where the flow
    # accelerates and shears — and that structure is established within the
    # first few hundred SIMPLE iterations, long before the residuals bottom
    # out. Paying for full convergence on every cycle multiplies loop cost for
    # a refinement map that barely moves. The final cycle runs longer so the
    # reported engineering quantity is trustworthy. Measured on the venturi
    # case: see tools/venturi_amr_validation.py, which reports the correlation
    # between the short-solve and converged indicator fields.
    solver_iterations: int = 400
    final_solver_iterations: int = 1500
    residual_control: float = 1e-4
    # --- parallelism -------------------------------------------------------
    # simpleFoam cores per solve (1 = serial). The serial solve dominates the
    # per-cycle wall time and scales superlinearly with cells, so a multi-core
    # solve is the difference between usable and unusable at the 6M-cell scale
    # of the real valve part (see tools/valve_resolution_check.py).
    solve_cores: int = 1

    # --- indicator / sizing ----------------------------------------------
    # Cells whose indicator exceeds this quantile of the (volume-weighted)
    # indicator distribution are refined. 0.85 refines the worst ~15% of the
    # flow volume per cycle, which keeps cell growth bounded while still
    # visibly moving the mesh.
    refine_quantile: float = 0.85
    # Hard clamp on how much a single cycle may shrink a cell. Remeshing from
    # CAD means there is no 2:1 topological constraint to respect, but letting
    # size drop 20x in one step produces a size field GMSH grades badly.
    max_refine_ratio: float = 3.0
    # Absolute floor on target cell size. None -> derived from the geometry
    # bounding box (1/2000 of the largest extent).
    min_cell_size: float | None = None

    # --- size field lattice ----------------------------------------------
    # Samples along the longest bounding-box axis. The lattice is what GMSH
    # trilinearly interpolates, so this sets how sharply a refined region can
    # be delineated. 64 on a 3 m part means ~5 cm resolution of the refinement
    # map itself, which is finer than any refinement region worth resolving.
    grid_resolution: int = 64
    # Max allowed ratio between neighbouring lattice values, applied as a
    # gradation sweep. Prevents an abrupt size jump GMSH would have to absorb
    # with badly-shaped transition cells.
    grid_growth_rate: float = 1.4

    # --- loop control -----------------------------------------------------
    max_cycles: int = 3
    max_cells: int = 3_000_000
    # Stop early once the engineering quantity of interest (pressure drop
    # inlet -> outlet) changes by less than this between consecutive cycles.
    # Convergence in the QoI, not "the mesh changed", is the thing that
    # actually says the answer stopped depending on the mesh.
    qoi_tolerance: float = 0.02

    # --- timeouts ---------------------------------------------------------
    solver_timeout_s: int = 3600
    foam_to_vtk_timeout_s: int = 900


@dataclass
class CycleReport:
    """What one solve -> indicate -> remesh cycle did."""
    cycle: int = 0
    cells_before: int = 0
    cells_after: int = 0
    solver_iterations_run: int = 0
    solver_converged: bool = False
    final_residuals: dict[str, float] = field(default_factory=dict)
    indicator_min: float = 0.0
    indicator_max: float = 0.0
    indicator_mean: float = 0.0
    indicator_p95: float = 0.0
    indicator_threshold: float = 0.0
    peak_location: tuple[float, float, float] = (0.0, 0.0, 0.0)
    target_size_min: float = 0.0
    target_size_max: float = 0.0
    n_cells_marked: int = 0
    qoi_pressure_drop: float = 0.0
    qoi_delta_rel: float | None = None
    solve_time_s: float = 0.0
    indicator_time_s: float = 0.0
    remesh_time_s: float = 0.0
    total_time_s: float = 0.0
    note: str = ""


@dataclass
class AdaptiveResult:
    success: bool = False
    cycles: list[CycleReport] = field(default_factory=list)
    initial_cells: int = 0
    final_cells: int = 0
    converged_on_qoi: bool = False
    stop_reason: str = ""
    total_time_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Solution-adaptive refinement: {len(self.cycles)} cycle(s), "
            f"{self.initial_cells:,} -> {self.final_cells:,} cells "
            f"in {self.total_time_s:.0f}s",
            f"Stopped because: {self.stop_reason}",
        ]
        for c in self.cycles:
            qoi = (f"{c.qoi_delta_rel * 100:+.1f}%"
                   if c.qoi_delta_rel is not None else "n/a")
            lines.append(
                f"  cycle {c.cycle}: {c.cells_before:,} -> {c.cells_after:,} cells | "
                f"eta max={c.indicator_max:.3f} p95={c.indicator_p95:.3f} "
                f"thr={c.indicator_threshold:.3f} | dp={c.qoi_pressure_drop:.4g} "
                f"({qoi}) | solve {c.solve_time_s:.0f}s remesh {c.remesh_time_s:.0f}s"
            )
        return "\n".join(lines)


# ----------------------------------------------------------------------------
# Indicator computation
# ----------------------------------------------------------------------------

def compute_indicator(
    grid: Any,
    velocity_field: str = "U",
    u_ref: float | None = None,
) -> dict[str, Any]:
    """Compute the per-cell refinement indicator eta = h*||grad U||_F / U_ref.

    Args:
        grid: a PyVista UnstructuredGrid carrying the solved fields (as written
            by ``foamToVTK``, i.e. velocity as cell data).
        velocity_field: name of the velocity array.
        u_ref: reference speed for normalisation. Defaults to the 99th
            percentile of |U| — not the max, which on a real case is usually a
            single spurious cell at a boundary-condition corner and would
            deflate the whole indicator field.

    Returns a dict with the indicator array, cell centres, cell sizes h, and
    distribution statistics.
    """
    import pyvista as pv  # noqa: F401  (import kept local: heavy, GUI-optional)

    if velocity_field not in grid.cell_data and velocity_field not in grid.point_data:
        raise RuntimeError(
            f"Field '{velocity_field}' not found in the solution. "
            f"cell_data={list(grid.cell_data.keys())} "
            f"point_data={list(grid.point_data.keys())}. "
            "The solve probably wrote no time directory — check the solver log."
        )

    # Cell length scale h = V^(1/3).
    sized = grid.compute_cell_sizes(length=False, area=False, volume=True)
    volumes = np.abs(np.asarray(sized.cell_data["Volume"], dtype=float))
    h = np.cbrt(np.maximum(volumes, 1e-300))

    # The gradient must be taken on point data — VTK's derivative filter
    # differentiates the interpolated field over each cell's shape functions,
    # which needs values at the nodes. foamToVTK writes cell data, so promote
    # first and demote the result afterwards. (Doing it the other way round,
    # differencing cell values directly, would need a face-neighbour traversal
    # this codebase has no reason to hand-roll — see module docstring.)
    work = grid.copy()
    if velocity_field in work.cell_data:
        work = work.cell_data_to_point_data()
    deriv = work.compute_derivative(scalars=velocity_field, gradient=True)
    grad_pt = np.asarray(deriv.point_data["gradient"], dtype=float)  # (n_pts, 9)

    grad_grid = deriv.copy()
    grad_grid.point_data.clear()
    grad_grid.point_data["gradient"] = grad_pt
    grad_cell = grad_grid.point_data_to_cell_data()
    grad = np.asarray(grad_cell.cell_data["gradient"], dtype=float)  # (n_cells, 9)

    grad_norm = np.linalg.norm(grad, axis=1)  # Frobenius norm of the 3x3 tensor

    # Reference speed.
    if velocity_field in grid.cell_data:
        u_arr = np.asarray(grid.cell_data[velocity_field], dtype=float)
    else:
        u_arr = np.asarray(
            grid.point_data_to_cell_data().cell_data[velocity_field], dtype=float
        )
    u_mag = np.linalg.norm(u_arr, axis=1) if u_arr.ndim == 2 else np.abs(u_arr)
    if u_ref is None:
        u_ref = float(np.percentile(u_mag, 99.0))
    u_ref = max(float(u_ref), 1e-12)

    eta = h * grad_norm / u_ref

    centres = np.asarray(grid.cell_centers().points, dtype=float)
    peak_idx = int(np.argmax(eta))

    # Volume-weighted percentiles: a mesh with many tiny cells in one corner
    # would otherwise let those cells dominate the distribution and drag the
    # refinement threshold to wherever the mesh already happens to be finest —
    # exactly the feedback loop that makes naive AMR refine the same spot
    # forever. Weighting by volume asks "what fraction of the FLOW DOMAIN is
    # under-resolved", which is mesh-independent.
    stats = _weighted_stats(eta, volumes)

    return {
        "eta": eta,
        "h": h,
        "volumes": volumes,
        "centres": centres,
        "grad_norm": grad_norm,
        "u_mag": u_mag,
        "u_ref": u_ref,
        "peak_index": peak_idx,
        "peak_location": tuple(float(x) for x in centres[peak_idx]),
        **stats,
    }


def _weighted_stats(eta: np.ndarray, weights: np.ndarray) -> dict[str, float]:
    order = np.argsort(eta)
    e_sorted = eta[order]
    w_sorted = weights[order]
    cw = np.cumsum(w_sorted)
    total = cw[-1] if len(cw) else 0.0

    def q(p: float) -> float:
        if total <= 0:
            return float(np.percentile(eta, p * 100.0)) if len(eta) else 0.0
        return float(e_sorted[int(np.searchsorted(cw, p * total))
                              if np.searchsorted(cw, p * total) < len(e_sorted)
                              else len(e_sorted) - 1])

    return {
        "eta_min": float(eta.min()) if len(eta) else 0.0,
        "eta_max": float(eta.max()) if len(eta) else 0.0,
        "eta_mean": float(np.average(eta, weights=weights)) if total > 0 else 0.0,
        "eta_p50": q(0.50),
        "eta_p95": q(0.95),
        "_quantile_fn": q,  # type: ignore[dict-item]
    }


def target_size_field(
    indicator: dict[str, Any],
    refine_quantile: float = 0.85,
    max_refine_ratio: float = 3.0,
    min_cell_size: float = 0.0,
    max_cells: int | None = None,
) -> dict[str, Any]:
    """Turn a refinement indicator into a per-cell target cell size.

    Equidistribution: pick a target indicator level eta_t (the *refine_quantile*
    of the volume-weighted distribution), then ask each cell above it for
    h_new = h * eta_t/eta, so that after refinement every cell would carry
    roughly the same error contribution. Cells at or below eta_t keep their
    current size — this field only ever refines.

    If the resulting field would blow past *max_cells*, eta_t is raised until it
    fits, and the caller is told (``budget_limited``) rather than the cap being
    applied silently.
    """
    eta = indicator["eta"]
    h = indicator["h"]
    qfn = indicator["_quantile_fn"]

    if len(eta) == 0:
        raise RuntimeError("Indicator is empty — the solution grid has no cells.")

    n_cells_now = len(eta)
    budget_limited = False
    quantile = float(refine_quantile)

    for _ in range(24):
        eta_t = max(qfn(quantile), 1e-12)
        ratio = np.clip(eta_t / np.maximum(eta, 1e-30), 1.0 / max_refine_ratio, 1.0)
        h_new = np.maximum(h * ratio, min_cell_size)
        # Predicted cell count: a cell of size h shrinking to h_new becomes
        # about (h/h_new)^3 cells of the new size.
        predicted = float(np.sum((h / np.maximum(h_new, 1e-300)) ** 3))
        if max_cells is None or predicted <= max_cells:
            break
        budget_limited = True
        quantile = quantile + (1.0 - quantile) * 0.35
        if quantile > 0.9995:
            break
    else:
        eta_t = max(qfn(quantile), 1e-12)
        ratio = np.clip(eta_t / np.maximum(eta, 1e-30), 1.0 / max_refine_ratio, 1.0)
        h_new = np.maximum(h * ratio, min_cell_size)
        predicted = float(np.sum((h / np.maximum(h_new, 1e-300)) ** 3))

    n_marked = int(np.count_nonzero(h_new < h * 0.999))
    return {
        "h_target": h_new,
        "eta_threshold": float(eta_t),
        "quantile_used": quantile,
        "n_cells_marked": n_marked,
        "predicted_cells": predicted,
        "budget_limited": budget_limited,
        "n_cells_now": n_cells_now,
    }


# ----------------------------------------------------------------------------
# Structured size-field lattice
# ----------------------------------------------------------------------------

def write_structured_size_field(
    path: Path | str,
    centres: np.ndarray,
    h_target: np.ndarray,
    bounds: tuple[float, float, float, float, float, float],
    resolution: int = 64,
    growth_rate: float = 1.4,
    baseline: float | None = None,
) -> dict[str, Any]:
    """Sample a per-cell target size onto a regular lattice GMSH can read.

    Writes GMSH's ``Structured`` field text format: origin line, spacing line,
    counts line, then n0*n1*n2 values ordered ``(i*n1 + j)*n2 + k``. Verified
    against GMSH 4.15.2 — a lattice fine in half a box produced a 117:1 node
    ratio between the two halves.

    Scattering, not interpolation. Each cell centre writes its target size into
    the eight lattice nodes surrounding it, taking the minimum. Sampling the
    other way round (each lattice node looks up its nearest cell) silently
    loses any refined region thinner than the lattice spacing — precisely the
    thin shear layers and throats worth refining. Scattering can only ever
    over-cover, never miss.

    Nodes no cell reached are left at *baseline* (default: the coarsest target
    size present), so GMSH's trilinear interpolation grades smoothly out of a
    refined region instead of interpolating towards a sentinel value.
    """
    path = Path(path)
    centres = np.asarray(centres, dtype=float)
    h_target = np.asarray(h_target, dtype=float)

    xmin, xmax, ymin, ymax, zmin, zmax = bounds
    # Pad so boundary cells still land strictly inside the lattice.
    ext = np.array([xmax - xmin, ymax - ymin, zmax - zmin], dtype=float)
    ext = np.maximum(ext, 1e-12)
    pad = ext * 0.02
    origin = np.array([xmin, ymin, zmin]) - pad
    span = ext + 2 * pad

    longest = float(span.max())
    n = np.maximum(
        np.ceil(span / longest * resolution).astype(int) + 1, 2
    )
    spacing = span / (n - 1)

    if baseline is None:
        baseline = float(np.max(h_target))
    grid = np.full(tuple(n), float(baseline), dtype=float)

    # --- scatter each cell's target size into its 8 surrounding nodes -------
    rel = (centres - origin) / spacing
    base_idx = np.floor(rel).astype(int)
    np.clip(base_idx, 0, n - 2, out=base_idx)
    for di in (0, 1):
        for dj in (0, 1):
            for dk in (0, 1):
                ii = base_idx[:, 0] + di
                jj = base_idx[:, 1] + dj
                kk = base_idx[:, 2] + dk
                np.minimum.at(grid, (ii, jj, kk), h_target)

    # --- gradation sweep ---------------------------------------------------
    # Limit how fast the target size may grow between adjacent nodes, so GMSH
    # never has to absorb an abrupt jump with a shell of badly-shaped
    # transition cells. Standard sizing-field smoothing: repeatedly cap each
    # node against its neighbours plus growth_rate * spacing, until stable.
    grid = _apply_gradation(grid, spacing, growth_rate)

    # repr(float(x)), not repr(x): under numpy >= 2 the repr of a np.float64 is
    # "np.float64(0.01)", which would write a size field GMSH cannot parse.
    lines = [
        " ".join(repr(float(v)) for v in origin),
        " ".join(repr(float(v)) for v in spacing),
        f"{int(n[0])} {int(n[1])} {int(n[2])}",
    ]
    # Flatten in gmsh's (i*n1 + j)*n2 + k order, which is plain C order.
    body = "\n".join(repr(float(v)) for v in grid.ravel(order="C"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n" + body + "\n", encoding="ascii")

    return {
        "path": path,
        "shape": tuple(int(x) for x in n),
        "origin": tuple(float(x) for x in origin),
        "spacing": tuple(float(x) for x in spacing),
        "min_size": float(grid.min()),
        "max_size": float(grid.max()),
        "n_nodes": int(grid.size),
        # The lattice itself, so the caller can sample it back and predict the
        # resulting cell count from what GMSH will actually read — see
        # predict_cells_from_lattice.
        "lattice": grid,
    }


def sample_structured_field(
    lattice: np.ndarray,
    origin: tuple[float, float, float] | np.ndarray,
    spacing: tuple[float, float, float] | np.ndarray,
    points: np.ndarray,
) -> np.ndarray:
    """Trilinearly sample a size lattice at *points* — the same interpolation
    GMSH performs internally when it reads the ``Structured`` field.
    """
    origin = np.asarray(origin, dtype=float)
    spacing = np.asarray(spacing, dtype=float)
    n = np.array(lattice.shape)

    rel = (np.asarray(points, dtype=float) - origin) / spacing
    base = np.floor(rel).astype(int)
    np.clip(base, 0, n - 2, out=base)
    frac = np.clip(rel - base, 0.0, 1.0)

    out = np.zeros(len(points), dtype=float)
    for di in (0, 1):
        wi = frac[:, 0] if di else 1.0 - frac[:, 0]
        for dj in (0, 1):
            wj = frac[:, 1] if dj else 1.0 - frac[:, 1]
            for dk in (0, 1):
                wk = frac[:, 2] if dk else 1.0 - frac[:, 2]
                out += (wi * wj * wk) * lattice[
                    base[:, 0] + di, base[:, 1] + dj, base[:, 2] + dk
                ]
    return out


# How many tetrahedra a mesher actually produces per unit volume when asked for
# size h, expressed as a multiple of the naive 1/h**3.
#
# Two things make the naive count wrong. First, units: h here is a
# VOLUME-EQUIVALENT length (h = V**(1/3)), while a GMSH size field value is a
# CHARACTERISTIC EDGE length. A regular tet of edge a has volume a**3/(6*sqrt2),
# so the two differ by (6*sqrt2)**(1/3) ~ 1.82 in length and 6*sqrt2 ~ 8.49 in
# count. Second, a real tet mesh is not made of regular tets, so the true factor
# sits below that ideal.
#
# Measured on the venturi cycle 1: a field whose naive sum was 357,687 produced
# 1,822,916 cells -> 5.10, against the ideal-regular-tet 8.49. Used only as the
# starting guess; SolutionAdaptiveRefiner recalibrates it from the real
# before/after counts each cycle, since the true value is geometry- and
# mesher-dependent and not worth pretending is universal.
TET_COUNT_CALIBRATION_DEFAULT = 5.1


def predict_cells_from_lattice(
    lattice: np.ndarray,
    origin: tuple[float, float, float] | np.ndarray,
    spacing: tuple[float, float, float] | np.ndarray,
    centres: np.ndarray,
    volumes: np.ndarray,
    calibration: float = TET_COUNT_CALIBRATION_DEFAULT,
    h_current: np.ndarray | None = None,
) -> float:
    """Predict the post-remesh cell count from the size field GMSH will read.

    Summing ``(h_old/h_target)**3`` over the per-cell wish list — the obvious
    estimate — is badly wrong, because the wish list is not what GMSH sees. The
    wish list goes through a finite lattice: scattering a cell's small target
    size into its 8 surrounding lattice nodes, then trilinear interpolation,
    smears that small size across a whole lattice cell. Measured on the venturi:
    the per-cell estimate said 116,598 cells and the remesh produced 1,822,916
    — 15.6x out, enough to sail straight through a 3M cell budget that should
    have bound.

    So predict from the actual lattice instead, sampled back at the existing
    cell centres (which keeps the estimate inside the fluid domain rather than
    integrating over the whole bounding box, most of which is solid or empty).
    Each existing cell of volume V that will be filled with cells of size h
    contributes V/h**3 of them.
    """
    lat_sum, geom_sum = _prediction_components(
        lattice, origin, spacing, centres, volumes, h_current
    )
    return lat_sum * float(calibration) + geom_sum


def _prediction_components(
    lattice: np.ndarray,
    origin, spacing,
    centres: np.ndarray,
    volumes: np.ndarray,
    h_current: np.ndarray | None,
) -> tuple[float, float]:
    """Split the predicted cell count into (lattice-governed, geometry-governed).

    The two halves must NOT share a calibration factor, and getting this wrong
    caused a 4x budget overshoot (cycle 2 of the venturi run: budget 2.5M,
    predicted 13.7M, produced 10.3M).

    Why: `h = V**(1/3)` means `sum(V/h**3)` is IDENTICALLY the current cell
    count. So in regions where geometry sizing wins the MIN, the contribution is
    already the true number of cells — exact, no conversion needed. Multiplying
    it by the tet calibration (~5) invented ~5x more cells than exist there, and
    put a floor of `n_cells * calibration` under every prediction. With a
    1.27M-cell mesh and calibration 5.68 that floor was 7.2M, so a 2.5M budget
    was unreachable no matter how far the target size was relaxed — the loop
    exhausted its attempts every time and proceeded anyway.

    The calibration belongs ONLY to the lattice-governed part, because it
    encodes how GMSH interprets a written field value: as a characteristic EDGE
    length, whereas these h values are volume-equivalent lengths.
    """
    h_lat = np.maximum(sample_structured_field(lattice, origin, spacing, centres), 1e-300)
    vol = np.asarray(volumes, dtype=float)
    if h_current is None:
        return float(np.sum(vol / h_lat ** 3)), 0.0
    h_cur = np.maximum(np.asarray(h_current, dtype=float), 1e-300)
    lattice_wins = h_lat < h_cur
    lat_sum = float(np.sum(vol[lattice_wins] / h_lat[lattice_wins] ** 3))
    geom_sum = float(np.sum(vol[~lattice_wins] / h_cur[~lattice_wins] ** 3))
    return lat_sum, geom_sum


def _apply_gradation(grid: np.ndarray, spacing: np.ndarray, rate: float,
                     max_sweeps: int = 40) -> np.ndarray:
    """Cap each node against its 6 face neighbours plus rate*spacing."""
    if rate <= 1.0:
        return grid
    g = grid
    for _ in range(max_sweeps):
        prev = g
        new = g.copy()
        for axis, dx in enumerate(spacing):
            step = float(rate - 1.0) * float(dx)
            shifted_fwd = np.roll(g, 1, axis=axis)
            shifted_bwd = np.roll(g, -1, axis=axis)
            # Roll wraps around; blank the wrapped face so opposite sides of
            # the domain don't leak size into each other.
            sl_f = [slice(None)] * 3
            sl_f[axis] = 0
            shifted_fwd[tuple(sl_f)] = np.inf
            sl_b = [slice(None)] * 3
            sl_b[axis] = -1
            shifted_bwd[tuple(sl_b)] = np.inf
            new = np.minimum(new, shifted_fwd + step)
            new = np.minimum(new, shifted_bwd + step)
        g = new
        if np.allclose(g, prev, rtol=1e-6, atol=0.0):
            break
    return g


# ----------------------------------------------------------------------------
# OpenFOAM interaction
# ----------------------------------------------------------------------------

_TIME_DIR_RE = re.compile(r"^\d+(\.\d+)?$")


def latest_time_dir(case_dir: Path) -> str | None:
    """Name of the highest-numbered non-zero time directory, or None."""
    times = []
    for p in Path(case_dir).iterdir():
        if p.is_dir() and _TIME_DIR_RE.match(p.name):
            try:
                v = float(p.name)
            except ValueError:
                continue
            if v > 0:
                times.append((v, p.name))
    if not times:
        return None
    return max(times)[1]


def set_end_time(case_dir: Path | str, iterations: int) -> int:
    """Patch ``system/controlDict`` endTime — the solver's iteration ceiling.

    The iteration budget is part of the case, not the solver invocation, so a
    cycle that wants a longer run must edit the case before solving.

    Also clamps ``writeInterval`` down to the new endTime: with
    ``writeControl timeStep``, a writeInterval larger than endTime means the
    solver writes NO time directory (seen live: 300 iterations ran 38 s and
    wrote nothing — silently losing the solution the whole indicator step
    depends on).
    """
    path = Path(case_dir) / "system" / "controlDict"
    text = path.read_text(encoding="ascii", errors="replace")
    text = re.sub(
        r"^endTime\s+\S+;",
        f"endTime         {int(iterations)};",
        text, count=1, flags=re.MULTILINE,
    )
    m_wi = re.search(r"^writeInterval\s+(\d+);", text, flags=re.MULTILINE)
    if m_wi and int(m_wi.group(1)) > int(iterations):
        text = re.sub(
            r"^writeInterval\s+\d+;",
            f"writeInterval   {int(iterations)};",
            text, count=1, flags=re.MULTILINE,
        )
    path.write_text(text, encoding="ascii")
    return int(iterations)


def _build_parallel_solve_cmd(
    cfg: OFConfig,
    case_dir: Path,
    application: str,
    n_cores: int,
) -> list[str]:
    """Build a WSL command that solves in parallel on native tmpfs.

    OpenMPI on WSL2 segfaults when the case lives on /mnt/c (9P filesystem)
    — the app's ``ParallelMeshEngine`` already works around this by copying
    the case to a native /tmp dir, running there, and copying results back.
    This mirrors that exact pattern for the solver: copy case -> tmpfs,
    decomposePar, ``mpirun ... -parallel``, reconstructPar, copy the latest
    time directory back.
    """
    linux_case = cfg.wsl_linux_case_path(case_dir)
    env_q = shlex.quote(cfg.env_script)
    app_q = shlex.quote(application)
    n = n_cores
    script = (
        "#!/bin/bash\n"
        f"export OMPI_MCA_btl=^openib,openfabric,uct\n"
        f"source {env_q} 2>/dev/null\n"
        f"SRC={shlex.quote(linux_case)}\n"
        f"TMPD=$(mktemp -d /tmp/cfmesh_solve_XXXXX)\n"
        f"cp -r \"$SRC/system\" \"$TMPD/\" 2>/dev/null\n"
        f"cp -r \"$SRC/0\" \"$TMPD/\" 2>/dev/null\n"
        f"mkdir -p \"$TMPD/constant\"\n"
        f"cp -r \"$SRC/constant/polyMesh\" \"$TMPD/constant/\" 2>/dev/null\n"
        # Exact filenames: the file is constant/transportProperties (no dot),
        # so a glob like *.transportProperties would never match.
        f'cp -f "$SRC/constant/transportProperties" "$SRC/constant/turbulenceProperties" '
        f'"$TMPD/constant/" 2>/dev/null\n'
        f"cd \"$TMPD\"\n"
        f"cat > system/decomposeParDict << 'EOF'\n"
        f"FoamFile {{ version 2.0; format ascii; class dictionary; object decomposeParDict; }}\n"
        f"numberOfSubdomains {n};\n"
        f"method scotch;\n"
        f"scotchCoeffs {{ preservePatches (boundary); }}\n"
        f"EOF\n"
        f"decomposePar -force 2>&1 | tail -15\n"
        f"RC1=${{PIPESTATUS[0]}}\n"
        # decomposePar writes the decomposed mesh + 0/ fields into processorN/
        # but NOT the uniform constant/ dictionaries (transportProperties,
        # turbulenceProperties ...) — the parallel solver aborts on rank 0
        # with "cannot find file processorN/constant/transportProperties".
        # Copy them into every rank before mpirun (exact names, see above).
        f"for i in $(seq 0 {n - 1}); do\n"
        f"  cp -f \"$TMPD/constant/transportProperties\" "
        f"\"$TMPD/constant/turbulenceProperties\" \"$TMPD/processor$i/constant/\" 2>/dev/null\n"
        f"done\n"
        f"SOLVE_RC=1\n"
        f"if [ $RC1 -eq 0 ]; then\n"
        f"  mpirun --allow-run-as-root --oversubscribe -np {n} {app_q} -parallel "
        f"2>&1 | tee \"$TMPD/solver_parallel.log\" | tail -400\n"
        f"  SOLVE_RC=${{PIPESTATUS[0]}}\n"
        f"fi\n"
        f"if [ $SOLVE_RC -eq 0 ]; then\n"
        f"  reconstructPar -latestTime 2>&1 | tail -10\n"
        f"  RC3=${{PIPESTATUS[0]}}\n"
        f"  LATEST=$(ls -d [0-9]*/ 2>/dev/null | tr -d '/' | sort -n | tail -1)\n"
        f"  if [ -n \"$LATEST\" ] && [ $RC3 -eq 0 ]; then\n"
        f"    cp -r \"$TMPD/$LATEST\" \"$SRC/\" 2>/dev/null\n"
        f"  fi\n"
        f"else\n"
        f"  RC3=$SOLVE_RC\n"
        f"fi\n"
        f"cp \"$TMPD/solver_parallel.log\" \"$SRC/\" 2>/dev/null\n"
        f"rm -rf \"$TMPD\"\n"
        f"exit $RC3\n"
    )
    script_path = case_dir / "system" / "_run_parallel_solve.sh"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="ascii", newline="")
    linux_script = cfg.wsl_linux_case_path(script_path)
    return cfg._build_wsl_cmd(f"bash {shlex.quote(linux_script)}")


def run_solver(
    case_dir: Path | str,
    of_config: OFConfig | None = None,
    application: str = "simpleFoam",
    timeout_s: int = 3600,
    on_line: LogFn = _noop_log,
    n_cores: int = 1,
) -> dict[str, Any]:
    """Run the solver, streaming its log live, and report how it ended.

    Streaming matters here beyond tidiness: a solve is the single longest step
    in the loop, and a silent one is indistinguishable from a hang.

    Passing *n_cores* > 1 solves in parallel (decomposePar -> mpirun ->
    reconstructPar on native WSL tmpfs, see ``_build_parallel_solve_cmd``).
    This is the single biggest lever for making the loop usable at scale: the
    serial solve dominates the per-cycle wall time (~2.4 s/iter at 1.26M
    cells, ~11 s/iter at 6M cells) and scales poorly.
    """
    from cfmesh_autogui.core.openfoam_runner import _stream_subprocess

    case_dir = Path(case_dir).resolve()
    cfg = of_config or OFConfig()
    if n_cores > 1:
        cmd = _build_parallel_solve_cmd(cfg, case_dir, application, n_cores)
    else:
        cmd = cfg._build_wsl_cmd(
            f"source {shlex.quote(cfg.env_script)} 2>/dev/null; "
            f"cd {cfg._quoted_linux_path(case_dir)} && {shlex.quote(application)}"
        )

    iteration = [0]
    residuals: dict[str, float] = {}
    converged = [False]
    # simpleFoam prints "Time = 37" per outer iteration and, per equation,
    # "Solving for Ux, Initial residual = 1.2e-03, ...". Track the last initial
    # residual seen for each equation so we can report how far it actually got.
    time_re = re.compile(r"^Time = (\d+)")
    res_re = re.compile(r"Solving for (\w+), Initial residual = ([0-9.eE+-]+)")

    def handle(line: str) -> None:
        m = time_re.match(line.strip())
        if m:
            it = int(m.group(1))
            iteration[0] = it
            # Don't echo every single iteration — on a 1500-iteration run that
            # is 1500 log lines of noise. Every 25th plus the residuals gives
            # the user live evidence of progress without drowning the panel.
            if it % 25 == 0:
                res_txt = ", ".join(f"{k}={v:.2e}" for k, v in sorted(residuals.items()))
                on_line(f"[solve] iteration {it}  {res_txt}")
            return
        m = res_re.search(line)
        if m:
            residuals[m.group(1)] = float(m.group(2))
            return
        if "SIMPLE solution converged" in line:
            converged[0] = True
            on_line(f"[solve] converged at iteration {iteration[0]}")
        elif "FOAM FATAL" in line or (
            "Floating point exception" in line and "trapping enabled" not in line
        ):
            # "trapFpe: Floating point exception trapping enabled" is OpenFOAM
            # announcing that FPE trapping is ON — a normal start-up banner, not
            # a fault. Flagging it as an error made every healthy run open with
            # a scary "[solve] ERROR" line.
            on_line(f"[solve] ERROR: {line}")

    start = time.monotonic()
    rc, stdout, stderr, timed_out = _stream_subprocess(
        cmd, run_cwd=None, timeout_s=timeout_s, on_line=handle, heartbeat_s=30,
    )
    elapsed = time.monotonic() - start

    if timed_out:
        raise RuntimeError(
            f"{application} timed out after {timeout_s}s at iteration {iteration[0]}"
        )
    if rc != 0:
        tail = "\n".join((stdout + stderr)[-25:])
        raise RuntimeError(f"{application} failed (exit {rc}):\n{tail}")

    return {
        "iterations": iteration[0],
        "converged": converged[0],
        "residuals": dict(residuals),
        "wall_time_s": elapsed,
    }


def load_solution(
    case_dir: Path | str,
    of_config: OFConfig | None = None,
    timeout_s: int = 900,
    on_line: LogFn = _noop_log,
) -> Any:
    """Convert the latest time directory to VTU and read it with PyVista.

    Deliberately does NOT pass ``-no-fields`` (which the mesh viewer's own
    export does, since it only needs geometry) — the whole point here is to get
    U and p back.
    """
    import subprocess

    case_dir = Path(case_dir).resolve()
    cfg = of_config or OFConfig()
    out_name = "VTK_solution"
    out_dir = case_dir / out_name

    latest = latest_time_dir(case_dir)
    if latest is None:
        raise RuntimeError(
            f"No time directory written in {case_dir} — the solver produced no "
            "solution to compute an indicator from."
        )
    on_line(f"[indicator] reading solution at time {latest}")

    cmd = cfg._build_wsl_cmd(
        f"source {shlex.quote(cfg.env_script)} 2>/dev/null; "
        f"cd {cfg._quoted_linux_path(case_dir)} && "
        f"foamToVTK -latestTime -overwrite -name {out_name}"
    )
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout_s, encoding="utf-8",
                                errors="backslashreplace")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"foamToVTK timed out after {timeout_s}s") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"foamToVTK failed: {(result.stderr or result.stdout)[-500:]}"
        )

    matches = sorted(out_dir.glob("*/internal.vtu"))
    if not matches:
        raise RuntimeError(f"foamToVTK produced no internal.vtu under {out_dir}")

    import pyvista as pv

    # Highest-numbered subdirectory = latest time.
    def _t(p: Path) -> float:
        m = re.search(r"_(\d+)$", p.parent.name)
        return float(m.group(1)) if m else -1.0

    vtu = max(matches, key=_t)
    grid = pv.read(str(vtu))
    on_line(
        f"[indicator] loaded {grid.n_cells:,} cells, "
        f"fields: {', '.join(sorted(grid.cell_data.keys())) or '(none)'}"
    )
    return grid


def pressure_drop_qoi(grid: Any, case_dir: Path) -> float:
    """Engineering quantity of interest: inlet-to-outlet pressure drop.

    Area-weighted mean p is the textbook definition, but the internal.vtu
    foamToVTK writes holds only the internal field; patch data lands in
    separate files. Rather than parse those, take the mean p over the cells in
    the first and last 5% of the flow-direction extent — a robust proxy that
    moves with the same physics and needs no extra I/O. It is used only as a
    *relative* convergence measure between cycles, where a consistent proxy is
    exactly as good as the true value.
    """
    if "p" not in grid.cell_data:
        return float("nan")
    p = np.asarray(grid.cell_data["p"], dtype=float)
    centres = np.asarray(grid.cell_centers().points, dtype=float)
    # Flow direction = the axis with the largest extent.
    extents = centres.max(axis=0) - centres.min(axis=0)
    axis = int(np.argmax(extents))
    c = centres[:, axis]
    lo, hi = c.min(), c.max()
    band = (hi - lo) * 0.05
    up = p[c <= lo + band]
    down = p[c >= hi - band]
    if len(up) == 0 or len(down) == 0:
        return float("nan")
    return float(up.mean() - down.mean())


# ----------------------------------------------------------------------------
# The loop
# ----------------------------------------------------------------------------

class SolutionAdaptiveRefiner:
    """Drives solve -> indicate -> remesh cycles on a CAD geometry.

    The caller supplies a *remesh_fn* so this class stays independent of how
    the mesh actually gets built (GUI worker, subprocess, direct call):

        remesh_fn(size_field_file: Path, cycle: int) -> (case_dir, n_cells)

    It must regenerate the mesh from the ORIGINAL CAD with the given size field
    applied (``gmsh_wrapper.generate_volume_mesh(size_field_file=...)``) and
    return the OpenFOAM case directory holding the new ``constant/polyMesh``.
    """

    def __init__(
        self,
        params: AdaptiveParams | None = None,
        of_config: OFConfig | None = None,
        on_line: LogFn = _noop_log,
    ) -> None:
        self.params = params or AdaptiveParams()
        self.of_config = of_config or OFConfig()
        self.log = on_line
        # Recalibrated after every remesh from the actual cell count the mesher
        # produced, so the budget guard gets more accurate as the loop runs
        # instead of trusting a constant measured on a different geometry.
        self._cell_calibration = TET_COUNT_CALIBRATION_DEFAULT
        self._last_components: tuple | None = None

    # -- one cycle's analysis half ----------------------------------------
    def analyse(
        self,
        case_dir: Path,
        cycle: int,
        size_field_path: Path,
        bounds: tuple[float, float, float, float, float, float],
    ) -> tuple[CycleReport, dict[str, Any] | None]:
        """Solve, compute the indicator, and write the next size field.

        Returns (report, size_field_info). *size_field_info* is None when the
        indicator says no refinement is warranted.
        """
        p = self.params
        rep = CycleReport(cycle=cycle)
        from cfmesh_autogui.core.boundary_reader import count_cells
        rep.cells_before = count_cells(case_dir)

        # --- solve --------------------------------------------------------
        # Intermediate cycles run short (the indicator only needs the spatial
        # structure of grad(U)); the final cycle runs long so the reported
        # engineering quantity is trustworthy. See AdaptiveParams docs.
        iters = p.final_solver_iterations if cycle >= p.max_cycles else p.solver_iterations
        set_end_time(case_dir, iters)
        self.log(f"[cycle {cycle}] solving on {rep.cells_before:,} cells (endTime={iters})...")
        t0 = time.monotonic()
        solve = run_solver(
            case_dir, self.of_config, timeout_s=p.solver_timeout_s,
            on_line=self.log, n_cores=p.solve_cores,
        )
        rep.solve_time_s = time.monotonic() - t0
        rep.solver_iterations_run = solve["iterations"]
        rep.solver_converged = solve["converged"]
        rep.final_residuals = solve["residuals"]
        res_txt = ", ".join(f"{k}={v:.2e}" for k, v in sorted(solve["residuals"].items()))
        self.log(
            f"[cycle {cycle}] solve finished: {solve['iterations']} iterations, "
            f"{'converged' if solve['converged'] else 'iteration limit reached'}, "
            f"residuals {res_txt} ({rep.solve_time_s:.0f}s)"
        )

        # --- indicator ----------------------------------------------------
        t0 = time.monotonic()
        grid = load_solution(
            case_dir, self.of_config, timeout_s=p.foam_to_vtk_timeout_s,
            on_line=self.log,
        )
        ind = compute_indicator(grid)
        rep.indicator_min = ind["eta_min"]
        rep.indicator_max = ind["eta_max"]
        rep.indicator_mean = ind["eta_mean"]
        rep.indicator_p95 = ind["eta_p95"]
        rep.peak_location = ind["peak_location"]
        rep.qoi_pressure_drop = pressure_drop_qoi(grid, case_dir)
        px, py, pz = rep.peak_location
        self.log(
            f"[cycle {cycle}] indicator eta=h|grad U|/U_ref: "
            f"max={rep.indicator_max:.3f} p95={rep.indicator_p95:.3f} "
            f"mean={rep.indicator_mean:.4f}, peak at "
            f"({px:.4g}, {py:.4g}, {pz:.4g}); U_ref={ind['u_ref']:.4g} m/s"
        )

        min_h = p.min_cell_size
        if min_h is None:
            extent = max(bounds[1] - bounds[0], bounds[3] - bounds[2],
                         bounds[5] - bounds[4])
            min_h = extent / 2000.0

        # Budget enforcement happens against the LATTICE, not the per-cell wish
        # list: build the field, sample it back, and if the real prediction
        # busts the cap, relax and rebuild. The per-cell estimate inside
        # target_size_field ignores the smearing the lattice introduces and
        # under-predicts badly (15.6x on the venturi's first cycle).
        #
        # The knob is a uniform SCALE on the target size, not the refine
        # quantile. Raising the quantile was tried first and is close to
        # useless: measured on the venturi, going 0.85 -> 0.9953 dropped the
        # prediction only 1.82M -> 1.23M and never reached a 700k budget,
        # because even 205 marked cells (0.27% of the mesh) smear their tiny
        # target size across whole lattice cells — dropping marginal cells
        # barely shrinks the refined VOLUME. Scaling the size field does work,
        # and analytically: predicted cell count goes as h^-3, so
        # s = (predicted/budget)^(1/3) lands on the budget in essentially one
        # step. It also preserves WHERE refinement goes (the indicator's
        # spatial distribution is untouched) and relaxes only HOW MUCH, which
        # is the right trade to make when a budget binds.
        tgt = target_size_field(
            ind,
            refine_quantile=p.refine_quantile,
            max_refine_ratio=p.max_refine_ratio,
            min_cell_size=min_h,
            max_cells=None,
        )
        sf = None
        scale = 1.0
        if tgt["n_cells_marked"] > 0:
            h_base = tgt["h_target"]
            h_orig = ind["h"]
            # Only the cells actually being REFINED go into the lattice, with a
            # coarse baseline everywhere else.
            #
            # Scattering every cell (the obvious thing, and what this did at
            # first) poisons the whole field: the initial mesh already contains
            # 0.77mm cells against a 7.5mm lattice spacing, purely from
            # geometry sizing near walls, so almost every lattice node inherited
            # a sub-millimetre size and GMSH refined the entire domain. Measured
            # on the venturi: the straight section ballooned 9.58x against the
            # throat's 12.90x — refinement barely targeted at all, and the
            # budget loop could not converge because relaxing the scale never
            # touched that inherited floor.
            #
            # The unrefined bulk does not need to be in this field: GMSH
            # min-combines it with the geometry-based sizing, which reproduces
            # the near-wall refinement on its own.
            refined = h_base < h_orig * 0.999
            baseline = float(np.max(h_orig))
            for attempt in range(4):
                h_try = np.minimum(h_base * scale, h_orig)
                still = h_try < h_orig * 0.999
                if not np.any(still):
                    still = refined  # scale grew so far nothing is refined
                sf = write_structured_size_field(
                    size_field_path, ind["centres"][still], h_try[still], bounds,
                    resolution=p.grid_resolution, growth_rate=p.grid_growth_rate,
                    baseline=baseline,
                )
                predicted = predict_cells_from_lattice(
                    sf["lattice"], sf["origin"], sf["spacing"],
                    ind["centres"], ind["volumes"],
                    calibration=self._cell_calibration,
                    h_current=h_orig,
                )
                sf["predicted_cells"] = predicted
                # Keep the two halves separately so recalibration can solve for
                # the calibration that would have been right:
                #   actual = cal * lat_sum + geom_sum
                lat_sum, geom_sum = _prediction_components(
                    sf["lattice"], sf["origin"], sf["spacing"],
                    ind["centres"], ind["volumes"], h_orig,
                )
                sf["lat_sum"], sf["geom_sum"] = lat_sum, geom_sum
                tgt["h_target"] = h_try
                if predicted <= p.max_cells or attempt == 3:
                    if predicted > p.max_cells:
                        self.log(
                            f"[cycle {cycle}] WARNING: still predicting "
                            f"{predicted:,.0f} cells (> {p.max_cells:,}) after "
                            "4 relaxations — proceeding anyway."
                        )
                    break
                # 1.02 leaves a little headroom so we land just under, not on,
                # the cap after the next rebuild's re-smearing.
                scale *= (predicted / p.max_cells) ** (1.0 / 3.0) * 1.02
                self.log(
                    f"[cycle {cycle}] lattice predicts {predicted:,.0f} cells "
                    f"(> budget {p.max_cells:,}) — relaxing target size by "
                    f"{scale:.2f}x and rebuilding the size field"
                )

        rep.indicator_threshold = tgt["eta_threshold"]
        rep.n_cells_marked = tgt["n_cells_marked"]
        rep.target_size_min = float(np.min(tgt["h_target"]))
        rep.target_size_max = float(np.max(tgt["h_target"]))
        rep.indicator_time_s = time.monotonic() - t0

        if rep.n_cells_marked == 0 or sf is None:
            rep.note = "indicator flagged no cells — nothing left to refine"
            self.log(f"[cycle {cycle}] {rep.note}")
            return rep, None

        if scale > 1.0:
            self.log(
                f"[cycle {cycle}] cell budget {p.max_cells:,} bound the refinement "
                f"— target cell size relaxed by {scale:.2f}x; the mesh is coarser "
                "at the throat than the indicator asked for."
            )

        self.log(
            f"[cycle {cycle}] {rep.n_cells_marked:,} of {rep.cells_before:,} cells "
            f"marked (eta > {rep.indicator_threshold:.3f}); target size "
            f"{rep.target_size_min:.5g}..{rep.target_size_max:.5g}"
        )
        self.log(
            f"[cycle {cycle}] size field written: {sf['shape'][0]}x{sf['shape'][1]}"
            f"x{sf['shape'][2]} lattice, size {sf['min_size']:.5g}..{sf['max_size']:.5g}"
            f"; lattice predicts ~{sf['predicted_cells']:,.0f} cells"
        )
        return rep, sf

    # -- full loop ---------------------------------------------------------
    def run(
        self,
        initial_case_dir: Path | str,
        bounds: tuple[float, float, float, float, float, float],
        remesh_fn: Callable[[Path, int], tuple[Path, int]],
        work_dir: Path | str | None = None,
    ) -> AdaptiveResult:
        p = self.params
        result = AdaptiveResult()
        case_dir = Path(initial_case_dir).resolve()
        work = Path(work_dir) if work_dir else case_dir.parent / "amr_work"
        work.mkdir(parents=True, exist_ok=True)

        from cfmesh_autogui.core.boundary_reader import count_cells
        result.initial_cells = count_cells(case_dir)
        result.final_cells = result.initial_cells
        loop_start = time.monotonic()
        prev_qoi: float | None = None

        try:
            for cycle in range(1, p.max_cycles + 1):
                cyc_start = time.monotonic()
                self.log(f"[adaptive] === cycle {cycle} of {p.max_cycles} ===")
                sf_path = work / f"size_field_cycle{cycle}.txt"

                rep, sf = self.analyse(case_dir, cycle, sf_path, bounds)
                self._last_components = (
                    (sf.get("lat_sum"), sf.get("geom_sum")) if sf else None
                )

                # QoI convergence: has the answer stopped moving?
                if prev_qoi is not None and np.isfinite(rep.qoi_pressure_drop) \
                        and abs(prev_qoi) > 1e-12:
                    rep.qoi_delta_rel = float(
                        (rep.qoi_pressure_drop - prev_qoi) / abs(prev_qoi)
                    )
                    self.log(
                        f"[cycle {cycle}] pressure drop {rep.qoi_pressure_drop:.5g} "
                        f"(was {prev_qoi:.5g}, {rep.qoi_delta_rel * 100:+.2f}%)"
                    )
                prev_qoi = rep.qoi_pressure_drop

                if rep.qoi_delta_rel is not None and \
                        abs(rep.qoi_delta_rel) < p.qoi_tolerance:
                    rep.cells_after = rep.cells_before
                    rep.total_time_s = time.monotonic() - cyc_start
                    result.cycles.append(rep)
                    result.converged_on_qoi = True
                    result.stop_reason = (
                        f"pressure drop changed only "
                        f"{rep.qoi_delta_rel * 100:+.2f}% (< "
                        f"{p.qoi_tolerance * 100:.0f}% tolerance) — the answer no "
                        f"longer depends on the mesh"
                    )
                    self.log(f"[adaptive] {result.stop_reason}")
                    break

                if sf is None:
                    rep.cells_after = rep.cells_before
                    rep.total_time_s = time.monotonic() - cyc_start
                    result.cycles.append(rep)
                    result.stop_reason = rep.note
                    break

                if cycle == p.max_cycles:
                    rep.cells_after = rep.cells_before
                    rep.total_time_s = time.monotonic() - cyc_start
                    result.cycles.append(rep)
                    result.stop_reason = f"reached max_cycles ({p.max_cycles})"
                    self.log(f"[adaptive] {result.stop_reason}")
                    break

                # --- remesh ---------------------------------------------
                self.log(f"[cycle {cycle}] remeshing from CAD with the new size field...")
                t0 = time.monotonic()
                case_dir, n_cells = remesh_fn(sf_path, cycle)
                case_dir = Path(case_dir).resolve()
                rep.remesh_time_s = time.monotonic() - t0
                rep.cells_after = n_cells
                rep.total_time_s = time.monotonic() - cyc_start
                result.cycles.append(rep)
                result.final_cells = n_cells
                self.log(
                    f"[cycle {cycle}] remesh done: {rep.cells_before:,} -> "
                    f"{n_cells:,} cells ({rep.remesh_time_s:.0f}s)"
                )

                # Recalibrate the cell-count model against what the mesher
                # actually did, so the next cycle's budget guard is honest.
                # Solve actual = cal * lat_sum + geom_sum for cal, i.e. attribute
                # the whole discrepancy to the lattice-governed part — the only
                # part a calibration legitimately applies to.
                if self._last_components and self._last_components[0]:
                    lat_sum, geom_sum = self._last_components
                    predicted_was = self._cell_calibration * lat_sum + geom_sum
                    observed = (n_cells - geom_sum) / lat_sum
                    if observed > 0.1:
                        self.log(
                            f"[cycle {cycle}] cell-count calibration "
                            f"{self._cell_calibration:.2f} -> {observed:.2f} "
                            f"(predicted {predicted_was:,.0f}, actual {n_cells:,})"
                        )
                        self._cell_calibration = observed
                    else:
                        self.log(
                            f"[cycle {cycle}] calibration left at "
                            f"{self._cell_calibration:.2f} — the geometry-governed "
                            f"estimate ({geom_sum:,.0f}) already exceeds the actual "
                            f"count ({n_cells:,}), so the discrepancy is not the "
                            "lattice's to absorb."
                        )

                if n_cells >= p.max_cells:
                    result.stop_reason = (
                        f"cell budget reached ({n_cells:,} >= {p.max_cells:,})"
                    )
                    self.log(f"[adaptive] {result.stop_reason}")
                    break
            else:
                result.stop_reason = f"reached max_cycles ({p.max_cycles})"

            result.success = True
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller/log
            result.errors.append(str(exc))
            logger.exception("Solution-adaptive refinement failed")
            self.log(f"[adaptive] FAILED: {exc}")
            result.stop_reason = f"error: {exc}"

        if result.cycles:
            result.final_cells = max(
                result.cycles[-1].cells_after or result.cycles[-1].cells_before,
                0,
            )
        result.total_time_s = time.monotonic() - loop_start
        return result
