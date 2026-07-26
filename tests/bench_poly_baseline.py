"""Baseline polyhedral mesh quality benchmark.

Generates two test geometries (venturi + S-bend), runs the current
polyhedral conversion pipeline, and records checkMesh metrics as
reference for measuring improvement from subsequent fixes.
"""
from __future__ import annotations

import json, math, os, re, shlex, shutil, subprocess, sys, time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import numpy as np
import trimesh

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.meshdict_gen import write_meshdict


def _wsl_ok(cfg: OFConfig) -> bool:
    try:
        return cfg.validate()
    except Exception:
        return False


def _run_wsl(cfg: OFConfig, bash: str, timeout: int = 600) -> subprocess.CompletedProcess:
    cmd = cfg._build_wsl_cmd(bash)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _checkmesh_metrics(case_dir: Path) -> dict:
    """Run checkMesh via WSL and parse quality metrics."""
    cfg = OFConfig()
    linux_case = cfg._quoted_linux_path(case_dir)
    env_q = shlex.quote(cfg.env_script)
    bash = (
        f"source {env_q} 2>/dev/null; "
        f"cd {linux_case} && checkMesh 2>&1"
    )
    r = _run_wsl(cfg, bash, timeout=120)
    text = r.stdout + r.stderr

    metrics = {
        "cells": _rex(r"cells\s*\(([\d,]+)\)\s*:", text),
        "max_skewness": _flt(r"Max skewness\s*:\s*([\d.]+)", text),
        "avg_skewness": _flt(r"average skewness\s*:\s*([\d.]+)", text),
        "max_non_orth": _flt(r"Maximum cell non-orthogonality = ([\d.]+)", text),
        "avg_non_orth": _flt(r"average non-orthogonality = ([\d.]+)", text),
        "max_aspect_ratio": _flt(r"Max aspect ratio\s*=\s*([\d.]+)", text),
        "min_volume": _flt(r"Min volume = ([\d.eE+-]+)", text),
        "max_volume": _flt(r"Max volume = ([\d.eE+-]+)", text),
        "n_neg_vol": _int(r"there are (\d+) negative volume cells", text),
        "n_bad_skew": _int(r"(\d+)\s+highly skew", text),
        "n_bad_nonortho": _int(r"(\d+)\s+severely non-orthogonal", text),
        "passed": "Mesh OK." in text,
        "return_code": r.returncode,
        "raw_output": text,
    }
    return metrics


def _rex(pat: str, text: str) -> str | None:
    m = re.search(pat, text)
    return m.group(1).replace(",", "") if m else None


def _flt(pat: str, text: str) -> float | None:
    m = re.search(pat, text)
    return float(m.group(1)) if m else None


def _int(pat: str, text: str) -> int:
    m = re.search(pat, text)
    return int(m.group(1)) if m else 0


def _hex_cells(case_dir: Path) -> int:
    from cfmesh_autogui.core.boundary_reader import count_cells
    return count_cells(case_dir)


# ── Geometry builders ────────────────────────────────────────────────

def make_venturi_stl(out_path: Path, cell_size: float = 0.02) -> None:
    """Venturi: cylinder with sinusoidal waist (radius 0.3 → 0.5 → 0.3)."""
    cyl = trimesh.creation.cylinder(radius=0.5, height=2.0, sections=48)
    verts = cyl.vertices.copy()
    z = verts[:, 2]
    r = np.sqrt(verts[:, 0]**2 + verts[:, 1]**2)
    waist_r = 0.30 + 0.20 * (1 - np.abs(z) / 1.0)  # 0.3 at center, 0.5 at ends
    mask_inner = z > -1.0  # only deform within [-1, 1]
    scale = np.where(r > 0.001, np.where(mask_inner, waist_r / r, 1.0), 1.0)
    verts[:, 0] *= scale
    verts[:, 1] *= scale
    m = trimesh.Trimesh(vertices=verts, faces=cyl.faces)
    m.export(str(out_path))
    print(f"  venturi: {len(m.faces)} faces, bbox={m.bounds}")


