#!/usr/bin/env python3
"""Benchmark cfMesh meshing pipeline on each geometry in benchmarks/geometries/.

For each STL:
  1. Load, compute bbox, suggest cell sizes
  2. Create temp case directory under C:/cfmesh_bench/ (no spaces)
  3. Write surface.stl, meshDict, controlDict, fvSchemes, fvSolution
  4. Run cartesianMesh via WSL2 (180s timeout)
  5. Run checkMesh via WSL2
  6. Parse quality metrics
  7. Record result

Output: benchmarks/results/<ISO_TIMESTAMP>.json
"""

from __future__ import annotations

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
) -> dict:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    w = warnings or []
    e = errors or []
    if is_negative_case:
        return {
            "geometry": geometry,
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
    }


def _benchmark_one(stl_path: Path, keep_case: bool = False) -> dict:
    name = stl_path.stem
    logger.info("=" * 60)
    logger.info("Benchmarking: %s", name)
    logger.info("=" * 60)

    try:
        meshes = load_stl(stl_path)
    except Exception as exc:
        logger.error("  FAIL load_stl: %s", exc)
        return _make_result(name, success=False, wall_time_s=0.0, errors=[str(exc)])

    watertight = all(m.is_watertight for m in meshes)
    if name in NEGATIVE_CASES:
        ok = not watertight
        return _make_result(
            name, success=ok, wall_time_s=0.0,
            warnings=[f"Watertight check: {watertight}"],
            errors=[] if ok else ["Expected rejection as non-watertight"],
            is_negative_case=True,
        )
    if not watertight:
        return _make_result(name, success=False, wall_time_s=0.0,
                            errors=["Non-watertight geometry"])

    try:
        bbox_dim = compute_bbox_dim(meshes)
    except Exception as exc:
        logger.error("  FAIL compute_bbox_dim: %s", exc)
        return _make_result(name, success=False, wall_time_s=0.0, errors=[str(exc)])

    try:
        max_cell, min_cell = suggest_cell_sizes(meshes, detail="medium")
        max_cell, min_cell, size_warnings = validate_cell_sizes(bbox_dim, max_cell, min_cell)
    except Exception as exc:
        logger.error("  FAIL suggest_cell_sizes: %s", exc)
        return _make_result(name, success=False, wall_time_s=0.0, errors=[str(exc)])

    logger.info("  bbox_dim=%.4f  max_cell=%.6f  min_cell=%.6f", bbox_dim, max_cell, min_cell)

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
        )

    except Exception as exc:
        logger.error("  UNEXPECTED ERROR: %s", exc)
        return _make_result(name, success=False, wall_time_s=0.0, errors=[str(exc)])
    finally:
        if not keep_case:
            shutil.rmtree(tmp_root, ignore_errors=True)


def _print_summary(results: list[dict]):
    print()
    print("=" * 90)
    print("BENCHMARK SUMMARY")
    print("=" * 90)
    header = f"{'Geometry':24s} {'Cells':>8s} {'Time(s)':>8s} {'NonOrtho':>10s} {'Skew':>8s} {'Aspect':>8s} {'Neg':>5s} {'Result':>20s}"
    print(header)
    print("-" * 90)
    for r in results:
        geom = r["geometry"]
        has_metrics = r["success"] and "max_non_ortho" in r and not r.get("negative_case")
        cells = str(r.get("cell_count", 0)) if has_metrics else "—"
        t = f"{r['wall_time_s']:.1f}" if r.get("wall_time_s", 0) > 0 else "—"
        no = f"{r['max_non_ortho']:.1f}" if has_metrics else "—"
        sk = f"{r['max_skewness']:.2f}" if has_metrics else "—"
        ar = f"{r['max_aspect_ratio']:.0f}" if has_metrics else "—"
        neg = str(r["neg_cells"]) if has_metrics else "—"
        if r.get("negative_case"):
            status = "PASS (rejected)" if r["success"] else "FAIL (not rejected)"
        else:
            status = "PASS" if r["success"] else "FAIL"
        print(f"{geom:24s} {cells:>8s} {t:>8s} {no:>10s} {sk:>8s} {ar:>8s} {neg:>5s} {status:>20s}")
    print("-" * 90)
    passed = sum(1 for r in results if r["success"])
    total = len(results)
    print(f"{'TOTAL':24s} {passed}/{total} passed")
    for r in results:
        if not r["success"] and r.get("errors"):
            print(f"  {r['geometry']}: {'; '.join(r['errors'][:3])}")


def main(args: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run benchmark suite")
    parser.add_argument("--keep", action="store_true", help="Keep case dirs for inspection")
    opts = parser.parse_args(args)

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

    results: list[dict] = []
    for stl_path in stl_files:
        result = _benchmark_one(stl_path, keep_case=opts.keep)
        results.append(result)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_path = RESULTS_DIR / f"{timestamp}.json"
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    logger.info("Results written to %s", out_path)

    _print_summary(results)

    return 0 if all(r["success"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
