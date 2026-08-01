"""Benchmark harness for tet->poly converters.

Reproduces the reference-baseline numbers on C:\\polybench\\ref1
(block-with-cylindrical-bore, 251,047 tets) and runs the new dual
tet->poly converter, reporting checkMesh metrics for each.

Usage:
    python tests/bench_tetpoly_dual.py [--mode baseline|variant_a|dual|tet]
"""
from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from cfmesh_autogui.config import OFConfig

BENCH_ROOT = Path("C:/polybench2")
REF_TET = Path("C:/polybench/ref1/constant/polyMesh_tet_backup")

CONTROL_DICT = (
    "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
    "    class       dictionary;\n    object      controlDict;\n}\n"
    "application  checkMesh;\nstartFrom startTime;\nstartTime 0;\n"
    "stopAt endTime;\nendTime 1;\ndeltaT 1;\nwriteControl timeStep;\n"
    "writeInterval 1;\npurgeWrite 0;\nwriteFormat ascii;\nwritePrecision 8;\n"
    "timeFormat general;\ntimePrecision 6;\nrunTimeModifiable true;\n"
)


def wsl_run(cfg: OFConfig, bash: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cfg._build_wsl_cmd(bash), capture_output=True, text=True, timeout=timeout,
    )


def checkmesh_raw(case_dir: Path) -> str:
    cfg = OFConfig()
    linux_case = cfg._quoted_linux_path(case_dir)
    env_q = shlex.quote(cfg.env_script)
    r = wsl_run(cfg, f"source {env_q} 2>/dev/null; cd {linux_case} && checkMesh 2>&1")
    return r.stdout + r.stderr


def _flt(s: str) -> float:
    return float(s.replace(",", "").rstrip("."))


def parse_checkmesh(text: str) -> dict:
    m = re.search(r"Overall number of cells of each type:\s*$([\s\S]*?)\n\n", text, re.M)
    cell_types: list[tuple[str, int]] = []
    if m:
        for line in m.group(1).splitlines():
            lm = re.match(r"\s*([a-zA-Z ]+?)\s*:\s*(\d+)", line)
            if lm:
                cell_types.append((lm.group(1), int(lm.group(2))))
    utils: dict[str, int | None] = {}
    total_cells = sum(n for _, n in cell_types)
    wrong = re.search(r"incorrectly oriented (\d+) faces", text)
    n_pyramid = re.search(r"Face pyramids:\s*(\d+) incorectly oriented faces", text)
    n_pyramid = n_pyramid or re.search(r"(\d+) incorectly oriented faces", text)
    failed = re.search(r"Failed (\d+) mesh checks", text)
    closed = re.search(r"(\d+) open cells", text)
    neg = re.search(r"(\d+) negative volume cells", text)
    skew_max = re.search(r"Max skewness\s*[:=]\s*([\d.eE+-]+)", text)
    skew_avg = re.search(r"average skewness\s*[:=]\s*([\d.eE+-]+)", text)
    no_max = re.search(r"non-orthogonality\s+Max:\s*([\d.]+)", text)
    no_avg = re.search(r"average:\s*([\d.]+)", text)
    ar_max = re.search(r"Max aspect ratio\s*[:=]\s*([\d.eE+-]+)", text)
    min_vol = re.search(r"Min volume\s*[:=]\s*([\d.eE+-]+)", text)
    max_vol = re.search(r"Max volume\s*[:=]\s*([\d.eE+-]+)", text)
    utils = {
        "total_cells": total_cells,
        "tetrahedra": next((n for ct, n in cell_types if ct == "tetrahedra"), None),
        "hexahedra": next((n for ct, n in cell_types if ct == "hexahedra"), None),
        "polyhedra": next((n for ct, n in cell_types if ct == "polyhedra"), None),
        "wrong_oriented": int(wrong.group(1)) if wrong else None,
        "failed_checks": int(failed.group(1)) if failed else None,
        "open_cells": int(closed.group(1)) if closed else None,
        "negative_cells": int(neg.group(1)) if neg else None,
        "max_skewness": _flt(skew_max.group(1)) if skew_max else None,
        "avg_skewness": _flt(skew_avg.group(1)) if skew_avg else None,
        "max_non_ortho": _flt(no_max.group(1)) if no_max else None,
        "avg_non_ortho": _flt(no_avg.group(1)) if no_avg else None,
        "max_aspect": _flt(ar_max.group(1)) if ar_max else None,
        "min_volume": _flt(min_vol.group(1)) if min_vol else None,
        "max_volume": _flt(max_vol.group(1)) if max_vol else None,
        "mesh_ok": "Mesh OK" in text,
    }
    return utils


