#!/usr/bin/env python3
"""Benchmark the meshing pipelines on each geometry in benchmarks/geometries/.

Two pipelines share the same geometry corpus and result format:

hex (default, unchanged behaviour):
  1. Load, compute bbox, suggest cell sizes
  2. Create temp case directory under C:/cfmesh_bench/ (no spaces)
  3. Write surface.stl, meshDict, controlDict, fvSchemes, fvSolution
  4. Run cartesianMesh via WSL2 (180s timeout)
  5. Run checkMesh via WSL2
  6. Parse quality metrics
  7. Record result

poly (the app's differentiating GMSH tet -> dual poly -> BL path):
  1. Same geometry prep as hex
  2. GMSH tetrahedral volume mesh (in-process entry point, subprocess driver)
  3. gmshToFoam via WSL2
  4. tet -> poly barycentric dual (core/tet_poly_dual.py, in-process)
  5. prismatic boundary layers (core/bl_poly.py, in-process)
  6. checkMesh via WSL2
  7. Record per-stage times, tet/poly counts, BL stats and the in-process
     defect count from the dual converter's checkMesh replica

Select with --pipeline {hex,poly,all} (default hex).

Output: benchmarks/results/<ISO_TIMESTAMP>.json (hex) and
        benchmarks/results/<ISO_TIMESTAMP>_poly.json (poly).
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import time
import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_SRC = _PROJECT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cfmesh_autogui.core.geometry import (
    compute_bbox_dim,
    load_stl,
    suggest_cell_sizes,
    validate_cell_sizes,
)
from cfmesh_autogui.core.meshdict_gen import write_meshdict
from cfmesh_autogui.core.openfoam_runner import parse_checkmesh_output, generate_fms
from cfmesh_autogui.core.stl_writer import export_surface_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("run_benchmarks")

OF_BASHRC = "/usr/lib/openfoam/openfoam2512/etc/bashrc"
WORK_ROOT = Path("C:/cfmesh_bench")

NEGATIVE_CASES = {"non_watertight"}
GEOMETRIES_DIR = _HERE / "geometries"
RESULTS_DIR = _HERE / "results"
TIMEOUT_S = 180

# Poly pipeline baseline: sibling of BASELINE.json (compare.py indexes results
# by geometry, so hex and poly entries cannot share one file without colliding).
POLY_BASELINE = RESULTS_DIR / "BASELINE_POLY.json"

# A poly case regresses only when its defect count exceeds the recorded
# baseline allowance by more than this fraction (min 1). The dual is
# deterministic for a given tet mesh, so the only noise source is GMSH
# version drift; 5% absorbs that without hiding a real regression.
DEFECT_ALLOWANCE_FRACTION = 0.05

# Boundary-layer parameters for the poly pipeline (app convention:
# firstLayerThickness = 0.005 * max_cell, see commercial/mesh_engine.py).
POLY_BL_N_LAYERS = 5
POLY_BL_GROWTH_RATE = 1.2
POLY_BL_FIRST_HEIGHT_FRACTION = 0.005

_KEEP_CASE = False


def _wsl_alive() -> bool:
    try:
        r = subprocess.run(
            ["wsl.exe", "-e", "bash", "-lc", "echo alive"],
            capture_output=True, text=True, timeout=10,
        )
        return "alive" in r.stdout.strip()
    except Exception:
        return False


def _wsl_of_run(cmd: str, timeout: int = TIMEOUT_S) -> tuple[int, str]:
    full_cmd = f". {OF_BASHRC} && {cmd}"
    try:
        r = subprocess.run(
            ["wsl.exe", "-e", "bash", "-lc", full_cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout + "\n" + r.stderr
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except FileNotFoundError:
        return -2, "WSL_NOT_FOUND"


def _to_wsl_path(win_path: Path) -> str:
    """Convert Windows absolute path to /mnt/ path for WSL."""
    win = win_path.resolve()
    drive = win.drive[0].lower()
    rel = str(win).split(":", 1)[1].replace("\\", "/")
    return f"/mnt/{drive}{rel}"


def _run_cartesian_mesh(case_dir: Path) -> tuple[int, str, float]:
    linux = _to_wsl_path(case_dir)
    t0 = time.perf_counter()
    rc, out = _wsl_of_run(f"cd {linux} && cartesianMesh 2>&1")
    elapsed = time.perf_counter() - t0
    return rc, out, elapsed


def _run_check_mesh(case_dir: Path) -> tuple[int, str, float]:
    linux = _to_wsl_path(case_dir)
    t0 = time.perf_counter()
    rc, out = _wsl_of_run(f"cd {linux} && checkMesh 2>&1")
    elapsed = time.perf_counter() - t0
    return rc, out, elapsed


def _vtk_quality_check(case_dir: Path, main_logger) -> dict | None:
    """Run foamToVTK then compute quality via pyvista.

    Returns dict with vtk quality metrics or None on failure.
    This is a fast local check (no WSL needed after foamToVTK completes).
    """
    try:
        import pyvista  # noqa: F401 — import-guard: availability probe, not a usage
    except ImportError:
        return None

    linux = _to_wsl_path(case_dir)
    vtk_ok, _ = _wsl_of_run(f"cd {linux} && foamToVTK -constant 2>&1", timeout=60)
    if vtk_ok != 0:
        return None

    try:
        from benchmarks.vtk_quality import vtk_quality_metrics
        report = vtk_quality_metrics(case_dir)
        if report is None:
            return None
        return {
            "vtk_cells": report.cells,
            "vtk_skew_max": round(report.skew_max, 4),
            "vtk_aspect_max": round(report.aspect_ratio_max, 4),
            "vtk_jacobian_min": round(report.scaled_jacobian_min, 4),
            "vtk_min_angle": round(report.min_angle_min, 2),
            "vtk_max_angle": round(report.max_angle_max, 2),
        }
    except Exception:
        return None


def _make_case_dir(parent: Path, geometry_name: str) -> Path:
    case = parent / f"bench_{geometry_name}"
    if case.exists():
        shutil.rmtree(case)
    case.mkdir(parents=True, exist_ok=True)
    (case / "system").mkdir(parents=True, exist_ok=True)
    (case / "constant").mkdir(parents=True, exist_ok=True)
    (case / "constant" / "polyMesh").mkdir(parents=True, exist_ok=True)
    return case


def _write_ctrl_dict(case_dir: Path) -> Path:
    path = case_dir / "system" / "controlDict"
    path.write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
        "application cartesianMesh;\n"
        "startFrom startTime; startTime 0;\n"
        "stopAt endTime; endTime 1000;\n"
        "deltaT 1;\n"
        "writeControl timeStep; writeInterval 1;\n"
        "purgeWrite 0; writeFormat ascii; writePrecision 6;\n"
        "writeCompression off; timeFormat general; timePrecision 6;\n"
        "runTimeModifiable true;\n",
        encoding="ascii",
    )
    return path


def _write_solver_dicts(case_dir: Path) -> None:
    system = case_dir / "system"
    (system / "fvSchemes").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
        "ddtSchemes { default steadyState; }\n"
        "gradSchemes { default Gauss linear; }\n"
        "divSchemes { default none; }\n"
        "laplacianSchemes { default Gauss linear corrected; }\n"
        "interpolationSchemes { default linear; }\n"
        "snGradSchemes { default corrected; }\n",
        encoding="ascii",
    )
    (system / "fvSolution").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
        "solvers {}\n",
        encoding="ascii",
    )


def _make_result(
    geometry: str,
    success: bool,
    wall_time_s: float,
    cell_count: int = 0,
    max_non_ortho: float = 0.0,
    avg_non_ortho: float = 0.0,
    max_skewness: float = 0.0,
    max_aspect_ratio: float = 0.0,
    neg_cells: int = 0,
    min_volume: float = 0.0,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    is_negative_case: bool = False,
    vtk_quality: dict | None = None,
    pipeline: str = "hex",
) -> dict:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    w = warnings or []
    e = errors or []
    if is_negative_case:
        return {
            "geometry": geometry,
            "pipeline": pipeline,
            "timestamp": now,
            "success": success,
            "negative_case": True,
            "wall_time_s": round(wall_time_s, 2),
            "cell_count": 0,
            "hard_gates": {"correctly_rejected": success},
            "failed_gates": [] if success else ["correctly_rejected"],
            "warnings": w,
            "errors": e,
        }
    gates = {
        "mesh_ok": success,
        "neg_cells_zero": neg_cells == 0,
        "max_non_ortho_below_70": max_non_ortho < 70,
        "max_skewness_below_4": max_skewness < 4,
        "no_fatal_errors": len(e) == 0,
        "cells_produced": cell_count > 0,
    }
    overall = all(gates.values())
    return {
        "geometry": geometry,
        "pipeline": pipeline,
        "timestamp": now,
        "success": overall,
        "wall_time_s": round(wall_time_s, 2),
        "cell_count": cell_count,
        "max_non_ortho": round(max_non_ortho, 2),
        "avg_non_ortho": round(avg_non_ortho, 2),
        "max_skewness": round(max_skewness, 2),
        "max_aspect_ratio": round(max_aspect_ratio, 2),
        "neg_cells": neg_cells,
        "min_volume": min_volume,
        "hard_gates": gates,
        "failed_gates": [k for k, v in gates.items() if not v],
        "warnings": w,
        "errors": e,
        "vtk_quality": vtk_quality,
    }


def _prepare_geometry(
    stl_path: Path, pipeline: str = "hex",
) -> tuple[dict | None, dict | None]:
    """Shared geometry prep for both pipelines.

    Returns ``(early_result, context)``. When ``early_result`` is not None the
    caller must return it immediately (load/sizing failure or a negative case).
    Otherwise ``context`` carries the prepared data both pipelines need.
    """
    name = stl_path.stem

    try:
        meshes = load_stl(stl_path)
    except Exception as exc:
        logger.error("  FAIL load_stl: %s", exc)
        return _make_result(
            name, success=False, wall_time_s=0.0, errors=[str(exc)],
            pipeline=pipeline,
        ), None

    watertight = all(m.is_watertight for m in meshes)
    if name in NEGATIVE_CASES:
        ok = not watertight
        return _make_result(
            name, success=ok, wall_time_s=0.0,
            warnings=[f"Watertight check: {watertight}"],
            errors=[] if ok else ["Expected rejection as non-watertight"],
            is_negative_case=True,
            pipeline=pipeline,
        ), None
    if not watertight:
        return _make_result(
            name, success=False, wall_time_s=0.0,
            errors=["Non-watertight geometry"], pipeline=pipeline,
        ), None

    try:
        bbox_dim = compute_bbox_dim(meshes)
    except Exception as exc:
        logger.error("  FAIL compute_bbox_dim: %s", exc)
        return _make_result(
            name, success=False, wall_time_s=0.0, errors=[str(exc)],
            pipeline=pipeline,
        ), None

    try:
        max_cell, min_cell = suggest_cell_sizes(meshes, detail="medium")
        max_cell, min_cell, size_warnings = validate_cell_sizes(bbox_dim, max_cell, min_cell)
    except Exception as exc:
        logger.error("  FAIL suggest_cell_sizes: %s", exc)
        return _make_result(
            name, success=False, wall_time_s=0.0, errors=[str(exc)],
            pipeline=pipeline,
        ), None

    logger.info("  bbox_dim=%.4f  max_cell=%.6f  min_cell=%.6f", bbox_dim, max_cell, min_cell)
    return None, {
        "name": name,
        "meshes": meshes,
        "bbox_dim": bbox_dim,
        "max_cell": max_cell,
        "min_cell": min_cell,
        "size_warnings": size_warnings,
    }


def _benchmark_one(stl_path: Path, keep_case: bool = False) -> dict:
    name = stl_path.stem
    logger.info("=" * 60)
    logger.info("Benchmarking: %s", name)
    logger.info("=" * 60)

    early, ctx = _prepare_geometry(stl_path, pipeline="hex")
    if early is not None:
        return early
    assert ctx is not None  # guaranteed when early is None
    meshes = ctx["meshes"]
    bbox_dim = ctx["bbox_dim"]
    max_cell = ctx["max_cell"]
    min_cell = ctx["min_cell"]
    size_warnings = ctx["size_warnings"]

    tmp_root = WORK_ROOT / f"batch_{int(time.time() * 1000)}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    case_dir = _make_case_dir(tmp_root, name)
    logger.info("  case_dir: %s", case_dir)

    errors: list[str] = []
    warnings: list[str] = list(size_warnings)

    try:
        stl_out = export_surface_file(meshes, case_dir)
        logger.info("  surface written: %s", stl_out)

        fms = generate_fms(case_dir, angle=60.0)
        surface_file = "constant/triSurface/surface.fms" if fms else "constant/triSurface/surface.stl"
        logger.info("  surface file: %s (FMS=%s)", surface_file, bool(fms))

        # Compute physics-based BL parameters via BLEngine
        try:
            from cfmesh_autogui.commercial.bl_engine import BLEngine, FlowConditions
            bl_engine = BLEngine()
            flow = FlowConditions.from_velocity(
                reference_velocity=1.0,
                reference_length=bbox_dim,
                turbulence_model="kOmegaSST",
            )
            blp = bl_engine.calculate_from_flow(flow, growth_rate=1.2)
            n_layers = blp.n_layers
            thickness_ratio = blp.growth_rate
        except Exception:
            n_layers = 5
            thickness_ratio = 1.2

        write_meshdict(
            case_dir,
            max_cell_size=max_cell,
            min_cell_size=min_cell,
            surface_file=surface_file,
            bl_params={
                "nLayers": n_layers,
                "thicknessRatio": thickness_ratio,
                "wallPatches": ["wall"],
            },
        )
        logger.info("  meshDict written")

        _write_ctrl_dict(case_dir)
        _write_solver_dicts(case_dir)

        logger.info("  --- cartesianMesh ---")
        mesh_rc, mesh_out, mesh_time = _run_cartesian_mesh(case_dir)
        logger.info("  cartesianMesh rc=%d  wall=%.1fs", mesh_rc, mesh_time)

        if mesh_rc == -2:
            return _make_result(name, success=False, wall_time_s=mesh_time,
                                errors=["WSL not found"])
        if mesh_rc == -1:
            return _make_result(name, success=False, wall_time_s=mesh_time,
                                errors=["cartesianMesh timed out"])
        if mesh_rc == 15:
            points_file = case_dir / "constant" / "polyMesh" / "points"
            if not points_file.exists():
                return _make_result(name, success=False, wall_time_s=mesh_time,
                                    errors=["cartesianMesh exit 15, no polyMesh/points"])
            warnings.append("cartesianMesh exit 15 (unconnected regions)")
        elif mesh_rc != 0:
            log_path = case_dir / "log.meshing"
            if log_path.exists():
                log_lines = log_path.read_text().splitlines()[-20:]
                errors.append(f"cartesianMesh exit {mesh_rc}. Last log lines:\n" + "\n".join(log_lines))
            else:
                errors.append(f"cartesianMesh exit code {mesh_rc}")
            return _make_result(name, success=False, wall_time_s=mesh_time,
                                errors=errors)

        logger.info("  --- checkMesh ---")
        cm_rc, cm_out, cm_time = _run_check_mesh(case_dir)
        logger.info("  checkMesh rc=%d  wall=%.1fs", cm_rc, cm_time)
        total_time = mesh_time + cm_time

        if cm_rc == -1:
            return _make_result(name, success=False, wall_time_s=total_time,
                                errors=["checkMesh timed out"])
        if cm_rc == -2:
            return _make_result(name, success=False, wall_time_s=total_time,
                                errors=["WSL not found"])

        report = parse_checkmesh_output(cm_out)
        logger.info(
            "  cells=%d  nonOrtho=%.1f/%.1f  skew=%.2f  aspect=%.0f  neg=%d  vol=%.2e",
            report.cells, report.max_non_ortho, report.avg_non_ortho,
            report.max_skewness, report.max_aspect_ratio,
            report.neg_cells, report.min_volume,
        )

        # VTK quality check (best-effort, local via pyvista)
        vtk_data = _vtk_quality_check(case_dir, logger)
        if vtk_data:
            logger.info(
                "  VTK: skew=%.4f  aspect=%.2f  jacobian=%.4f  angles=[%.1f, %.1f]",
                vtk_data.get("vtk_skew_max", 0),
                vtk_data.get("vtk_aspect_max", 0),
                vtk_data.get("vtk_jacobian_min", 0),
                vtk_data.get("vtk_min_angle", 0),
                vtk_data.get("vtk_max_angle", 0),
            )
        else:
            logger.info("  VTK quality: skipped (pyvista/WSL not available)")

        return _make_result(
            geometry=name,
            success=report.passed,
            wall_time_s=total_time,
            cell_count=report.cells,
            max_non_ortho=report.max_non_ortho,
            avg_non_ortho=report.avg_non_ortho,
            max_skewness=report.max_skewness,
            max_aspect_ratio=report.max_aspect_ratio,
            neg_cells=report.neg_cells,
            min_volume=report.min_volume,
            warnings=warnings,
            vtk_quality=vtk_data,
        )

    except Exception as exc:
        logger.error("  UNEXPECTED ERROR: %s", exc)
        return _make_result(name, success=False, wall_time_s=0.0, errors=[str(exc)])
    finally:
        if not keep_case:
            shutil.rmtree(tmp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Poly pipeline: GMSH tet -> gmshToFoam -> dual poly -> BL -> checkMesh
# ---------------------------------------------------------------------------

def _defect_tolerance(allowance: int) -> int:
    """Tolerance above the recorded baseline defect count before a poly case
    is judged to have regressed. The dual is deterministic for a given tet
    mesh, so the only noise source is GMSH version drift; a small relative
    allowance absorbs that without hiding a real regression."""
    return max(1, int(DEFECT_ALLOWANCE_FRACTION * allowance))


def _load_poly_allowances() -> dict[str, int]:
    """Per-geometry defect allowances from the poly baseline, if present.

    The allowance is the baseline run's own ``defect_count`` (the in-process
    checkMesh replica count from the dual converter). A missing baseline means
    no allowance is recorded yet — the defect gate then does not bind.
    """
    if not POLY_BASELINE.exists():
        return {}
    try:
        data = json.loads(POLY_BASELINE.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Could not read %s — defect gate will not bind", POLY_BASELINE)
        return {}
    out: dict[str, int] = {}
    for entry in data:
        g = entry.get("geometry")
        dc = entry.get("defect_count")
        if g and isinstance(dc, int):
            out[g] = dc
    return out


def _make_poly_result(
    geometry: str,
    success: bool,
    wall_time_s: float,
    stage_times: dict | None = None,
    stages: dict | None = None,
    tet_count: int = 0,
    poly_count: int = 0,
    bl_prism_cells: int = 0,
    bl_thickness: float = 0.0,
    defect_count: int = 0,
    defect_breakdown: dict | None = None,
    defect_allowance: int | None = None,
    cell_count: int = 0,
    max_non_ortho: float = 0.0,
    avg_non_ortho: float = 0.0,
    max_skewness: float = 0.0,
    max_aspect_ratio: float = 0.0,
    neg_cells: int = 0,
    min_volume: float = 0.0,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict:
    """Build a poly-pipeline result dict.

    Gates: the same checkMesh thresholds as hex (non-ortho < 70, skew < 4,
    no negative cells, cells produced, no fatal errors) PLUS an explicit,
    recorded allowance for the dual's known concave-feature defect count
    (``defects_within_allowance``) and a requirement that the boundary layer
    was actually produced (``bl_produced``). A case regressing beyond its
    recorded baseline allowance fails.
    """
    now = datetime.datetime.now().isoformat(timespec="seconds")
    w = warnings or []
    e = errors or []
    reduction = (tet_count / poly_count) if poly_count else 0.0
    defects_ok = (
        defect_allowance is None
        or defect_count <= defect_allowance + _defect_tolerance(defect_allowance)
    )
    gates = {
        "mesh_ok": success,
        "neg_cells_zero": neg_cells == 0,
        "max_non_ortho_below_70": max_non_ortho < 70,
        "max_skewness_below_4": max_skewness < 4,
        "no_fatal_errors": len(e) == 0,
        "cells_produced": cell_count > 0,
        "defects_within_allowance": defects_ok,
        "bl_produced": bl_prism_cells > 0,
    }
    overall = all(gates.values())
    return {
        "geometry": geometry,
        "pipeline": "poly",
        "timestamp": now,
        "success": overall,
        "wall_time_s": round(wall_time_s, 2),
        "stage_times": stage_times or {},
        "stages": stages or {},
        "tet_count": tet_count,
        "poly_count": poly_count,
        "reduction_ratio": round(reduction, 3),
        "bl_prism_cells": bl_prism_cells,
        "bl_thickness": round(bl_thickness, 6),
        "defect_count": defect_count,
        "defect_breakdown": defect_breakdown or {},
        "defect_allowance": defect_allowance,
        "cell_count": cell_count,
        "max_non_ortho": round(max_non_ortho, 2),
        "avg_non_ortho": round(avg_non_ortho, 2),
        "max_skewness": round(max_skewness, 2),
        "max_aspect_ratio": round(max_aspect_ratio, 2),
        "neg_cells": neg_cells,
        "min_volume": min_volume,
        "hard_gates": gates,
        "failed_gates": [k for k, v in gates.items() if not v],
        "warnings": w,
        "errors": e,
    }


def _benchmark_poly_one(
    stl_path: Path,
    keep_case: bool = False,
    allowances: dict[str, int] | None = None,
) -> dict:
    """Run the GMSH tet -> gmshToFoam -> dual poly -> BL -> checkMesh pipeline.

    Each stage is individually timed and its success/failure recorded, so a
    regression can be attributed to a stage rather than to "poly is bad".
    """
    name = stl_path.stem
    logger.info("=" * 60)
    logger.info("Benchmarking (poly): %s", name)
    logger.info("=" * 60)

    early, ctx = _prepare_geometry(stl_path, pipeline="poly")
    if early is not None:
        return early
    assert ctx is not None  # guaranteed when early is None
    meshes = ctx["meshes"]
    max_cell = ctx["max_cell"]
    min_cell = ctx["min_cell"]
    size_warnings = ctx["size_warnings"]

    tmp_root = WORK_ROOT / f"batch_{int(time.time() * 1000)}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    case_dir = _make_case_dir(tmp_root, name)
    logger.info("  case_dir: %s", case_dir)

    errors: list[str] = []
    warnings: list[str] = list(size_warnings)
    stages: dict[str, bool] = {}
    stage_times: dict[str, float] = {}

    def _fail() -> dict:
        return _make_poly_result(
            name, success=False, wall_time_s=sum(stage_times.values()),
            stage_times=stage_times, stages=stages,
            warnings=warnings, errors=errors,
        )

    try:
        # --- stage 1: GMSH tetrahedral volume mesh --------------------------
        t0 = time.perf_counter()
        msh_path = case_dir / "mesh.msh"
        try:
            from cfmesh_autogui.core.gmsh_subprocess import run_gmsh_volume
            run_gmsh_volume(
                stl_path, msh_path, detail="medium",
                user_lc=max_cell, min_lc=min_cell,
                on_line=lambda m: logger.info("  [gmsh] %s", m),
                timeout_s=600,
                # Force single-threaded GMSH: HXT's multi-threaded meshing is
                # non-deterministic (measured: pipe 9956 vs 9976 tets, and a
                # BL scale flip that swung thin_gap skew 3.4 -> 19.3 between
                # identical runs). A regression benchmark must be reproducible.
                threads=1,
            )
            stages["gmsh_volume"] = True
        except Exception as exc:
            stages["gmsh_volume"] = False
            errors.append(f"gmsh_volume: {exc}")
        stage_times["gmsh_volume"] = round(time.perf_counter() - t0, 2)
        if not stages["gmsh_volume"]:
            logger.error("  GMSH volume meshing failed")
            return _fail()

        # --- stage 2: gmshToFoam via WSL ------------------------------------
        _write_ctrl_dict(case_dir)
        _write_solver_dicts(case_dir)
        t0 = time.perf_counter()
        linux = _to_wsl_path(case_dir)
        rc, out = _wsl_of_run(f"cd {linux} && gmshToFoam mesh.msh 2>&1", timeout=600)
        stage_times["gmsh_to_foam"] = round(time.perf_counter() - t0, 2)
        if rc == -1:
            stages["gmsh_to_foam"] = False
            errors.append("gmshToFoam timed out")
            return _fail()
        if rc == -2:
            stages["gmsh_to_foam"] = False
            errors.append("WSL not found")
            return _fail()
        if rc != 0:
            stages["gmsh_to_foam"] = False
            tail = "\n".join(out.splitlines()[-20:])
            errors.append(f"gmshToFoam exit {rc}. Last log lines:\n{tail}")
            return _fail()
        stages["gmsh_to_foam"] = True
        logger.info("  gmshToFoam OK")

        # --- stage 3: tet -> poly dual (in-process) -------------------------
        t0 = time.perf_counter()
        try:
            from cfmesh_autogui.core.tet_poly_dual import TetPolyDualConverter
            conv = TetPolyDualConverter(
                case_dir,
                log=lambda m: logger.info("  [dual] %s", m),
                # Production settings (see openfoam_runner.PolyDualWorker):
                # one polygonal boundary face per boundary vertex, so the BL
                # extrudes one prism stack per boundary face.
                collapse_smooth_edges=True,
                boundary_feature_angle=40.0,
                collapse_volume_tolerance=0.10,
            )
            dres = conv.run()
            stage_times["dual"] = round(time.perf_counter() - t0, 2)
            if not dres.success:
                stages["dual"] = False
                errors.append("dual: " + "; ".join(dres.errors))
                return _fail()
            stages["dual"] = True
        except Exception as exc:
            stages["dual"] = False
            errors.append(f"dual: {exc}")
            return _fail()

        tet_count = dres.n_tets_before
        poly_count = dres.n_cells_after
        defect_count = dres.residual_defects
        defect_breakdown = dict(dres.defect_breakdown)
        logger.info(
            "  dual: %d tets -> %d poly cells (ratio %.2f), defects=%d",
            tet_count, poly_count,
            (tet_count / poly_count) if poly_count else 0.0,
            defect_count,
        )

        # --- stage 4: prismatic boundary layers (in-process) ----------------
        bl_prism_cells = 0
        bl_thickness = 0.0
        t0 = time.perf_counter()
        try:
            from cfmesh_autogui.core.bl_poly import PolyBoundaryLayerEngine
            bres = PolyBoundaryLayerEngine(
                case_dir, log=lambda m: logger.info("  [bl] %s", m),
            ).run(
                n_layers=POLY_BL_N_LAYERS,
                first_height=POLY_BL_FIRST_HEIGHT_FRACTION * max_cell,
                growth_rate=POLY_BL_GROWTH_RATE,
                apply_to_all=True,
            )
            stage_times["bl"] = round(time.perf_counter() - t0, 2)
            if bres.success:
                stages["bl"] = True
                bl_prism_cells = bres.n_prism_cells
                bl_thickness = bres.total_thickness
                logger.info(
                    "  BL: %d prism cells, total thickness %.6g m",
                    bl_prism_cells, bl_thickness,
                )
            else:
                stages["bl"] = False
                warnings.append("BL failed: " + "; ".join(bres.errors))
        except Exception as exc:
            stages["bl"] = False
            warnings.append(f"BL raised: {exc}")

        # --- stage 5: checkMesh via WSL -------------------------------------
        t0 = time.perf_counter()
        cm_rc, cm_out, cm_time = _run_check_mesh(case_dir)
        stage_times["check_mesh"] = round(time.perf_counter() - t0, 2)
        logger.info("  checkMesh rc=%d  wall=%.1fs", cm_rc, cm_time)

        if cm_rc == -1:
            stages["check_mesh"] = False
            errors.append("checkMesh timed out")
            return _fail()
        if cm_rc == -2:
            stages["check_mesh"] = False
            errors.append("WSL not found")
            return _fail()
        stages["check_mesh"] = True

        report = parse_checkmesh_output(cm_out)
        logger.info(
            "  cells=%d  nonOrtho=%.1f/%.1f  skew=%.2f  aspect=%.0f  neg=%d  vol=%.2e",
            report.cells, report.max_non_ortho, report.avg_non_ortho,
            report.max_skewness, report.max_aspect_ratio,
            report.neg_cells, report.min_volume,
        )

        allowance = (allowances or {}).get(name)
        return _make_poly_result(
            geometry=name,
            success=report.passed,
            wall_time_s=sum(stage_times.values()),
            stage_times=stage_times,
            stages=stages,
            tet_count=tet_count,
            poly_count=poly_count,
            bl_prism_cells=bl_prism_cells,
            bl_thickness=bl_thickness,
            defect_count=defect_count,
            defect_breakdown=defect_breakdown,
            defect_allowance=allowance,
            cell_count=report.cells,
            max_non_ortho=report.max_non_ortho,
            avg_non_ortho=report.avg_non_ortho,
            max_skewness=report.max_skewness,
            max_aspect_ratio=report.max_aspect_ratio,
            neg_cells=report.neg_cells,
            min_volume=report.min_volume,
            warnings=warnings,
            errors=errors,
        )

    except Exception as exc:
        logger.error("  UNEXPECTED ERROR: %s", exc)
        return _make_poly_result(
            name, success=False, wall_time_s=sum(stage_times.values()),
            stage_times=stage_times, stages=stages,
            warnings=warnings, errors=[str(exc)],
        )
    finally:
        if not keep_case:
            shutil.rmtree(tmp_root, ignore_errors=True)


def _print_summary(results: list[dict], pipeline: str = "hex"):
    print()
    print("=" * 90)
    print(f"BENCHMARK SUMMARY ({pipeline.upper()} pipeline)")
    print("=" * 90)
    if pipeline == "poly":
        header = (
            f"{'Geometry':24s} {'Tet->Poly':>14s} {'Prism':>8s} {'Defects':>8s} "
            f"{'NonOrtho':>10s} {'Skew':>8s} {'Neg':>5s} {'Time(s)':>8s} {'Result':>20s}"
        )
    else:
        header = (
            f"{'Geometry':24s} {'Cells':>8s} {'Time(s)':>8s} {'NonOrtho':>10s} "
            f"{'Skew':>8s} {'Aspect':>8s} {'Neg':>5s} {'Result':>20s}"
        )
    print(header)
    print("-" * 90)
    for r in results:
        geom = r["geometry"]
        if pipeline == "poly":
            # The poly pipeline records checkMesh metrics even when the gates
            # fail (the mesh was produced, just not good enough) — show them.
            has_metrics = "max_non_ortho" in r and not r.get("negative_case")
        else:
            has_metrics = r["success"] and "max_non_ortho" in r and not r.get("negative_case")
        t = f"{r['wall_time_s']:.1f}" if r.get("wall_time_s", 0) > 0 else "—"
        no = f"{r['max_non_ortho']:.1f}" if has_metrics else "—"
        sk = f"{r['max_skewness']:.2f}" if has_metrics else "—"
        neg = str(r["neg_cells"]) if has_metrics else "—"
        if r.get("negative_case"):
            status = "PASS (rejected)" if r["success"] else "FAIL (not rejected)"
        else:
            status = "PASS" if r["success"] else "FAIL"
        if pipeline == "poly":
            if has_metrics:
                tp = f"{r.get('tet_count', 0)}->{r.get('poly_count', 0)}"
                pr = str(r.get("bl_prism_cells", 0))
                df = str(r.get("defect_count", 0))
            else:
                tp = pr = df = "—"
            print(
                f"{geom:24s} {tp:>14s} {pr:>8s} {df:>8s} {no:>10s} {sk:>8s} "
                f"{neg:>5s} {t:>8s} {status:>20s}"
            )
        else:
            cells = str(r.get("cell_count", 0)) if has_metrics else "—"
            ar = f"{r['max_aspect_ratio']:.0f}" if has_metrics else "—"
            print(
                f"{geom:24s} {cells:>8s} {t:>8s} {no:>10s} {sk:>8s} {ar:>8s} "
                f"{neg:>5s} {status:>20s}"
            )
    print("-" * 90)
    passed = sum(1 for r in results if r["success"])
    total = len(results)
    print(f"{'TOTAL':24s} {passed}/{total} passed")
    for r in results:
        if not r["success"] and r.get("errors"):
            print(f"  {r['geometry']}: {'; '.join(r['errors'][:3])}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run benchmark suite")
    parser.add_argument("--keep", action="store_true", help="Keep case dirs for inspection")
    parser.add_argument(
        "--pipeline", choices=["hex", "poly", "all"], default="hex",
        help="Which meshing pipeline to benchmark (default: hex)",
    )
    return parser


def main(args: list[str] | None = None) -> int:
    opts = _build_parser().parse_args(args)

    if not _wsl_alive():
        logger.warning("WSL not responsive. Attempting restart...")
        subprocess.run(["wsl.exe", "--shutdown"], capture_output=True, timeout=30)
        time.sleep(5)
        if not _wsl_alive():
            logger.error("WSL still not reachable.")
            return 1

    stl_files = sorted(GEOMETRIES_DIR.glob("*.stl"))
    if not stl_files:
        logger.error("No STL files found in %s", GEOMETRIES_DIR)
        return 1

    logger.info("Found %d geometries to benchmark", len(stl_files))
    for s in stl_files:
        logger.info("  %s", s.name)

    pipelines = ["hex", "poly"] if opts.pipeline == "all" else [opts.pipeline]
    allowances = _load_poly_allowances() if "poly" in pipelines else {}

    results_by_pipeline: dict[str, list[dict]] = {p: [] for p in pipelines}
    for stl_path in stl_files:
        for p in pipelines:
            if p == "hex":
                result = _benchmark_one(stl_path, keep_case=opts.keep)
            else:
                result = _benchmark_poly_one(
                    stl_path, keep_case=opts.keep, allowances=allowances,
                )
            results_by_pipeline[p].append(result)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    exit_code = 0
    for p in pipelines:
        results = results_by_pipeline[p]
        suffix = "" if p == "hex" else "_poly"
        out_path = RESULTS_DIR / f"{timestamp}{suffix}.json"
        out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        logger.info("Results written to %s", out_path)

        _print_summary(results, pipeline=p)
        if not all(r["success"] for r in results):
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
