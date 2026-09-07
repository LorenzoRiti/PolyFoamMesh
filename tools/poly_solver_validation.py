"""Fase 2 — solver validation: does the dual poly mesh actually compute?

Runs potentialFoam (potential flow, the cheapest real solver) on the SAME
geometry meshed two ways — the original tetrahedral mesh and its
barycentric-dual polyhedral mesh — with identical boundary conditions,
and compares the physical results:

  - total volume (must be identical)
  - volumetric flow rate through the bore (must match within a few %)
  - max |U| and the pressure field stats

checkMesh: Mesh OK is geometry; this is the evidence that the conversion is
conservative in practice, not just topologically valid.

    python tools/poly_solver_validation.py                    # ref1 (default)
    python tools/poly_solver_validation.py --case valve1      # valve
    python tools/poly_solver_validation.py --case valve1 --variant production
        # production = converter params used by the GUI runner
        # (median_faces=True, wedge_cells=True); default variant uses the
        # converter defaults that pin the valve baseline (852 pyramids)
"""
from __future__ import annotations

import argparse
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

# case -> (tet backup, work dir, inlet, outlet, walls)
CONFIGS = {
    "ref1": {
        "tet": Path("C:/polybench/ref1/constant/polyMesh_tet_backup"),
        "work": Path("C:/polybench2/solver_ref1"),
        "inlet": "surface_1",
        "outlet": "surface_6",
        "walls": ["surface_2", "surface_3", "surface_4", "surface_5", "surface_7"],
    },
    # valve: flat end caps at the two extremes of the main axis
    # (surface_77 at x=+1.5227, surface_89 at x=-1.4773 — measured from the
    # tet backup; everything else is a wall).
    "valve1": {
        "tet": Path("C:/polybench/valve1/constant/polyMesh_tet_backup"),
        "work": Path("C:/polybench2/solver_valve1"),
        "inlet": "surface_77",
        "outlet": "surface_89",
        "walls": None,  # computed at runtime: every patch except inlet/outlet
    },
}

# production converter params (GUI runner, openfoam_runner.py:1134-1140)
PRODUCTION_PARAMS = {"median_faces": True, "wedge_cells": True}


def _wsl(cfg: OFConfig, bash: str, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        cfg._build_wsl_cmd(bash), capture_output=True, text=True, timeout=timeout,
    )


def write_case(case: Path, mesh_src: Path, inlet: str, outlet: str,
               walls: list[str]) -> None:
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

    wall_lines = "\n".join(
        f"        {w}\n        {{\n            type            zeroGradient;\n        }}"
        for w in walls
    )
    (zero / "p").write_text(
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        "    class       volScalarField;\n    object      p;\n}\n"
        "dimensions      [0 2 -2 0 0 0 0];\ninternalField   uniform 0;\n"
        "boundaryField\n{\n"
        f"        {inlet}\n        {{\n            type            zeroGradient;\n        }}\n"
        f"        {outlet}\n        {{\n            type            fixedValue;\n            value           uniform 0;\n        }}\n"
        f"{wall_lines}\n}}\n", encoding="ascii")

    (zero / "U").write_text(
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        "    class       volVectorField;\n    object      U;\n}\n"
        "dimensions      [0 1 -1 0 0 0 0];\ninternalField   uniform (0 0 0);\n"
        "boundaryField\n{\n"
        f"        {inlet}\n        {{\n            type            uniformFixedValue;\n            uniformValue    constant (1 0 0);\n        }}\n"
        f"        {outlet}\n        {{\n            type            zeroGradient;\n        }}\n"
        f"{wall_lines}\n}}\n", encoding="ascii")


def run_potential(cfg: OFConfig, case: Path) -> dict:
    env_q = shlex.quote(cfg.env_script)
    lc = cfg._quoted_linux_path(case)
    logfile = case / "log.potential"
    # full log to a file (for residual parsing) + a compact tail on stdout
    r = _wsl(
        cfg,
        f"source {env_q} 2>/dev/null; cd {lc} && "
        f"potentialFoam -writePhi > log.potential 2>&1; "
        f"echo 'RC=$?'; tail -30 log.potential",
    )
    out = r.stdout + r.stderr
    ok = "End" in out and "FOAM FATAL" not in out
    log = ""
    if logfile.exists():
        log = logfile.read_text(encoding="utf-8", errors="replace")

    # residuals per GAMG solve line, last occurrence per field; the fields
    # are Phi (potential) — U is reconstructed, not solved, in potentialFoam.
    resid = {}
    for field in ("Phi", "p"):
        pat = re.compile(
            rf"{field}.*?Initial residual = ([\d.eE+-]+), "
            rf"Final residual = ([\d.eE+-]+), No Iterations (\d+)"
        )
        ms = pat.findall(log)
        if ms:
            i0, i1, it = ms[-1]
            resid[field] = {"initial": float(i0), "final": float(i1),
                            "iters": int(it)}
    # totals
    it_tot = sum(v["iters"] for v in resid.values())
    cont = None
    m = re.search(r"Continuity error = ([\d.eE+-]+)", log)
    if m:
        cont = float(m.group(1))
    uerr = None
    m = re.search(r"Interpolated velocity error = ([\d.eE+-]+)", log)
    if m:
        uerr = float(m.group(1))
    return {
        "ok": ok,
        "log_tail": out[-1500:],
        "residuals": resid,
        "total_iters": it_tot,
        "continuity_error": cont,
        "velocity_error": uerr,
        "run_s": float(re.search(r"ExecutionTime = ([\d.]+) s", log).group(1))
        if re.search(r"ExecutionTime = ([\d.]+) s", log) else None,
    }


