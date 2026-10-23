#!/usr/bin/env python3
"""R1 re-run from Parte4.step (M4 closure follow-up).

Reconstructs REGRESSION_CASES R1 (173334) with the recovered truth input
`Parte4.step` (newer revision than the lost mesh.msh): GMSH surface ->
fTetWild volume (eps 0.3 mm absolute, milestone-A envelope) -> convert ->
production dual -> 5-layer production BL -> real checkMesh (OF/WSL2).

Writes r1_result.json (numbers + timings + input hash + settings) into
the workdir and prints a stage table. Fail-fast: any stage raising stops
the run with the partial JSON saved.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

STEP = Path(r"C:\Users\Davide Valoroso\Desktop\Report\Parte4.step")
WORK = Path(sys.argv[sys.argv.index("--workdir") + 1]) if "--workdir" in sys.argv else Path(r"C:\polyfoammesh_r1")
DETAIL = sys.argv[sys.argv.index("--detail") + 1] if "--detail" in sys.argv else "medium"
TET_ONLY = "--tet-only" in sys.argv
REUSE_TET = "--reuse-tet" in sys.argv
STL_INPUT = Path(sys.argv[sys.argv.index("--stl-input") + 1]) if "--stl-input" in sys.argv else None

BL_PARAMS = {
    "nLayers": 5,
    "thicknessRatio": 1.2,
    "firstLayerThickness": 0.00126,
    "applyToAll": False,
    "minBaseEdgeFraction": 0.9,
    "terminateByBudget": True,
}

FTET_OVERRIDES = {
    "consume": False,
    "min_base_edge_fraction": 0.0,
    "drop_feature_dihedral": 50.0,
    "pyr_tolerance": 200,
    "cover_thin_first": False,
}

out: dict = {"stages": {}}


def stage(name: str):
    print(f"== R1 stage: {name} ==", flush=True)
    out["stages"][name] = {"t0": time.monotonic()}


def done(name: str, **kw):
    out["stages"][name].update(kw)
    out["stages"][name]["seconds"] = round(time.monotonic() - out["stages"][name].pop("t0"), 1)
    print(f"   -> {name}: {kw} ({out['stages'][name]['seconds']}s)", flush=True)


def save():
    (WORK / "r1_result.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


def main() -> int:
    import numpy as np

    WORK.mkdir(parents=True, exist_ok=True)
    assert STEP.is_file(), f"truth input missing: {STEP}"
    h = hashlib.sha256(STEP.read_bytes()).hexdigest()[:16]
    out["input"] = {
        "path": str(STEP),
        "bytes": STEP.stat().st_size,
        "mtime": STEP.stat().st_mtime,
        "sha256_16": h,
    }
    out["bl_params"] = BL_PARAMS
    print(f"input: {STEP} bytes={out['input']['bytes']} sha={h}", flush=True)

    try:
        from polyfoammesh.core.gmsh_wrapper import generate_surface_stl

        stage("surface")
        surf = WORK / "parte4_surface.stl"
        if STL_INPUT is not None and STL_INPUT.is_file():
            # Direct watertight STL (e.g. Parte4.stl 18/05): no GMSH
            # surface pass needed; fTetWild meshes it as-is.
            surf = STL_INPUT
            out["surface_input"] = str(surf)
            out["surface_detail"] = "input-stl"
            print(f"   using input STL directly: {surf}", flush=True)
        elif surf.is_file() and surf.stat().st_size > 1000000:
            out["surface_reused"] = True
            print(f"   reusing existing surface: {surf} ({surf.stat().st_size} bytes)", flush=True)
        else:
            names = generate_surface_stl(str(STEP), surf, detail=DETAIL)
            out["surface_detail"] = DETAIL
            done("surface", patches=len(names) if isinstance(names, list) else names,
                 stl_bytes=surf.stat().st_size)

        from polyfoammesh.core import ftetwild_wrapper as ftw

        stage("tetrahedralize")
        _npz = WORK / "r1_tet.npz"
        if REUSE_TET and _npz.is_file():
            _d = np.load(_npz)
            V, T, info = np.asarray(_d["V"]), np.asarray(_d["T"]), {}
            out["tet_reused"] = True
            print(f"   reused tet from {_npz}: {len(V)} nodes, {len(T)} tets", flush=True)
        else:
            V, T, info = ftw.tetrahedralize(surf, epsilon_abs=0.0003)
        V = np.asarray(V)
        T = np.asarray(T)
        assert len(V) > 0 and len(T) > 0
        out["n_nodes"] = int(len(V))
        out["n_tets"] = int(len(T))
        out["ftet_stats"] = {k: (float(v) if isinstance(v, (int, float)) else str(v)) for k, v in dict(info or {}).items()}
        done("tetrahedralize", n_tets=len(T))
        # Persist the tet mesh: a crash downstream must never waste
        # another ~100 min re-tetrahedralizing.
        np.savez_compressed(WORK / "r1_tet.npz", V=V, T=T)
        out["tet_npz"] = str(WORK / "r1_tet.npz")
        save()
        if TET_ONLY:
            out["status"] = "TET-ONLY COMPLETE"
            save()
            print(json.dumps({k: v for k, v in out.items() if k != "stages"}, indent=1), flush=True)
            return 0

        from polyfoammesh.core.mesh_converter import msh_to_of_polymesh

        stage("convert")
        faces, owner = ftw.boundary_faces(V, T)
        faces = ftw.orient_faces_outward(V, T, faces, owner)
        msh = WORK / "parte4.msh"
        ftw.write_msh22(msh, V, T, np.asarray(faces), np.ones(len(faces), dtype=np.int64), ["wall"])
        case = WORK / "case"
        if case.exists():
            shutil.rmtree(case)
        msh_to_of_polymesh(msh, case)
        from polyfoammesh.core.case_setup import write_control_dict

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
        done("convert", boundary_faces=int(len(faces)))

        from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter

        stage("dual")
        dres = TetPolyDualConverter(
            case, log=lambda m: None,
            collapse_smooth_edges=True,
            boundary_feature_angle=40.0,
            collapse_volume_tolerance=0.10,
            smooth_free_boundary=True,
        ).run()
        out["dual"] = {
            "success": bool(dres.success),
            "n_cells_after": int(dres.n_cells_after),
            "n_poly_cells": int(dres.n_poly_cells),
            "residual_defects": int(dres.residual_defects),
            "defect_breakdown": {k: int(v) for k, v in dres.defect_breakdown.items()},
            "min_cell_volume": float(dres.min_cell_volume),
            "volume_rel_err": float(abs(dres.volume_after - dres.volume_before) / max(dres.volume_before, 1e-300)),
            "errors": list(dres.errors),
        }
        done("dual", **{k: out["dual"][k] for k in ("success", "n_cells_after", "residual_defects")})
        if not dres.success:
            raise RuntimeError(f"dual failed: {dres.errors}")

        from polyfoammesh.core.bl_config import BLConfig
        from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine

        stage("bl_5layer")
        cfg, warnings = BLConfig.from_mapping_tolerant(dict(BL_PARAMS), max_cell=None)
        kw = cfg.engine_kwargs(None)
        kw.update(FTET_OVERRIDES)
        out["bl_effective_kwargs"] = {k: (float(v) if isinstance(v, float) else v) for k, v in kw.items()}
        r = PolyBoundaryLayerEngine(case, log=lambda m: None).run(**kw)
        brep = r.bl_report if isinstance(getattr(r, "bl_report", None), dict) else {}
        out["bl"] = {
            "success": bool(r.success),
            "n_prism_cells": int(getattr(r, "n_prism_cells", getattr(r, "bl_prism_cells", 0))),
            "coverage": brep.get("coverage"),
            "h1": brep.get("h1"),
            "errors": list(getattr(r, "errors", [])),
            "warnings": list(getattr(r, "warnings", [])),
        }
        done("bl_5layer", success=out["bl"]["success"], prisms=out["bl"]["n_prism_cells"])
        if not r.success:
            raise RuntimeError(f"BL failed: {r.errors}")

        from polyfoammesh.config import OFConfig

        stage("checkmesh")
        cfg_of = OFConfig()
        cmd = cfg_of.build_check_mesh_cmd(case)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        text = (proc.stdout or "") + (proc.stderr or "")
        verdict = "Mesh OK" if "Mesh OK" in text else ("Failed" if "Failed" in text else f"rc={proc.returncode}")
        (WORK / "checkmesh_r1.log").write_text(text[-30000:], encoding="utf-8", errors="replace")
        import re

        m = re.search(r"non-orthogonality Max:\s*([\d.]+)", text)
        s = re.search(r"Max skewness\s*=\s*([\d.]+)", text)
        f = re.search(r"Failed (\d+) mesh checks", text)
        out["checkmesh"] = {
            "rc": int(proc.returncode),
            "verdict": verdict,
            "nomax": float(m.group(1)) if m else None,
            "skewmax": float(s.group(1)) if s else None,
            "failed_checks": int(f.group(1)) if f else (0 if verdict == "Mesh OK" else None),
        }
        done("checkmesh", **out["checkmesh"])
        if proc.returncode != 0:
            raise RuntimeError(f"checkMesh rc={proc.returncode}")

        out["status"] = "COMPLETE"
    except Exception as exc:  # noqa: BLE001
        out["status"] = f"STOPPED: {type(exc).__name__}: {exc}"
        print(out["status"], flush=True)
    save()
    print(json.dumps({k: v for k, v in out.items() if k != "stages"}, indent=1), flush=True)
    return 0 if out.get("status") == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
