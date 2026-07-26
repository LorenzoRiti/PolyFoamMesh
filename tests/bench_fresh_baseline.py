"""Fresh baseline for polyhedral quality improvements."""
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

def _setup_case(case_dir, stl, mc, mi):
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

def _checkmesh(case_dir):
    lc = cfg.wsl_linux_case_path(case_dir)
    r = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {lc} && checkMesh 2>&1"),
                       capture_output=True, text=True, timeout=120)
    t = r.stdout
    return {
        "cells": int(_val(r"cells:\s+(\d+)", t) or 0),
        "skew": _val(r"Max skewness = ([\d.]+)", t),
        "non_ortho_max": _val(r"non-orthogonality Max: ([\d.]+)", t),
        "non_ortho_avg": _val(r"non-orthogonality Max: [\d.]+\s+average:\s*([\d.]+)", t),
        "aspect_ratio": _val(r"Max aspect ratio = ([\d.]+)", t),
        "min_vol": _val(r"Min volume = ([\d.eE+-]+)", t),
        "passed": "Mesh OK." in t,
    }

print(f"{'Geometry':>12} {'Stage':>10} {'Cells':>8} {'Skew':>8} {'NOmax':>8} {'NOavg':>8} {'AspRa':>8} {'Pass':>6}")
print("-" * 72)

for name, stl, mc, mi in [
    ("venturi", Path("C:/cfmesh_poly_bench/venturi.stl"), 0.08, 0.02),
    ("s_bend", Path("C:/cfmesh_poly_bench/s_bend.stl"), 0.06, 0.015),
]:
    case_dir = Path(f"C:/cfmesh_poly_bench/fresh_{name}")
    _setup_case(case_dir, stl, mc, mi)
    lc = cfg.wsl_linux_case_path(case_dir)

    # cartesianMesh
    r = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {lc} && cartesianMesh 2>&1 | tail -10"),
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        print(f"{name:>12} {'cartesian':>10} {'FAIL':>8}")
        continue

    hm = _checkmesh(case_dir)
    print(f"{name:>12} {'hex':>10} {hm['cells']:>8} {hm['skew']:>8.4f} {hm['non_ortho_max']:>8.2f} {hm['non_ortho_avg']:>8.2f} {hm['aspect_ratio']:>8.2f} {str(hm['passed']):>6}")

    # polyDualMesh
    cmd = cfg.build_poly_dual_cmd(case_dir, feature_angle=30, concave_multi=True)
    rp = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    # Check if 0/polyMesh exists
    rd = subprocess.run(cfg._build_wsl_cmd(f"ls {lc}/0/polyMesh/ 2>/dev/null && echo Y || echo N"),
                        capture_output=True, text=True, timeout=30)
    has_zero = rd.stdout.strip() == "Y"

    pm = _checkmesh(case_dir)
    red = round((1 - pm['cells']/hm['cells'])*100, 1) if hm['cells'] else 0
    print(f"{name:>12} {'poly':>10} {pm['cells']:>8} {pm['skew']:>8.4f} {pm['non_ortho_max']:>8.2f} {pm['non_ortho_avg']:>8.2f} {pm['aspect_ratio']:>8.2f} {str(pm['passed']):>6}")
    print(f"{'':>12} {'':>10} {'red='+str(red)+'%':>8} {'0/poly='+('Y' if has_zero else 'N'):>16}")
    print()
