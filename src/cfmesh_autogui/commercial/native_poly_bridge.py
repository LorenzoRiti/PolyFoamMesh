"""Native Poly bridge — EXPERIMENTAL native cut-cell → median dual (Fasi 1-3).

Pure-Python generation, no GMSH/cfMesh:

  1. core.native_mesher   — cut-cell "castellation" from the loaded trimesh
     surfaces (algoritmo nostro): background Cartesian grid, inside/outside/
     cut classification, clipped-triangle cut cells.  Output written to
     ``constant/polyMesh`` and preserved as ``constant/polyMesh_hex_native``.
  2. core.hex_poly_dual   — our median dual: 100% polyhedral mesh, written
     over ``constant/polyMesh`` (the deliverable).
  3. In-process quality metrics (checkMesh replica) for the log; the real
     gate is the WSL ``checkMesh`` the GUI launches afterwards.

WSL/OpenFOAM is only used downstream for validation, never for generation.
"""
from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from cfmesh_autogui.core.geometry import suggest_cell_sizes
from cfmesh_autogui.core.hex_poly_dual import HexPolyDualConverter
from cfmesh_autogui.core.native_mesher import NativeMesher
from cfmesh_autogui.core import foam_mesh_io as fio
from cfmesh_autogui.core.tet_poly_dual import (
    _cell_centres,
    _detect_defects,
    _face_geometry,
)

logger = logging.getLogger(__name__)


@dataclass
class NativePolyParams:
    detail_level: str = "medium"
    cell_size: float = 0.0  # 0 => auto from geometry bbox (suggest_cell_sizes)
    # Fase 4b (docs/poly_mesher_STATO.md 6.1): graded margin band, NOT
    # per-region refinement near the surface -- see the module note below.
    # 1.0 = uniform grid (unchanged default).
    margin_growth: float = 1.0
    flow: object | None = None  # commercial.bl_engine.FlowConditions, optional


@dataclass
class NativePolyResult:
    success: bool = False
    n_cells: int = 0
    n_hex_cells: int = 0
    n_cut_cells: int = 0
    max_skewness: float = 0.0
    max_non_ortho: float = 0.0
    defects: dict = field(default_factory=dict)
    message: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    case_dir: str = ""
    hex_dir: str = ""  # preserved castellation (polyMesh_hex_native)
    stage_times: dict = field(default_factory=dict)
    bl_recommendation: dict = field(default_factory=dict)  # Fase 5, sizing only


def _quality(points, faces, owner, neighbour):
    """checkMesh-replica in-process metrics (skewness, non-ortho, defects)."""
    n_int = int(len(neighbour))
    n_cells = int(owner.max()) + 1
    sf, cf = _face_geometry(points, faces)
    ctr, _vol = _cell_centres(sf, cf, owner, neighbour, n_int, n_cells)
    bad, counts = _detect_defects(
        points, faces, sf, cf, ctr, owner, neighbour, n_int, n_cells,
    )
    d = ctr[neighbour] - ctr[owner[:n_int]]
    dn = np.linalg.norm(d, axis=1)
    sn = np.linalg.norm(sf[:n_int], axis=1)
    cos = (d * sf[:n_int]).sum(axis=1) / np.maximum(dn * sn, 1e-300)
    no = np.rad2deg(np.arccos(np.clip(cos, -1.0, 1.0)))
    cpf = cf[:n_int] - ctr[owner[:n_int]]
    denom = (sf[:n_int] * d).sum(axis=1)
    scale = (sf[:n_int] * cpf).sum(axis=1) / np.where(
        np.abs(denom) > 0, denom, 1e-300)
    sv = cpf - scale[:, None] * d
    hat = sv / np.maximum(np.linalg.norm(sv, axis=1), 1e-300)[:, None]
    out = np.zeros(n_int, dtype=np.float64)
    sizes = np.fromiter(
        (len(faces[i]) for i in range(n_int)), dtype=np.int64, count=n_int)
    for k in np.unique(sizes):
        idx = np.flatnonzero(sizes == k)
        v = np.array([faces[i] for i in idx], dtype=np.int64)
        rel = points[v] - cf[idx][:, None, :]
        out[idx] = np.abs((rel * hat[idx][:, None, :]).sum(axis=2)).max(axis=1)
    return {
        "max_skewness": float(np.nanmax(out)) if n_int else 0.0,
        "max_non_ortho": float(np.nanmax(no)) if n_int else 0.0,
        "defects": counts,
    }