def make_s_bend_stl(out_path: Path, cell_size: float = 0.015) -> None:
    """S-bend: cylinder core deformed into S-curve, capped at ends."""
    cyl = trimesh.creation.cylinder(radius=0.20, height=2.0, sections=32)
    verts = cyl.vertices.copy()
    z = verts[:, 2]
    verts[:, 0] += 0.4 * np.sin((z + 1.0) * np.pi)
    verts[:, 1] += 0.15 * np.sin((z + 1.0) * np.pi * 2)
    m = trimesh.Trimesh(vertices=verts, faces=cyl.faces)
    m.export(str(out_path))
    print(f"  s_bend: {len(m.faces)} faces, bbox={m.bounds}")


# ── Pipeline ─────────────────────────────────────────────────────────

def run_single_baseline(
    geometry_name: str, stl_path: Path,
    cell_sizes: tuple[float, float],
    bl_params: dict | None,
) -> dict:
    """Run cartesianMesh + polyDualMesh + checkMesh on one geometry."""
    cfg = OFConfig()
    case_dir = Path(f"C:/cfmesh_poly_bench/{geometry_name}")
    shutil.rmtree(case_dir, ignore_errors=True)

    tri_dir = case_dir / "constant" / "triSurface"
    tri_dir.mkdir(parents=True)
    sys_dir = case_dir / "system"
    sys_dir.mkdir(parents=True)
    shutil.copy2(stl_path, tri_dir / "surface.stl")

    (sys_dir / "controlDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; "
        "object controlDict; }\n"
        "application cartesianMesh;\n"
        "startFrom startTime; startTime 0; stopAt endTime; endTime 1000; "
        "deltaT 1;\n"
        "writeControl timeStep; writeInterval 1; purgeWrite 0; "
        "writeFormat binary;\nwritePrecision 6; writeCompression on; "
        "timeFormat general; timePrecision 6;\nrunTimeModifiable true;\n",
        encoding="ascii",
    )

    max_cell, min_cell = cell_sizes
    write_meshdict(
        case_dir,
        max_cell_size=max_cell,
        min_cell_size=min_cell,
        surface_file="constant/triSurface/surface.stl",
        bl_params=bl_params,
        patch_names=["wall", "inlet", "outlet"],
    )

    env_q = shlex.quote(cfg.env_script)
    linux_case = cfg._quoted_linux_path(case_dir)

    # ── Step 1: cartesianMesh ──
    print(f"  [{geometry_name}] cartesianMesh...", end=" ", flush=True)
    bash = f"source {env_q} 2>/dev/null; cd {linux_case} && cartesianMesh 2>&1 | tail -30"
    t0 = time.time()
    r = _run_wsl(cfg, bash, timeout=1800)
    cart_time = time.time() - t0
    if r.returncode != 0:
        return {"geometry": geometry_name, "phase": "cartesianMesh",
                "error": (r.stderr or r.stdout)[-500:], "return_code": r.returncode}
    hex_count = _hex_cells(case_dir)
    print(f"{hex_count} cells ({cart_time:.0f}s)")

    # ── Step 2: checkMesh on hex ──
    hex_m = _checkmesh_metrics(case_dir)
    print(f"  [{geometry_name}] hex checkMesh: max_skew={hex_m['max_skewness']}, "
          f"non_ortho={hex_m['max_non_orth']}°(avg{hex_m['avg_non_orth']}°)")

    # ── Step 3: polyDualMesh (baseline: no featureAngle) ──
    print(f"  [{geometry_name}] polyDualMesh (NO featureAngle)...", end=" ", flush=True)
    bash_poly = (
        f"source {env_q} 2>/dev/null; "
        f"cd {linux_case} && polyDualMesh -constant 2>&1 | tail -20"
    )
    t0 = time.time()
    r_poly = _run_wsl(cfg, bash_poly, timeout=600)
    poly_time = time.time() - t0
    if r_poly.returncode != 0:
        return {"geometry": geometry_name, "phase": "polyDualMesh",
                "error": (r_poly.stderr or r_poly.stdout)[-500:],
                "return_code": r_poly.returncode, "hex_metrics": hex_m}

    # ── Step 4: checkMesh on poly ──
    poly_m = _checkmesh_metrics(case_dir)
    poly_count = _hex_cells(case_dir)
    print(f"{poly_count} cells ({poly_time:.0f}s)")
    print(f"  [{geometry_name}] poly checkMesh: max_skew={poly_m['max_skewness']}, "
          f"non_ortho={poly_m['max_non_orth']}°(avg{poly_m['avg_non_orth']}°)")

    return {
        "geometry": geometry_name, "phase": "polyhedral", "success": True,
        "hex_cells": hex_count, "poly_cells": poly_count,
        "reduction_pct": round((1 - poly_count / max(hex_count, 1)) * 100, 1),
        "hex_metrics": hex_m, "poly_metrics": poly_m,
        "timing": {"cartesianMesh_s": round(cart_time, 1),
                   "polyDualMesh_s": round(poly_time, 1)},
    }


