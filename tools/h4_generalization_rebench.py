"""H4 generalization rebench: decoupled_vertex on cubo duct, groove1, slot1.

Goal: confirm (or falsify) that `local_termination="decoupled_vertex"`
generalizes to the remaining validation geometries (cube duct,
groove1, slot1) beyond the cylinder and the production-collapsed
valve where H4 was already measured.

Pattern copied from tools/bench_bl_production_topology.py
(`run_bl` function) which already supports `decoupled_vertex`. The
geometry builders are copied verbatim from existing bench tools
(tools/bench_bl_thinfirst.py for groove1/slot1 tet-backup prep,
tools/bench_bl_poly_partial.py for the in-memory cube duct).

Re-measurement of the historical predictions in
notes/h4_generalization_reasoning.md. Comparison mode: `decoupled`
(face-level, current default for LT) vs `decoupled_vertex`
(per-vertex exclusion, H4 opt-in) on the same exact-converter output
(no collapse; this is the normal BL path, not the production-topology
path). Real checkMesh via WSL on every successful run.

Working dir: C:/polybench2/h4_generalization/
"""
from __future__ import annotations

import io
import json
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parent / "tests"))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

import numpy as np  # noqa: E402

import polyfoammesh.core.foam_mesh_io as fio  # noqa: E402
from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine  # noqa: E402
from polyfoammesh.core.gmsh_subprocess import write_case_skeleton  # noqa: E402
from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402

WORK = Path("C:/polybench2/h4_generalization")
GROOVE1_TET = Path("C:/polybench/groove1/constant/polyMesh_tet_backup")
SLOT1_TET = Path("C:/polybench/slot1/constant/polyMesh_tet_backup")
N_LAYERS = 3
H1 = 0.005


# ---------------------------------------------------------------------------
# Builders (verbatim copies; do not modify the geometry logic)
# ---------------------------------------------------------------------------

def build_cube_duct_case() -> Path:
    """In-memory tiny cube duct tet -> dual (copied from
    tools/bench_bl_poly_partial.build_cube_duct)."""
    from test_tet_poly_dual import build_tet_case
    case = WORK / "case_cube"
    shutil.rmtree(case, ignore_errors=True)
    (case / "constant").mkdir(parents=True, exist_ok=True)
    c = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=float)
    pts = np.vstack([c, [[0.5, 0.5, 0.5]]])
    faces2d = [
        [0, 1, 2, 3], [0, 4, 5, 1], [1, 5, 6, 2],
        [2, 6, 7, 3], [3, 7, 4, 0], [4, 7, 6, 5],
    ]
    tets = []
    for f in faces2d:
        for tri in ((f[0], f[1], f[2]), (f[0], f[2], f[3])):
            tets.append([tri[0], tri[1], tri[2], 8])
    build_tet_case(case, pts, tets, boundary_patch="all")
    return case


def _prepare_tet_case(src: Path, dst: Path) -> Path:
    """Copy a tet-backup into a fresh case dir + system skeleton.
    (Verbatim pattern from tools/bench_bl_thinfirst.py.)"""
    shutil.rmtree(dst, ignore_errors=True)
    (dst / "constant").mkdir(parents=True)
    shutil.copytree(src, dst / "constant" / "polyMesh")
    (dst / "system").mkdir(parents=True, exist_ok=True)
    # write_case_skeleton imported at module level
    write_case_skeleton(dst)
    return dst


def _convert_default(case: Path) -> dict:
    """Convert with default settings (exact dual; no collapse)."""
    t0 = time.perf_counter()
    res = TetPolyDualConverter(case, log=lambda m: None).run()
    dt = time.perf_counter() - t0
    pts, faces, owner, neigh, _ = fio.read_polymesh(
        case / "constant" / "polyMesh")
    n_int = len(neigh)
    return {
        "converted": bool(res.success),
        "cells": int(max(owner.max(), neigh.max())) + 1,
        "n_boundary": int(len(faces) - n_int),
        "time_s": round(dt, 1),
        "defects": dict(res.defect_breakdown or {}),
    }


# ---------------------------------------------------------------------------
# BL runner (verbatim pattern from tools/bench_bl_production_topology.py)
# ---------------------------------------------------------------------------

def run_bl(case: Path, mode: str) -> dict:
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    if mode == "decoupled_vertex":
        lt = "decoupled_vertex"
    elif mode == "legacy":
        lt = True
    else:
        lt = "decoupled"
    t0 = time.perf_counter()
    res = eng.run(
        n_layers=N_LAYERS, first_height=H1, growth_rate=1.2,
        apply_to_all=True, local_termination=lt,
        concavity_criterion="dual_convexity",
    )
    dt = time.perf_counter() - t0
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


