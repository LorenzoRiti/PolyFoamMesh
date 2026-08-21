"""P3a benchmark: cfMesh hex + BL -> polyDualMesh -> checkMesh.

Validates the "BL + poly" path on a real venturi:
1. cartesianMesh with boundary layers (8 layers, y+~30 style)
2. checkMesh on the hex+prism mesh (prisms must be present)
3. polyDualMesh (dual of the hex mesh)
4. checkMesh on the poly mesh (prisms preserved? Mesh OK?)

Reuses bench_quality_baseline.py's case-writing helpers.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from cfmesh_autogui.config import OFConfig  # noqa: E402
from cfmesh_autogui.core.meshdict_gen import write_meshdict  # noqa: E402

cfg = OFConfig()
env_q = __import__("shlex").quote(cfg.env_script)

BL = {
    "nLayers": 8,
    "thicknessRatio": 1.2,
    "firstLayerThickness": 0.001,
    "wallPatches": ["wall"],
}


def _num(pat, text):
    m = re.search(pat, text)
    if m:
        try:
            return float(m.group(1).rstrip("."))
        except ValueError:
            return 0.0
    return 0.0


def parse_checkmesh(text: str) -> dict:
    return {
        "cells": int(_num(r"cells:\s+(\d+)", text) or 0),
        "hexahedra": int(_num(r"hexahedra:\s+(\d+)", text)),
        "prisms": int(_num(r"prisms:\s+(\d+)", text)),
        "polyhedra": int(_num(r"polyhedra:\s+(\d+)", text)),
        "tetrahedra": int(_num(r"tetrahedra:\s+(\d+)", text)),
        "pyramids": int(_num(r"pyramids:\s+(\d+)", text)),
        "wedges": int(_num(r"wedges:\s+(\d+)", text)),
        "skew": _num(r"Max skewness = ([\d.]+)", text),
        "no_max": _num(r"non-orthogonality Max: ([\d.]+)", text),
        "no_avg": _num(r"non-orthogonality Max: [\d.]+\s+average:\s*([\d.]+)", text),
        "ar": _num(r"Max aspect ratio = ([\d.]+)", text),
        "passed": "Mesh OK." in text,
    }


def run(case_dir: Path, stl: Path, mc: float, mi: float,
        use_bc: bool = True) -> tuple[dict, dict] | None:
    shutil.rmtree(case_dir, ignore_errors=True)
    for d in ("constant/triSurface", "system"):
        (case_dir / d).mkdir(parents=True)
    shutil.copy2(stl, case_dir / "constant" / "triSurface" / "surface.stl")
    for f, c in [
        ("controlDict", "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\napplication cartesianMesh;\nstartFrom startTime; startTime 0; stopAt endTime; endTime 1000;\ndeltaT 1;\nwriteControl timeStep; writeInterval 1; purgeWrite 0;\nwriteFormat binary;\nwritePrecision 6; writeCompression on;\ntimeFormat general; timePrecision 6;\nrunTimeModifiable true;\n"),
        ("fvSchemes", "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\nddtSchemes { default steadyState; }\ngradSchemes { default Gauss linear; }\ndivSchemes { default Gauss linear; }\nlaplacianSchemes { default Gauss linear corrected; }\ninterpolationSchemes { default linear; }\nsnGradSchemes { default corrected; }\n"),
        ("fvSolution", "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\nsolvers { p { solver PCG; preconditioner DIC; tolerance 1e-6; relTol 0.1; } }\n"),
    ]:
        (case_dir / "system" / f).write_text(c, encoding="ascii")

    kw = dict(
        max_cell_size=mc,
        min_cell_size=mi,
        surface_file="constant/triSurface/surface.stl",
        bl_params=BL,
        patch_names=["wall", "inlet", "outlet"],
    )
    if use_bc:
        kw["boundary_cell_size"] = mi * 2.0
        kw["boundary_refinement_thickness"] = mc * 2.0
    write_meshdict(case_dir, **kw)

    lc = cfg.wsl_linux_case_path(case_dir)
    print("  cartesianMesh...", end=" ", flush=True)
    r = subprocess.run(
        cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {lc} && cartesianMesh 2>&1 | tail -10"),
        capture_output=True, text=True, timeout=1800,
    )
    if r.returncode != 0:
        print(f"FAILED: {r.stderr[:200]}")
        return None
    rh = subprocess.run(
        cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {lc} && checkMesh 2>&1"),
        capture_output=True, text=True, timeout=120,
    )
    hm = parse_checkmesh(rh.stdout)
    print(f"{hm['cells']} cells, hex={hm['hexahedra']}, prism={hm['prisms']}, "
          f"skew={hm['skew']:.4f}, NOmax={hm['no_max']:.2f}, pass={hm['passed']}")

    print("  polyDualMesh (FA=90)...", end=" ", flush=True)
    rp = subprocess.run(
        cfg.build_poly_dual_cmd(case_dir, feature_angle=90),
        capture_output=True, text=True, timeout=600,
    )
    rp2 = subprocess.run(
        cfg._build_wsl_cmd(f"source {env_q} 2>/dev/null; cd {lc} && checkMesh 2>&1"),
        capture_output=True, text=True, timeout=120,
    )
    pm = parse_checkmesh(rp2.stdout)
    print(f"{pm['cells']} cells, hex={pm['hexahedra']}, prism={pm['prisms']}, "
          f"poly={pm['polyhedra']}, skew={pm['skew']:.4f}, NOmax={pm['no_max']:.2f}, "
          f"pass={pm['passed']}")
    return hm, pm


if __name__ == "__main__":
    import argparse
    import os

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stl", default=os.environ.get("CFMESH_GEOM", "C:/cfmesh_poly_bench/venturi.stl"),
                    help="surface STL to mesh (default: $CFMESH_GEOM)")
    ap.add_argument("--work", default=os.environ.get("CFMESH_WORK", "C:/cfmesh_poly_bench"),
                    help="scratch dir for cases (default: $CFMESH_WORK)")
    args = ap.parse_args()

    stl = Path(args.stl)
    if not stl.exists():
        print(f"SKIP: geometry not found: {stl} (set CFMESH_GEOM)")
        sys.exit(0)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    for bc in (True, False):
        tag = "bc" if bc else "nobc"
        print(f"\n===== config: {tag} =====")
        out = run(work / f"p3a_venturi_{tag}", stl, 0.08, 0.02,
                  use_bc=bc)
        if out:
            hm, pm = out
            print("\nRESULT:")
            print(f"  hex+BL : cells={hm['cells']} prisms={hm['prisms']} pass={hm['passed']}")
            print(f"  poly   : cells={pm['cells']} prisms={pm['prisms']} "
                  f"poly={pm['polyhedra']} pass={pm['passed']}")
            ok = hm["passed"] and pm["passed"] and pm["prisms"] > 0 and pm["polyhedra"] > 0
            print(f"  P3a VERDICT ({tag}): "
                  f"{'PASS (prisms+poly through polyDualMesh, Mesh OK)' if ok else 'FAIL'}")
    print("P3a: no configuration produced prism+poly with Mesh OK")
    sys.exit(1)
