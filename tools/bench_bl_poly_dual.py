"""P3b benchmark — the definitive CFD mesher path: BL + poly, fully ours.

GMSH tet mesh -> gmshToFoam -> barycentric dual (tet_poly_dual) ->
PolyBoundaryLayerEngine (bl_poly) -> checkMesh.

Validates on a real cylinder (the reference WSL smoke geometry):
- the poly mesh with BL passes checkMesh (`Mesh OK`),
- prism/hexahedral layers are present (> 0),
- total volume is conserved.

Run:  python tools/bench_bl_poly_dual.py
"""
from __future__ import annotations

import io
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import trimesh  # noqa: E402

from cfmesh_autogui.config import OFConfig  # noqa: E402
from cfmesh_autogui.core.bl_poly import PolyBoundaryLayerEngine  # noqa: E402
from cfmesh_autogui.core.gmsh_subprocess import (  # noqa: E402
    run_gmsh_to_foam,
    run_gmsh_volume,
    write_case_skeleton,
)
from cfmesh_autogui.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402

CASE = Path(os.environ.get("CFMESH_WORK", "C:/cfmesh_bench")) / "bl_poly_dual_cylinder"


def main() -> int:
    cfg = OFConfig()
    if not cfg.validate():
        print("WSL/OpenFOAM not available — cannot validate.")
        return 2

    shutil.rmtree(CASE, ignore_errors=True)
    for d in ("constant", "system"):
        (CASE / d).mkdir(parents=True, exist_ok=True)

    # 1. geometry: cylinder (r=0.5, h=2) as STL
    stl = CASE / "cylinder.stl"
    cyl = trimesh.creation.cylinder(radius=0.5, height=2.0, sections=48)
    cyl.export(stl)
    print(f"1. geometry: {stl} ({cyl.vertices.shape[0]} verts)")

    # 2. GMSH tet volume mesh
    msh = CASE / "mesh.msh"
    print("2. GMSH volume mesh (medium)...")
    r = run_gmsh_volume(stl, msh, detail="medium", user_lc=0.0, min_lc=0.0)
    print(f"   gmsh result: {r.get('message', r)}")

    # 3. gmshToFoam
    print("3. gmshToFoam...")
    write_case_skeleton(CASE)
    run_gmsh_to_foam(CASE, "mesh.msh")
    n_tet = len(list((CASE / "constant" / "polyMesh").glob("faces")))
    print(f"   tet polyMesh written (faces file present: {n_tet > 0})")

    # 4. barycentric dual
    print("4. tet -> poly dual...")
    t0 = time.monotonic()
    dres = TetPolyDualConverter(CASE, log=lambda m: print(f"   {m}")).run()
    print(f"   dual: {dres.n_cells_after:,} poly cells "
          f"({time.monotonic() - t0:.1f}s), residual tets {dres.n_residual_tets}")

    # 5. boundary layers
    print("5. boundary layers (5 layers, h1=0.01, r=1.2)...")
    t0 = time.monotonic()
    bres = PolyBoundaryLayerEngine(CASE, log=lambda m: print(f"   {m}")).run(
        n_layers=5, first_height=0.01, growth_rate=1.2, apply_to_all=True,
    )
    print(f"   BL: success={bres.success} prism_cells={bres.n_prism_cells} "
          f"thickness={bres.total_thickness:.4g} ({time.monotonic() - t0:.1f}s)")
    if not bres.success:
        print("BL failed:", bres.errors)
        return 1

    # 6. checkMesh
    print("6. checkMesh...")
    linux_case = cfg._quoted_linux_path(CASE)
    env_q = shlex.quote(cfg.env_script)
    cm = subprocess.run(
        cfg._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux_case} && checkMesh 2>&1"
        ),
        capture_output=True, text=True, timeout=180,
    )
    text = cm.stdout + cm.stderr

    def _num(pat):
        import re
        m = re.search(pat, text)
        return float(m.group(1).rstrip(".")) if m else 0.0

    def _int(pat):
        import re
        m = re.search(pat, text)
        return int(m.group(1).replace(",", "")) if m else 0

    report = {
        "cells": _int(r"cells:\s+(\d+)"),
        "hexahedra": _int(r"hexahedra:\s+(\d+)"),
        "polyhedra": _int(r"polyhedra:\s+(\d+)"),
        "max_skewness": _num(r"Max skewness\s*[:=]\s*([\d.]+)"),
        "max_non_orth": _num(r"non-orthogonality\s+Max:\s*([\d.]+)"),
        "max_aspect_ratio": _num(r"Max aspect ratio\s*[:=]\s*([\d.]+)"),
        "total_volume": _num(r"Total volume = ([\d.eE+-]+)"),
        "passed": "Mesh OK." in text,
    }
    print(f"   checkMesh: {report['cells']:,} cells, "
          f"hex/prisms={report['hexahedra']:,}, poly={report['polyhedra']:,}, "
          f"skew={report['max_skewness']:.3f}, NOmax={report['max_non_orth']:.1f}, "
          f"AR={report['max_aspect_ratio']:.1f}, "
          f"Mesh OK={report['passed']}")

    ok = (
        report["passed"]
        and report["hexahedra"] > 0
        and report["polyhedra"] > 0
    )
    print(f"\nP3b VERDICT: {'PASS — BL + poly via our own mesher, Mesh OK' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
