"""Compare polyDualMesh with different flags on fresh baseline."""
from __future__ import annotations
import shutil, subprocess, re, sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.meshdict_gen import write_meshdict

cfg = OFConfig()
env_q = __import__("shlex").quote(cfg.env_script)
BL = {"nLayers": 4, "thicknessRatio": 1.2, "firstLayerThickness": 0.002, "wallPatches": ["wall"]}

def _val(pat, text):
    m = re.search(pat, text)
    if m:
        try: return float(m.group(1).rstrip("."))
        except: return 0.0
    return 0.0

def _checkmesh(case_dir):
    lc = cfg.wsl_linux_case_path(case_dir)
    r = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {lc} && checkMesh 2>&1"),
                       capture_output=True, text=True, timeout=120)
    t = r.stdout
    return {
        "cells": int(_val(r"cells:\s+(\d+)", t) or 0),
        "skew": _val(r"Max skewness = ([\d.]+)", t),
        "no_max": _val(r"non-orthogonality Max: ([\d.]+)", t),
        "no_avg": _val(r"non-orthogonality Max: [\d.]+\s+average:\s*([\d.]+)", t),
        "ar": _val(r"Max aspect ratio = ([\d.]+)", t),
        "passed": "Mesh OK." in t,
    }

# Test matrix
CONFIGS = [
    ("plain30", {"feature_angle": 30, "concave_multi": False, "split_all": False}),
    ("concave30", {"feature_angle": 30, "concave_multi": True, "split_all": False}),
    ("split30", {"feature_angle": 30, "concave_multi": False, "split_all": True}),
    ("all30", {"feature_angle": 30, "concave_multi": True, "split_all": True}),
    ("angle15", {"feature_angle": 15, "concave_multi": False, "split_all": False}),
    ("angle60", {"feature_angle": 60, "concave_multi": False, "split_all": False}),
]

print(f"{'Geo':>8} {'Cfg':>10} {'Cells':>8} {'Red%':>6} {'Skew':>8} {'NOmax':>8} {'NOavg':>8} {'AspRa':>8} {'Pass':>6}")
print("-" * 74)

for name, stl, mc, mi in [
    ("venturi", Path("C:/cfmesh_poly_bench/venturi.stl"), 0.08, 0.02),
    ("s_bend",  Path("C:/cfmesh_poly_bench/s_bend.stl"), 0.06, 0.015),
]:
    # One hex mesh for each geometry
    case_dir = Path(f"C:/cfmesh_poly_bench/sweep_{name}")
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
    write_meshdict(case_dir, max_cell_size=mc, min_cell_size=mi,
                   surface_file="constant/triSurface/surface.stl", bl_params=BL,
                   patch_names=["wall", "inlet", "outlet"])

    lc = cfg.wsl_linux_case_path(case_dir)
    r = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {lc} && cartesianMesh 2>&1 | tail -10"),
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        print(f"{name:>8} {'cartesian':>10} FAILED"); continue
    hm = _checkmesh(case_dir)

    for cname, cparams in CONFIGS:
        # Clone mesh dir for each config
        sub_case = Path(f"C:/cfmesh_poly_bench/sweep_{name}_{cname}")
        shutil.rmtree(sub_case, ignore_errors=True)
        # Copy the full cartesianMesh result
        subprocess.run(cfg._build_wsl_cmd(f"cp -r {lc}/constant {cfg.wsl_linux_case_path(sub_case)}/ && cp -r {lc}/system {cfg.wsl_linux_case_path(sub_case)}/"),
                       capture_output=True, timeout=60)

        cmd = cfg.build_poly_dual_cmd(
            sub_case,
            feature_angle=cparams["feature_angle"],
            concave_multi=cparams["concave_multi"],
            split_all_faces=cparams["split_all"],
        )
        rp = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        pm = _checkmesh(sub_case)
        red = round((1 - pm['cells']/hm['cells'])*100, 1) if hm['cells'] else 0
        print(f"{name:>8} {cname:>10} {pm['cells']:>8} {red:>6.1f} {pm['skew']:>8.4f} {pm['no_max']:>8.2f} {pm['no_avg']:>8.2f} {pm['ar']:>8.2f} {str(pm['passed']):>6}")

print("\nDone.")
