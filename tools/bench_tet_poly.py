"""Unified tet->poly benchmark harness.

Runs the tet->poly conversion on a real case and reports checkMesh metrics as
a machine-readable JSON row.  Every number this project publishes about
converter quality comes from this harness, on C:\\polybench cases: each case
keeps its pristine tet mesh in ``constant/polyMesh_tet_backup``, which is
restored before every conversion so different converters see one identical
input (GMSH is non-deterministic — a re-mesh would invalidate the comparison).

Usage::

    python tools/bench_tet_poly.py ref1                 # dual, reuse C:\\polybench
    python tools/bench_tet_poly.py ref1 --conv dual --json out.json
    python tools/bench_tet_poly.py ref1 --conv dual --repeat 3   # stability

Only the barycentric-dual converter is benchmarked here: the merge-based
terminal-face converter is retired and has been removed from this harness.

Supported cases (must exist in C:\\polybench with a tet backup, or be built
with --geom): ref1..ref3, valve1, valve2, smoke.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

POLYBENCH = Path("C:/polybench")
WORK_ROOT = Path("C:/polybench2")
VALVE_STEP = Path(r"C:\Users\Davide Valoroso\Desktop\Report\Parte4.stp")

from polyfoammesh.config import OFConfig  # noqa: E402

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

# ---------------------------------------------------------------------------
# checkMesh
# ---------------------------------------------------------------------------

def _run_wsl(cfg: OFConfig, bash: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        cfg._build_wsl_cmd(bash), capture_output=True, text=True, timeout=timeout,
    )


def checkmesh_raw(cfg: OFConfig, case_dir: Path) -> str:
    env_q = shlex.quote(cfg.env_script)
    r = _run_wsl(
        cfg,
        f"source {env_q} 2>/dev/null; cd {cfg._quoted_linux_path(case_dir)} "
        "&& checkMesh 2>&1",
        timeout=900,
    )
    return r.stdout + r.stderr


def _flt(s: str) -> float:
    return float(s.replace(",", "").rstrip("."))


def parse_checkmesh(text: str) -> dict:
    cells = {}
    m = re.search(r"Overall number of cells of each type:\s*\n((?:.*\n)*?)\n", text)
    if m:
        for line in m.group(1).splitlines():
            lm = re.match(r"\s*([a-zA-Z ]+?)\s*:\s*(\d+)", line)
            if lm:
                cells[lm.group(1).strip()] = int(lm.group(2))
    perr = re.search(r"\*\*\*Error in face pyramids:\s*(\d+) faces are incorrectly oriented", text)
    failed = re.search(r"Failed (\d+) mesh checks", text)
    open_cells = re.search(r"(\d+) open cells", text)
    neg = re.search(r"(\d+) negative volume cells", text)
    skew_max = re.search(r"Max skewness = ([\d.eE+-]+)", text)
    no_max = re.search(r"non-orthogonality Max: ([\d.]+)", text)
    no_avg = re.search(r"non-orthogonality Max: [\d.]+\s+average: ([\d.]+)", text)
    ar = re.search(r"Max aspect ratio = ([\d.eE+-]+)", text)
    total_vol = re.search(r"Total volume = ([\d.eE+-]+)", text)
    min_vol = re.search(r"Min volume = ([\d.eE+-]+)", text)
    max_vol = re.search(r"Max volume = ([\d.eE+-]+)", text)
    return {
        "cells": sum(cells.values()),
        "tetrahedra": cells.get("tetrahedra", 0),
        "hexahedra": cells.get("hexahedra", 0),
        "polyhedra": cells.get("polyhedra", 0),
        "mesh_ok": "Mesh OK" in text,
        "failed_checks": int(failed.group(1)) if failed else 0,
        "wrong_oriented": int(perr.group(1)) if perr else 0,
        "open_cells": int(open_cells.group(1)) if open_cells else 0,
        "negative_cells": int(neg.group(1)) if neg else 0,
        "max_skewness": _flt(skew_max.group(1)) if skew_max else None,
        "max_non_ortho": _flt(no_max.group(1)) if no_max else None,
        "avg_non_ortho": _flt(no_avg.group(1)) if no_avg else None,
        "max_aspect": _flt(ar.group(1)) if ar else None,
        "total_volume": _flt(total_vol.group(1)) if total_vol else None,
        "min_volume": _flt(min_vol.group(1)) if min_vol else None,
        "max_volume": _flt(max_vol.group(1)) if max_vol else None,
        "raw_output": text,
    }


# ---------------------------------------------------------------------------
# case preparation
# ---------------------------------------------------------------------------

def prepare_case(name: str, tet_backup: Path, tag: str) -> Path:
    """Copy a pristine tet backup into a fresh working case."""
    case = WORK_ROOT / f"{name}_{tag}"
    if case.exists():
        shutil.rmtree(case)
    poly = case / "constant" / "polyMesh"
    sysd = case / "system"
    poly.mkdir(parents=True)
    sysd.mkdir(parents=True)
    for f in ("points", "faces", "owner", "neighbour", "boundary"):
        shutil.copy2(tet_backup / f, poly / f)
    (sysd / "controlDict").write_text(CONTROL_DICT, encoding="ascii")
    (sysd / "fvSchemes").write_text(FVSCHEMES, encoding="ascii")
    (sysd / "fvSolution").write_text(FVSOLUTION, encoding="ascii")
    return case


def tet_backup_of(name: str) -> Path:
    return POLYBENCH / name / "constant" / "polyMesh_tet_backup"


# ---------------------------------------------------------------------------
# converters
# ---------------------------------------------------------------------------

def run_dual(case_dir: Path) -> dict:
    from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter

    t0 = time.monotonic()
    r = TetPolyDualConverter(case_dir, log=lambda m: None).run()
    return {
        "success": r.success,
        "tets_in": r.n_tets_before,
        "cells_out": r.n_cells_after,
        "coverage_pct": 100.0,
        "converter_s": round(time.monotonic() - t0, 2),
        "converter_errors": r.errors,
        "internal_faces": r.n_internal_faces,
        "boundary_faces": r.n_boundary_faces,
    }


CONVERTERS = {
    "dual": run_dual,
}


# ---------------------------------------------------------------------------
# fresh build (STEP -> gmsh -> gmshToFoam) — used only when no tet backup
# ---------------------------------------------------------------------------

def build_case_from_step(name: str, step_path: "Path | None", detail: str) -> tuple[Path, Path]:
    raise SystemExit(
        "Fresh STEP meshing is not wired into this harness yet; use an existing "
        f"C:\\polybench\\{name} case (--reuse) or add the geometry first."
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def print_table(rows: list[dict]) -> None:
    def fmt(x) -> str:
        return "-" if x is None else f"{x:.3g}" if isinstance(x, float) else str(x)

    print(f"{'case':<8}{'conv':<9}{'tets':>11}{'cells':>10}{'poly%':>7}{'verdict':<14}{'wrong':>6}{'skew':>8}{'n-ortho':>9}{'aspect':>9}{'vol':>12}{'sec':>7}")
    for r in rows:
        cm = r.get("checkmesh") or {}
        verdict = "Mesh OK" if cm.get("mesh_ok") else f"Failed {cm.get('failed_checks')}"
        skew = cm.get("max_skewness")
        nono = cm.get("max_non_ortho")
        aspect = cm.get("max_aspect")
        vol = cm.get("total_volume")
        print(
            f"{str(r.get('case','?')):<8}{str(r.get('conv','?')):<9}"
            f"{str(r.get('tets_in','?')):>11}{str(r.get('cells_out','?')):>10}"
            f"{str(r.get('coverage_pct','?')):>7}"
            f"{verdict:<14}{str(cm.get('wrong_oriented','?')):>6}"
            f"{fmt(skew):>8}{fmt(nono):>9}{fmt(aspect):>9}"
            f"{fmt(vol):>12}{str(r.get('converter_s','?')):>7}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("case", help="case name (ref1/ref2/ref3/valve1/valve2/smoke)")
    ap.add_argument("--conv", default="dual", choices=["dual"],
                    help="converter to run (the terminal-face converter is retired)")
    ap.add_argument("--json", type=Path, default=None, help="write results as JSON")
    ap.add_argument("--repeat", type=int, default=1, help="conversion repeats (stability)")
    ap.add_argument("--reuse", action="store_true", help="use existing C:\\polybench case")
    ap.add_argument("--geom", choices=["block", "valve"], default=None)
    ap.add_argument("--tag", default="bench", help="working-case suffix")
    args = ap.parse_args()

    if not args.reuse:
        tb = tet_backup_of(args.case)
        if tb.exists():
            args.reuse = True
        elif args.geom is None:
            sys.exit(f"No tet backup at {tb} and no --geom given. Cannot convert.")
    if not args.reuse:
        build_case_from_step(args.case, VALVE_STEP if args.geom == "valve" else None, "medium")

    cfg = OFConfig()
    rows: list[dict] = []
    convs = [args.conv]
    for rpt in range(args.repeat):
        for conv in convs:
            case = prepare_case(args.case, tet_backup_of(args.case), f"{args.tag}{rpt}")
            cdata = CONVERTERS[conv](case)
            if not cdata["success"]:
                cdata.update({"case": args.case, "conv": conv, "checkmesh": None,
                              "error": cdata["converter_errors"]})
                rows.append(cdata)
                print(json.dumps(cdata))
                continue
            text = checkmesh_raw(cfg, case)
            cm = parse_checkmesh(text)
            cdata.update({"case": args.case, "conv": conv, "checkmesh": {
                k: v for k, v in cm.items() if k != "raw_output"}})
            rows.append(cdata)
            print("")
            for line in [l for l in text.splitlines()
                         if "Failed" in l or "Mesh OK" in l or "Wrong orientation" in l
                         or "incorrectly oriented" in l or "skewness = " in l
                         or "non-orthogonality Max" in l or "aspect ratio" in l]:
                print("  " + line.strip())

    print("")
    print_table(rows)
    if args.json:
        out = []
        for r in rows:
            o = dict(r)
            if "checkmesh" in o and o["checkmesh"] is not None:
                o.pop("raw_output", None)
            out.append(o)
        args.json.write_text(
            json.dumps(out, indent=2, default=str), encoding="utf-8"
        )
        print(f"\nJSON -> {args.json}")


if __name__ == "__main__":
    main()