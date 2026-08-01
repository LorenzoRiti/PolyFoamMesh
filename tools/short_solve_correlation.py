"""Short-solve hypothesis: does a 200/400-iteration solve give the same
refinement indicator as a converged one?

The SAMR loop's intermediate cycles solve only ~300 iterations on the
justified-but-unverified claim that the indicator needs just the spatial
structure of grad(U), which forms early. This measures it: solve the SAME
mesh to 200 / 400 / 3000 iterations, compute the normalised eta field each
time, and report the Pearson correlation and relative L2 difference of
eta(200)/eta(400) against eta(3000).

Usage:
    PY tools/short_solve_correlation.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(r"C:\Users\Davide Valoroso\cfmesh-autogui")
sys.path.insert(0, str(REPO / "src"))

from cfmesh_autogui.config import OFConfig  # noqa: E402
from cfmesh_autogui.core.boundary_reader import parse_boundary  # noqa: E402
from cfmesh_autogui.core.case_setup import (  # noqa: E402
    infer_patch_roles, set_wall_patch_types, setup_case,
)
from cfmesh_autogui.core.gmsh_subprocess import run_gmsh_to_foam, run_gmsh_volume, write_case_skeleton  # noqa: E402
from cfmesh_autogui.core.solution_adaptive import (  # noqa: E402
    compute_indicator, load_solution, run_solver, set_end_time,
)

WORK = Path(r"C:\cfmesh_work\short_solve")
STEP = Path(r"C:\cfmesh_work\venturi_run8\venturi.step")
BUDGETS = [200, 400, 3000]
cfg = OFConfig()


def build_case() -> Path:
    case = WORK / "case"
    if (case / "constant" / "polyMesh" / "points").exists():
        return case
    msh = case / "mesh_c0.msh"
    run_gmsh_volume(STEP, msh, "medium", user_lc=10 / 1000.0,
                    min_lc=(10 / 1000.0) * 0.2)
    write_case_skeleton(case)
    run_gmsh_to_foam(case, msh.name)
    patches = parse_boundary(case / "constant" / "polyMesh" / "boundary")
    roles = infer_patch_roles(case, patches, flow_direction=(1.0, 0.0, 0.0))
    set_wall_patch_types(case, [n for n, r in roles.items() if r == "wall"])
    setup_case(case, patches, inlet_velocity=(1.0, 0.0, 0.0),
               end_time=3000, residual_control=1e-5, write_interval=1000,
               patch_roles=roles)
    print(f"[build] case {case} ({len(patches)} patches)")
    return case


def eta_for(case: Path, budget: int) -> dict:
    set_end_time(case, budget)
    solve = run_solver(case, cfg, application="simpleFoam",
                       timeout_s=5400, on_line=lambda m: print(f"[solve] {m}"))
    grid = load_solution(case, cfg, timeout_s=1800, on_line=lambda m: print(m))
    ind = compute_indicator(grid)
    ind["iterations"] = solve["iterations"]
    ind["converged"] = solve["converged"]
    return ind


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    case = build_case()
    results = {}
    for budget in BUDGETS:
        print(f"\n=== solving to {budget} iterations ===")
        t0 = time.monotonic()
        ind = eta_for(case, budget)
        results[budget] = {
            "eta": ind["eta"].tolist(),
            "u_ref": ind["u_ref"],
            "converged": ind["converged"],
            "iterations": ind["iterations"],
            "eta_max": float(ind["eta_max"]),
            "eta_p95": float(ind["eta_p95"]),
            "wall_s": time.monotonic() - t0,
            "peak": list(ind["peak_location"]),
        }
        print(f"[{budget}] iterations_run={ind['iterations']} "
              f"converged={ind['converged']} eta_max={ind['eta_max']:.3f} "
              f"p95={ind['eta_p95']:.3f} u_ref={ind['u_ref']:.3f} "
              f"({results[budget]['wall_s']:.0f}s)")

    base = results[3000]["eta"]
    base = np.array(base)
    print("\n=== correlation vs the 3000-iteration solve ===")
    for budget in (200, 400):
        eta = np.array(results[budget]["eta"])
        r = np.corrcoef(eta, base)[0, 1]
        # Relative L2 of the NORMALISED fields (share u_ref scale).
        nrm = np.linalg.norm(eta) + 1e-12
        rel = np.linalg.norm(eta - base) / nrm
        print(f"  {budget} iter: Pearson r = {r:.4f}, rel-L2 = {rel:.3f}")

    (WORK / "short_solve_report.json").write_text(
        json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "eta"}
                    for k, v in results.items()}, indent=2, default=str)
    )
    print(f"\nsummary: {WORK / 'short_solve_report.json'}")


if __name__ == "__main__":
    main()