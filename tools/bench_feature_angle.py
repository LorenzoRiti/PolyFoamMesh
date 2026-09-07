"""Compare polyDualMesh quality across feature angles."""
from __future__ import annotations

import json, os, re, shlex, shutil, subprocess, sys, time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from polyfoammesh.config import OFConfig
from polyfoammesh.core.meshdict_gen import write_meshdict

cfg = OFConfig()
env_q = shlex.quote(cfg.env_script)

ANGLES = [10, 20, 30, 45, 60, 90, 120]
CASE_BASE = Path("C:/cfmesh_poly_bench/angle_test")
STL = Path("C:/cfmesh_poly_bench/venturi.stl")
def _val(p, t):
    m = re.search(p, t)
    if m:
        v = m.group(1).rstrip(".")
        try: return float(v)
        except: return 0.0
    return 0.0
BM = _val

os.makedirs("C:/cfmesh_poly_bench", exist_ok=True)

print(f"{'Angle':>6} {'Hex':>8} {'Poly':>8} {'Red%':>6} {'Skew':>8} {'NOmax':>8} {'NOavg':>8} {'AspRa':>8} {'Pass':>6}")
print("-" * 72)

for angle in ANGLES:
    case_dir = Path(f"{CASE_BASE}_{angle}")
    shutil.rmtree(case_dir, ignore_errors=True)
    for d in ["constant/triSurface", "system"]:
        (case_dir / d).mkdir(parents=True)
    shutil.copy2(STL, case_dir / "constant" / "triSurface" / "surface.stl")

    for fname, content in [
        ("controlDict", "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\napplication cartesianMesh;\nstartFrom startTime; startTime 0; stopAt endTime; endTime 1000;\ndeltaT 1;\nwriteControl timeStep; writeInterval 1; purgeWrite 0;\nwriteFormat binary;\nwritePrecision 6; writeCompression on;\ntimeFormat general; timePrecision 6;\nrunTimeModifiable true;\n"),
        ("fvSchemes", "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\nddtSchemes { default steadyState; }\ngradSchemes { default Gauss linear; }\ndivSchemes { default Gauss linear; }\nlaplacianSchemes { default Gauss linear corrected; }\ninterpolationSchemes { default linear; }\nsnGradSchemes { default corrected; }\n"),
        ("fvSolution", "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\nsolvers { p { solver PCG; preconditioner DIC; tolerance 1e-6; relTol 0.1; } }\n"),
    ]:
        (case_dir / "system" / fname).write_text(content, encoding="ascii")

    write_meshdict(case_dir, max_cell_size=0.08, min_cell_size=0.02,
                   surface_file="constant/triSurface/surface.stl",
                   bl_params={"nLayers": 4, "thicknessRatio": 1.2, "firstLayerThickness": 0.002, "wallPatches": ["wall"]},
                   patch_names=["wall", "inlet", "outlet"])

    lc = cfg.wsl_linux_case_path(case_dir)

    # cartesianMesh
    r = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {lc} && cartesianMesh 2>&1 | tail -10"),
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        print(f"{angle:>6} CARMESH FAIL")
        continue

    # hex checkMesh
    rh = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {lc} && checkMesh 2>&1"),
                        capture_output=True, text=True, timeout=120)
    hex_cells = int(BM(r"cells:\s+(\d+)", rh.stdout))
    hex_skew = BM(r"Max skewness\s*=\s*([\d.]+)", rh.stdout)
    hex_no = BM(r"non-orthogonality Max:\s*([\d.]+)", rh.stdout)

    # polyDualMesh
    rp = subprocess.run(cfg._build_wsl_cmd(
        f"source {env_q} 2>/dev/null; cd {lc} && polyDualMesh {angle} -overwrite 2>&1 | tail -10"),
        capture_output=True, text=True, timeout=300)
    if rp.returncode != 0:
        print(f"{angle:>6} POLY FAIL")
        continue

    # poly checkMesh
    rp2 = subprocess.run(cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {lc} && checkMesh 2>&1"),
                         capture_output=True, text=True, timeout=120)
    t = rp2.stdout
    cells = int(BM(r"cells:\s+(\d+)", t))
    skew = BM(r"Max skewness\s*=\s*([\d.]+)", t)
    no_max = BM(r"non-orthogonality Max:\s*([\d.]+)", t)
    no_avg = BM(r"non-orthogonality Max:\s*[\d.]+\s+average:\s*([\d.]+)", t)
    ar = BM(r"Max aspect ratio\s*=\s*([\d.]+)", t)
    mv = BM(r"Min volume = ([\d.eE+-]+)", t)
    passed = "Mesh OK." in t
    red = round((1 - cells / hex_cells) * 100, 1) if hex_cells else 0

    print(f"{angle:>6} {hex_cells:>8} {cells:>8} {red:>6.1f} {skew:>8.4f} {no_max:>8.2f} {no_avg:>8.2f} {ar:>8.2f} {str(passed):>6}")

print("\nDone.")