def prepare_case(name: str, tet_poly: Path = REF_TET) -> Path:
    case = BENCH_ROOT / name
    if case.exists():
        shutil.rmtree(case)
    poly = case / "constant" / "polyMesh"
    sysd = case / "system"
    poly.mkdir(parents=True)
    sysd.mkdir(parents=True)
    for f in ("points", "faces", "owner", "neighbour", "boundary"):
        shutil.copy2(tet_poly / f, poly / f)
    (sysd / "controlDict").write_text(CONTROL_DICT, encoding="ascii")
    (sysd / "fvSchemes").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
        "ddtSchemes { default steadyState; }\n"
        "gradSchemes { default Gauss linear; }\n"
        "divSchemes { default Gauss linear; }\n"
        "laplacianSchemes { default Gauss linear corrected; }\n"
        "interpolationSchemes { default linear; }\n"
        "snGradSchemes { default corrected; }\n",
        encoding="ascii",
    )
    (sysd / "fvSolution").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
        "solvers { p { solver PCG; preconditioner DIC; tolerance 1e-6; relTol 0.1; } }\n",
        encoding="ascii",
    )
    return case


def report(name: str, metrics: dict, extra: str = "") -> None:
    print(f"--- {name} ---")
    if metrics is None:
        print(extra)
        return
    print(
        f"  cells={metrics['total_cells']} "
        f"(tet={metrics['tetrahedra']} hex={metrics['hexahedra']} poly={metrics['polyhedra']})"
    )
    print(
        f"  wrong_oriented={metrics['wrong_oriented']} "
        f"failed_checks={metrics['failed_checks']} "
        f"open={metrics['open_cells']} neg_vol={metrics['negative_cells']}"
    )
    print(
        f"  skewness max={metrics['max_skewness']} avg={metrics['avg_skewness']} | "
        f"non-ortho max={metrics['max_non_ortho']} avg={metrics['avg_non_ortho']} | "
        f"aspect={metrics['max_aspect']}"
    )
    print(f"  min_vol={metrics['min_volume']} max_vol={metrics['max_volume']} mesh_ok={metrics['mesh_ok']}")
    print(extra)


def maybe_run_tet(case: Path) -> dict:
    text = checkmesh_raw(case)
    return parse_checkmesh(text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="tet", choices=["tet", "baseline", "variant_a", "dual"])
    ap.add_argument("--case", default="ref1", choices=["ref1", "valve1"])
    args = ap.parse_args()

    from cfmesh_autogui.core.terminal_face import TerminalFaceConverter

    if args.case == "valve1":
        TET = Path("C:/polybench/valve1/constant/polyMesh_tet_backup")
    else:
        TET = REF_TET

    case = prepare_case(f"{args.mode}_{args.case}_1", TET)

    if args.mode == "tet":
        m = maybe_run_tet(case)
        report("tet input", m)
        return

    t0 = time.monotonic()
    conv = TerminalFaceConverter(case)
    if args.mode == "baseline":
        conv._augment_disjoint_pair_matching = lambda *a, **k: 0
    res = conv.run()
    dt = time.monotonic() - t0
    text = checkmesh_raw(case)
    m = parse_checkmesh(text)
    report(args.mode, m, extra=f"  converter: success={res.success} cells {res.n_tets_before}->{res.n_cells_after} time={dt:.1f}s errors={res.errors}")


if __name__ == "__main__":
    main()
