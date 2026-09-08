"""Compare polyDualMesh quality: featureAngle + splitAllFaces sweep."""
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
        "passed": "Mesh OK." in text,
    }

def setup_case(case_dir, stl, mc, mi, use_bc):
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
              surface_file="constant/triSurface/surface.stl", bl_params=BL,
              patch_names=["wall", "inlet", "outlet"])
    if use_bc:
        kw["boundary_cell_size"] = (mc * mi) ** 0.5
        kw["boundary_refinement_thickness"] = mc * 3.0
    write_meshdict(case_dir, **kw)

ANGLES = [30, 45, 60]
SPLIT_OPTIONS = [False, True]
CONFIGS = [
    ("venturi", "C:/cfmesh_poly_bench/venturi.stl", 0.08, 0.02, True),
    ("s_bend",  "C:/cfmesh_poly_bench/s_bend.stl", 0.06, 0.015, True),
]

print(f"{'Geo':>8} {'Cfg':>12} {'Cells':>7} {'Hex':>7} {'Prism':>7} {'Poly':>7} {'Skew':>8} {'NOmax':>8} {'NOavg':>8} {'AspRa':>8} {'Red%':>6} {'Pass':>6}")
print("-"*105)

for gname, stl, mc, mi, use_bc in CONFIGS:
    # One hex mesh per geometry
    base_dir = Path(f"C:/cfmesh_poly_bench/sw_base_{gname}")
    setup_case(base_dir, stl, mc, mi, use_bc)
    lc = cfg.wsl_linux_case_path(base_dir)

    r = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null;cd {lc}&&cartesianMesh 2>&1|tail -10"),
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        print(f"{gname:>8} hex FAILED"); continue

    rh = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null;cd {lc}&&checkMesh 2>&1"),
                        capture_output=True, text=True, timeout=120)
    hm = cm(rh.stdout)
    print(f"{gname:>8} {'hex':>12} {hm['cells']:>7} {hm['hex']:>7} {hm['prisms']:>7} {hm['poly']:>7} {hm['skew']:>8.4f} {hm['no_max']:>8.2f} {hm['no_avg']:>8.2f} {hm['ar']:>8.2f} {'':>6} {str(hm['passed']):>6}")

    for fa in ANGLES:
        for sf in SPLIT_OPTIONS:
            # Clone the hex mesh for each config
            sub_dir = Path(f"C:/cfmesh_poly_bench/sw_{gname}_fa{fa}{'_split' if sf else ''}")
            shutil.rmtree(sub_dir, ignore_errors=True)
            sub_linux = cfg.wsl_linux_case_path(sub_dir)
            subprocess.run(cfg._build_wsl_cmd(
                f"mkdir -p {sub_linux}/constant {sub_linux}/system && "
                f"cp -r {lc}/constant/* {sub_linux}/constant/ && "
                f"cp -r {lc}/system/* {sub_linux}/system/"),
                capture_output=True, timeout=60)

            cmd = cfg.build_poly_dual_cmd(sub_dir, feature_angle=fa, split_all_faces=sf)
            rp = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if rp.returncode != 0:
                continue

            rp2 = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null;cd {cfg.wsl_linux_case_path(sub_dir)}&&checkMesh 2>&1"),
                                 capture_output=True, text=True, timeout=120)
            pm = cm(rp2.stdout)
            red = round((1 - pm['cells']/hm['cells'])*100, 1) if hm['cells'] else 0
            lbl = f"fa{fa}{'+s' if sf else ''}"
            print(f"{gname:>8} {lbl:>12} {pm['cells']:>7} {pm['hex']:>7} {pm['prisms']:>7} {pm['poly']:>7} {pm['skew']:>8.4f} {pm['no_max']:>8.2f} {pm['no_avg']:>8.2f} {pm['ar']:>8.2f} {red:>+6.1f} {str(pm['passed']):>6}")

print("\nDone.")
