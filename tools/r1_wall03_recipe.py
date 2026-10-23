#!/usr/bin/env python3
"""R1 wall03-recipe attempt (W18): saved medium fTet tets -> collapsed dual
+ split_boundary_defects -> BL 5L -> checkMesh. The production-default dual
rejected the boundary as non-manifold; the wall03 recipe is the documented
lever for exactly this defect class. Result decides R1: numbers or closed.
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

WORK = Path(r"C:\polyfoammesh_r1w03")
NPZ = Path(r"C:\polyfoammesh_r1\r1_tet.npz")
OUT = WORK / "r1_w03_result.json"

out: dict = {"stages": {}, "npz": str(NPZ)}


def stage(name: str):
    print(f"[r1w03] stage: {name}", flush=True)
    out["stages"][name] = {"t0": time.monotonic()}


def done(name: str, **kw):
    out["stages"][name].update(kw)
    out["stages"][name]["seconds"] = round(time.monotonic() - out["stages"][name].pop("t0"), 1)
    print(f"   -> {name}: {kw} ({out['stages'][name]['seconds']}s)", flush=True)


def save():
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")


def main() -> int:
    import numpy as np

    from polyfoammesh.core import ftetwild_wrapper as ftw
    from polyfoammesh.core.case_setup import write_control_dict
    from polyfoammesh.core.mesh_converter import msh_to_of_polymesh
    from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter

    WORK.mkdir(parents=True, exist_ok=True)
    try:
        stage("load_convert")
        d = np.load(NPZ)
        V, T = np.asarray(d["V"]), np.asarray(d["T"])
        out["n_tets"] = int(len(T))
        faces, owner = ftw.boundary_faces(V, T)
        faces = ftw.orient_faces_outward(V, T, faces, owner)
        msh = WORK / "r1.msh"
        ftw.write_msh22(msh, V, T, np.asarray(faces), np.ones(len(faces), dtype=np.int64), ["wall"])
        case = WORK / "case"
        if case.exists():
            shutil.rmtree(case)
        msh_to_of_polymesh(msh, case)
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
        done("load_convert", tets=int(len(T)))

        stage("dual_wall03")
        res = TetPolyDualConverter(
            case, log=lambda m: None,
            collapse_smooth_edges=True,
            boundary_feature_angle=40.0,
            collapse_volume_tolerance=0.10,
            smooth_free_boundary=True,
            split_boundary_defects=True,
        ).run()
        out["dual"] = {
            "success": bool(res.success),
            "n_cells_after": int(res.n_cells_after),
            "residual_defects": int(res.residual_defects),
            "breakdown": {k: int(v) for k, v in res.defect_breakdown.items()},
            "min_cell_volume": float(res.min_cell_volume),
            "errors": list(res.errors),
        }
        done("dual_wall03", **{k: out["dual"][k] for k in ("success", "n_cells_after", "residual_defects")})
        if not res.success:
            raise RuntimeError(f"dual failed: {res.errors}")

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
        from polyfoammesh.config import OFConfig

        cmd = OFConfig().build_check_mesh_cmd(case)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        text = (proc.stdout or "") + (proc.stderr or "")
        verdict = "Mesh OK" if "Mesh OK" in text else ("Failed" if "Failed" in text else f"rc={proc.returncode}")
        (WORK / "checkmesh_r1w03.log").write_text(text[-30000:], encoding="utf-8", errors="replace")
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