def main() -> None:
    os.makedirs("C:/cfmesh_poly_bench", exist_ok=True)

    print("=== Generating test geometries ===")
    make_venturi_stl(Path("C:/cfmesh_poly_bench/venturi.stl"))
    make_s_bend_stl(Path("C:/cfmesh_poly_bench/s_bend.stl"))

    cfg = OFConfig()
    if not _wsl_ok(cfg):
        print("\nSKIP: WSL2/OpenFOAM not available (run: wsl.exe -d Ubuntu)")
        return

    geoms = [
        ("venturi", Path("C:/cfmesh_poly_bench/venturi.stl"), (0.08, 0.02)),
        ("s_bend",  Path("C:/cfmesh_poly_bench/s_bend.stl"),  (0.06, 0.015)),
    ]

    bl = {"nLayers": 4, "thicknessRatio": 1.2, "firstLayerThickness": 0.002,
          "wallPatches": ["wall"]}

    results = []
    for name, stl, sizes in geoms:
        print(f"\n── {name} ──────────────────────")
        r = run_single_baseline(name, stl, sizes, bl)
        results.append(r)
        if r.get("success"):
            pm = r["poly_metrics"]
            print(f"  ✓ {name}: {r['hex_cells']}→{r['poly_cells']} cells "
                  f"({r['reduction_pct']}%), "
                  f"skew={pm['max_skewness']}, nonOrtho={pm['max_non_orth']}°")
        else:
            print(f"  ✗ {name}: FAILED at {r.get('phase')} — {r.get('error','')[:120]}")

    # Summary table
    print("\n─── BASELINE SUMMARY ─────────────────────────────")
    print(f"{'Geometry':<12} {'Hex':<8} {'Poly':<8} {'Red%':<6} "
          f"{'Skew':<8} {'N-Orth°':<8} {'AvgNO':<6} {'AspRat':<8} {'Pass':<6}")
    print("-" * 75)
    for r in results:
        if r.get("success"):
            pm = r["poly_metrics"]
            print(f"{r['geometry']:<12} {r['hex_cells']:<8} {r['poly_cells']:<8} "
                  f"{r['reduction_pct']:<6} {pm['max_skewness'] or '?':<8} "
                  f"{pm['max_non_orth'] or '?':<8} {pm['avg_non_orth'] or '?':<6} "
                  f"{pm['max_aspect_ratio'] or '?':<8} {pm['passed']}")
        else:
            print(f"{r['geometry']:<12} {'FAIL':<8}")

    Path("C:/cfmesh_poly_bench/baseline_metrics.json").write_text(
        json.dumps(results, indent=2, default=str))
    print(f"\nBaseline saved to C:/cfmesh_poly_bench/baseline_metrics.json")


if __name__ == "__main__":
    main()
