"""A/B bench: exact per-corner dual boundary vs collapsed (polyDualMesh) one.

Runs BOTH modes of ``TetPolyDualConverter`` on the SAME pristine tet backup
(GMSH is not deterministic — re-meshing would invalidate the comparison) and
reports the real ``checkMesh`` verdict for each, plus the two numbers the
collapse exists to change:

  - boundary face count  (one prism stack per boundary face is extruded by
    ``core/bl_poly.py``, so this IS the boundary-layer cell count factor)
  - volume drift         (collapse trades the exact tiling for a surface offset)

Usage (needs WSL + OpenFOAM for the checkMesh part)::

    python tools/bench_collapse_ab.py                 # ref1 (block + bore)
    python tools/bench_collapse_ab.py --case valve1   # the hard one
    python tools/bench_collapse_ab.py --conv-only     # no WSL, converter only
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfmesh_autogui.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402

POLYBENCH = Path("C:/polybench")
# Space-free work root: the WSL path conversion drops the space in
# ``C:\Users\Davide Valoroso\...`` (fileName::stripInvalid).
WORK_ROOT = Path("C:/polybench2/collapse_ab")

CONTROL_DICT = (
    "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
    "    class       dictionary;\n    object      controlDict;\n}\n"
    "application  checkMesh;\nstartFrom startTime;\nstartTime 0;\n"
    "stopAt endTime;\nendTime 1;\ndeltaT 1;\nwriteControl timeStep;\n"
    "writeInterval 1;\npurgeWrite 0;\nwriteFormat ascii;\nwritePrecision 8;\n"
    "timeFormat general;\ntimePrecision 6;\nrunTimeModifiable true;\n"
)
FVSCHEMES = (
    "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
    "ddtSchemes { default steadyState; }\n"
    "gradSchemes { default Gauss linear; }\n"
    "divSchemes { default Gauss linear; }\n"
    "laplacianSchemes { default Gauss linear corrected; }\n"
    "interpolationSchemes { default linear; }\n"
    "snGradSchemes { default corrected; }\n"
)
FVSOLUTION = (
    "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
    "solvers { p { solver PCG; preconditioner DIC; tolerance 1e-6; relTol 0; } }\n"
)


def convert(backup: Path, tag: str, **kw) -> tuple[Path, object, float]:
    """Convert the pristine backup in a throwaway case. Returns (case, res, s)."""
    case = WORK_ROOT / tag
    if case.exists():
        shutil.rmtree(case)
    shutil.copytree(backup, case / "constant" / "polyMesh")
    sysd = case / "system"
    sysd.mkdir(parents=True, exist_ok=True)
    (sysd / "controlDict").write_text(CONTROL_DICT, encoding="ascii")
    (sysd / "fvSchemes").write_text(FVSCHEMES, encoding="ascii")
    (sysd / "fvSolution").write_text(FVSOLUTION, encoding="ascii")
    t0 = time.monotonic()
    res = TetPolyDualConverter(case, log=lambda m: None, **kw).run()
    return case, res, time.monotonic() - t0


def checkmesh_of(case: Path) -> dict:
    from cfmesh_autogui.config import OFConfig

    cfg = OFConfig()
    cmd = (
        f"source {shlex.quote(cfg.env_script)} 2>/dev/null; "
        f"cd {cfg._quoted_linux_path(case)} && checkMesh 2>&1 | "
        f"grep -E 'Failed|Mesh OK|incorrectly oriented|negative volume|"
        f"Max skewness|non-orthogonality Max|Max aspect ratio'"
    )
    r = subprocess.run(
        cfg._build_wsl_cmd(cmd), capture_output=True, text=True, timeout=1800,
        check=False,
    )
    text = r.stdout + r.stderr

    def _flt(pat):
        m = re.search(pat, text)
        return float(m.group(1)) if m else None

    wrong = re.search(
        r"\*\*\*Error in face pyramids:\s*(\d+) faces are incorrectly oriented", text)
    failed = re.search(r"Failed (\d+) mesh checks", text)
    neg = re.search(r"(\d+) negative volume cells", text)
    return {
        "mesh_ok": "Mesh OK" in text,
        "failed_checks": int(failed.group(1)) if failed else 0,
        "wrong_oriented": int(wrong.group(1)) if wrong else 0,
        "negative_cells": int(neg.group(1)) if neg else 0,
        "max_skewness": _flt(r"Max skewness = ([\d.eE+-]+)"),
        "max_non_ortho": _flt(r"non-orthogonality Max: ([\d.]+)"),
        "max_aspect": _flt(r"Max aspect ratio = ([\d.eE+-]+)"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="ref1", help="case under C:/polybench")
    ap.add_argument("--conv-only", action="store_true", help="skip WSL checkMesh")
    ap.add_argument("--feature-angle", type=float, default=90.0)
    ap.add_argument("--vol-tol", type=float, default=0.05)
    args = ap.parse_args()

    backup = POLYBENCH / args.case / "constant" / "polyMesh_tet_backup"
    if not backup.exists():
        print(f"NO BACKUP: {backup}")
        return 2

    rows = []
    for tag, kw in (
        ("exact", {}),
        ("collapsed", {"collapse_smooth_edges": True,
                       "boundary_feature_angle": args.feature_angle,
                       "collapse_volume_tolerance": args.vol_tol}),
    ):
        print(f"[{tag}] converting {args.case} ...", flush=True)
        case, res, secs = convert(backup, tag, smooth=False, **kw)
        row = {
            "mode": tag,
            "success": bool(res.success),
            "errors": list(res.errors),
            "cells": int(res.n_cells_after or 0),
            "internal_faces": int(res.n_internal_faces or 0),
            "boundary_faces": int(res.n_boundary_faces or 0),
            "residual_defects": int(res.residual_defects or 0),
            "defect_breakdown": dict(res.defect_breakdown or {}),
            "volume_before": res.volume_before,
            "volume_after": res.volume_after,
            "seconds": round(secs, 1),
        }
        if res.volume_before:
            row["volume_drift_pct"] = round(
                100.0 * (res.volume_after - res.volume_before) / abs(res.volume_before), 4)
        if res.success and not args.conv_only:
            print(f"[{tag}] checkMesh ...", flush=True)
            row["checkmesh"] = checkmesh_of(case)
        rows.append(row)

    print()
    for r in rows:
        if not r["success"]:
            print(f"{r['mode']:10s} FAILED: {r['errors']}")
            continue
        cm = r.get("checkmesh") or {}
        print(
            f"{r['mode']:10s} cells={r['cells']:,} bnd_faces={r['boundary_faces']:,} "
            f"drift={r.get('volume_drift_pct', 0):+.3f}% "
            f"defects={r['residual_defects']} {r['defect_breakdown']} "
            f"[{r['seconds']}s]"
        )
        if cm:
            print(
                f"{'':10s} checkMesh: {'Mesh OK' if cm['mesh_ok'] else 'FAILED ' + str(cm['failed_checks'])}"
                f", wrong-oriented={cm['wrong_oriented']}, neg={cm['negative_cells']}, "
                f"skew={cm['max_skewness']}, nonOrtho={cm['max_non_ortho']}, "
                f"aspect={cm['max_aspect']}"
            )
    ok = [r for r in rows if r["success"]]
    if len(ok) == 2:
        a, b = ok
        if a["boundary_faces"]:
            print(f"\nboundary-face (and therefore BL prism) factor: "
                  f"{a['boundary_faces'] / max(b['boundary_faces'], 1):.2f}x fewer "
                  f"with collapse")
    print("\nJSON " + json.dumps(rows, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
