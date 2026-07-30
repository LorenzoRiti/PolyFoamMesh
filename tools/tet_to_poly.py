"""Standalone tet->poly pipeline: STEP -> GMSH tetrahedra -> OpenFOAM
(via gmshToFoam) -> checkMesh -> polyDualMesh -> checkMesh.

Workaround for a not-yet-resolved freeze when the gmshToFoam conversion
step runs from inside the live GUI process (see main_window.py's
_continue_gmsh_direct docstring for the full story) — this exact same
pipeline runs reliably as a standalone script; it just isn't reliably
reachable from a single "Run" click in the GUI yet.

Usage:
    py -3.11 tools\\tet_to_poly.py <geometry.step> [case_dir] [--detail medium]

Then load the resulting <case_dir> into the app's viewer to inspect
the mesh (File > ... or point the integrated viewer at it), or open it
in ParaView (Strumenti > Launch ParaView, now fixed to actually open).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfmesh_autogui.config import OFConfig  # noqa: E402


def _write_system_files(case_dir: Path) -> None:
    (case_dir / "system").mkdir(parents=True, exist_ok=True)
    files = {
        "controlDict": (
            "FoamFile { version 2.0; format ascii; class dictionary; "
            "object controlDict; }\napplication foamRun;\n"
            "startFrom startTime; startTime 0; stopAt endTime; endTime 1000;\n"
            "deltaT 1;\nwriteControl timeStep; writeInterval 1; purgeWrite 0;\n"
            "writeFormat binary;\nwritePrecision 6; writeCompression on;\n"
            "timeFormat general; timePrecision 6;\nrunTimeModifiable true;\n"
        ),
        "fvSchemes": (
            "FoamFile { version 2.0; format ascii; class dictionary; "
            "object fvSchemes; }\nddtSchemes { default steadyState; }\n"
            "gradSchemes { default Gauss linear; }\n"
            "divSchemes { default Gauss linear; }\n"
            "laplacianSchemes { default Gauss linear corrected; }\n"
            "interpolationSchemes { default linear; }\n"
            "snGradSchemes { default corrected; }\n"
        ),
        "fvSolution": (
            "FoamFile { version 2.0; format ascii; class dictionary; "
            "object fvSolution; }\nsolvers { p { solver PCG; "
            "preconditioner DIC; tolerance 1e-6; relTol 0.1; } }\n"
        ),
    }
    for name, content in files.items():
        (case_dir / "system" / name).write_text(content, encoding="ascii")


def _parse_check(text: str) -> dict:
    ok = "Mesh OK" in text
    m = re.search(r"Failed (\d+) mesh checks", text)
    return {"ok": ok, "failed_checks": m.group(1) if m else ("0" if ok else "?")}


def run(step_path: Path, case_dir: Path, detail: str, feature_angle: float) -> int:
    if not step_path.exists():
        print(f"ERROR: geometry not found: {step_path}")
        return 1
    if " " in str(case_dir):
        print(f"ERROR: case_dir must not contain spaces (OpenFOAM/WSL limitation): {case_dir}")
        return 1

    import shutil
    shutil.rmtree(case_dir, ignore_errors=True)
    case_dir.mkdir(parents=True)
    _write_system_files(case_dir)

    print(f"=== 1/5: GMSH tetrahedral volume mesh (detail={detail}) ===")
    msh_path = case_dir / "mesh.msh"
    r = subprocess.run(
        [sys.executable, "-m", "cfmesh_autogui.core.gmsh_wrapper",
         "volume", str(step_path), str(msh_path), detail, "0", "0", "1.2"],
        capture_output=True, text=True, timeout=180, cwd=str(ROOT),
    )
    if r.returncode != 0:
        print("FAILED:", r.stderr[-1000:] or r.stdout[-1000:])
        return 1
    info = json.loads(r.stdout.strip().splitlines()[-1])
    print(f"  OK — patches: {info.get('names')}")

    cfg = OFConfig()

    print("\n=== 2/5: gmshToFoam (WSL, convert .msh -> OpenFOAM polyMesh) ===")
    r = subprocess.run(cfg.build_gmsh_to_foam_cmd(case_dir, "mesh.msh"),
                        capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not (case_dir / "constant" / "polyMesh" / "points").exists():
        print("FAILED:", (r.stdout + r.stderr)[-1000:])
        return 1
    print("  OK")

    print("\n=== 3/5: checkMesh on tet baseline ===")
    r = subprocess.run(cfg.build_check_mesh_cmd(case_dir), capture_output=True, text=True, timeout=180)
    tet_check = _parse_check(r.stdout + r.stderr)
    print(f"  mesh_ok={tet_check['ok']} failed_checks={tet_check['failed_checks']}")

    print(f"\n=== 4/5: polyDualMesh (featureAngle={feature_angle}) — dualizes the "
          "unstructured tet mesh into genuine irregular polyhedra ===")
    r = subprocess.run(cfg.build_poly_dual_cmd(case_dir, feature_angle=feature_angle),
                        capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        print("FAILED:", (r.stdout + r.stderr)[-1000:])
        return 1
    print("  OK")

    print("\n=== 5/5: checkMesh on final polyhedral mesh ===")
    r = subprocess.run(cfg.build_check_mesh_cmd(case_dir), capture_output=True, text=True, timeout=180)
    poly_text = r.stdout + r.stderr
    poly_check = _parse_check(poly_text)
    n_poly = re.search(r"polyhedra:\s+(\d+)", poly_text)
    print(f"  mesh_ok={poly_check['ok']} failed_checks={poly_check['failed_checks']} "
          f"polyhedra={n_poly.group(1) if n_poly else '?'}")

    print(f"\n{'='*60}")
    print(f"DONE. Case directory:\n  {case_dir}")
    print("Open it in the app's viewer, or Strumenti > Launch ParaView.")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("step_file", type=Path)
    p.add_argument("case_dir", type=Path, nargs="?", default=None)
    p.add_argument("--detail", default="medium", choices=["very_coarse", "coarse", "medium", "fine", "very_fine"])
    p.add_argument("--feature-angle", type=float, default=90)
    args = p.parse_args()
    case_dir = args.case_dir or (Path.home() / "cfmesh_cases" / f"tet_poly_{args.step_file.stem}")
    if " " in str(case_dir):
        case_dir = Path(str(case_dir).replace(" ", "_"))
    sys.exit(run(args.step_file, case_dir, args.detail, args.feature_angle))