def run_native_poly(
    meshes: list,
    case_dir: Path | str,
    params: NativePolyParams | None = None,
    progress: Callable[[int, str, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> NativePolyResult:
    """Run the experimental native cut-cell → median-dual pipeline.

    ``meshes`` is the list of trimesh surfaces already loaded in the GUI
    (kept read-only).  Generation is pure Python; no WSL is invoked here.
    """
    t_start = time.monotonic()
    params = params or NativePolyParams()
    res = NativePolyResult()
    case_dir = Path(case_dir).resolve()

    def prog(pct: int, stage: str, msg: str) -> None:
        if progress is not None:
            try:
                progress(pct, stage, msg)
            except Exception:  # pragma: no cover
                pass

    try:
        if not meshes:
            raise RuntimeError("no geometry meshes loaded")
        if cancel and cancel():
            res.message = "cancelled before start"
            return res

        detail = params.detail_level if params.detail_level in (
            "coarse", "medium", "fine", "very_fine") else "medium"
        s_max, s_min = suggest_cell_sizes(meshes, detail=detail)
        cell = float(params.cell_size) if params.cell_size > 0 else float(s_max)
        res.warnings.append(
            f"background cell size {cell:.6g} (s_max for '{detail}'; "
            f"s_min would be {s_min:.6g} — LOCAL refinement near a small "
            f"internal feature not supported, needs an octree; "
            + (f"margin_growth={params.margin_growth:g} grades the far-field "
               f"pad band"
               if params.margin_growth > 1.0 else
               "margin_growth=1.0, far-field pad band is uniform"))

        prog(10, "castellation", f"cut-cell nativo, cell={cell:.6g} ...")
        # clean_cells=True: drop cut cells whose boundary is non-manifold
        # (the concave/overlapping-CAD cases) — the median dual REQUIRES
        # clean polyhedra, and the validation below rejects open cells.
        native = NativeMesher(
            meshes, cell_size=cell, clean_cells=True,
            margin_growth=params.margin_growth, flow=params.flow,
        )
        nres = native.run(case_dir)
        if not nres.success:
            raise RuntimeError(
                "native castellation failed: " + "; ".join(nres.errors))
        res.stage_times["native"] = nres.stage_times.get("total", 0.0)
        res.n_hex_cells = int(nres.n_hex_cells)
        res.n_cut_cells = int(nres.n_cut_cells)
        res.bl_recommendation = dict(nres.bl_recommendation)
        res.warnings.extend(nres.warnings)
        prog(40, "castellation",
             f"{nres.n_cells:,} cells ({nres.n_hex_cells:,} hex + "
             f"{nres.n_cut_cells:,} cut)")

        # preserve the castellation; feed the dual from a scratch copy
        poly = case_dir / "constant" / "polyMesh"
        hex_dir = case_dir / "constant" / "polyMesh_hex_native"
        if hex_dir.exists():
            shutil.rmtree(hex_dir)
        shutil.copytree(poly, hex_dir)
        res.hex_dir = str(hex_dir)
        shutil.rmtree(poly)
        shutil.copytree(hex_dir, poly)

        prog(60, "dual", "dual mediano -> 100% poly ...")
        conv = HexPolyDualConverter(case_dir, out_rel="polyMesh")
        dres = conv.run()
        if not dres.success:
            # The dual converter reads FROM constant/polyMesh (the
            # castellation copy) and only overwrites it on success, so a
            # failure here leaves the RAW, un-dualized cartesian cut-cell
            # mesh sitting in constant/polyMesh looking like a finished
            # result -- any later viewer refresh or mesher run against this
            # case_dir would pick it up as if it were valid output. The
            # castellation is already safe in polyMesh_hex_native, so drop
            # the leftover here rather than leave a broken mesh on disk.
            if poly.exists():
                shutil.rmtree(poly)
            raise RuntimeError(
                "native dual failed (serve lo snapping per superfici "
                "parallele alla griglia): " + "; ".join(dres.errors))
        res.stage_times["dual"] = dres.stage_times.get("total", 0.0)

        prog(85, "quality", "metriche in-process (replica checkMesh)...")
        pts, faces, own, nb, _patches = fio.read_polymesh(poly)
        res.n_cells = int(own.max()) + 1
        q = _quality(pts, faces, own, nb)
        res.max_skewness = q["max_skewness"]
        res.max_non_ortho = q["max_non_ortho"]
        res.defects = dict(q["defects"])
        res.case_dir = str(case_dir)
        res.stage_times["total"] = round(time.monotonic() - t_start, 2)
        res.success = True
        res.message = (
            f"{res.n_cells:,} celle 100% poly "
            f"(skew={res.max_skewness:.2f}, nonOrtho={res.max_non_ortho:.1f})")
        prog(100, "done", res.message)
    except Exception as exc:  # noqa: BLE001 — reported to the caller
        logger.exception("Native Poly failed")
        err = str(exc) or exc.__class__.__name__
        res.errors.append(err)
        res.message = "; ".join(res.errors)
    res.stage_times.setdefault("total", round(time.monotonic() - t_start, 2))
    return res
