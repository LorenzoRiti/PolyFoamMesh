#!/usr/bin/env python3
"""R1 GMSH-tet completion (W18 §3.4): convert -> dual -> BL 5L -> checkMesh.

Tet mesh: C:\\polyfoammesh_r1\\probe.msh (GMSH volume on Parte4.step,
medium detail: 180,427 nodes / 361,421 tets, 89 patches — measured).
Writes r1_gmsh_result.json. Fail-fast with partial JSON saved.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORK = Path(r"C:\polyfoammesh_r1gmsh")
MSH = WORK / "r1_gmsh.msh"
STEP = Path(sys.argv[sys.argv.index("--step") + 1]) if "--step" in sys.argv else Path(r"C:\Users\Davide Valoroso\Desktop\Report\Parte4.step")
OUT = WORK / "r1_gmsh_result.json"
DETAIL = sys.argv[sys.argv.index("--detail") + 1] if "--detail" in sys.argv else "medium"

out: dict = {"tet_msh": str(MSH), "stages": {}}


def stage(name: str):
    print(f"[r1gmsh] stage: {name}", flush=True)
    out["stages"][name] = {"t0": time.monotonic()}


def done(name: str, **kw):
    out["stages"][name].update(kw)
    out["stages"][name]["seconds"] = round(time.monotonic() - out["stages"][name].pop("t0"), 1)
    print(f"   -> {name}: {kw} ({out['stages'][name]['seconds']}s)", flush=True)


def save():
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")


def main() -> int:
    from polyfoammesh.config import OFConfig
    from polyfoammesh.core.case_setup import write_control_dict
    from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter

    WORK.mkdir(parents=True, exist_ok=True)
    try:
        from polyfoammesh.core.gmsh_wrapper import generate_volume_mesh

        stage("tet_gmsh")
        # Fresh process = clean OCC kernel (the table driver's 15th
        # in-process GMSH call failed on this STEP; direct probe works).
        _mpath, _names = generate_volume_mesh(STEP, MSH, detail=DETAIL)
        out["tet_detail"] = DETAIL

        out["tet"] = {"msh": str(MSH), "bytes": MSH.stat().st_size,
                      "patches": len(_names)}
        done("tet_gmsh", patches=len(_names))
        stage("convert")
        case = WORK / "case"
        if case.exists():
            shutil.rmtree(case)
        # Real GUI path: raw GMSH .msh -> OpenFOAM's gmshToFoam via WSL
        # (msh_to_of_polymesh only parses our own msh22 writer output).
        # gmshToFoam reads system/controlDict, so the skeleton goes FIRST.
        import subprocess as _sp

        case.mkdir(parents=True, exist_ok=True)
        shutil.copy(MSH, case / "mesh.msh")
        write_control_dict(case)
        (case / "system" / "fvSchemes").write_text(
            "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
            "ddtSchemes { default steadyState; }\n"
            "gradSchemes { default Gauss linear; }\n"
            "divSchemes { default Gauss linear; }\n"
            "laplacianSchemes { default Gauss linear corrected; }\n"
            "interpolationSchemes { default linear; }\n"
            "snGradSchemes { default corrected; }\n",
            encoding="ascii",
        )
        (case / "system" / "fvSolution").write_text(
            "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
            "solvers { p { solver PCG; preconditioner DIC; tolerance 1e-6; relTol 0.1; } }\n",
            encoding="ascii",
        )
        conv = _sp.run(
            [sys.executable, str(ROOT / "src" / "polyfoammesh" / "core" / "gmsh_wrapper.py"),
             "convert_to_foam", str(case), "mesh.msh"],
            capture_output=True, text=True, timeout=900,
        )
        try:
            cres = json.loads(conv.stdout.strip().splitlines()[-1])
        except Exception:
            cres = {"success": False,
                    "raw": (conv.stdout or "")[-500:] + (conv.stderr or "")[-500:]}
        # gmshToFoam can exit 0 while writing nothing (e.g. 2D-only MSH:
        # "No cells read" is FATAL but rc stays 0) — verify the artifact.
        _pts = case / "constant" / "polyMesh" / "points"
        if not cres.get("success") or not _pts.is_file() or _pts.stat().st_size == 0:
            (WORK / "gmshToFoam_tail.log").write_text(
                (conv.stdout or "")[-4000:] + (conv.stderr or "")[-2000:],
                encoding="utf-8", errors="replace")
            raise RuntimeError(
                f"gmshToFoam produced no polyMesh (rc={conv.returncode}, "
                f"points exists={_pts.is_file()}); tail in gmshToFoam_tail.log")
        done("convert")

        stage("dual")
        dres = TetPolyDualConverter(
            case, log=lambda m: None,
            collapse_smooth_edges=True,
            boundary_feature_angle=40.0,
            collapse_volume_tolerance=0.10,
            smooth_free_boundary=False,
        ).run()
        out["dual"] = {
            "success": bool(dres.success),
            "n_cells_after": int(dres.n_cells_after),
            "n_poly_cells": int(dres.n_poly_cells),
            "residual_defects": int(dres.residual_defects),
            "breakdown": {k: int(v) for k, v in dres.defect_breakdown.items()},
            "min_cell_volume": float(dres.min_cell_volume),
            "vol_err": float(abs(dres.volume_after - dres.volume_before) / max(dres.volume_before, 1e-300)),
            "errors": list(dres.errors),
        }
        done("dual", **{k: out["dual"][k] for k in ("success", "n_cells_after", "residual_defects")})
        if not dres.success:
            raise RuntimeError(f"dual failed: {dres.errors}")

        stage("bl_5layer")
        from polyfoammesh.core.bl_config import BLConfig
        from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine

        cfg, _w = BLConfig.from_mapping_tolerant({
            "nLayers": 5, "thicknessRatio": 1.2, "firstLayerThickness": 0.00126,
            "applyToAll": False, "minBaseEdgeFraction": 0.9,
            "terminateByBudget": True,
        }, max_cell=None)
        r = PolyBoundaryLayerEngine(case, log=lambda m: None).run(**cfg.engine_kwargs(None))
        brep = r.bl_report if isinstance(getattr(r, "bl_report", None), dict) else {}
        out["bl"] = {
            "success": bool(r.success),
            "prisms": int(getattr(r, "n_prism_cells", getattr(r, "bl_prism_cells", 0))),
            "coverage": brep.get("coverage"),
            "errors": list(getattr(r, "errors", [])),
        }
        done("bl_5layer", success=out["bl"]["success"], prisms=out["bl"]["prisms"])
        if not r.success:
            raise RuntimeError(f"BL failed: {r.errors}")

        stage("checkmesh")
        cmd = OFConfig().build_check_mesh_cmd(case)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        text = (proc.stdout or "") + (proc.stderr or "")
        verdict = "Mesh OK" if "Mesh OK" in text else ("Failed" if "Failed" in text else f"rc={proc.returncode}")
        (WORK / "checkmesh_r1gmsh.log").write_text(text[-30000:], encoding="utf-8", errors="replace")
        import re

        m = re.search(r"non-orthogonality Max:\s*([\d.]+)", text)
        s = re.search(r"Max skewness\s*=\s*([\d.]+)", text)
        f = re.search(r"Failed (\d+) mesh checks", text)
        p = re.search(r"(\d+) +incorrectly oriented", text)
        out["checkmesh"] = {
            "rc": int(proc.returncode),
            "verdict": verdict,
            "nomax": float(m.group(1)) if m else None,
            "skewmax": float(s.group(1)) if s else None,
            "failed_checks": int(f.group(1)) if f else (0 if verdict == "Mesh OK" else None),
            "bad_pyramids": int(p.group(1)) if p else 0,
        }
        done("checkmesh", **out["checkmesh"])
        out["status"] = "COMPLETE"
    except Exception as exc:  # noqa: BLE001
        out["status"] = f"STOPPED: {type(exc).__name__}: {exc}"
        print(out["status"], flush=True)
    save()
    print(json.dumps({k: v for k, v in out.items() if k != "stages"}, indent=1), flush=True)
    return 0 if out.get("status") == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
