"""FASE 7 — dual di PRODUZIONE (collapse ON) vs exact + BL local termination.

La produzione (`core/openfoam_runner.py:1652`) converte tet->poly con
`collapse_smooth_edges=True, boundary_feature_angle=40.0,
collapse_volume_tolerance=0.10`. Tutte le misure BL FASE 2-6 usano il
converter con i default (collapse OFF, tre quad per triangolo di bordo).

Per ogni geometria (valvola da tet backup, cilindro GMSH):
  1. prepara un case tet e lo converte DUE volte (exact e production);
  2. riporta statistiche converter (celle, facce di bordo, istogramma
     facce, defect_breakdown, drift di volume) + checkMesh di input;
  3. esegue il BL (`decoupled` e/o `early_exit`) su ciascun dual, con
     copia fresca per run;
  4. checkMesh reale (WSL) dopo ogni run riuscito.

Uso:
  python tools/bench_bl_production_topology.py
  python tools/bench_bl_production_topology.py --cases valve --modes early_exit
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
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import numpy as np  # noqa: E402

import polyfoammesh.core.foam_mesh_io as fio  # noqa: E402
from polyfoammesh.config import OFConfig  # noqa: E402
from polyfoammesh.core.bl_poly import (  # noqa: E402
    PolyBoundaryLayerEngine, _pyramid_violations,
)
from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402

WORK = Path("C:/polybench2/fase7_production")
VALVE_TET = Path("C:/polybench/valve1/constant/polyMesh_tet_backup")

# Exactly the production converter kwargs (openfoam_runner.py:1652).
PROD_KWARGS = dict(
    collapse_smooth_edges=True,
    boundary_feature_angle=40.0,
    collapse_volume_tolerance=0.10,
)

SYS_CTRL = (
    "FoamFile { version 2.0; format ascii; class dictionary; "
    "object controlDict; }\n"
    "application checkMesh;\nstartFrom startTime;\nstartTime 0;\n"
    "stopAt endTime;\nendTime 1;\ndeltaT 1;\nwriteControl timeStep;\n"
    "writeInterval 1;\npurgeWrite 0;\nwriteFormat ascii;\n"
    "writePrecision 16;\nwriteCompression off;\ntimeFormat general;\n"
    "timePrecision 6;\nrunTimeModifiable false;\n"
)
SYS_SCHEMES = (
    "FoamFile { version 2.0; format ascii; class dictionary; "
    "object fvSchemes; }\nddtSchemes { default steadyState; }\n"
    "gradSchemes { default Gauss linear; }\n"
    "divSchemes { default none; }\n"
    "laplacianSchemes { default Gauss linear corrected; }\n"
    "interpolationSchemes { default linear; }\n"
    "snGradSchemes { default corrected; }\n"
)
SYS_SOLUTION = (
    "FoamFile { version 2.0; format ascii; class dictionary; "
    "object fvSolution; }\nsolvers { }\nSIMPLE { }\n"
)


def write_skeleton(case: Path) -> None:
    (case / "system").mkdir(parents=True, exist_ok=True)
    (case / "system" / "controlDict").write_text(SYS_CTRL, encoding="ascii")
    (case / "system" / "fvSchemes").write_text(SYS_SCHEMES, encoding="ascii")
    (case / "system" / "fvSolution").write_text(SYS_SOLUTION, encoding="ascii")


def checkmesh(cfg: OFConfig, case: Path) -> dict:
    r = subprocess.run(
        cfg._build_wsl_cmd(
            f"source {shlex.quote(cfg.env_script)} 2>/dev/null; "
            f"cd {cfg._quoted_linux_path(case)} && checkMesh 2>&1"
        ),
        capture_output=True, text=True, timeout=1800,
    )
    text = r.stdout + r.stderr

    def _int(pat):
        m = re.search(pat, text)
        return int(m.group(1).replace(",", "")) if m else 0

    def _num(pat):
        m = re.search(pat, text)
        return float(m.group(1).rstrip(".")) if m else 0.0

    return {
        "cells": _int(r"cells:\s+(\d+)"),
        "wrong_oriented": _int(r"(\d+)\s+faces are incorrectly oriented"),
        "nonortho_errors": _int(r"non-orthogonality errors:\s*(\d+)"),
        "max_non_orth": _num(r"non-orthogonality\s+Max:\s*([\d.]+)"),
        "max_skewness": _num(r"Max skewness\s*[:=]\s*([\d.]+)"),
        "max_aspect_ratio": _num(r"Max aspect ratio\s*[:=]\s*([\d.]+)"),
        "neg_volume_cells": _int(r"negative volume cells:\s*(\d+)"),
        "mesh_ok": "Mesh OK." in text,
        "failed_checks": _int(r"Failed\s+(\d+)\s+mesh checks"),
    }


# ---------------------------------------------------------------------------
# case preparation / conversion
# ---------------------------------------------------------------------------

def prepare_tet_case(src: Path, case: Path) -> Path:
    shutil.rmtree(case, ignore_errors=True)
    (case / "constant").mkdir(parents=True)
    shutil.copytree(src, case / "constant" / "polyMesh")
    write_skeleton(case)
    return case


def convert(case: Path, production: bool) -> dict:
    kwargs = dict(PROD_KWARGS) if production else {}
    t0 = time.monotonic()
    res = TetPolyDualConverter(case, log=lambda m: None, **kwargs).run()
    dt = time.monotonic() - t0
    vol_fraction = None
    if res.volume_before:
        vol_fraction = abs(res.volume_after - res.volume_before) / abs(res.volume_before)
    return {
        "converted": bool(res.success),
        "cells": int(res.n_cells_after),
        "time_s": round(dt, 1),
        "defects": dict(res.defect_breakdown or {}),
        "volume_drift": vol_fraction,
        "errors": list(res.errors)[:3],
    }


def polymesh_stats(poly: Path) -> dict:
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    n_int = len(neigh)
    sizes = Counter(len(f) for f in faces)
    return {
        "n_cells": int(max(owner.max(), neigh.max())) + 1,
        "n_points": int(len(points)),
        "n_faces": int(len(faces)),
        "n_internal": int(n_int),
        "n_boundary": int(len(faces) - n_int),
        "face_sizes_boundary": {str(k): v for k, v in
                                sorted(Counter(len(f) for f in faces[n_int:]).items())},
        "face_sizes_all": {str(k): v for k, v in sorted(sizes.items())},
    }


def copy_case(src_case: Path, dst_case: Path) -> Path:
    shutil.rmtree(dst_case, ignore_errors=True)
    (dst_case / "constant").mkdir(parents=True)
    shutil.copytree(src_case / "constant" / "polyMesh",
                    dst_case / "constant" / "polyMesh")
    write_skeleton(dst_case)
    return dst_case


# ---------------------------------------------------------------------------
# cylinder builder (come bench_bl_thinfirst / bench_bl_poly_partial)
# ---------------------------------------------------------------------------

def build_cylinder(case: Path) -> Path:
    import trimesh  # noqa: E402
    from polyfoammesh.core.gmsh_subprocess import (  # noqa: E402
        run_gmsh_to_foam, run_gmsh_volume, write_case_skeleton,
    )
    shutil.rmtree(case, ignore_errors=True)
    for d in ("constant", "system"):
        (case / d).mkdir(parents=True, exist_ok=True)
    stl = case / "cylinder.stl"
    cyl = trimesh.creation.cylinder(radius=0.5, height=2.0, sections=48)
    cyl.export(stl)
    msh = case / "mesh.msh"
    run_gmsh_volume(stl, msh, detail="medium", user_lc=0.0, min_lc=0.0)
    write_case_skeleton(case)
    run_gmsh_to_foam(case, "mesh.msh")
    points, faces, owner, neigh, patches = fio.read_polymesh(
        case / "constant" / "polyMesh")
    n_int = len(neigh)

    def classify(pi):
        s, k = patches[pi]["startFace"], patches[pi]["nFaces"]
        nz, area = 0.0, 0.0
        for fi in range(s, s + k):
            f = faces[fi]
            p = points[f]
            c0 = p.mean(0)
            a = p - c0
            b = np.roll(p, -1, axis=0) - c0
            sf = 0.5 * np.cross(a, b).sum(0)
            nz += abs(sf[2])
            area += np.linalg.norm(sf)
        return "wall" if area and nz / area < 0.9 else "cap"

    caps = [pi for pi in range(len(patches)) if classify(pi) == "cap"]
    cap_names = {pi: (["inlet", "outlet"] + [f"cap{i}" for i in range(len(caps) - 2)])
                 [k] for k, pi in enumerate(caps)}
    new_patches = []
    for pi, p in enumerate(patches):
        if pi in caps:
            nm, tp = cap_names[pi], "patch"
        else:
            nm, tp = "wall", "wall"
        new_patches.append({**p, "name": nm, "type": tp})
    fio.write_polymesh(
        case / "constant" / "polyMesh", points, faces,
        np.asarray(owner, dtype=np.int64), neigh, new_patches,
    )
    return case


# ---------------------------------------------------------------------------

def run_bl(case: Path, mode: str, n_layers: int, h1: float,
           max_rounds: int = 3, criterion: str = "dual_convexity",
           exclude_widen: int = 0) -> dict:
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    early = mode == "early_exit"
    legacy = mode == "legacy"
    if mode == "decoupled_vertex":
        lt: bool | str = "decoupled_vertex"
    elif legacy:
        lt = True
    else:
        lt = "decoupled"
    t0 = time.monotonic()
    res = eng.run(
        n_layers=n_layers, first_height=h1, growth_rate=1.2,
        apply_to_all=True, local_termination=lt,
        concavity_criterion=criterion,
        early_exit_intermediates=early,
        local_max_rounds=max_rounds,
        local_exclude_widen=exclude_widen,
    )
    dt = time.monotonic() - t0
    n_builds = sum(1 for w in res.warnings
                   if "local validation failed" in w) + (1 if res.success else 0)
    return {
        "mode": mode,
        "success": bool(res.success),
        "scale": res.stats.get("scale"),
        "excluded_iterative": res.stats.get("local_excluded_faces"),
        "n_prism_cells": int(res.n_prism_cells),
        "n_builds": int(n_builds),
        "time_s": round(dt, 1),
        "warnings": list(res.warnings),
        "errors": list(res.errors)[:3],
    }


def evaluate(cfg: OFConfig, name: str, tet_case: Path, production: bool,
             modes: list[str], n_layers: int, h1: float,
             results: dict, max_rounds: int = 3,
             criterion: str = "dual_convexity",
             exclude_widen: int = 0) -> None:
    variant = "production_collapsed" if production else "exact"
    print(f"\n===== {name} / {variant} =====", flush=True)
    case = prepare_tet_case(tet_case, WORK / f"{name}_{variant}")
    cstats = convert(case, production)
    print(f"  converter: {json.dumps(cstats)}", flush=True)
    entry: dict = {"converter": cstats}
    results.setdefault(name, {})[variant] = entry
    if not cstats["converted"]:
        return
    pstats = polymesh_stats(case / "constant" / "polyMesh")
    entry["polymesh"] = pstats
    print(f"  polymesh: cells={pstats['n_cells']} "
          f"bnd={pstats['n_boundary']} "
          f"bnd_sizes={pstats['face_sizes_boundary']}", flush=True)
    # input pyramid-defect split (boundary faces are the only ones the BL
    # selection can ever exclude; internal ones permanently consume budget)
    _pts, _faces, _own, _nei, _ = fio.read_polymesh(
        case / "constant" / "polyMesh")
    _n_int = len(_nei)
    _n_cells = int(max(_own.max(), _nei.max())) + 1
    pyr_t, pyr_idx = _pyramid_violations(_pts, _faces, _own, _nei, _n_int,
                                         _n_cells, return_idx=True)
    pyr_b = int(sum(1 for fi in pyr_idx if int(fi) >= _n_int))
    entry["pyr_input"] = {"total": int(pyr_t), "boundary": pyr_b,
                          "internal": int(pyr_t) - pyr_b}
    print(f"  input pyr: {entry['pyr_input']}", flush=True)
    cm_in = checkmesh(cfg, case)
    entry["checkmesh_input"] = cm_in
    print(f"  input checkMesh: {json.dumps(cm_in)}", flush=True)

    for mode in modes:
        run_case = copy_case(case, WORK / f"{name}_{variant}_{mode}")
        r = run_bl(run_case, mode, n_layers, h1,
                   max_rounds=max_rounds, criterion=criterion,
                   exclude_widen=exclude_widen)
        r["max_rounds"] = max_rounds
        r["criterion"] = criterion
        r["exclude_widen"] = exclude_widen
        entry[mode] = r
        print(f"  BL {mode}: success={r['success']} scale={r['scale']} "
              f"excluded={r['excluded_iterative']} prisms={r['n_prism_cells']} "
              f"builds={r['n_builds']} {r['time_s']}s err={r['errors'][:1]}",
              flush=True)
        for w in r["warnings"]:
            print(f"      warn: {w}", flush=True)
        if r["success"]:
            cm = checkmesh(cfg, run_case)
            r["checkmesh"] = cm
            print(f"    checkMesh: {json.dumps(cm)}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", default="valve,cylinder",
                    help="valve, cylinder (virgola)")
    ap.add_argument("--modes", default="early_exit,decoupled",
                    help="early_exit, decoupled, decoupled_vertex, legacy "
                         "(virgola)")
    ap.add_argument("--variants", default="exact,production",
                    help="exact, production (virgola)")
    ap.add_argument("--max-rounds", type=int, default=3,
                    help="local_max_rounds per scala (default 3)")
    ap.add_argument("--criterion", default="dual_convexity",
                    help="angle_fade | dual_convexity")
    ap.add_argument("--exclude-widen", type=int, default=0,
                    help="local_exclude_widen: 0..3, default 0 (off)")
    ap.add_argument("--skip-wsl", action="store_true")
    ap.add_argument("--work-dir", type=str, default=None,
                    help="override WORK (default C:/polybench2/fase7_production)")
    args = ap.parse_args()

    cfg = OFConfig()
    if not cfg.validate() and not args.skip_wsl:
        print("WSL/OpenFOAM non disponibile — usa --skip-wsl")
        return 2
    global WORK
    if args.work_dir:
        WORK = Path(args.work_dir)
    WORK.mkdir(parents=True, exist_ok=True)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    want = {c.strip() for c in args.cases.split(",") if c.strip()}
    want_var = {v.strip() for v in args.variants.split(",") if v.strip()}
    results: dict = {}

    if "cylinder" in want:
        cyl_case = build_cylinder(WORK / "build_cylinder")
        print(f"[cyl] tet case pronto ({len(fio.read_polymesh(cyl_case / 'constant' / 'polyMesh')[1])} faces)",
              flush=True)
        for prod in (False, True):
            if ("production" if prod else "exact") not in want_var:
                continue
            evaluate(cfg, "cylinder", cyl_case / "constant" / "polyMesh",
                     prod, modes, 3, 0.005, results,
                     max_rounds=args.max_rounds, criterion=args.criterion,
                     exclude_widen=args.exclude_widen)
    if "valve" in want:
        if not VALVE_TET.exists():
            print(f"[valve] tet backup mancante: {VALVE_TET}")
        else:
            for prod in (False, True):
                if ("production" if prod else "exact") not in want_var:
                    continue
                evaluate(cfg, "valve", VALVE_TET, prod, modes, 2, 1e-5,
                         results, max_rounds=args.max_rounds,
                         criterion=args.criterion,
                         exclude_widen=args.exclude_widen)

    out = WORK / (
        f"fase7_result_r{args.max_rounds}_{args.criterion}.json")
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nrisultati in {out}")

    print("\n=== RIEPILOGO ===")
    for name, variants in results.items():
        for variant, e in variants.items():
            pm = e.get("polymesh", {})
            line = (f"{name}/{variant}: cells={pm.get('n_cells')} "
                    f"bnd={pm.get('n_boundary')} "
                    f"defects={e.get('converter', {}).get('defects')}")
            for mode in modes:
                r = e.get(mode)
                if r:
                    line += (f" | {mode}: ok={r['success']} scale={r['scale']} "
                             f"excl={r['excluded_iterative']} "
                             f"prisms={r['n_prism_cells']} {r['time_s']}s")
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
