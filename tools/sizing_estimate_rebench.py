"""Sizing And Estimates: predicted (geometric) vs actual cell count.

Re-measure the displayed-estimated-cell-count claim in
`docs/residual_risks.md` "Sizing And Estimates": a budget/cap based on
the geometric formula (`estimate_cell_count_geometric` in
`core/geometry.py`); when the volume cannot be trusted (non-watertight
tessellation) the estimate is omitted rather than invented.

Approach (cheap, no extra GMSH runs beyond the cylinder build):
1. Build the cylinder once at the production mesh path (GMSH adaptive,
   detail "medium") so we know the actual cell count.
2. Read the actual cell count from the written polyMesh.
3. Compute the predicted (low, nominal, high) via
   `estimate_cell_count_geometric(meshes, volume, max_cell, min_cell,
   patch_sizes)` for a sweep of `max_cell_size` slider values
   (simulating the slider).
4. Document the "omitted" path: the formula's `patch_sizes=None` /
   `volume<=0` fallback (matching the UI's pre-formula gate that
   omits the display when the volume is invalid). The UI's hard
   "omitted" check on non-watertight tessellation happens upstream
   of this function; we measure what we can.

This is a one-shot benchmark; not part of the regular test suite.

Working dir: C:/polybench2/sizing_estimate_rebench/
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import trimesh  # noqa: E402

import polyfoammesh.core.foam_mesh_io as fio  # noqa: E402
from polyfoammesh.core.geometry import (  # noqa: E402
    compute_patch_cell_sizes, estimate_cell_count, estimate_cell_count_geometric,
)
from polyfoammesh.core.gmsh_subprocess import (  # noqa: E402
    run_gmsh_to_foam, run_gmsh_volume, write_case_skeleton,
)

WORK = Path("C:/polybench2/sizing_estimate_rebench")


def _cylinder_case() -> tuple[Path, list[trimesh.Trimesh], float, dict, int]:
    """Build a cylinder GMSH case at medium fineness; return
    (case_dir, meshes, volume, context, actual_cell_count)."""
    case = WORK / "case_cyl"
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
    _, faces, owner, neigh, _ = fio.read_polymesh(
        case / "constant" / "polyMesh")
    n_int = len(neigh)
    actual = int(max(owner.max(), neigh.max())) + 1
    meshes = [trimesh.creation.cylinder(radius=0.5, height=2.0, sections=48)]
    volume = float(sum(m.volume for m in meshes))
    detail = "medium"
    safe_max = 0.05
    safe_min = 0.01
    qmult = {"coarse": 1.5, "medium": 1.0, "fine": 0.6,
             "very_fine": 0.4}.get(detail, 1.0)
    eff_max = safe_max * qmult
    eff_min = safe_min * qmult
    patch_sizes, _, _ = compute_patch_cell_sizes(meshes, detail=detail)
    return case, meshes, volume, {
        "max_cell_size": eff_max, "min_cell_size": eff_min,
        "patch_sizes": patch_sizes,
    }, actual


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)

    print("=== 1. cylinder GMSH build (medium) ===")
    t0 = time.perf_counter()
    case, meshes, volume, ctx, actual = _cylinder_case()
    dt = time.perf_counter() - t0
    print(f"  built in {dt:.1f}s; volume={volume:.6g} m^3; "
          f"max_cell_size={ctx['max_cell_size']:.4f}; "
          f"min_cell_size={ctx['min_cell_size']:.4f}; "
          f"actual_cells={actual}")
    print(f"  patch_sizes: {ctx['patch_sizes']}")

    print("\n=== 2. predict_cells sweep (slider over max_cell_size) ===")
    print(f"  actual cell count: {actual}")
    print(f"  {'max_cell_size':>14s}  {'predicted_nom':>14s}  "
          f"{'predicted_low':>14s}  {'predicted_high':>14s}  "
          f"{'pred/actual':>11s}")
    rows = []
    for max_cell in (0.10, 0.05, 0.02, 0.01):
        min_cell = max_cell * 0.2
        core_cell = (max_cell + min_cell) / 2.0
        ps = dict(ctx["patch_sizes"])
        lo, nom, hi = estimate_cell_count_geometric(
            meshes, volume, core_cell, ps)
        ratio = nom / max(actual, 1)
        print(f"  {max_cell:>14.4f}  {nom:>14d}  {lo:>14d}  {hi:>14d}  "
              f"{ratio:>10.2f}x")
        rows.append({"max_cell": max_cell, "min_cell": min_cell,
                     "core_cell": core_cell,
                     "predicted_low": lo, "predicted_nom": nom,
                     "predicted_high": hi, "predicted_over_actual": ratio})
    blind_lo, blind_nom, blind_hi = estimate_cell_count(
        volume, ctx["max_cell_size"], ctx["min_cell_size"])
    print(f"  (blind estimate_cell_count: nominal={blind_nom}, "
          f"ratio={blind_nom / max(actual, 1):.2f}x)")

    print("\n=== 3. omitted/non-watertight fallback (formula's own branch) ===")
    # The formula returns the floor (100,100,100) when volume <= 0 or
    # patch_sizes is None; the UI's upstream "omitted" check on
    # non-watertight tessellation lives in main_window. Here we exercise
    # the formula's own fallbacks as a proxy.
    lo_a, nom_a, hi_a = estimate_cell_count_geometric(
        meshes, 0.0, 0.05, ctx["patch_sizes"])
    lo_b, nom_b, hi_b = estimate_cell_count_geometric(
        meshes, volume, 0.05, None)
    print(f"  volume=0, patch_sizes=given  -> ({lo_a},{nom_a},{hi_a})")
    print(f"  volume=ok, patch_sizes=None -> ({lo_b},{nom_b},{hi_b})")
    floor_hit = (lo_a == 100 and nom_a == 100 and hi_a == 100
                 and lo_b == blind_lo and nom_b == blind_nom
                 and hi_b == blind_hi)
    print(f"  formula falls back to floor (= blind estimate) when its inputs"
          f" are invalid: {floor_hit}")
    print("  UI's hard 'omitted on non-watertight' check is upstream of"
          " this formula (compute_volume in main_window) and not measured"
          " here -- the formula's own fallbacks are the relevant unit.")

    summary = {
        "actual_cells": actual,
        "volume_m3": volume,
        "rows": rows,
        "blind_nominal": blind_nom,
        "formula_floor_fallback_hit": floor_hit,
    }
    out_path = WORK / "result.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
