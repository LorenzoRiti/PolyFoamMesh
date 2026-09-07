"""Valve defect baseline — regression guard for the residual dual defect.

Pins the honest numbers measured in Fase 2 (docs/poly_mesher_megaprompt.md)
so any future change to the barycentric dual converter is compared against a
fixed baseline instead of a moving memory:

    A_default (median_faces=False, split_rounds=0) on C:\\polybench\\valve1:
      residual_defects (in-process replica)  = 1027  (852 pyramids + 175 non-ortho)
      checkMesh: 3 failed checks, 852 wrong-oriented faces, 0 negative cells
      skewness 17.12, non-ortho 95.72, volume conserved to ~1e-15

Any attempt to fix the valve defect (split, wedge cells, median faces, ...)
must show a strictly better number on THIS baseline, or it did not help.

Usage (needs WSL + OpenFOAM for the checkMesh part)::

    python tools/valve_defect_baseline.py            # full A/B baseline
    python tools/valve_defect_baseline.py --conv-only # converter part only
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

from polyfoammesh.core.tet_poly_dual import (
    DualPolyResult,
    TetPolyDualConverter,
)

POLYBENCH = Path("C:/polybench")
CASE = "valve1"
# Work root WITHOUT spaces: the WSL path conversion drops the space in
# ``C:\Users\Davide Valoroso\...`` (see fileName::stripInvalid), so temp
# dirs must live under a clean path — the bench harness uses the same root.
WORK_ROOT = Path("C:/polybench2/valve_baseline")

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
    "solvers { p { solver PCG; preconditioner DIC; tolerance 1e-6; relTol 0.1; } }\n"
)

# Pinned baseline (measured 2026-08-03, Fase 2 A_default variant).
PINNED = {
    "residual_defects": 1027,
    "wrong_oriented": 852,
    "negative_cells": 0,
    "mesh_ok": False,
}


def run_conversion(backup: Path) -> tuple[Path, DualPolyResult, float]:
    """Convert the pristine tet backup in a throwaway case; return (case, res, s)."""
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    tmp = WORK_ROOT
    dst = tmp / "constant" / "polyMesh"
    shutil.copytree(backup, dst)
    sysd = tmp / "system"
    sysd.mkdir(parents=True, exist_ok=True)
    (sysd / "controlDict").write_text(CONTROL_DICT, encoding="ascii")
    (sysd / "fvSchemes").write_text(FVSCHEMES, encoding="ascii")
    (sysd / "fvSolution").write_text(FVSOLUTION, encoding="ascii")
    conv = TetPolyDualConverter(tmp, log=lambda m: None)
    t0 = time.monotonic()
    res = conv.run()
    return tmp, res, time.monotonic() - t0


def checkmesh_of(case: Path) -> dict:
    """Run checkMesh (WSL) and return the parse_checkmesh-style metrics."""
    from polyfoammesh.config import OFConfig

    cfg = OFConfig()
    env_q = shlex.quote(cfg.env_script)
    linux = cfg._quoted_linux_path(case)
    cmd = (
        f"source {env_q} 2>/dev/null; cd {linux} && checkMesh 2>&1 | "
        f"grep -E 'Failed|Mesh OK|incorrectly oriented|negative volume|"
        f"Max skewness|non-orthogonality Max|Max aspect ratio'"
    )
    r = subprocess.run(
        cfg._build_wsl_cmd(cmd), capture_output=True, text=True, timeout=900,
        check=False,
    )
    text = r.stdout + r.stderr

    def _flt(pat: str):
        m = re.search(pat, text)
        return float(m.group(1)) if m else None

    wrong = re.search(r"\*\*\*Error in face pyramids:\s*(\d+) faces are incorrectly oriented", text)
    failed = re.search(r"Failed (\d+) mesh checks", text)
    neg = re.search(r"(\d+) negative volume cells", text)
    return {
        "wrong_oriented": int(wrong.group(1)) if wrong else 0,
        "failed_checks": int(failed.group(1)) if failed else 0,
        "negative_cells": int(neg.group(1)) if neg else 0,
        "mesh_ok": "Mesh OK" in text,
        "max_skewness": _flt(r"Max skewness = ([\d.eE+-]+)"),
        "max_non_ortho": _flt(r"non-orthogonality Max: ([\d.]+)"),
        "max_aspect": _flt(r"Max aspect ratio = ([\d.eE+-]+)"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--conv-only", action="store_true",
                    help="skip the WSL checkMesh part")
    args = ap.parse_args()

    backup = POLYBENCH / CASE / "constant" / "polyMesh_tet_backup"
    if not backup.exists():
        print(f"NO BACKUP: {backup}")
        return 2

    case, res, conv_s = run_conversion(backup)
    measured = {
        "residual_defects": int(res.residual_defects),
        "breakdown": dict(res.defect_breakdown),
        "n_cells_after": int(res.n_cells_after),
        "n_tets_before": int(res.n_tets_before),
        "volume_before": float(res.volume_before),
        "volume_after": float(res.volume_after),
        "min_cell_volume": float(res.min_cell_volume),
        "converter_s": round(conv_s, 2),
    }
    if not args.conv_only:
        measured["checkmesh"] = checkmesh_of(case)
        for k in ("wrong_oriented", "negative_cells", "mesh_ok"):
            measured[k] = measured["checkmesh"][k]

    print(json.dumps(measured, indent=2, default=str))

    ok = True
    for k, pinned in PINNED.items():
        if k not in measured:
            continue
        got = measured[k]
        if got != pinned:
            print(f"BASELINE SHIFT: {k} = {got} (pinned {pinned})")
            ok = False
    # Volume conservation is a hard invariant, not a baseline.
    rel = abs(measured["volume_before"] - measured["volume_after"]) / measured["volume_before"]
    if rel > 1e-12:
        print(f"VOLUME NOT CONSERVED: rel err {rel:.3e}")
        ok = False
    print("VERDICT:", "PASS — valve defect baseline unchanged" if ok
          else "CHANGED — inspect before trusting any new converter number")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
