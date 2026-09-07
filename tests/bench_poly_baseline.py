"""Baseline polyhedral mesh quality benchmark.
Generates two test geometries (venturi + S-bend), runs the current
polyhedral conversion pipeline, and records checkMesh metrics.
"""
from __future__ import annotations

import json, os, re, shlex, shutil, subprocess, sys, time
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import numpy as np
import trimesh
from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.meshdict_gen import write_meshdict
from cfmesh_autogui.core.of_reader import of_list_count


def _wsl_ok(cfg: OFConfig) -> bool:
    try: return cfg.validate()
    except: return False

def _run_wsl(cfg: OFConfig, bash: str, timeout=600):
    return subprocess.run(cfg._build_wsl_cmd(bash), capture_output=True, text=True, timeout=timeout)

def _checkmesh_raw(case_dir: Path) -> str:
    cfg = OFConfig()
    linux_case = cfg._quoted_linux_path(case_dir)
    env_q = shlex.quote(cfg.env_script)
    r = _run_wsl(cfg, f"source {env_q} 2>/dev/null; cd {linux_case} && checkMesh 2>&1", timeout=120)
    return r.stdout + r.stderr

def _checkmesh_metrics(case_dir: Path) -> dict:
    text = _checkmesh_raw(case_dir)
    metrics = {
        "cells": int(_rex(r"cells:\s+(\d+)", text) or 0),
        "max_skewness": _flt(r"Max skewness\s*[:=]\s*([\d.]+)", text),
        "avg_skewness": _flt(r"average skewness\s*[:=]\s*([\d.]+)", text),
        "max_non_orth": _flt(r"non-orthogonality\s+Max:\s*([\d.]+)", text),
        "avg_non_orth": _flt(r"non-orthogonality\s+Max:\s*[\d.]+\s+average:\s*([\d.]+)", text),
        "max_aspect_ratio": _flt(r"Max aspect ratio\s*[:=]\s*([\d.]+)", text),
        "min_volume": _flt(r"Min volume = ([\d.eE+-]+)", text),
        "max_volume": _flt(r"Max volume = ([\d.eE+-]+)", text),
        "n_neg_vol": _int(r"there are (\d+) negative volume cells", text),
        "n_bad_skew": _int(r"(\d+)\s+highly skew", text),
        "n_bad_nonortho": _int(r"(\d+)\s+severely non-orthogonal", text),
        "passed": "Mesh OK." in text,
        "return_code": 0,
        "raw_output": text,
    }
    return metrics

def _rex(pat, text):
    m = re.search(pat, text)
    return m.group(1).replace(",", "") if m else None

def _flt(pat, text):
    m = re.search(pat, text)
    if m:
        val = m.group(1).rstrip('.')
        try: return float(val)
        except: return None
    return None

def _int(pat, text):
    m = re.search(pat, text)
    return int(m.group(1)) if m else 0

def _hex_cells(case_dir: Path) -> int:
    """Count cells from existing checkMesh log if available."""
    cm = _checkmesh_metrics(case_dir)
    return cm["cells"]


# Geometry builders
def make_venturi_stl(out_path: Path) -> None:
    cyl = trimesh.creation.cylinder(radius=0.5, height=2.0, sections=48)
    verts = cyl.vertices.copy()
    z = verts[:, 2]
    r = np.sqrt(np.maximum(verts[:, 0]**2 + verts[:, 1]**2, 1e-12))
    waist_r = 0.30 + 0.20 * np.clip(1 - np.abs(z) / 1.0, 0, 1)
    scale = np.where(r > 0.001, np.where(z > -1.0, waist_r / r, 1.0), 1.0)
    verts[:, 0] *= scale; verts[:, 1] *= scale
    m = trimesh.Trimesh(vertices=verts, faces=cyl.faces)
    m.export(str(out_path))
    print(f"  venturi: {len(m.faces)} faces")

def make_s_bend_stl(out_path: Path) -> None:
    cyl = trimesh.creation.cylinder(radius=0.20, height=2.0, sections=32)
    verts = cyl.vertices.copy()
    z = verts[:, 2]
    verts[:, 0] += 0.4 * np.sin((z + 1.0) * np.pi)
    verts[:, 1] += 0.15 * np.sin((z + 1.0) * np.pi * 2)
    m = trimesh.Trimesh(vertices=verts, faces=cyl.faces)
    m.export(str(out_path))
    print(f"  s_bend: {len(m.faces)} faces")


def _setup_case(case_dir: Path, stl_path: Path, cell_sizes, bl_params):
    shutil.rmtree(case_dir, ignore_errors=True)
    tri = case_dir / "constant" / "triSurface"
    sysd = case_dir / "system"
    tri.mkdir(parents=True); sysd.mkdir(parents=True)
    shutil.copy2(stl_path, tri / "surface.stl")
    _write_text(sysd / "controlDict",
        "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
        "application cartesianMesh;\nstartFrom startTime; startTime 0; stopAt endTime; endTime 1000;\n"
        "deltaT 1;\nwriteControl timeStep; writeInterval 1; purgeWrite 0;\n"
        "writeFormat binary;\nwritePrecision 6; writeCompression on;\n"
        "timeFormat general; timePrecision 6;\nrunTimeModifiable true;\n")
    _write_text(sysd / "fvSchemes",
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
        "ddtSchemes { default steadyState; }\ngradSchemes { default Gauss linear; }\n"
        "divSchemes { default Gauss linear; }\nlaplacianSchemes { default Gauss linear corrected; }\n"
        "interpolationSchemes { default linear; }\nsnGradSchemes { default corrected; }\n")
    _write_text(sysd / "fvSolution",
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
        "solvers { p { solver PCG; preconditioner DIC; tolerance 1e-6; relTol 0.1; } }\n")
    write_meshdict(case_dir, max_cell_size=cell_sizes[0], min_cell_size=cell_sizes[1],
                   surface_file="constant/triSurface/surface.stl", bl_params=bl_params,
                   patch_names=["wall", "inlet", "outlet"])