def read_phi_flows(case: Path, inlet: str, outlet: str) -> dict:
    """Sum of phi over the inlet and outlet patches from the written 1/phi
    file — the SOLVED fluxes, i.e. the conservation evidence."""
    phi_file = case / "1" / "phi"
    if not phi_file.exists():
        return {}
    text = phi_file.read_text(encoding="ascii", errors="replace")

    def patch_flux(name: str) -> float | None:
        m = re.search(rf"{name}\s*\{{([^}}]*)\}}", text)
        if not m:
            return None
        body = m.group(1)
        vm = re.search(r"value\s+uniform\s+([\d.eE+-]+)", body)
        if vm:
            return float(vm.group(1))
        lm = re.search(
            r"value\s+nonuniform\s+List<scalar>\s*\n(\d+)\s*\(\s*([\s\S]*?)\s*\)",
            body,
        )
        if lm:
            vals = [float(x) for x in lm.group(2).split()]
            return sum(vals)
        return None

    return {"inlet": patch_flux(inlet), "outlet": patch_flux(outlet)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", choices=sorted(CONFIGS), default="ref1")
    ap.add_argument("--variant", choices=["default", "production"], default="default")
    args = ap.parse_args()

    cfg_c = CONFIGS[args.case]
    tet_src = cfg_c["tet"]
    WORK = cfg_c["work"]
    INLET, OUTLET = cfg_c["inlet"], cfg_c["outlet"]
    walls = cfg_c["walls"] or []
    if not tet_src.exists():
        print(f"NO TET BACKUP: {tet_src}")
        return 2

    # walls = every patch except inlet/outlet (computed for the valve)
    if not walls:
        from cfmesh_autogui.core import foam_mesh_io as fio

        _, _, _, _, patches = fio.read_polymesh(tet_src)
        walls = [p["name"] for p in patches
                 if p["name"] not in (INLET, OUTLET)]

    cfg = OFConfig()
    WORK.mkdir(parents=True, exist_ok=True)

    tet_case = WORK / "tet"
    write_case(tet_case, tet_src, INLET, OUTLET, walls)
    from cfmesh_autogui.core.tet_poly_dual import TetPolyDualConverter
    poly_case = WORK / "poly"
    write_case(poly_case, tet_src, INLET, OUTLET, walls)
    conv_kw = PRODUCTION_PARAMS if args.variant == "production" else {}
    conv = TetPolyDualConverter(poly_case, log=lambda m: None, **conv_kw).run()
    if not conv.success:
        raise SystemExit(f"dual conversion failed: {conv.errors}")

    print(f"=== potentialFoam on TET mesh ({args.case}, {args.variant}) ===")
    rt = run_potential(cfg, tet_case)
    print("ok:", rt["ok"], "| total GAMG iters:", rt["total_iters"],
          "| continuity err:", rt["continuity_error"],
          "| vel err:", rt["velocity_error"], "| run_s:", rt["run_s"])
    print("residuals:", rt["residuals"])
    print("=== potentialFoam on POLY mesh ===")
    rp = run_potential(cfg, poly_case)
    print("ok:", rp["ok"], "| total GAMG iters:", rp["total_iters"],
          "| continuity err:", rp["continuity_error"],
          "| vel err:", rp["velocity_error"], "| run_s:", rp["run_s"])
    print("residuals:", rp["residuals"])

    # full comparison of the solved fields
    from cfmesh_autogui.core.foam_mesh_io import read_polymesh

    def analyze(case: Path) -> dict:
        pts, faces, own, nei, pat = read_polymesh(case / "constant" / "polyMesh")
        n_int = len(nei)
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
        # prescribed inlet flux (U=(1,0,0) at the inlet cap): sum of the
        # x-component of the inlet face area vectors — measures whether the
        # dual boundary reproduces the primal inlet area (exact subdivision)
        flow = float(np.abs(sf[s:s + k, 0]).sum())
        return {
            "cells": n_cells,
            "volume": float(vol.sum()),
            "flow_inlet": flow,
        }

    tet = analyze(tet_case)
    poly = analyze(poly_case)

    # solved fluxes (conservation evidence)
    ft = read_phi_flows(tet_case, INLET, OUTLET)
    fp = read_phi_flows(poly_case, INLET, OUTLET)
    print("\n=== SOLVED PHI FLUX (m^3/s) ===")
    print(f"{'':<10}{'tet':>16}{'poly':>16}")
    for k in ("inlet", "outlet"):
        a, b = ft.get(k), fp.get(k)
        if a is None or b is None:
            print(f"{k:<10}{str(a):>16}{str(b):>16}")
            continue
        d = abs(a - b) / max(abs(a), 1e-300) * 100
        print(f"{k:<10}{a:>16.6g}{b:>16.6g}   diff {d:.2f}%")
    if ft.get("inlet") and ft.get("outlet"):
        bal = 100.0 * (1.0 - ft["outlet"] / ft["inlet"])
        print(f"tet  mass balance (in vs out): {bal:+.3f}%")
    if fp.get("inlet") and fp.get("outlet"):
        bal = 100.0 * (1.0 - fp["outlet"] / fp["inlet"])
        print(f"poly mass balance (in vs out): {bal:+.3f}%")

    print("\n=== COMPARISON (tet vs poly, same geometry, same BCs) ===")
    print(f"{'metric':<14}{'tet':>16}{'poly':>16}{'diff%':>10}")
    for k in ("cells", "volume", "flow_inlet"):
        a, b = tet[k], poly[k]
        d = abs(a - b) / max(abs(a), 1e-300) * 100
        print(f"{k:<14}{a:>16.6g}{b:>16.6g}{d:>9.2f}%")
    return 0


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
    sys.exit(main())
