"""Comprehensive polyhedral mesh quality baseline.
Runs hex + poly conversion, captures ALL checkMesh metrics.
"""
from __future__ import annotations
import shutil, subprocess, re, sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.meshdict_gen import write_meshdict

cfg = OFConfig()
env_q = __import__("shlex").quote(cfg.env_script)

BL = {"nLayers": 5, "thicknessRatio": 1.2, "firstLayerThickness": 0.0015, "wallPatches": ["wall"]}

def val(pat, text):
    m = re.search(pat, text)
    if m:
        try: return float(m.group(1).rstrip("."))
        except: return 0.0
    return 0.0

def parse_checkmesh(text):
    return {
        "cells": int(val(r"cells:\s+(\d+)", text) or 0),
        "hexahedra": int(val(r"hexahedra:\s+(\d+)", text)),
        "prisms": int(val(r"prisms:\s+(\d+)", text)),
        "polyhedra": int(val(r"polyhedra:\s+(\d+)", text)),
        "tetrahedra": int(val(r"tetrahedra:\s+(\d+)", text)),
        "pyramids": int(val(r"pyramids:\s+(\d+)", text)),
        "wedges": int(val(r"wedges:\s+(\d+)", text)),
        "skew": val(r"Max skewness = ([\d.]+)", text),
        "no_max": val(r"non-orthogonality Max: ([\d.]+)", text),
        "no_avg": val(r"non-orthogonality Max: [\d.]+\s+average:\s*([\d.]+)", text),
        "ar": val(r"Max aspect ratio = ([\d.]+)", text),
        "min_vol": val(r"Min volume = ([\d.eE+-]+)", text),
        "max_vol": val(r"Max volume = ([\d.eE+-]+)", text),
        "passed": "Mesh OK." in text,
    }

def run_case(case_dir, stl, mc, mi, bl=BL, use_bc=True, label=""):
    shutil.rmtree(case_dir, ignore_errors=True)
    for d in ["constant/triSurface", "system"]:
        (case_dir / d).mkdir(parents=True)
    shutil.copy2(stl, case_dir / "constant" / "triSurface" / "surface.stl")
    for f, c in [
        ("controlDict", "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\napplication cartesianMesh;\nstartFrom startTime; startTime 0; stopAt endTime; endTime 1000;\ndeltaT 1;\nwriteControl timeStep; writeInterval 1; purgeWrite 0;\nwriteFormat binary;\nwritePrecision 6; writeCompression on;\ntimeFormat general; timePrecision 6;\nrunTimeModifiable true;\n"),
        ("fvSchemes", "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\nddtSchemes { default steadyState; }\ngradSchemes { default Gauss linear; }\ndivSchemes { default Gauss linear; }\nlaplacianSchemes { default Gauss linear corrected; }\ninterpolationSchemes { default linear; }\nsnGradSchemes { default corrected; }\n"),
        ("fvSolution", "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\nsolvers { p { solver PCG; preconditioner DIC; tolerance 1e-6; relTol 0.1; } }\n"),
    ]:
        (case_dir / "system" / f).write_text(c, encoding="ascii")

    # Write meshDict
    kw = dict(max_cell_size=mc, min_cell_size=mi,
              surface_file="constant/triSurface/surface.stl", bl_params=bl,
              patch_names=["wall", "inlet", "outlet"])
    if use_bc:
        kw["boundary_cell_size"] = mi * 2.0
        kw["boundary_refinement_thickness"] = mc * 2.0  # 2x max cell for smooth gradient
    write_meshdict(case_dir, **kw)

    lc = cfg.wsl_linux_case_path(case_dir)
    print(f"  [{label}] cartesianMesh...", end=" ", flush=True)
    r = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {lc} && cartesianMesh 2>&1 | tail -10"),
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        print(f"FAILED: {r.stderr[:100]}")
        return None

    rh = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {lc} && checkMesh 2>&1"),
                        capture_output=True, text=True, timeout=120)
    hm = parse_checkmesh(rh.stdout)
    print(f"{hm['cells']} cells, skew={hm['skew']:.4f}, NOmax={hm['no_max']:.2f}")

    # polyDualMesh
    print(f"  [{label}] polyDualMesh (FA=45)...", end=" ", flush=True)
    cmd = cfg.build_poly_dual_cmd(case_dir, feature_angle=45)
    rp = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    rp2 = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {lc} && checkMesh 2>&1"),
                         capture_output=True, text=True, timeout=120)
    pm = parse_checkmesh(rp2.stdout)
    red = round((1 - pm['cells']/hm['cells'])*100, 1) if hm['cells'] else 0
    print(f"{pm['cells']} cells ({red:+}%), skew={pm['skew']:.4f}, NOmax={pm['no_max']:.2f}")
    return hm, pm


print("=" * 80)
print("POLYHEDRAL MESH QUALITY BASELINE")
print("=" * 80)

# Test different configurations
configs = [
    # (label, stl_path, max_cell, min_cell, use_boundary_cell)
    ("VENTURI-bc",  Path("C:/cfmesh_poly_bench/venturi.stl"), 0.08, 0.02, True),
    ("VENTURI-nobc", Path("C:/cfmesh_poly_bench/venturi.stl"), 0.08, 0.02, False),
    ("S-BEND-bc",   Path("C:/cfmesh_poly_bench/s_bend.stl"),  0.06, 0.015, True),
    ("S-BEND-nobc",  Path("C:/cfmesh_poly_bench/s_bend.stl"),  0.06, 0.015, False),
]

results = []
for label, stl, mc, mi, use_bc in configs:
    print(f"\n-- {label} --")
    case_dir = Path(f"C:/cfmesh_poly_bench/base_{label}")
    r = run_case(case_dir, stl, mc, mi, use_bc=use_bc, label=label)
    if r:
        hm, pm = r
        results.append((label, hm, pm))

# Summary
print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
hdr = f"{'Config':>15} {'Stage':>6} {'Cells':>8} {'Hex':>7} {'Prism':>7} {'Poly':>7} {'Skew':>8} {'NOmax':>8} {'NOavg':>8} {'AspRa':>8} {'Pass':>6}"
print(hdr)
print("-" * len(hdr))
for label, hm, pm in results:
    print(f"{label:>15} {'hex':>6} {hm['cells']:>8} {hm['hexahedra']:>7} {hm['prisms']:>7} {hm['polyhedra']:>7} {hm['skew']:>8.4f} {hm['no_max']:>8.2f} {hm['no_avg']:>8.2f} {hm['ar']:>8.2f} {str(hm['passed']):>6}")
    print(f"{label:>15} {'poly':>6} {pm['cells']:>8} {pm['hexahedra']:>7} {pm['prisms']:>7} {pm['polyhedra']:>7} {pm['skew']:>8.4f} {pm['no_max']:>8.2f} {pm['no_avg']:>8.2f} {pm['ar']:>8.2f} {str(pm['passed']):>6}")
    print()
