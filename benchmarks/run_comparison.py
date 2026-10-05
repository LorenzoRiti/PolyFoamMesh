"""PolyFoamMesh vs snappyHexMesh comparison runner.

Parent mode: orchestrates geometry x runner, hard 1500 s timeout per run
via subprocess (spec D6), writes benchmarks/results/comparison_<ts>.json.
Child mode (--single): runs ONE runner for ONE geometry, writes one record.

Runners:
  hex           cfMesh cartesianMesh          (run_benchmarks._benchmark_one)
  poly          GMSH tet -> dual -> BL        (run_benchmarks._benchmark_poly_one)
  gmsh_direct   GMSH tet -> gmshToFoam        (this file; no dual/BL)
  snappy_base   snappy manual/tutorial style  (Task 7)
  snappy_expert snappy feature+layer tuned    (Task 7)

Local only: never pushed; exit code != 0 when one of OUR runners misses
the absolute thresholds (the fast gate).
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_SRC = _PROJECT / "src"
for _p in (str(_SRC), str(_PROJECT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from benchmarks.corpus import (  # noqa: E402
    ALL_RUNNERS, CORPUS, OUR_RUNNERS, RESULTS_DIR, RUN_TIMEOUT_S,
    fast_tier, full_tier, geometry_path,
)
from benchmarks.gates import passes_absolute_thresholds  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_comparison")


def make_record(
    geometry: str,
    runner: str,
    *,
    success: bool,
    wall_time_s: float,
    metrics: dict,
    errors: list[str],
    warnings: list[str],
    timed_out: bool = False,
) -> dict:
    fatal = any(("FATAL" in e) or ("timed out" in e.lower()) for e in errors)
    gate_input = {
        "cell_count": metrics.get("cell_count", 0),
        "max_non_ortho": metrics.get("max_non_ortho", 0.0),
        "max_skewness": metrics.get("max_skewness", 0.0),
        "max_aspect_ratio": metrics.get("max_aspect_ratio", 0.0),
        "neg_cells": metrics.get("neg_cells", 0),
        "timed_out": timed_out,
        "fatal": fatal,
    }
    passed, failed = passes_absolute_thresholds(gate_input)
    return {
        "geometry": geometry,
        "runner": runner,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "success": passed,
        "timed_out": timed_out,
        "wall_time_s": round(wall_time_s, 2),
        "cell_count": int(metrics.get("cell_count", 0)),
        "max_non_ortho": round(float(metrics.get("max_non_ortho", 0.0)), 2),
        "avg_non_ortho": round(float(metrics.get("avg_non_ortho", 0.0)), 2),
        "max_skewness": round(float(metrics.get("max_skewness", 0.0)), 2),
        "max_aspect_ratio": round(float(metrics.get("max_aspect_ratio", 0.0)), 2),
        "neg_cells": int(metrics.get("neg_cells", 0)),
        "min_volume": float(metrics.get("min_volume", 0.0)),
        "thresholds_pass": passed,
        "failed_thresholds": failed,
        "errors": errors,
        "warnings": warnings,
        "_raw_success": success,  # runner's own verdict, kept for diagnosis
    }


def _record_from_bench(geometry: str, runner: str, bench: dict) -> dict:
    """Normalise a run_benchmarks result dict into a comparison record."""
    errors = list(bench.get("errors") or [])
    timed_out = any("timed out" in e.lower() for e in errors)
    return make_record(
        geometry, runner,
        success=bool(bench.get("success")),
        wall_time_s=float(bench.get("wall_time_s", 0.0)),
        metrics={
            "cell_count": bench.get("cell_count", 0),
            "max_non_ortho": bench.get("max_non_ortho", 0.0),
            "avg_non_ortho": bench.get("avg_non_ortho", 0.0),
            "max_skewness": bench.get("max_skewness", 0.0),
            "max_aspect_ratio": bench.get("max_aspect_ratio", 0.0),
            "neg_cells": bench.get("neg_cells", 0),
            "min_volume": bench.get("min_volume", 0.0),
        },
        errors=errors,
        warnings=list(bench.get("warnings") or []),
        timed_out=timed_out,
    )


# Stage budgets (child-side); parent hard-cap is RUN_TIMEOUT_S = 1500.
HEX_MESH_TIMEOUT_S = 1200
CHECK_TIMEOUT_S = 120
GMSH_TIMEOUT_S = 1200
GMSH_TO_FOAM_TIMEOUT_S = 180
SNAPPY_BLOCK_TIMEOUT_S = 120
SNAPPY_FEATURE_TIMEOUT_S = 60
SNAPPY_MESH_TIMEOUT_S = 1200


def _stage_failure(stage: str, out: str, tail: int = 15) -> str:
    """Compose the error string for a failed stage.

    run_benchmarks._wsl_of_run signals a stage-budget overrun with the
    sentinel "TIMEOUT"; normalise it to the "<stage> timed out" wording used
    elsewhere in the harness (run_benchmarks.py, e.g. "cartesianMesh timed
    out") so the "timed out" predicates catch it — _record_from_bench's
    `timed_out` scan (it inspects the bench errors) and make_record's `fatal`
    predicate — and the record gets timed_out/fatal instead of a plain
    "failed: TIMEOUT" string that no predicate matches.
    """
    if out.strip() == "TIMEOUT":
        return f"{stage} timed out"
    return f"{stage} failed: " + "\n".join(out.splitlines()[-tail:])


def _check_mesh_failure(rc: int) -> str:
    """Compose the error string for a failed checkMesh stage from its rc.

    _run_check_mesh -> _wsl_of_run returns rc == -1 on a stage-budget
    overrun (and -2 when WSL is missing). Emitting the bare
    "checkMesh exit -1" matched neither the "timed out" nor the "FATAL"
    predicate, so the record kept timed_out=False despite the overrun:
    mirror run_benchmarks.py:451 ("checkMesh timed out") instead.
    """
    if rc == -1:
        return "checkMesh timed out"
    if rc == -2:
        return "WSL not found"
    return f"checkMesh exit {rc}"


def _make_result_fail(name, errors, warnings, stage_times) -> dict:
    return {
        "geometry": name, "success": False,
        "wall_time_s": sum(stage_times.values()),
        "cell_count": 0, "max_non_ortho": 0.0, "avg_non_ortho": 0.0,
        "max_skewness": 0.0, "max_aspect_ratio": 0.0, "neg_cells": 0,
        "min_volume": 0.0, "errors": errors, "warnings": warnings,
    }


def _run_gmsh_direct(stl_path: Path) -> dict:
    """GMSH tet volume -> gmshToFoam -> checkMesh (no dual, no BL)."""
    import benchmarks.run_benchmarks as rb

    name = stl_path.stem
    early, ctx = rb._prepare_geometry(stl_path, pipeline="gmsh_direct")
    if early is not None:
        early["pipeline"] = "gmsh_direct"
        return early
    assert ctx is not None  # guaranteed when early is None
    max_cell, min_cell = ctx["max_cell"], ctx["min_cell"]

    tmp_root = rb.WORK_ROOT / f"cmp_{int(time.time() * 1000)}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    stage_times: dict[str, float] = {}
    errors: list[str] = []
    warnings: list[str] = list(ctx["size_warnings"])
    stages: dict[str, bool] = {}
    try:
        case_dir = rb._make_case_dir(tmp_root, name)
        t0 = time.perf_counter()
        try:
            from polyfoammesh.core.gmsh_subprocess import run_gmsh_volume
            run_gmsh_volume(
                stl_path, case_dir / "mesh.msh", detail="medium",
                user_lc=max_cell, min_lc=min_cell,
                timeout_s=GMSH_TIMEOUT_S, threads=1,
            )
            stages["gmsh_volume"] = True
        except Exception as exc:
            stages["gmsh_volume"] = False
            errors.append(f"gmsh_volume: {exc}")
        stage_times["gmsh_volume"] = round(time.perf_counter() - t0, 2)
        if not stages["gmsh_volume"]:
            return _make_result_fail(name, errors, warnings, stage_times)

        rb._write_ctrl_dict(case_dir)
        rb._write_solver_dicts(case_dir)
        t0 = time.perf_counter()
        rc_, out = rb._wsl_of_run(
            f"cd {rb._to_wsl_path(case_dir)} && gmshToFoam mesh.msh 2>&1",
            timeout=GMSH_TO_FOAM_TIMEOUT_S,
        )
        stage_times["gmsh_to_foam"] = round(time.perf_counter() - t0, 2)
        if rc_ != 0:
            errors.append(_stage_failure("gmshToFoam", out))
            return _make_result_fail(name, errors, warnings, stage_times)

        t0 = time.perf_counter()
        cm_rc, cm_out, _cm_t = rb._run_check_mesh(case_dir, timeout=CHECK_TIMEOUT_S)
        stage_times["check_mesh"] = round(time.perf_counter() - t0, 2)
        if cm_rc != 0:
            errors.append(_check_mesh_failure(cm_rc))
            return _make_result_fail(name, errors, warnings, stage_times)

        from polyfoammesh.core.openfoam_runner import parse_checkmesh_output
        report = parse_checkmesh_output(cm_out)
        return {
            "geometry": name, "pipeline": "gmsh_direct", "success": report.passed,
            "wall_time_s": sum(stage_times.values()),
            "cell_count": report.cells, "max_non_ortho": report.max_non_ortho,
            "avg_non_ortho": report.avg_non_ortho, "max_skewness": report.max_skewness,
            "max_aspect_ratio": report.max_aspect_ratio, "neg_cells": report.neg_cells,
            "min_volume": report.min_volume, "errors": errors, "warnings": warnings,
        }
    except Exception as exc:
        errors.append(str(exc))
        return _make_result_fail(name, errors, warnings, stage_times)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _run_snappy(stl_path: Path, mode: str) -> dict:
    """blockMesh [+surfaceFeatureExtract] + snappyHexMesh + checkMesh via WSL."""
    import benchmarks.run_benchmarks as rb
    from benchmarks.snappy_baseline import prepare_snappy_case

    name = stl_path.stem
    early, ctx = rb._prepare_geometry(
        stl_path, pipeline=f"snappy_{mode}", require_watertight=False,
    )
    if early is not None:
        return early
    assert ctx is not None  # guaranteed when early is None
    meshes, max_cell, min_cell = ctx["meshes"], ctx["max_cell"], ctx["min_cell"]

    tmp_root = rb.WORK_ROOT / f"cmp_snappy_{int(time.time() * 1000)}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    warnings: list[str] = list(ctx["size_warnings"])
    stage_times: dict[str, float] = {}
    try:
        case_dir = rb._make_case_dir(tmp_root, name)
        prepare_snappy_case(case_dir, stl_path, meshes, mode, max_cell, min_cell)
        linux = rb._to_wsl_path(case_dir)

        t0 = time.perf_counter()
        rc_, out = rb._wsl_of_run(f"cd {linux} && blockMesh 2>&1", timeout=SNAPPY_BLOCK_TIMEOUT_S)
        stage_times["blockMesh"] = round(time.perf_counter() - t0, 2)
        if rc_ != 0:
            errors.append(_stage_failure("blockMesh", out, tail=10))
            return _make_result_fail(name, errors, warnings, stage_times)

        if mode == "expert":
            t0 = time.perf_counter()
            # OF v2512: surfaceFeatureExtract reads system/surfaceFeatureExtractDict
            # (written by prepare_snappy_case); no geometry argument on the CLI.
            rc_, out = rb._wsl_of_run(
                f"cd {linux} && surfaceFeatureExtract 2>&1",
                timeout=SNAPPY_FEATURE_TIMEOUT_S,
            )
            stage_times["featureExtract"] = round(time.perf_counter() - t0, 2)
            if rc_ != 0:
                warnings.append(f"surfaceFeatureExtract exit {rc_} (non-fatal)")
            elif not (case_dir / "constant" / "triSurface" / "surface.eMesh").exists():
                warnings.append("surfaceFeatureExtract produced no surface.eMesh")

        t0 = time.perf_counter()
        rc_, out = rb._wsl_of_run(
            f"cd {linux} && snappyHexMesh -overwrite 2>&1",
            timeout=SNAPPY_MESH_TIMEOUT_S,
        )
        stage_times["snappyHexMesh"] = round(time.perf_counter() - t0, 2)
        if rc_ != 0:
            errors.append(_stage_failure("snappyHexMesh", out))
            return _make_result_fail(name, errors, warnings, stage_times)

        t0 = time.perf_counter()
        cm_rc, cm_out, _ = rb._run_check_mesh(case_dir, timeout=CHECK_TIMEOUT_S)
        stage_times["check_mesh"] = round(time.perf_counter() - t0, 2)
        if cm_rc != 0:
            errors.append(_check_mesh_failure(cm_rc))
            return _make_result_fail(name, errors, warnings, stage_times)

        from polyfoammesh.core.openfoam_runner import parse_checkmesh_output
        report = parse_checkmesh_output(cm_out)
        return {
            "geometry": name, "success": report.passed,
            "wall_time_s": sum(stage_times.values()),
            "cell_count": report.cells, "max_non_ortho": report.max_non_ortho,
            "avg_non_ortho": report.avg_non_ortho, "max_skewness": report.max_skewness,
            "max_aspect_ratio": report.max_aspect_ratio, "neg_cells": report.neg_cells,
            "min_volume": report.min_volume, "errors": errors, "warnings": warnings,
        }
    except Exception as exc:
        errors.append(str(exc))
        return _make_result_fail(name, errors, warnings, stage_times)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def run_single(
    runner: str,
    stl_path: Path,
    out_path: Path,
    keep_case: bool = False,
    poly_sliver_dissolve: float = 0.0,
) -> int:
    import benchmarks.run_benchmarks as rb

    name = stl_path.stem
    if runner == "hex":
        bench = rb._benchmark_one(
            stl_path, keep_case=keep_case,
            mesh_timeout_s=HEX_MESH_TIMEOUT_S, check_timeout_s=CHECK_TIMEOUT_S,
        )
        rec = _record_from_bench(name, runner, bench)
    elif runner == "poly":
        bench = rb._benchmark_poly_one(
            stl_path, keep_case=keep_case,
            gmsh_timeout_s=GMSH_TIMEOUT_S,
            gmsh_to_foam_timeout_s=GMSH_TO_FOAM_TIMEOUT_S,
            check_timeout_s=CHECK_TIMEOUT_S,
            sliver_dissolve=poly_sliver_dissolve,
        )
        rec = _record_from_bench(name, runner, bench)
    elif runner == "gmsh_direct":
        rec = _record_from_bench(name, runner, _run_gmsh_direct(stl_path))
    elif runner == "snappy_base":
        rec = _record_from_bench(name, runner, _run_snappy(stl_path, "base"))
    elif runner == "snappy_expert":
        rec = _record_from_bench(name, runner, _run_snappy(stl_path, "expert"))
    else:
        raise ValueError(f"unknown runner {runner!r}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    logger.info("record: %s/%s pass=%s cells=%d", name, runner,
                rec["thresholds_pass"], rec["cell_count"])
    return 0 if rec["thresholds_pass"] else 1


def _kill_tree(pid: int) -> None:
    """Best-effort Windows process-tree kill (hard-timeout cleanup)."""
    if pid <= 0:
        return
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True, timeout=15,
    )


def _tier_specs(tier: str):
    return fast_tier() if tier == "fast" else full_tier()


def run_parent(
    tier: str,
    runners: list[str] | None,
    geometries: list[str] | None,
    keep: bool = False,
    poly_sliver_dissolve: float = 0.0,
    _child_out_dir: Path | None = None,
    _write_results: bool = True,
) -> tuple[list[dict], int]:
    specs = _tier_specs(tier)
    if geometries:
        specs = [s for s in specs if s.name in geometries]
    runner_list = runners or list(ALL_RUNNERS)

    child_dir = _child_out_dir or (RESULTS_DIR / f".tmp_{int(time.time() * 1000)}")
    child_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []

    for spec in specs:
        for runner in runner_list:
            out = child_dir / f"{spec.name}__{runner}.json"
            cmd = [
                sys.executable, str(Path(__file__).resolve()),
                "--single", "--runner", runner,
                "--geometry", spec.name, "--out", str(out),
            ]
            if keep:
                cmd.append("--keep")
            if poly_sliver_dissolve > 0.0:
                cmd += ["--poly-sliver-dissolve", str(poly_sliver_dissolve)]
            logger.info("RUN %s / %s (cap %ds)", spec.name, runner, RUN_TIMEOUT_S)
            try:
                proc = subprocess.run(cmd, timeout=RUN_TIMEOUT_S, capture_output=True, text=True)
                if out.exists():
                    rec = json.loads(out.read_text("utf-8"))
                else:
                    rec = make_record(
                        spec.name, runner, success=False, wall_time_s=float(RUN_TIMEOUT_S),
                        metrics={}, errors=[f"child exited {proc.returncode} without a record"],
                        warnings=[], timed_out=True,
                    )
            except subprocess.TimeoutExpired as exc:
                _kill_tree(getattr(exc, "pid", 0))
                rec = make_record(
                    spec.name, runner, success=False, wall_time_s=float(RUN_TIMEOUT_S),
                    metrics={}, errors=[f"hard timeout {RUN_TIMEOUT_S}s"],
                    warnings=[], timed_out=True,
                )
            records.append(rec)

    if _write_results:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        out_path = RESULTS_DIR / f"comparison_{ts}.json"
        out_path.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
        logger.info("results: %s (%d records)", out_path, len(records))
        shutil.rmtree(child_dir, ignore_errors=True)

    exit_code = 0
    for rec in records:
        if rec["runner"] in OUR_RUNNERS and not rec.get("thresholds_pass"):
            exit_code = 1
    return records, exit_code


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="PolyFoamMesh vs snappy comparison")
    p.add_argument("--single", action="store_true", help="child mode: one run")
    p.add_argument("--tier", choices=["fast", "full"], default="fast",
                   help="parent mode: which tier to run (default: fast)")
    p.add_argument("--runner", action="append", choices=list(ALL_RUNNERS),
                   help="parent mode: restrict runners (repeatable)")
    p.add_argument("--geometry", action="append",
                   help="parent mode: restrict geometries (repeatable); "
                        "child mode: geometry name")
    p.add_argument("--out", help="child mode: record output path")
    p.add_argument("--keep", action="store_true")
    p.add_argument("--poly-sliver-dissolve", type=float, default=0.0,
                   help="opt-in: dissolve sub-threshold boundary stars on the "
                        "poly runner (0 = off)")
    return p


def main(args: list[str] | None = None) -> int:
    opts = _build_parser().parse_args(args)
    if opts.single:
        geom = opts.geometry[0] if opts.geometry else None
        if not (opts.runner and geom and opts.out):
            logger.error("--single requires --runner --geometry --out")
            return 2
        spec = next((s for s in CORPUS if s.name == geom), None)
        if spec is None:
            logger.error("unknown geometry %r", geom)
            return 2
        try:
            return run_single(
                opts.runner[0], geometry_path(spec), Path(opts.out), opts.keep,
                poly_sliver_dissolve=opts.poly_sliver_dissolve,
            )
        except ValueError as exc:
            logger.error("%s", exc)
            return 2
    _, exit_code = run_parent(
        opts.tier, opts.runner, opts.geometry, opts.keep,
        poly_sliver_dissolve=opts.poly_sliver_dissolve,
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
