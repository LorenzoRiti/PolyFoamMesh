#!/usr/bin/env python3
"""W18 §3.4 verification table: auto vs explicit engines, real runs.

For each case: preflight + choose_polymesh_engine (auto chain), then
tet -> dual -> real checkMesh for the auto primary AND each explicit
engine, same detail/settings. R1 additionally runs the 5-layer
production BL (coverage is part of its exit criterion).

Writes w18_table.json incrementally (per-case rows survive crashes).
Slow: hours in background. checkMesh needs WSL2+OF.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CASES = [
    "venturi",
    "concave_bracket",
    "dirty_cad",
    "mini_valve",
    "thin_gap",
    "cylinder",
    "cube_duct",
]
GEO = ROOT / "benchmarks" / "geometries"
WORK = Path(r"C:\polyfoammesh_w18")
R1_STEP = Path(os.environ.get("POLYFOAMMESH_R1_STEP", "r1_part.step"))
R1_SURFACE = Path(r"C:\polyfoammesh_r1c\parte4_surface.stl")

TABLE = WORK / "w18_table.json"


def save(rows: list[dict]):
    TABLE.write_text(json.dumps(rows, indent=1), encoding="utf-8")


def load_rows() -> list[dict]:
    if TABLE.is_file():
        try:
            return json.loads(TABLE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def log(msg: str):
    print(f"[w18table] {msg}", flush=True)


def tet_mesher_key_to_source(key: str) -> str:
    return {"gmsh_direct_poly": "gmsh", "ftetwild_poly": "ftetwild"}[key]


def run_tet(engine: str, geom: Path, msh: Path, detail: str = "medium") -> dict:
    """Tet stage. Retries fTetWild once on manifold flake (wall03 pattern).

    Records the EFFECTIVE source: run_tet_source falls back on explicit
    failure, so an "explicit gmsh" row may carry an fTetWild mesh — the
    caller must not credit the explicit engine for a fallback mesh.
    """
    from polyfoammesh.core.tet_source import TetSourceSettings, run_tet_source

    source = tet_mesher_key_to_source(engine)
    attempts = 2 if source == "ftetwild" else 1
    last: dict = {}
    for _ in range(attempts):
        res = run_tet_source(
            geom, msh,
            TetSourceSettings(source=source, detail=detail),
            requested=source,
        )
        last = res.to_mapping()
        last["effective_mesher"] = (
            "ftetwild_poly" if res.effective_source == "ftetwild"
            else "gmsh_direct_poly" if res.effective_source == "gmsh"
            else None
        )
        if res.success:
            return last
        if "non-manifold" not in (res.failure_reason or "").lower():
            return last
    return last


def convert_case(msh: Path, case: Path):
    import numpy as np  # noqa: F401 (kept for parity with gate flow)

    from polyfoammesh.core.case_setup import write_control_dict
    from polyfoammesh.core.mesh_converter import msh_to_of_polymesh

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


def run_dual(case: Path, ftetwild_path: bool | None = None) -> dict:
    """Dual conversion.  With ``ftetwild_path`` set, the GUI's exact options.

    ``None`` keeps the historical W18 options (free-boundary smoothing on,
    no boundary split), which the product does NOT use: only for
    reproducing old W18 numbers.
    """
    from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter

    t0 = time.monotonic()
    if ftetwild_path is None:
        kwargs = dict(collapse_smooth_edges=True, boundary_feature_angle=40.0,
                      collapse_volume_tolerance=0.10, smooth_free_boundary=True)
    else:
        from polyfoammesh.core.openfoam_runner import production_dual_kwargs
        kwargs = production_dual_kwargs(ftetwild_path=ftetwild_path)
    res = TetPolyDualConverter(case, log=lambda m: None, **kwargs).run()
    return {
        "success": bool(res.success),
        "n_cells": int(res.n_cells_after),
        "n_polys": int(res.n_poly_cells),
        "defects": int(res.residual_defects),
        "breakdown": {k: int(v) for k, v in res.defect_breakdown.items()},
        "vol_err": float(abs(res.volume_after - res.volume_before) / max(res.volume_before, 1e-300)),
        "seconds": round(time.monotonic() - t0, 1),
        "errors": list(res.errors),
    }


def reference_first_height(geom: Path) -> float:
    """h1 at the GUI default fraction of max cell, sized on this geometry.

    Fail-soft for geometries outside the benchmark corpus (real user parts
    via auto_chain_e2e --geom): the benchmark sizing returns no reference
    there, so the tool's documented BL default (h1=0.00126) is used with a
    printed note instead of raising — the chain verdict stays measurable
    and the caveat is declared.
    """
    sys.path.insert(0, str(ROOT))
    import benchmarks.run_benchmarks as rb

    _, ctx = rb._prepare_geometry(geom, pipeline="auto", require_watertight=False)
    if ctx is None:
        print(
            f"[auto] no reference size for {geom.name} - BL default "
            "h1=0.00126 (declared: not corpus-sized)",
            flush=True,
        )
        return 0.00126
    return 0.01 * float(ctx["max_cell"])


def run_bl(case: Path, first_height: float = 0.00126, n_layers: int = 5) -> dict:
    """Default h1 is the R1 value: other geometries must pass their own."""
    from polyfoammesh.core.bl_config import BLConfig
    from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine

    t0 = time.monotonic()
    cfg, _warnings = BLConfig.from_mapping_tolerant({
        "nLayers": n_layers, "thicknessRatio": 1.2, "firstLayerThickness": first_height,
        "applyToAll": False, "minBaseEdgeFraction": 0.9,
        "terminateByBudget": True,
    }, max_cell=None)
    kw = cfg.engine_kwargs(None)
    r = PolyBoundaryLayerEngine(case, log=lambda m: None).run(**kw)
    brep = r.bl_report if isinstance(getattr(r, "bl_report", None), dict) else {}
    stats = r.stats if isinstance(getattr(r, "stats", None), dict) else {}
    return {
        "success": bool(r.success),
        "prisms": int(getattr(r, "n_prism_cells", getattr(r, "bl_prism_cells", 0))),
        # The engine publishes coverage in stats; bl_report is the older place.
        "coverage": brep.get("coverage", stats.get("wall_face_coverage_pct")),
        "median_layers": stats.get("median_layers_per_face"),
        "h1_ratio": stats.get("h1_ratio"),
        "seconds": round(time.monotonic() - t0, 1),
        "errors": list(getattr(r, "errors", [])),
    }


def run_checkmesh(case: Path) -> dict:
    import re

    from polyfoammesh.config import OFConfig

    t0 = time.monotonic()
    cmd = OFConfig().build_check_mesh_cmd(case)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    text = (proc.stdout or "") + (proc.stderr or "")
    verdict = "Mesh OK" if "Mesh OK" in text else ("Failed" if "Failed" in text else f"rc={proc.returncode}")
    m = re.search(r"non-orthogonality Max:\s*([\d.]+)", text)
    s = re.search(r"Max skewness\s*=\s*([\d.]+)", text)
    f = re.search(r"Failed (\d+) mesh checks", text)
    p = re.search(r"(\d+) +incorrectly oriented|(\d+) faces are incorrectly", text)
    return {
        "rc": int(proc.returncode),
        "verdict": verdict,
        "nomax": float(m.group(1)) if m else None,
        "skewmax": float(s.group(1)) if s else None,
        "failed_checks": int(f.group(1)) if f else (0 if verdict == "Mesh OK" else None),
        "bad_pyramids": int(p.group(1) or p.group(2)) if p else 0,
        "seconds": round(time.monotonic() - t0, 1),
    }


def run_engine(engine: str, geom: Path, tag: str, detail: str = "medium", with_bl: bool = False) -> dict:
    wd = WORK / tag
    wd.mkdir(parents=True, exist_ok=True)
    row: dict = {"engine": engine, "tag": tag}
    t0 = time.monotonic()
    try:
        msh = wd / "case.msh"
        tet = run_tet(engine, geom, msh, detail)
        row["tet"] = {"success": tet.get("success"), "n_tets": tet.get("n_tets"),
                      "stats": tet.get("stats"), "error": tet.get("failure_reason"),
                      "seconds": tet.get("stage_times")}
        if not tet.get("success"):
            raise RuntimeError(f"tet failed: {tet.get('failure_reason')}")
        case = wd / "case"
        convert_case(msh, case)
        row["dual"] = run_dual(case)
        if not row["dual"]["success"]:
            raise RuntimeError(f"dual failed: {row['dual']['errors']}")
        if with_bl:
            row["bl"] = run_bl(case)
            if not row["bl"]["success"]:
                raise RuntimeError(f"BL failed: {row['bl']['errors']}")
        row["checkmesh"] = run_checkmesh(case)
        row["status"] = "DONE"
    except Exception as exc:
        row["status"] = f"STOPPED: {type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc(limit=3)
    row["seconds"] = round(time.monotonic() - t0, 1)
    return row


def main() -> int:
    from polyfoammesh.core.tet_source import choose_polymesh_engine, preflight_tet_source

    WORK.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    done = {(r.get("case"), r.get("engine")) for r in rows if r.get("status") == "DONE"}

    def record(case: str, engine: str, row: dict, auto_chain: list | None = None):
        row.update({"case": case, "engine": engine,
                    "auto_chain": auto_chain, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
        rows.append(row)
        save(rows)
        log(f"{case}/{engine}: {row.get('status')} "
            f"check={((row.get('checkmesh') or {}).get('verdict'))} "
            f"{row.get('seconds')}s")

    for case in CASES:
        geom = GEO / f"{case}.stl"
        pre = preflight_tet_source(geom)
        plan = choose_polymesh_engine(pre)
        chain = plan.chain()
        log(f"{case}: auto -> {chain} ({plan.reason})")
        for engine in [chain[0]] + [e for e in ("gmsh_direct_poly", "ftetwild_poly") if e != chain[0]]:
            if (case, engine) in done:
                log(f"{case}/{engine}: already DONE, skip")
                continue
            record(case, engine, run_engine(engine, geom, f"{case}_{engine}"), chain)

    # R1: auto chain on the recovered STEP (watertight probe skips by design).
    if R1_STEP.is_file():
        pre = preflight_tet_source(R1_STEP)
        plan = choose_polymesh_engine(pre)
        chain = plan.chain()
        log(f"R1/Parte4.step: auto -> {chain} ({plan.reason})")
        for engine in chain:
            if ("R1", engine) in done:
                log(f"R1/{engine}: already DONE, skip")
                continue
            geom = R1_SURFACE if (engine == "ftetwild_poly" and R1_SURFACE.is_file()) else R1_STEP
            # fTetWild runs on the coarse surface STL built earlier; the
            # policy still records the STEP as the geometry (declared).
            record("R1", engine, run_engine(engine, geom, f"R1_{engine}",
                                            detail="coarse", with_bl=True), chain)
    else:
        log("R1 STEP missing — R1 skipped")
    log("TABLE COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
