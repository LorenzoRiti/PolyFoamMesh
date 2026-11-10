#!/usr/bin/env python3
"""Compare fTetWild, GMSH and snappyHexMesh on the question that matters:
how many geometries end with a valid mesh that really has boundary layers?

Every engine gets the same max/min cell size from the benchmark sizing and is
judged by the same checkMesh text.  The script does not simulate the GUI retries
or the Auto chain: it measures each engine alone.

Not comparable one to one, and reported separately:
- layers: poly 5 layers at h1 = 1% of max cell, snappy its "expert" template;
- BL extent: poly "coverage" is the share of wall faces layered, snappy reports
  mean layers and thickness percent.

HEAVY: runs meshing and WSL/OpenFOAM, one job at a time.  Do not start it while
other simulations need the machine; --dry-run only prints the plan.  Results are
saved after each run and finished runs are skipped when restarted.

Usage: python tools/compare_mesher_bl.py [--dry-run] [--cases a b]
           [--engines gmsh ftetwild snappy] [--workdir DIR]
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT / "benchmarks" / "geometries"
ENGINES = ("gmsh", "ftetwild", "snappy")
#: Must be rejected by design, so it has no mesh to compare.
EXCLUDED = {"non_watertight"}
BL_LAYERS = 5
TET_KEY = {"gmsh": "gmsh_direct_poly", "ftetwild": "ftetwild_poly"}

_LAYER_ROW = re.compile(r"^\s*(\S+)\s+(\d+)\s+([\d.]+)\s+([\d.eE+-]+)\s+([\d.]+)\s*$")


def parse_snappy_layers(log: str) -> dict | None:
    """Read the last per-patch layer table of a snappyHexMesh log.

    Returns face-weighted mean layers and thickness percent, or None when the
    log has no table.  A table with zero layers is a measured "no BL", not None.
    """
    head = log.rfind("overall thickness")
    if head < 0:
        return None
    rows = []
    for line in log[head:].splitlines()[1:]:
        match = _LAYER_ROW.match(line)
        if match:
            rows.append((int(match[2]), float(match[3]), float(match[5])))
        elif rows:
            break
    faces = sum(row[0] for row in rows)
    if not faces:
        return None
    return {
        "faces": faces,
        "avg_layers": round(sum(f * n for f, n, _ in rows) / faces, 3),
        "thickness_pct": round(sum(f * p for f, _, p in rows) / faces, 1),
    }


def has_bl(row: dict) -> bool:
    """True when the run really added layers, whichever way it reports them."""
    if row.get("engine") == "snappy":
        return float((row.get("bl") or {}).get("avg_layers") or 0.0) > 0.0
    return int((row.get("bl") or {}).get("prisms") or 0) > 0


def summarize(rows: list[dict]) -> dict:
    """Per-engine counts.  A fallback mesh never counts for the requested engine."""
    out: dict = {}
    for engine in sorted({r.get("engine") for r in rows}):
        mine = [r for r in rows if r.get("engine") == engine and r.get("status") != "SKIPPED"]
        credited = [r for r in mine if not r.get("fallback_used")]
        valid = [r for r in credited if r.get("verdict") == "Mesh OK"]
        with_bl = [r for r in valid if has_bl(r)]

        def median(key: str) -> float | None:
            values = [r[key] for r in with_bl if r.get(key) is not None]
            return round(statistics.median(values), 2) if values else None

        out[engine] = {
            "runs": len(mine),
            "fallback_not_credited": len(mine) - len(credited),
            "mesh_ok": len(valid),
            "mesh_ok_with_bl": len(with_bl),
            "median_non_ortho": median("non_ortho"),
            "median_skew": median("skew"),
            "median_seconds": median("seconds"),
        }
    return out


def _check(row: dict, case: Path) -> None:
    import benchmarks.run_benchmarks as rb
    from polyfoammesh.core.openfoam_runner import parse_checkmesh_output

    rc, text, _ = rb._run_check_mesh(case, timeout=900)
    row["verdict"] = "Mesh OK" if "Mesh OK" in text else f"Failed (rc={rc})"
    report = parse_checkmesh_output(text)
    row.update(cells=report.cells, non_ortho=report.max_non_ortho, skew=report.max_skewness)


def _run_poly(engine: str, geom: Path, wd: Path, max_cell: float, min_cell: float, row: dict) -> None:
    from w18_auto_table import convert_case, run_bl, run_dual

    from polyfoammesh.core.tet_source import TetSourceSettings, run_tet_source

    msh = wd / "tet.msh"
    res = run_tet_source(
        geom, msh,
        TetSourceSettings(source=engine, detail="medium", max_cell=max_cell, min_cell=min_cell),
        requested=engine,
    )
    row["effective_source"] = res.effective_source
    row["fallback_used"] = res.effective_source != engine
    if not res.success:
        raise RuntimeError(f"tet: {res.failure_reason}")
    case = wd / "case"
    convert_case(msh, case)
    row["dual"] = run_dual(case)
    if not row["dual"]["success"]:
        raise RuntimeError(f"dual: {row['dual']['errors']}")
    row["bl"] = run_bl(case, first_height=0.01 * max_cell, n_layers=BL_LAYERS)
    _check(row, case)


def _run_snappy(geom: Path, wd: Path, ctx: dict, row: dict) -> None:
    import benchmarks.run_benchmarks as rb
    from benchmarks.snappy_baseline import prepare_snappy_case

    case = rb._make_case_dir(wd, geom.stem)
    prepare_snappy_case(case, geom, ctx["meshes"], "expert", ctx["max_cell"], ctx["min_cell"])
    linux = rb._to_wsl_path(case)
    rc, _ = rb._wsl_of_run(f"cd {linux} && blockMesh 2>&1", timeout=120)
    if rc != 0:
        raise RuntimeError(f"blockMesh: exit {rc}")
    # Non-fatal, as in the benchmark runner: snappy can still mesh without it.
    row["feature_extract_rc"], _ = rb._wsl_of_run(
        f"cd {linux} && surfaceFeatureExtract 2>&1", timeout=60,
    )
    rc, log = rb._wsl_of_run(f"cd {linux} && snappyHexMesh -overwrite 2>&1", timeout=1200)
    (wd / "snappy.log").write_text(log, encoding="utf-8", errors="replace")
    if rc != 0:
        raise RuntimeError(f"snappyHexMesh: exit {rc}")
    row["bl"] = parse_snappy_layers(log)
    _check(row, case)


def run_one(engine: str, geom: Path, wd: Path) -> dict:
    """One engine on one geometry.  A failure is a result and never stops the run."""
    import benchmarks.run_benchmarks as rb

    row: dict = {"case": geom.stem, "engine": engine, "status": "FAILED"}
    t0 = time.monotonic()
    try:
        early, ctx = rb._prepare_geometry(geom, pipeline=engine, require_watertight=False)
        if early is not None:
            row.update(status="SKIPPED", detail=str(early.get("errors")))
            return row
        row["max_cell"], row["min_cell"] = ctx["max_cell"], ctx["min_cell"]
        wd.mkdir(parents=True, exist_ok=True)
        if engine == "snappy":
            _run_snappy(geom, wd, ctx, row)
        else:
            _run_poly(engine, geom, wd, ctx["max_cell"], ctx["min_cell"], row)
        row["status"] = "DONE"
    except Exception as exc:  # noqa: BLE001 - one run must not end the comparison
        row["detail"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        row["traceback"] = traceback.format_exc(limit=3)
    row["seconds"] = round(time.monotonic() - t0, 1)
    return row


def default_cases() -> list[str]:
    return sorted(p.stem for p in GEO.glob("*.stl") if p.stem not in EXCLUDED)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", nargs="+", default=default_cases())
    parser.add_argument("--engines", nargs="+", choices=ENGINES, default=list(ENGINES))
    parser.add_argument("--workdir", type=Path, default=Path("C:/polyfoammesh_compare"))
    parser.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    args = parser.parse_args(argv)

    plan = [(case, engine) for case in args.cases for engine in args.engines]
    if args.dry_run:
        for case, engine in plan:
            print(f"{case} / {engine}")
        print(f"{len(plan)} runs, nothing executed")
        return 0

    sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tools")]
    args.workdir.mkdir(parents=True, exist_ok=True)
    results = args.workdir / "compare_results.json"
    rows: list[dict] = json.loads(results.read_text(encoding="utf-8")) if results.is_file() else []
    # Only an interruption is re-run: a failed mesh is a measured result.
    done = {(r["case"], r["engine"]) for r in rows}
    for case, engine in plan:
        if (case, engine) in done:
            continue
        row = run_one(engine, GEO / f"{case}.stl", args.workdir / case / engine)
        rows.append(row)
        results.write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
        print(f"[compare] {case}/{engine}: {row['status']} {row.get('verdict', '')} "
              f"{row.get('detail', '')} {row.get('seconds')}s", flush=True)
    summary = summarize(rows)
    (args.workdir / "compare_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