def _write_text(path, content):
    path.write_text(content, encoding="ascii")


def run_single_baseline(name, stl_path, cell_sizes, bl_params):
    cfg = OFConfig()
    case_dir = Path(f"C:/cfmesh_poly_bench/{name}")
    _setup_case(case_dir, stl_path, cell_sizes, bl_params)
    env_q = shlex.quote(cfg.env_script)
    linux_case = cfg._quoted_linux_path(case_dir)

    # Step 1: cartesianMesh
    print(f"  [{name}] cartesianMesh...", end=" ", flush=True)
    t0 = time.time()
    r = _run_wsl(cfg, f"source {env_q} 2>/dev/null; cd {linux_case} && cartesianMesh 2>&1 | tail -30", 1800)
    ct = time.time() - t0
    if r.returncode != 0:
        return {"geometry": name, "phase": "cartesianMesh", "error": (r.stderr or r.stdout)[-500:], "return_code": r.returncode}
    hc = _hex_cells(case_dir)
    print(f"{hc} cells ({ct:.0f}s)")

    # Step 2: checkMesh hex
    hm = _checkmesh_metrics(case_dir)
    print(f"  [{name}] hex: max_skew={hm['max_skewness']}, non_ortho={hm['max_non_orth']}")

    # Step 3: polyDualMesh with featureAngle=90 (best measured quality)
    print(f"  [{name}] polyDualMesh (fa=90)...", end=" ", flush=True)
    t0 = time.time()
    rp = _run_wsl(cfg, f"source {env_q} 2>/dev/null; cd {linux_case} && polyDualMesh 90 -overwrite 2>&1 | tail -20", 600)
    pt = time.time() - t0
    if rp.returncode != 0:
        return {"geometry": name, "phase": "polyDualMesh", "error": (rp.stderr or rp.stdout)[-500:], "return_code": rp.returncode, "hex_metrics": hm}
    pc = _hex_cells(case_dir)
    pm = _checkmesh_metrics(case_dir)
    print(f"{pc} cells ({pt:.0f}s)")
    print(f"  [{name}] poly: max_skew={pm['max_skewness']}, non_ortho={pm['max_non_orth']}")

    return {"geometry": name, "phase": "polyhedral", "success": True,
            "hex_cells": hc, "poly_cells": pc,
            "reduction_pct": round((1 - pc / max(hc, 1)) * 100, 1),
            "hex_metrics": hm, "poly_metrics": pm,
            "timing": {"cartesianMesh_s": round(ct, 1), "polyDualMesh_s": round(pt, 1)}}


def main():
    os.makedirs("C:/cfmesh_poly_bench", exist_ok=True)
    print("=== Generating geometries ===")
    make_venturi_stl(Path("C:/cfmesh_poly_bench/venturi.stl"))
    make_s_bend_stl(Path("C:/cfmesh_poly_bench/s_bend.stl"))

    cfg = OFConfig()
    if not _wsl_ok(cfg):
        print("SKIP: no WSL2/OpenFOAM"); return

    bl = {"nLayers": 4, "thicknessRatio": 1.2, "firstLayerThickness": 0.002, "wallPatches": ["wall"]}
    geoms = [("venturi", 0.08, 0.02), ("s_bend", 0.06, 0.015)]
    results = []

    for name, max_c, min_c in geoms:
        print(f"\n-- {name} --")
        r = run_single_baseline(name, Path(f"C:/cfmesh_poly_bench/{name}.stl"), (max_c, min_c), bl)
        results.append(r)
        if r.get("success"):
            m = r["poly_metrics"]
            print(f"  OK: {r['hex_cells']}->{r['poly_cells']} cells ({r['reduction_pct']}%), "
                  f"skew={m['max_skewness']}, nonOrtho={m['max_non_orth']}")
        else:
            print(f"  FAIL at {r.get('phase')}: {r.get('error','')[:100]}")

    print("\n-- BASELINE SUMMARY --")
    print(f"{'Geo':<10} {'Hex':<8} {'Poly':<8} {'Red%':<6} {'Skew':<8} {'NOmax':<8} {'NOavg':<6} {'AspRa':<8} {'Pass':<6}")
    print("-"*65)
    for r in results:
        if r.get("success"):
            m = r["poly_metrics"]
            print(f"{r['geometry']:<10} {r['hex_cells']:<8} {r['poly_cells']:<8} "
                  f"{r['reduction_pct']:<6} {m['max_skewness'] or '?':<8} "
                  f"{m['max_non_orth'] or '?':<8} {m['avg_non_orth'] or '?':<6} "
                  f"{m['max_aspect_ratio'] or '?':<8} {m['passed']}")
        else:
            print(f"{r['geometry']:<10} FAIL")

    Path("C:/cfmesh_poly_bench/baseline_metrics.json").write_text(
        json.dumps(results, indent=2, default=str))
    print("\nBaseline saved")

if __name__ == "__main__":
    main()
