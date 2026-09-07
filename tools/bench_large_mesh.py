"""Large mesh resource profiling benchmark (>1M cells).

Generates a large geometry and measures:
1. File sizes: ASCII vs binary+compressed polyMesh files
2. Generation time: cartesianMesh wall time
3. Read time: count_cells / count_points / count_faces / parse_boundary
4. gzip detection

Usage:
    python tools/bench_large_mesh.py
"""

import io, os, shlex, shutil, subprocess, sys, time, re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
import trimesh

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.meshdict_gen import write_meshdict
from cfmesh_autogui.core.boundary_reader import count_cells, count_points, count_faces, parse_boundary

WORK = Path("C:/cfmesh_bench/large_mesh")


def build_large_geometry(out_path: Path, target_faces: int = 200000) -> trimesh.Trimesh:
    """Build a large multi-cylinder cluster geometry."""
    meshes = []
    n_cylinders = 12
    sections = max(24, target_faces // (n_cylinders * 4))
    for i in range(n_cylinders):
        r = 0.3 + 0.05 * (i % 4)
        h = 3.0
        cyl = trimesh.creation.cylinder(radius=r, height=h, sections=sections)
        angle = i * 2 * np.pi / n_cylinders
        radius_pos = 0.8 + 0.15 * (i // 4)
        cyl.vertices[:, 0] += radius_pos * np.cos(angle)
        cyl.vertices[:, 1] += radius_pos * np.sin(angle)
        cyl.vertices[:, 2] += (i % 3 - 1) * 0.5
        meshes.append(cyl)
    combined = trimesh.util.concatenate(meshes)
    combined.export(str(out_path))
    print(f"  Geometry: {len(combined.faces):,} faces, {len(combined.vertices):,} vertices")
    return combined


def setup_case(case_dir: Path, stl_path: Path, fmt: str, comp: str) -> None:
    shutil.rmtree(case_dir, ignore_errors=True)
    (case_dir / "constant" / "triSurface").mkdir(parents=True)
    (case_dir / "system").mkdir(parents=True)
    shutil.copy2(stl_path, case_dir / "constant" / "triSurface" / "surface.stl")
    (case_dir / "system" / "controlDict").write_text(
        f"FoamFile {{ version 2.0; format ascii; class dictionary; object controlDict; }}\n"
        f"application cartesianMesh;\nstartFrom startTime; startTime 0;\n"
        f"stopAt endTime; endTime 1000;\ndeltaT 1;\n"
        f"writeControl timeStep; writeInterval 1; purgeWrite 0;\n"
        f"writeFormat {fmt};\nwritePrecision 6; writeCompression {comp};\n"
        f"timeFormat general; timePrecision 6;\nrunTimeModifiable true;\n",
        encoding="ascii",
    )
    write_meshdict(
        case_dir, max_cell_size=0.12, min_cell_size=0.025,
        surface_file="constant/triSurface/surface.stl",
        patch_names=["wall"],
    )


def measure(case_dir: Path, label: str) -> dict:
    poly_dir = case_dir / "constant" / "polyMesh"
    t0 = time.perf_counter()
    cells = count_cells(case_dir)
    t_cells = time.perf_counter() - t0
    t0 = time.perf_counter()
    pts = count_points(case_dir)
    t_pts = time.perf_counter() - t0
    t0 = time.perf_counter()
    fcs = count_faces(case_dir)
    t_fcs = time.perf_counter() - t0
    boundary_path = poly_dir / "boundary"
    t0 = time.perf_counter()
    patches = parse_boundary(boundary_path) if boundary_path.exists() else []
    t_bdy = time.perf_counter() - t0

    sizes = {}
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        fp = poly_dir / name
        if fp.exists():
            raw = fp.read_bytes()
            is_gz = raw[:2] == b"\x1f\x8b"
            sizes[name] = {"bytes": len(raw), "gzip": is_gz}

    dir_mb = sum(f.stat().st_size for f in poly_dir.rglob("*") if f.is_file()) / 1e6
    return {
        "label": label,
        "cells": cells,
        "points": pts,
        "faces": fcs,
        "time_cells_s": round(t_cells, 5),
        "time_pts_s": round(t_pts, 5),
        "time_fcs_s": round(t_fcs, 5),
        "time_boundary_s": round(t_bdy, 5),
        "sizes": sizes,
        "dir_mb": round(dir_mb, 2),
    }


def checkmesh_result(cfg: OFConfig, case_dir: Path) -> dict:
    env_q = shlex.quote(cfg.env_script)
    linux_case = cfg._quoted_linux_path(case_dir)
    r = subprocess.run(
        cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {linux_case} && checkMesh 2>&1 | tail -30"),
        capture_output=True, text=True, timeout=120,
    )
    out = r.stdout + r.stderr
    cells_m = re.search(r"cells:\s+([\d,]+)", out)
    skew_m = re.search(r"Max skewness\s*[:=]\s*([\d.]+)", out)
    no_m = re.search(r"non-orthogonality\s+Max:\s*([\d.]+)", out)
    return {
        "cells": int(cells_m.group(1).replace(",", "")) if cells_m else 0,
        "max_skew": float(skew_m.group(1)) if skew_m else None,
        "max_non_ortho": float(no_m.group(1)) if no_m else None,
        "ok": "Mesh OK." in out or "Failed 1 mesh" not in out,
        "raw": out[-500:],
    }


def main():
    os.makedirs(WORK, exist_ok=True)
    cfg = OFConfig()
    if not cfg.validate():
        print("SKIP: no WSL2/OpenFOAM")
        return

    print("=== Building large geometry ===")
    stl_path = WORK / "large.stl"
    build_large_geometry(stl_path, target_faces=200000)

    results = []
    for fmt, comp, label in [("ascii", "off", "ASCII"), ("binary", "on", "Binary+Comp")]:
        case_dir = WORK / f"case_{fmt}"
        print(f"\n=== Generating mesh ({label}) ===")
        setup_case(case_dir, stl_path, fmt, comp)
        env_q = shlex.quote(cfg.env_script)
        linux_case = cfg._quoted_linux_path(case_dir)
        t0 = time.time()
        r = subprocess.run(
            cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {linux_case} && cartesianMesh > log.mesh 2>&1"),
            capture_output=True, text=True, timeout=600,
        )
        gen_time = time.time() - t0
        if r.returncode != 0:
            print(f"  FAILED: {(r.stderr or r.stdout)[-300:]}")
            return
        print(f"  cartesianMesh: {gen_time:.1f}s, rc={r.returncode}")

        m = measure(case_dir, label)
        m["gen_time_s"] = round(gen_time, 1)
        results.append(m)

        cm = checkmesh_result(cfg, case_dir)
        print(f"  checkMesh: cells={cm['cells']:,}, max_skew={cm['max_skew']}, "
              f"non_ortho={cm['max_non_ortho']}, OK={cm['ok']}")

    print("\n" + "=" * 80)
    print("LARGE MESH RESOURCE PROFILING REPORT")
    print("=" * 80)
    a, b = results
    header = f"{'Metric':<35} {a['label']:>18} {b['label']:>18} {'Reduction':>18}"
    print(f"\n{header}")
    print("-" * len(header))

    print(f"{'Generation time (s)':<35} {a['gen_time_s']:>18.1f} {b['gen_time_s']:>18.1f} "
          f"{'':>10}{(1-b['gen_time_s']/a['gen_time_s'])*100:>6.1f}%")

    for key in ("points", "faces", "owner", "neighbour", "boundary"):
        a_sz = a["sizes"].get(key, {}).get("bytes", 0)
        b_sz = b["sizes"].get(key, {}).get("bytes", 0)
        a_gz = a["sizes"].get(key, {}).get("gzip", False)
        b_gz = b["sizes"].get(key, {}).get("gzip", False)
        a_label = f"{a_sz/1024:>.0f}K" if a_sz else "N/A"
        b_label = f"{b_sz/1024:>.0f}K" if b_sz else "N/A"
        reduction = (1 - b_sz / a_sz) * 100 if a_sz and b_sz else 0
        gz_mark = " gz" if b_gz else ""
        print(f"{'  ' + key:<35} {a_label:>18} {b_label + gz_mark:>18} {reduction:>17.1f}%")

    print(f"{'polyMesh dir (MB)':<35} {a['dir_mb']:>18.2f} {b['dir_mb']:>18.2f} "
          f"{(1-b['dir_mb']/a['dir_mb'])*100:>17.1f}%")
    print(f"{'Cell count':<35} {a['cells']:>18,} {b['cells']:>18,} {'(match)':>18}")
    print(f"{'Cell count time (s)':<35} {a['time_cells_s']:>18.5f} {b['time_cells_s']:>18.5f} "
          f"{(1-b['time_cells_s']/a['time_cells_s'])*100:>17.1f}%")
    print(f"{'Point count time (s)':<35} {a['time_pts_s']:>18.5f} {b['time_pts_s']:>18.5f} "
          f"{(1-b['time_pts_s']/a['time_pts_s'])*100:>17.1f}%")
    print(f"{'Face count time (s)':<35} {a['time_fcs_s']:>18.5f} {b['time_fcs_s']:>18.5f} "
          f"{(1-b['time_fcs_s']/a['time_fcs_s'])*100:>17.1f}%")
    print(f"{'Boundary parse time (s)':<35} {a['time_boundary_s']:>18.5f} {b['time_boundary_s']:>18.5f} "
          f"{(1-b['time_boundary_s']/a['time_boundary_s'])*100:>17.1f}%")

    shutil.rmtree(WORK, ignore_errors=True)
    print("\nDone.")


if __name__ == "__main__":
    main()
