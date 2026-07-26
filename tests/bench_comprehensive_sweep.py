"""Comprehensive polyhedral quality sweep: featureAngle + flags."""
from __future__ import annotations
import shutil, subprocess, re, sys, json
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.meshdict_gen import write_meshdict
from cfmesh_autogui.core.of_reader import of_list_count

cfg = OFConfig()
env_q = __import__("shlex").quote(cfg.env_script)
BL = {"nLayers": 5, "thicknessRatio": 1.2, "firstLayerThickness": 0.0015, "wallPatches": ["wall"]}

def val(pat, t):
    m = re.search(pat, t)
    if m:
        try: return float(m.group(1).rstrip("."))
        except: return 0.0
    return 0.0

def run(name, stl, mc, mi, use_bc, bl=BL):
    case_dir = Path(f"C:/cfmesh_poly_bench/combo_{name}")
    shutil.rmtree(case_dir, ignore_errors=True)
    for d in ["constant/triSurface", "system"]:
        (case_dir / d).mkdir(parents=True)
    shutil.copy2(stl, case_dir / "constant" / "triSurface" / "surface.stl")
    for f, c in [
        ("controlDict", "FoamFile{version 2.0;format ascii;class dictionary;object controlDict;}\napplication cartesianMesh;\nstartFrom startTime;startTime 0;stopAt endTime;endTime 1000;\ndeltaT 1;\nwriteControl timeStep;writeInterval 1;purgeWrite 0;\nwriteFormat binary;\nwritePrecision 6;writeCompression on;\ntimeFormat general;timePrecision 6;\nrunTimeModifiable true;\n"),
        ("fvSchemes", "FoamFile{version 2.0;format ascii;class dictionary;object fvSchemes;}\nddtSchemes{default steadyState;}\ngradSchemes{default Gauss linear;}\ndivSchemes{default Gauss linear;}\nlaplacianSchemes{default Gauss linear corrected;}\ninterpolationSchemes{default linear;}\nsnGradSchemes{default corrected;}\n"),
        ("fvSolution", "FoamFile{version 2.0;format ascii;class dictionary;object fvSolution;}\nsolvers{p{solver PCG;preconditioner DIC;tolerance 1e-6;relTol 0.1;}}\n"),
    ]:
        (case_dir / "system" / f).write_text(c, encoding="ascii")

    kw = dict(max_cell_size=mc, min_cell_size=mi,
              surface_file="constant/triSurface/surface.stl", bl_params=bl,
              patch_names=["wall", "inlet", "outlet"])
    if use_bc:
        kw["boundary_cell_size"] = (mc * mi) ** 0.5
        kw["boundary_refinement_thickness"] = mc * 3.0
    write_meshdict(case_dir, **kw)

    lc = cfg.wsl_linux_case_path(case_dir)
    r = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null;cd {lc}&&cartesianMesh 2>&1|tail -10"),
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0: return None
    rh = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null;cd {lc}&&checkMesh 2>&1"),
                        capture_output=True, text=True, timeout=120)
    return case_dir, rh.stdout

def checkmesh(text):
    return {
        "cells": int(val(r"cells:\s+(\d+)", text) or 0),
        "hex": int(val(r"hexahedra:\s+(\d+)", text)),
        "prisms": int(val(r"prisms:\s+(\d+)", text)),
        "poly": int(val(r"polyhedra:\s+(\d+)", text)),
        "skew": val(r"Max skewness = ([\d.]+)", text),
        "no_max": val(r"non-orthogonality Max: ([\d.]+)", text),
        "no_avg": val(r"non-orthogonality Max: [\d.]+\s+average:\s*([\d.]+)", text),
        "ar": val(r"Max aspect ratio = ([\d.]+)", text),
        "passed": "Mesh OK." in text,
    }

def fmt(m):
    return f"{m['cells']:>7} {m['hex']:>7} {m['prisms']:>7} {m['poly']:>7} {m['skew']:>8.4f} {m['no_max']:>8.2f} {m['no_avg']:>8.2f} {m['ar']:>8.2f}"

# ── SWEEP ──
results = []

# Test matrix: geometry, stl, max_cell, min_cell, use_bc, angles
tests = [
    ("venturi", "C:/cfmesh_poly_bench/venturi.stl", 0.08, 0.02, True),
    ("s_bend",  "C:/cfmesh_poly_bench/s_bend.stl", 0.06, 0.015, True),
]
ANGLES = [20, 30, 45, 60, 90, 120, 180]

for gname, stl, mc, mi, use_bc in tests:
    print(f"\n## {gname}")
    r = run(gname, stl, mc, mi, use_bc)
    if r is None:
        print("  cartesianMesh FAILED"); continue
    case_dir, hex_text = r
    hm = checkmesh(hex_text)

    for fa in ANGLES:
        for sf in [False, True]:
            # polyDualMesh
            cmd = cfg.build_poly_dual_cmd(case_dir, feature_angle=fa, split_all_faces=sf)
            rp = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if rp.returncode != 0:
                continue
            rp2 = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null;cd {cfg.wsl_linux_case_path(case_dir)}&&checkMesh 2>&1"),
                                 capture_output=True, text=True, timeout=120)
            pm = checkmesh(rp2.stdout)
            red = round((1 - pm['cells']/hm['cells'])*100, 1) if hm['cells'] else 0
            label = f"fa{fa}{'+split' if sf else ''}"
            results.append((gname, label, hm, pm, red))
            print(f"  {label:>12}: {fmt(pm)} red={red:+.1f}% pass={pm['passed']}")

# Summary table
print("\n\n" + "="*120)
print("SUMMARY - ALL CONFIGURATIONS")
print("="*120)
hdr = f"{'Geo':>8} {'Cfg':>12} {'Cells':>7} {'Hex':>7} {'Prism':>7} {'Poly':>7} {'Skew':>8} {'NOmax':>8} {'NOavg':>8} {'AspRa':>8} {'Red%':>6} {'Pass':>6}"
print(hdr)
print("-"*120)
for gname, label, hm, pm, red in results:
    print(f"{gname:>8} {label:>12} {fmt(pm)} {red:>+6.1f} {str(pm['passed']):>6}")
