"""Resource profiling benchmark — measures file sizes, read times,
and memory overhead of the optimization changes.

Generates a large mesh case via WSL2 (if available) and measures:
1. File size: ascii vs binary+compressed polyMesh files
2. Read time: count_cells / parse_boundary on each format
3. Memory: simulated _meshes vs _unscaled_meshes overhead

Usage:
    python tests/bench_resource_profile.py
"""

from __future__ import annotations

import io
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import trimesh
import numpy as np

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.boundary_reader import count_cells, count_points, count_faces, parse_boundary
from cfmesh_autogui.core.meshdict_gen import write_meshdict
from cfmesh_autogui.core.of_reader import read_of_text, of_list_count, of_label_list

WORK = Path("C:/cfmesh_bench/resource_profile")

# ── Helpers ────────────────────────────────────────────────────────────

def _wsl_ok(cfg: OFConfig) -> bool:
    try:
        return cfg.validate()
    except Exception:
        return False


def _run_wsl(cfg: OFConfig, bash: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cfg._build_wsl_cmd(bash),
        capture_output=True, text=True, timeout=timeout,
    )


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="ascii")


def _make_large_geometry(out_path: Path, n_faces: int = 50000) -> None:
    """Create a large-ish STL geometry by combining many cylinders."""
    meshes = []
    for i in range(8):
        r = 0.5 + 0.1 * i
        h = 2.0
        cyl = trimesh.creation.cylinder(radius=r, height=h, sections=max(24, n_faces // 64))
        offset = (i - 4) * 1.2
        cyl.vertices[:, 0] += offset
        meshes.append(cyl)
    combined = trimesh.util.concatenate(meshes)
    combined.export(str(out_path))
    print(f"  Geometry: {len(combined.faces)} faces, {len(combined.vertices)} vertices")
    return combined


def _setup_case(case_dir: Path, stl_path: Path, fmt: str, compression: str) -> None:
    """Set up a case with the given writeFormat and writeCompression."""
    shutil.rmtree(case_dir, ignore_errors=True)
    tri = case_dir / "constant" / "triSurface"
    sysd = case_dir / "system"
    tri.mkdir(parents=True)
    sysd.mkdir(parents=True)
    shutil.copy2(stl_path, tri / "surface.stl")
    _write_text(
        sysd / "controlDict",
        f"FoamFile {{ version 2.0; format ascii; class dictionary; object controlDict; }}\n"
        f"application cartesianMesh;\n"
        f"startFrom startTime; startTime 0; stopAt endTime; endTime 1000;\n"
        f"deltaT 1;\nwriteControl timeStep; writeInterval 1; purgeWrite 0;\n"
        f"writeFormat {fmt};\nwritePrecision 6; writeCompression {compression};\n"
        f"timeFormat general; timePrecision 6;\nrunTimeModifiable true;\n",
    )
    write_meshdict(case_dir, max_cell_size=0.15, min_cell_size=0.03,
                   surface_file="constant/triSurface/surface.stl",
                   patch_names=["wall"])


def _dir_size_mb(path: Path) -> float:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def _measure_read_speed(case_dir: Path, label: str) -> dict:
    """Measure how long count_cells / parse_boundary / count_points take."""
    t0 = time.perf_counter()
    cells = count_cells(case_dir)
    t_cells = time.perf_counter() - t0

    t0 = time.perf_counter()
    pts = count_points(case_dir)
    t_pts = time.perf_counter() - t0

    t0 = time.perf_counter()
    faces = count_faces(case_dir)
    t_faces = time.perf_counter() - t0

    t0 = time.perf_counter()
    boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
    patches = parse_boundary(boundary_path) if boundary_path.exists() else []
    t_boundary = time.perf_counter() - t0

    poly_dir = case_dir / "constant" / "polyMesh"
    # Measure individual file sizes
    sizes = {}
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        f = poly_dir / name
        if f.exists():
            sizes[name] = f.stat().st_size
            # Check if gzipped
            with open(f, "rb") as fh:
                magic = fh.read(2)
            sizes[f"{name}_gzip"] = magic == b"\x1f\x8b"

    return {
        "label": label,
        "cells": cells,
        "points": pts,
        "faces_count": faces,
        "time_cells_s": round(t_cells, 5),
        "time_pts_s": round(t_pts, 5),
        "time_faces_s": round(t_faces, 5),
        "time_boundary_s": round(t_boundary, 5),
        "file_sizes_bytes": sizes,
        "poly_dir_size_mb": round(_dir_size_mb(poly_dir), 2),
    }


def main():
    os.makedirs(WORK, exist_ok=True)
    cfg = OFConfig()
    if not _wsl_ok(cfg):
        print("SKIP: no WSL2/OpenFOAM")
        return

    # ── Step 1: Generate geometry ──────────────────────────────────────
    print("=== Generating large test geometry ===")
    stl_path = WORK / "large_geom.stl"
    _make_large_geometry(stl_path)

    # ── Step 2: ASCII case ─────────────────────────────────────────────
    ascii_dir = WORK / "ascii_case"
    print("\n=== Generating mesh with writeFormat=ascii, writeCompression=off ===")
    _setup_case(ascii_dir, stl_path, "ascii", "off")
    env_q = shlex.quote(cfg.env_script)
    linux_case = cfg._quoted_linux_path(ascii_dir)
    t0 = time.time()
    r = _run_wsl(cfg, f"source {env_q} 2>/dev/null; cd {linux_case} && cartesianMesh > log.mesh 2>&1", 600)
    t_ascii = time.time() - t0
    if r.returncode != 0:
        print(f"  FAILED: {(r.stderr or r.stdout)[-300:]}")
        return
    print(f"  cartesianMesh: {t_ascii:.1f}s, returncode={r.returncode}")
    ascii_metrics = _measure_read_speed(ascii_dir, "ascii_off")

    # ── Step 3: Binary+compression case ────────────────────────────────
    bin_dir = WORK / "binary_case"
    print("\n=== Generating mesh with writeFormat=binary, writeCompression=on ===")
    _setup_case(bin_dir, stl_path, "binary", "on")
    linux_case = cfg._quoted_linux_path(bin_dir)
    t0 = time.time()
    r = _run_wsl(cfg, f"source {env_q} 2>/dev/null; cd {linux_case} && cartesianMesh > log.mesh 2>&1", 600)
    t_bin = time.time() - t0
    if r.returncode != 0:
        print(f"  FAILED: {(r.stderr or r.stdout)[-300:]}")
        return
    print(f"  cartesianMesh: {t_bin:.1f}s, returncode={r.returncode}")
    bin_metrics = _measure_read_speed(bin_dir, "binary_on")

    # ── Step 4: checkMesh on both ──────────────────────────────────────
    print("\n=== checkMesh verification ===")
    for label, d in [("ascii", ascii_dir), ("binary", bin_dir)]:
        linux_case = cfg._quoted_linux_path(d)
        r = _run_wsl(cfg, f"source {env_q} 2>/dev/null; cd {linux_case} && checkMesh 2>&1 | tail -20", 120)
        if "Mesh OK." in (r.stdout + r.stderr):
            print(f"  {label}: ✓ Mesh OK")
        else:
            print(f"  {label}: ✗ checkMesh issues found")

    # ── Step 5: Report ─────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("RESOURCE PROFILING REPORT")
    print("=" * 70)

    print(f"\n{'Metric':<30} {'ASCII':>15} {'Binary+Comp':>15} {'Reduction':>15}")
    print("-" * 75)

    # Mesh generation time
    print(f"{'Mesh generation time (s)':<30} {t_ascii:>15.1f} {t_bin:>15.1f} "
          f"{(1-t_bin/t_ascii)*100:>14.1f}%")

    # File sizes
    for key in ("points", "faces", "owner", "boundary"):
        a_sz = ascii_metrics["file_sizes_bytes"].get(key, 0)
        b_sz = bin_metrics["file_sizes_bytes"].get(key, 0)
        a_label = f"{a_sz/1024:.1f}K" if a_sz > 0 else "N/A"
        b_label = f"{b_sz/1024:.1f}K" if b_sz > 0 else "N/A"
        reduction = (1 - b_sz / a_sz) * 100 if a_sz > 0 and b_sz > 0 else 0
        print(f"{'  ' + key + ' size':<30} {a_label:>15} {b_label:>15} {reduction:>14.1f}%")
        gz = bin_metrics["file_sizes_bytes"].get(f"{key}_gzip", False)
        print(f"{'    gzipped':<30} {'':>15} {'✓' if gz else '✗':>15}")

    # polyMesh dir size
    print(f"{'polyMesh total (MB)':<30} {ascii_metrics['poly_dir_size_mb']:>15.2f} "
          f"{bin_metrics['poly_dir_size_mb']:>15.2f} "
          f"{(1-bin_metrics['poly_dir_size_mb']/ascii_metrics['poly_dir_size_mb'])*100:>14.1f}%")

    # Read speeds
    print(f"{'Cell count time (s)':<30} {ascii_metrics['time_cells_s']:>15.5f} "
          f"{bin_metrics['time_cells_s']:>15.5f} "
          f"{(1-bin_metrics['time_cells_s']/ascii_metrics['time_cells_s'])*100:>14.1f}%")
    print(f"{'Point count time (s)':<30} {ascii_metrics['time_pts_s']:>15.5f} "
          f"{bin_metrics['time_pts_s']:>15.5f} "
          f"{(1-bin_metrics['time_pts_s']/ascii_metrics['time_pts_s'])*100:>14.1f}%")
    print(f"{'Face count time (s)':<30} {ascii_metrics['time_faces_s']:>15.5f} "
          f"{bin_metrics['time_faces_s']:>15.5f} "
          f"{(1-bin_metrics['time_faces_s']/ascii_metrics['time_faces_s'])*100:>14.1f}%")
    print(f"{'Boundary parse time (s)':<30} {ascii_metrics['time_boundary_s']:>15.5f} "
          f"{bin_metrics['time_boundary_s']:>15.5f} "
          f"{(1-bin_metrics['time_boundary_s']/ascii_metrics['time_boundary_s'])*100:>14.1f}%")

    # Cells
    print(f"{'Cell count':<30} {ascii_metrics['cells']:>15,} {bin_metrics['cells']:>15,} {'(expected match)':>15}")

    # ── Memory simulation ──────────────────────────────────────────────
    print("\n--- Memory: _meshes vs _unscaled_meshes simulation ---")
    # Load the geometry twice to simulate old behaviour
    t0 = time.perf_counter()
    m1 = trimesh.load(str(stl_path))
    m2 = m1.copy()
    t_copy = time.perf_counter() - t0
    m1_mem = m1.vertices.nbytes + m1.faces.nbytes
    total_mem = m1_mem * 2  # both copies
    print(f"  Single mesh RAM (verts+faces): {m1_mem / 1024:.0f} KB")
    print(f"  Two copies (old behaviour):    {total_mem / 1024:.0f} KB")
    print(f"  Copy time:                     {t_copy*1000:.1f} ms")
    print(f"  Scale 1.0 (new behaviour):     {m1_mem / 1024:.0f} KB (zero copy)")

    # Cleanup
    shutil.rmtree(WORK, ignore_errors=True)
    print("\nDone.")


if __name__ == "__main__":
    main()