def _checkmesh(cfg_factory, case: Path) -> dict:
    if cfg_factory is None:
        return {}
    cfg = cfg_factory()
    if not cfg.validate():
        return {}
    r = subprocess_run_checkmesh(cfg, case)
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
        "max_skewness": _num(r"Max skewness\s*[:=]\s*([\d.]+)"),
        "max_aspect_ratio": _num(r"Max aspect ratio\s*[:=]\s*([\d.]+)"),
        "mesh_ok": "Mesh OK." in text,
        "failed_checks": _int(r"Failed\s+(\d+)\s+mesh checks"),
    }


def subprocess_run_checkmesh(cfg, case):
    import subprocess
    r = subprocess.run(
        cfg._build_wsl_cmd(
            f"source {shlex.quote(cfg.env_script)} 2>/dev/null; "
            f"cd {cfg._quoted_linux_path(case)} && checkMesh 2>&1"
        ),
        capture_output=True, text=True, timeout=1800,
    )
    return r


# ---------------------------------------------------------------------------
# Per-geometry orchestration
# ---------------------------------------------------------------------------

def run_geom(name: str, prep_callable, cfg_factory) -> dict:
    case = prep_callable()
    cstats = _convert_default(case)
    out = {"converter": cstats, "cases": {}}
    for mode in ("decoupled", "decoupled_vertex"):
        run_case = case
        # fresh copy to avoid converter state between BL runs
        dst = WORK / f"{name}_{mode}"
        shutil.rmtree(dst, ignore_errors=True)
        (dst / "constant").mkdir(parents=True, exist_ok=True)
        shutil.copytree(case / "constant" / "polyMesh",
                        dst / "constant" / "polyMesh")
        (dst / "system").mkdir(parents=True, exist_ok=True)
        for fname in ("controlDict", "fvSchemes", "fvSolution"):
            pass
        # copy a minimal system skeleton (reuse write_case_skeleton helper)
        # write_case_skeleton imported at module level
        write_case_skeleton(dst)
        r = run_bl(dst, mode)
        out["cases"][mode] = r
        if r["success"]:
            cm = _checkmesh(cfg_factory, dst)
            out["cases"][mode]["checkmesh"] = cm
    return out


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    from polyfoammesh.config import OFConfig
    cfg = OFConfig()
    if not cfg.validate():
        print("WSL/OpenFOAM non disponibile: skip checkMesh; report only "
              "engine outcomes")
        cfg_factory = None
    else:
        cfg_factory = lambda: cfg

    prep = {
        "cube_duct": build_cube_duct_case,
        "groove1": lambda: _prepare_tet_case(GROOVE1_TET, WORK / "groove1_case"),
        "slot1":   lambda: _prepare_tet_case(SLOT1_TET,   WORK / "slot1_case"),
    }

    summary = {}
    for name, prep_call in prep.items():
        if not prep_call.__name__ == "<lambda>":
            # built-in builder (cube)
            try:
                summary[name] = run_geom(name, prep_call, cfg_factory)
            except Exception as exc:
                summary[name] = {"error": f"{type(exc).__name__}: {exc}"}
        else:
            src = GROOVE1_TET if "groove" in name else SLOT1_TET
            if not src.exists():
                summary[name] = {"skipped": f"{src} missing"}
                continue
            try:
                summary[name] = run_geom(name, prep_call, cfg_factory)
            except Exception as exc:
                summary[name] = {"error": f"{type(exc).__name__}: {exc}"}

    out = WORK / "result.json"
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"wrote {out}")

    # Console table
    for name, blob in summary.items():
        if "skipped" in blob or "error" in blob:
            print(f"\n=== {name}: {blob} ===")
            continue
        cstats = blob.get("converter", {})
        print(f"\n=== {name} ===  converter: {cstats.get('cells')} cells, "
              f"{cstats.get('n_boundary')} boundary faces, "
              f"defects={cstats.get('defects')}")
        for mode, r in blob["cases"].items():
            cm = r.get("checkmesh", {})
            print(f"  {mode:>20s}: success={r['success']} "
                  f"scale={r['scale']} excl={r['excluded_iterative']} "
                  f"prisms={r['n_prism_cells']} builds={r['n_builds']} "
                  f"{r['time_s']}s  "
                  f"checkMesh: {cm or '(skipped)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
