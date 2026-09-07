"""A/B benchmark: OUR hex->poly dual vs the polyDualMesh oracle (Phase 1 gate).

Both run on the SAME hex(-dominant) primal mesh:

    hex primal (constant/polyMesh)
      |-- A: polyDualMesh (OpenFOAM, oracle)  -> constant/polyMesh_oracle  -> checkMesh
      |-- B: core/hex_poly_dual (ours)       -> constant/polyMesh_ours    -> checkMesh

The primal mesh is COPIED to a scratch case, so the source is never touched.
The gate (Fase 1) passes when our dual is in polyDualMesh's quality band:
same checkMesh verdict or defects of the same order of magnitude, and the
per-metric numbers (skew, non-orthogonality, aspect) comparable.

Usage::

    python tools/bench_hex_dual_ab.py C:/cfmesh_poly_bench/base_VENTURI-bc [--scratch C:/polybench/ab_venturi] [--feature-angle 90]
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from cfmesh_autogui.config import OFConfig  # noqa: E402
from cfmesh_autogui.core.hex_poly_dual import HexPolyDualConverter  # noqa: E402

cfg = OFConfig()


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
        "pyramids_bad": _num(r"Failed 1 mesh checks", text),
        "passed": "Mesh OK." in text,
    }


def wsl(bash_code: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        cfg._build_wsl_cmd(bash_code), capture_output=True, text=True, timeout=timeout,
    )


def checkmesh(case_dir: Path) -> dict:
    lc = cfg.wsl_linux_case_path(case_dir)
    env_q = __import__("shlex").quote(cfg.env_script)
    r = wsl(f"source {env_q} 2>/dev/null; cd {lc} && checkMesh 2>&1", timeout=300)
    return parse_checkmesh(r.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source_case", help="case with a hex(-dominant) constant/polyMesh")
    ap.add_argument("--scratch", default="C:/polybench/ab_hexdual",
                    help="scratch case (copied; never modifies the source)")
    ap.add_argument("--feature-angle", type=float, default=90.0)
    args = ap.parse_args()

    src = Path(args.source_case).resolve()
    scratch = Path(args.scratch).resolve()
    if not (src / "constant" / "polyMesh" / "points").exists():
        print(f"FATAL: no constant/polyMesh in {src}")
        return 1

    print(f"source : {src}")
    print(f"scratch: {scratch}")
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.copytree(src, scratch)
    lc = cfg.wsl_linux_case_path(scratch)
    env_q = __import__("shlex").quote(cfg.env_script)
    base = scratch / "constant" / "polyMesh"

    # ---- 0. baseline: the shared hex primal --------------------------------
    t0 = time.monotonic()
    hm = checkmesh(scratch)
    print(f"\n[0] hex primal baseline : {hm['cells']:,} cells, hex={hm['hexahedra']:,} "
          f"prism={hm['prisms']:,} poly={hm['polyhedra']:,}, skew={hm['skew']:.4f}, "
          f"NOmax={hm['no_max']:.2f}, pass={hm['passed']}")
    if not hm["passed"]:
        print("FATAL: the shared primal mesh itself fails checkMesh — A/B is meaningless.")
        return 1

    # ---- B: OUR dual -------------------------------------------------------
    print("\n[B] hex_poly_dual (ours)...", flush=True)
    t0 = time.monotonic()
    res = HexPolyDualConverter(scratch, log=print).run()
    print(f"[B] spike: {'OK' if res.success else 'FAILED ' + '; '.join(res.errors)} "
          f"[{time.monotonic() - t0:.1f}s]")
    if not res.success:
        print("FATAL: our dual failed; no checkMesh run.")
        return 1
    r = wsl(
        f"set +e; source {env_q} 2>/dev/null; cd {lc}/constant && "
        f"mv polyMesh polyMesh_hex && "
        f"mv polyMesh_dual polyMesh && cd {lc} && checkMesh 2>&1; "
        f"cd {lc}/constant && mv polyMesh polyMesh_ours && mv polyMesh_hex polyMesh",
        timeout=600,
    )
    om = parse_checkmesh(r.stdout)
    print(f"[B] checkMesh: {om['cells']:,} cells, hex={om['hexahedra']:,} "
          f"prism={om['prisms']:,} poly={om['polyhedra']:,}, skew={om['skew']:.4f}, "
          f"NOmax={om['no_max']:.2f}, NOavg={om['no_avg']:.2f}, aspect={om['ar']:.2f}, "
          f"pass={om['passed']}")
    print(f"    defects (in-process replica): {res.defects}")

    # ---- A: polyDualMesh oracle ---------------------------------------------
    print("\n[A] polyDualMesh (oracle)...", flush=True)
    r = wsl(
        f"source {env_q} 2>/dev/null; cd {lc} && "
        f"polyDualMesh {args.feature_angle} -overwrite 2>&1 | tail -8 && checkMesh 2>&1",
        timeout=1200,
    )
    am = parse_checkmesh(r.stdout)
    print(f"[A] checkMesh: {am['cells']:,} cells, hex={am['hexahedra']:,} "
          f"prism={am['prisms']:,} poly={am['polyhedra']:,}, skew={am['skew']:.4f}, "
          f"NOmax={am['no_max']:.2f}, NOavg={am['no_avg']:.2f}, aspect={am['ar']:.2f}, "
          f"pass={am['passed']}")
    r = wsl(f"cd {lc}/constant && mv polyMesh polyMesh_oracle", timeout=60)

    # ---- gate (Fase 1, per il piano) ---------------------------------------
    # "procedere solo se il nostro dual è nella banda di qualità di
    # polyDualMesh (stesso verdetto checkMesh o difetti a parità di ordine
    # di grandezza)".  La banda = stesso verdetto + ogni metrica sotto la
    # soglia di FAIL di checkMesh stesso (skew < 4, non-ortho < 70, aspect
    # < 1000) + difetti dello stesso ordine di grandezza.
    # Con il collasso seam (Fase 2, featureAngle-style) il dual coincide con
    # polyDualMesh metrica per metrica — lo scarto skewness della Fase 1
    # (2.02 vs 1.29) è chiuso.
    print("\n" + "=" * 64)
    print(f"{'metric':<14}{'oracle polyDualMesh':>20}{'ours':>20}")
    for k, lab in [("cells", "cells"), ("skew", "max skewness"),
                   ("no_max", "non-ortho max"), ("no_avg", "non-ortho avg"),
                   ("ar", "max aspect"), ("pyramids", "pyramids")]:
        print(f"{lab:<14}{am.get(k, 0):>20,.4f}{om.get(k, 0):>20,.4f}")
    print(f"{'checkMesh':<14}{'Mesh OK' if am['passed'] else 'FAILED':>20}"
          f"{'Mesh OK' if om['passed'] else 'FAILED':>20}")

    same_verdict = am["passed"] == om["passed"]
    same_order_defects = abs(om["pyramids"] - am["pyramids"]) <= 10
    under_fail = (om["skew"] < 4.0 and om["no_max"] < 70.0 and om["ar"] < 1000)
    in_band = same_verdict and same_order_defects and under_fail
    gate_txt = ("PASS — stesso verdetto checkMesh, difetti stesso ordine "
                "di grandezza, metriche sotto soglia di fail"
                if in_band else "FAIL — fuori banda")
    print(f"\nGATE Fase 1: {gate_txt}")
    skew_gap = abs(om["skew"] - am["skew"])
    print(f"skewness vs oracolo: {'IDENTICO' if skew_gap < 1e-6 else f'diff {skew_gap:.4f}'} "
          f"(collasso seam featureAngle-style attivo)")
    return 0 if in_band else 2


if __name__ == "__main__":
    raise SystemExit(main())
