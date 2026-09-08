"""Quick polyhedral quality comparison: optimal parameter test."""
from __future__ import annotations
import shutil, subprocess, re, sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
from polyfoammesh.config import OFConfig
from polyfoammesh.core.meshdict_gen import write_meshdict

cfg = OFConfig()
env_q = __import__("shlex").quote(cfg.env_script)
BL = {"nLayers": 5, "thicknessRatio": 1.2, "firstLayerThickness": 0.0015, "wallPatches": ["wall"]}

def val(pat, t):
    m = re.search(pat, t)
    if m:
        try: return float(m.group(1).rstrip("."))
        except: return 0.0
    return 0.0

def cm(text):
    return {
        "cells": int(val(r"cells:\s+(\d+)", text) or 0),
        "hex": int(val(r"hexahedra:\s+(\d+)", text)),
        "prisms": int(val(r"prisms:\s+(\d+)", text)),
        "poly": int(val(r"polyhedra:\s+(\d+)", text)),
        "skew": val(r"Max skewness = ([\d.]+)", text),
        "no_max": val(r"non-orthogonality Max: ([\d.]+)", text),
        "no_avg": val(r"non-orthogonality Max: [\d.]+\s+average:\s*([\d.]+)", text),
        "ar": val(r"Max aspect ratio = ([\d.]+)", text),
        "min_vol": val(r"Min volume = ([\d.eE+-]+)", text),
        "passed": "Mesh OK." in text,
    }

def make_case(case_dir, stl, mc, mi, bl, **kw):
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
    write_meshdict(case_dir, max_cell_size=mc, min_cell_size=mi,
                   surface_file="constant/triSurface/surface.stl", bl_params=bl,
                   patch_names=["wall", "inlet", "outlet"], **kw)
    lc = cfg.wsl_linux_case_path(case_dir)
    r = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null;cd {lc}&&cartesianMesh 2>&1|tail -10"),
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        print(f"  cartesianMesh FAILED: {r.stderr[:200]}")
        return None
    rh = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null;cd {lc}&&checkMesh 2>&1"),
                        capture_output=True, text=True, timeout=120)
    return case_dir, cm(rh.stdout)

# Test matrix: each case has a unique name and tests a specific configuration
TESTS = [
    # (name, stl_path, max_cell, min_cell, use_bc, fa, split, desc)
    ("hex-only",  "C:/cfmesh_poly_bench/venturi.stl", 0.08, 0.02, True, None, False, "Hex mesh only (reference)"),
    ("poly-fa45", "C:/cfmesh_poly_bench/venturi.stl", 0.08, 0.02, True, 45, False, "Baseline poly (FA=45)"),
    ("poly-fa60", "C:/cfmesh_poly_bench/venturi.stl", 0.08, 0.02, True, 60, False, "High FA=60"),
    ("poly-fa90", "C:/cfmesh_poly_bench/venturi.stl", 0.08, 0.02, True, 90, False, "Very high FA=90"),
    ("poly-split45", "C:/cfmesh_poly_bench/venturi.stl", 0.08, 0.02, True, 45, True, "FA=45 + splitAllFaces"),
    ("poly-nobc45", "C:/cfmesh_poly_bench/venturi.stl", 0.08, 0.02, False, 45, False, "No boundaryCellSize"),
]

for name, stl, mc, mi, use_bc, fa, sf, desc in TESTS:
    kw = {}
    if use_bc:
        kw["boundary_cell_size"] = (mc * mi) ** 0.5
        kw["boundary_refinement_thickness"] = mc * 3.0

    case_dir = Path(f"C:/cfmesh_poly_bench/test_{name}")
    result = make_case(case_dir, stl, mc, mi, BL, **kw)
    if result is None:
        print(f"{name:>15}: FAILED"); continue
    cdir, hm = result
    lc = cfg.wsl_linux_case_path(cdir)

    if fa is None:
        # Hex only
        print(f"{name:>15}: hex  cells={hm['cells']} skew={hm['skew']:.4f} NOmax={hm['no_max']:.2f} NOavg={hm['no_avg']:.2f} prisms={hm['prisms']}")
    else:
        cmd = cfg.build_poly_dual_cmd(cdir, feature_angle=fa, split_all_faces=sf)
        rp = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if rp.returncode != 0:
            print(f"{name:>15}: poly FAILED ({rp.stderr[:100]})")
            continue
        rp2 = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null;cd {lc}&&checkMesh 2>&1"),
                             capture_output=True, text=True, timeout=120)
        pm = cm(rp2.stdout)
        red = round((1 - pm['cells']/hm['cells'])*100, 1) if hm['cells'] else 0
        print(f"{name:>15}: poly cells={pm['cells']} ({red:+.1f}%) skew={pm['skew']:.4f} NOmax={pm['no_max']:.2f} NOavg={pm['no_avg']:.2f} prisms={pm['prisms']} ar={pm['ar']:.2f} pass={pm['passed']}")

print("\nDone.")
