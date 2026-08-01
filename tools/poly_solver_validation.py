"""Fase 2 — solver validation: does the dual poly mesh actually compute?

Runs potentialFoam (potential flow, the cheapest real solver) on the SAME
block-with-bore geometry meshed two ways — the original tetrahedral mesh and
its barycentric-dual polyhedral mesh — with identical boundary conditions,
and compares the physical results:

  - total volume (must be identical)
  - volumetric flow rate through the bore (must match within a few %)
  - max |U| and the pressure field stats

checkMesh: Mesh OK is geometry; this is the evidence that the conversion is
conservative in practice, not just topologically valid.

    python tools/poly_solver_validation.py
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from cfmesh_autogui.config import OFConfig  # noqa: E402

REF_TET = Path("C:/polybench/ref1/constant/polyMesh_tet_backup")
WORK = Path("C:/polybench2/solver_ref1")

INLET = "surface_1"
OUTLET = "surface_6"
WALLS = ["surface_2", "surface_3", "surface_4", "surface_5", "surface_7"]


def _wsl(cfg: OFConfig, bash: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        cfg._build_wsl_cmd(bash), capture_output=True, text=True, timeout=timeout,
    )


def write_case(case: Path, mesh_src: Path) -> None:
    if case.exists():
        shutil.rmtree(case)
    poly = case / "constant" / "polyMesh"
    sysd = case / "system"
    zero = case / "0"
    poly.mkdir(parents=True)
    sysd.mkdir(parents=True)
    zero.mkdir(parents=True)
    for f in ("points", "faces", "owner", "neighbour", "boundary"):
        shutil.copy2(mesh_src / f, poly / f)

    (sysd / "controlDict").write_text(
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        "    class       dictionary;\n    object      controlDict;\n}\n"
        "application     potentialFoam;\n"
        "startFrom       startTime;\nstartTime       0;\n"
        "stopAt          endTime;\nendTime         1;\ndeltaT          1;\n"
        "writeControl    timeStep;\nwriteInterval   1;\npurgeWrite      0;\n"
        "writeFormat     ascii;\nwritePrecision   8;\n"
        "timeFormat      general;\ntimePrecision   6;\n"
        "runTimeModifiable true;\n", encoding="ascii")

    (sysd / "fvSchemes").write_text(
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        "    class       dictionary;\n    object      fvSchemes;\n}\n"
        "ddtSchemes     { default steadyState; }\n"
        "gradSchemes    { default Gauss linear; }\n"
        "divSchemes     { default Gauss linear; }\n"
        "laplacianSchemes { default Gauss linear corrected; }\n"
        "interpolationSchemes { default linear; }\n"
        "snGradSchemes  { default corrected; }\n", encoding="ascii")

    (sysd / "fvSolution").write_text(
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        "    class       dictionary;\n    object      fvSolution;\n}\n"
        "solvers\n{\n    Phi\n    {\n        solver          GAMG;\n"
        "        smoother        DIC;\n        tolerance       1e-06;\n"
        "        relTol          0.01;\n    }\n    p\n    {\n        $Phi;\n    }\n}\n"
        "potentialFlow\n{\n    nNonOrthogonalCorrectors 3;\n}\n", encoding="ascii")

    walls = "\n".join(f"        {w}\n        {{\n            type            zeroGradient;\n        }}" for w in WALLS)
    (zero / "p").write_text(
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        "    class       volScalarField;\n    object      p;\n}\n"
        "dimensions      [0 2 -2 0 0 0 0];\ninternalField   uniform 0;\n"
        "boundaryField\n{\n"
        f"        {INLET}\n        {{\n            type            zeroGradient;\n        }}\n"
        f"        {OUTLET}\n        {{\n            type            fixedValue;\n            value           uniform 0;\n        }}\n"
        f"{walls}\n}}\n", encoding="ascii")

    (zero / "U").write_text(
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        "    class       volVectorField;\n    object      U;\n}\n"
        "dimensions      [0 1 -1 0 0 0 0];\ninternalField   uniform (0 0 0);\n"
        "boundaryField\n{\n"
        f"        {INLET}\n        {{\n            type            uniformFixedValue;\n            uniformValue    constant (1 0 0);\n        }}\n"
        f"        {OUTLET}\n        {{\n            type            zeroGradient;\n        }}\n"
        f"{walls}\n}}\n", encoding="ascii")

    (zero / "phi").write_text(
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        "    class       surfaceScalarField;\n    object      phi;\n}\n"
        "dimensions      [0 3 -1 0 0 0 0];\ninternalField   uniform 0;\n"
        "boundaryField\n{\n"
        f"        {INLET}\n        {{\n            type            calculated;\n            value           uniform 0;\n        }}\n"
        f"        {OUTLET}\n        {{\n            type            calculated;\n            value           uniform 0;\n        }}\n"
        f"{walls}\n}}\n", encoding="ascii")


def run_potential(cfg: OFConfig, case: Path) -> dict:
    env_q = shlex.quote(cfg.env_script)
    lc = cfg._quoted_linux_path(case)
    r = _wsl(cfg, f"source {env_q} 2>/dev/null; cd {lc} && potentialFoam 2>&1 | tail -25")
    out = r.stdout + r.stderr
    ok = "End" in out and "FOAM FATAL" not in out
    # flow rate through the bore: sum of phi on the inlet patch (from the
    # written 1/phi file) — or parse from the log if printed.
    flow = None
    m = re.search(r"flow rate.*?([\d.eE+-]+)", out, re.I)
    if m:
        flow = float(m.group(1))
    return {"ok": ok, "log_tail": out[-1500:], "flow": flow}


def read_phi_flow(case: Path) -> float | None:
    """Sum of phi over the inlet patch from the written 1/phi file."""
    phi_file = case / "1" / "phi"
    if not phi_file.exists():
        return None
    text = phi_file.read_text(encoding="ascii", errors="replace")
    m = re.search(rf"{INLET}\s*\{{([^}}]*)\}}", text)
    if not m:
        return None
    body = m.group(1)
    vm = re.search(r"value\s+uniform\s+([\d.eE+-]+)", body)
    if vm:
        return float(vm.group(1))
    # list of values: sum them
    lm = re.search(r"value\s+nonuniform\s+List<scalar>\s*\n(\d+)\s*\(\s*([\s\S]*?)\s*\)", body)
    if lm:
        vals = [float(x) for x in lm.group(2).split()]
        return sum(vals)
    return None


def main() -> None:
    cfg = OFConfig()
    WORK.mkdir(parents=True, exist_ok=True)

    # tet case
    tet_case = WORK / "tet"
    write_case(tet_case, REF_TET)
    # poly case: convert the same tet mesh with the dual
    from cfmesh_autogui.core.tet_poly_dual import TetPolyDualConverter
    poly_case = WORK / "poly"
    write_case(poly_case, REF_TET)
    conv = TetPolyDualConverter(poly_case, log=lambda m: None).run()
    if not conv.success:
        raise SystemExit(f"dual conversion failed: {conv.errors}")

    print("=== potentialFoam on TET mesh ===")
    rt = run_potential(cfg, tet_case)
    print("ok:", rt["ok"])
    print("=== potentialFoam on POLY mesh ===")
    rp = run_potential(cfg, poly_case)
    print("ok:", rp["ok"])

    # full comparison of the solved fields
    from cfmesh_autogui.core.foam_mesh_io import read_polymesh

    def analyze(case: Path) -> dict:
        pts, faces, own, nei, pat = read_polymesh(case / "constant" / "polyMesh")
        n_int = len(nei)
        U = _read_U(case / "0" / "U")
        n_cells = int(max(own.max(), nei.max())) + 1
        sf = np.zeros((len(faces), 3))
        for i, f in enumerate(faces):
            p = pts[f]
            c0 = p.mean(0)
            a = p - c0
            b = np.roll(p, -1, axis=0) - c0
            sf[i] = 0.5 * np.cross(a, b).sum(0)
        cf = np.array([pts[f].mean(0) for f in faces])
        vol = np.zeros(n_cells)
        np.add.at(vol, own, (cf * sf).sum(1) / 3.0)
        np.add.at(vol, nei[:n_int], -(cf[:n_int] * sf[:n_int]).sum(1) / 3.0)
        inlet = next(p for p in pat if p["name"] == INLET)
        s, k = inlet["startFace"], inlet["nFaces"]
        flow = sum(float(U[int(own[i])] @ sf[i]) for i in range(s, s + k))
        magU = np.linalg.norm(U, axis=1)
        return {
            "cells": n_cells,
            "volume": float(vol.sum()),
            "flow_inlet": float(flow),
            "mean_U": float(magU.mean()),
            "max_U": float(magU.max()),
            "U_energy": float((magU ** 2).sum() * vol.sum() / n_cells),
        }

    tet = analyze(tet_case)
    poly = analyze(poly_case)
    print("\n=== COMPARISON (tet vs poly, same geometry, same BCs) ===")
    print(f"{'metric':<14}{'tet':>16}{'poly':>16}{'diff%':>10}")
    for k in ("cells", "volume", "flow_inlet", "mean_U", "max_U", "U_energy"):
        a, b = tet[k], poly[k]
        d = abs(a - b) / max(abs(a), 1e-300) * 100
        print(f"{k:<14}{a:>16.6g}{b:>16.6g}{d:>9.2f}%")


def _read_U(path: Path) -> np.ndarray:
    text = path.read_text(encoding="ascii", errors="replace")
    lines = text.splitlines()
    i = 0
    while i < len(lines) and "internalField" not in lines[i]:
        i += 1
    i += 1
    while i < len(lines) and not lines[i].strip().isdigit():
        i += 1
    cnt = int(lines[i].strip())
    i += 1
    while i < len(lines) and lines[i].strip() != "(":
        i += 1
    i += 1
    vals = []
    while i < len(lines) and lines[i].strip() != ")":
        toks = lines[i].replace("(", " ").replace(")", " ").split()
        if toks:
            vals.append([float(t) for t in toks])
        i += 1
    return np.array(vals)


if __name__ == "__main__":
    main()