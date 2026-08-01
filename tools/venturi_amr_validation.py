"""Venturi solution-adaptive refinement validation.

Runs the full solve -> indicate -> remesh loop from
``cfmesh_autogui.core.solution_adaptive`` on a venturi (pipe that narrows to
a throat and widens back), proving with numbers that refinement lands where
the physics demands it:

  (a) velocity / velocity-gradient magnitude peaks at the throat,
  (b) after refinement, more cells concentrate in a fixed window around the
      throat than before,
  (c) the same-sized window in a straight section does NOT balloon.

Usage (from repo root, with the Python that has gmsh/pyvista):

    & "C:\\Users\\Davide Valoroso\\AppData\\Local\\Programs\\Python\\Python311\\python.exe" tools\\venturi_amr_validation.py --cycles 3

Every stage is echoed to the console with the same [tag] conventions the GUI
uses, and a JSON summary is written into the workdir.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\Davide Valoroso\cfmesh-autogui")
PY = r"C:\Users\Davide Valoroso\AppData\Local\Programs\Python\Python311\python.exe"

# Geometry: pipe along X. Stations (x, radius) in mm.
STATIONS = [
    (0.0, 50.0), (80.0, 50.0), (140.0, 38.0), (180.0, 24.0), (220.0, 15.0),
    (260.0, 15.0), (320.0, 30.0), (380.0, 44.0), (430.0, 50.0), (460.0, 50.0),
]
# Fixed 100 mm analysis windows (equal length, different physics).
THROAT_WINDOW = (200.0, 300.0)
STRAIGHT_WINDOW = (30.0, 130.0)


def build_venturi(step_path: Path) -> None:
    """Loft the radius stations into a solid and export STEP (mm units)."""
    import cadquery as cq

    wp = cq.Workplane("YZ")
    prev_x = None
    for x, r in STATIONS:
        if prev_x is None:
            wp = wp.circle(r)
        else:
            # workplane(offset=...) is RELATIVE to the current plane — the
            # cumulative station position must be passed as successive deltas.
            wp = wp.workplane(offset=x - prev_x).circle(r)
        prev_x = x
    solid = wp.loft()
    step_path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(solid, str(step_path))
    print(f"[venturi] geometry written: {step_path} ({len(STATIONS)} stations)")


def run_gmsh_volume(step_path: Path, msh_path: Path, detail: str,
                    user_lc: float, size_field: Path | None = None,
                    timeout_s: int = 1800) -> list[str]:
    """Run the app's own out-of-process GMSH meshing entry point."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    env["GMSH_SOLUTION_SIZE_FIELD"] = str(size_field) if size_field else ""
    cmd = [
        PY, "-m", "cfmesh_autogui.core.gmsh_wrapper", "volume",
        str(step_path), str(msh_path), detail,
        "0", "0", "1.2",
        repr(user_lc), repr(user_lc * 0.2), "0",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                       env=env, cwd=REPO, encoding="utf-8", errors="backslashreplace")
    if r.returncode != 0:
        raise RuntimeError(
            f"GMSH volume meshing failed:\n{r.stdout[-1500:]}\n{r.stderr[-500:]}"
        )
    try:
        info = json.loads(r.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GMSH subprocess output not JSON:\n{r.stdout[-1500:]}") from exc
    if not info.get("success"):
        raise RuntimeError(f"GMSH volume meshing failed: {info.get('error')}")
    print(f"[mesh] {msh_path.name}: {info}")
    return info.get("names", [])


def convert_to_foam(case_dir: Path, msh_path: Path, timeout_s: int = 600) -> None:
    """Run the app's own out-of-process gmshToFoam entry point."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    cmd = [PY, "-m", "cfmesh_autogui.core.gmsh_wrapper", "convert_to_foam",
           str(case_dir), msh_path.name]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                       env=env, cwd=REPO, encoding="utf-8", errors="backslashreplace")
    if r.returncode != 0:
        raise RuntimeError(f"gmshToFoam failed:\n{r.stdout[-1500:]}\n{r.stderr[-500:]}")
    info = json.loads(r.stdout.strip().splitlines()[-1])
    if not info.get("success"):
        raise RuntimeError(f"gmshToFoam failed (rc={info.get('returncode')}):\n{info.get('stderr')}")
    print(f"[convert] gmshToFoam ok into {case_dir.name}")


def setup_case_runnable(case_dir: Path) -> None:
    """Read patches, infer roles geometrically, write a runnable case."""
    from cfmesh_autogui.core.boundary_reader import parse_boundary
    from cfmesh_autogui.core.case_setup import setup_case, infer_patch_roles

    patches = parse_boundary(case_dir / "constant" / "polyMesh" / "boundary")
    roles = infer_patch_roles(case_dir, patches, flow_direction=(1.0, 0.0, 0.0))
    print(f"[setup] patch roles: {roles}")
    setup_case(
        case_dir, patches,
        inlet_velocity=(1.0, 0.0, 0.0),
        end_time=400, residual_control=1e-4, write_interval=1000,
        patch_roles=roles,
    )


def window_counts(case_dir: Path, x_lo: float, x_hi: float) -> int:
    """Number of cells whose centre lies in a fixed x-window.

    Prefers an existing solution export, but falls back to exporting the mesh
    geometry alone. The final mesh of an adaptive run is never solved on (the
    loop stops after remeshing), so a solution-only lookup returned 0 for the
    "after" column — which silently reported the acceptance numbers as
    0 -> 0 and made the whole comparison meaningless.
    """
    import pyvista as pv

    vtu_dirs = sorted(
        (case_dir / "VTK_solution").glob("*/internal.vtu"),
        key=lambda p: p.parent.name,
    )
    if vtu_dirs:
        vtu = vtu_dirs[-1]
    else:
        from cfmesh_autogui.core.mesh_export import _build_internal_vtu
        try:
            vtu = _build_internal_vtu(case_dir)
        except Exception as exc:  # noqa: BLE001 — reported, not swallowed
            print(f"[warn] window_counts: no export for {case_dir.name}: {exc}")
            return 0
    grid = pv.read(str(vtu))
    centres = grid.cell_centers().points
    mask = (centres[:, 0] >= x_lo) & (centres[:, 0] <= x_hi)
    return int(mask.sum())


def physics_probe(case_dir: Path) -> dict:
    """Max |U| and where it sits, from the latest solution of a case."""
    import numpy as np
    import pyvista as pv

    vtu_dirs = sorted(
        (case_dir / "VTK_solution").glob("*/internal.vtu"),
        key=lambda p: p.parent.name,
    )
    if not vtu_dirs:
        return {}
    grid = pv.read(str(vtu_dirs[-1]))
    u = np.asarray(grid.cell_data["U"], dtype=float)
    u_mag = np.linalg.norm(u, axis=1)
    centres = grid.cell_centers().points
    i = int(np.argmax(u_mag))
    return {
        "u_max": float(u_mag[i]),
        "u_peak_xyz": [float(x) for x in centres[i]],
        "u_mean": float(u_mag.mean()),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", default=r"C:\cfmesh_work\venturi")
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--solve-iters", type=int, default=400,
                    help="iterations per intermediate solve")
    ap.add_argument("--final-iters", type=int, default=1200,
                    help="iterations for the last solve")
    ap.add_argument("--mesh-size", type=float, default=5.0,
                    help="initial max cell size in mm (GMSH reads metres)")
    ap.add_argument("--detail", default="medium")
    ap.add_argument("--max-cells", type=int, default=3_000_000,
                    help="cell budget the refinement may not exceed")
    ap.add_argument("--cores", type=int, default=None,
                    help="solve with this many MPI cores (decomposePar/mpirun/"
                    "reconstructPar); None = serial")
    ap.add_argument("--no-solve", action="store_true",
                    help="build geometry + mesh + case only")
    args = ap.parse_args()

    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    step_path = work / "venturi.step"

    sys.path.insert(0, str(REPO / "src"))
    from cfmesh_autogui.config import OFConfig
    from cfmesh_autogui.core.boundary_reader import count_cells, parse_boundary
    from cfmesh_autogui.core.case_setup import (
        setup_case, infer_patch_roles, set_wall_patch_types,
    )
    from cfmesh_autogui.core.solution_adaptive import (
        AdaptiveParams, SolutionAdaptiveRefiner, AdaptiveResult,
    )

    cfg = OFConfig()
    ok, reason = cfg.validate_case_path(work)
    if not ok:
        raise SystemExit(f"Workdir rejected: {reason}")

    # ------------------------------------------------------------------
    print("=== stage 1: geometry ===")
    if not step_path.exists():
        build_venturi(step_path)
    else:
        print(f"[venturi] reusing existing {step_path}")

    # ------------------------------------------------------------------
    def make_case(cycle: int, size_field: Path | None) -> tuple[Path, int]:
        """Mesh from the ORIGINAL CAD with an optional size field, convert to
        a fresh OpenFOAM case, write runnable field files, return
        (case_dir, n_cells). This is the remesh_fn the engine calls."""
        case_dir = work / f"case_c{cycle}"
        msh = case_dir / f"mesh_c{cycle}.msh"
        msh.parent.mkdir(parents=True, exist_ok=True)
        run_gmsh_volume(step_path, msh, args.detail, args.mesh_size / 1000.0,
                        size_field=size_field)
        # gmshToFoam needs a real OpenFOAM case skeleton — the same minimal
        # system/controlDict the GUI writes before conversion (main_window
        # _write_control_dict). setup_case() overwrites it properly after.
        (case_dir / "system").mkdir(parents=True, exist_ok=True)
        (case_dir / "constant").mkdir(parents=True, exist_ok=True)
        (case_dir / "system" / "controlDict").write_text(
            "FoamFile { version 2.0; format ascii; class dictionary; "
            "object controlDict; }\n"
            "application cartesianMesh;\n"
            "startFrom startTime; startTime 0;\n"
            "stopAt endTime; endTime 1000;\n"
            "deltaT 1;\n"
            "writeControl timeStep; writeInterval 1;\n"
            "writeFrequency 1;\n"
            "purgeWrite 0; writeFormat binary; writePrecision 6;\n"
            "writeCompression on; timeFormat general; timePrecision 6;\n"
            "runTimeModifiable true;\n",
            encoding="ascii",
        )
        convert_to_foam(case_dir, msh)
        patches = parse_boundary(case_dir / "constant" / "polyMesh" / "boundary")
        roles = infer_patch_roles(case_dir, patches, flow_direction=(1.0, 0.0, 0.0))
        # gmshToFoam types every patch 'patch'; wall-function BCs abort at
        # solve startup unless walls are typed 'wall' in the mesh boundary.
        set_wall_patch_types(
            case_dir, [n for n, r in roles.items() if r == "wall"]
        )
        setup_case(case_dir, patches, inlet_velocity=(1.0, 0.0, 0.0),
                   end_time=args.solve_iters, residual_control=1e-4,
                   write_interval=1000, patch_roles=roles)
        n = count_cells(case_dir)
        print(f"[remesh] case {case_dir.name}: {n:,} cells, roles={roles}")
        return case_dir, n

    # ------------------------------------------------------------------
    if args.no_solve:
        make_case(0, None)
        return

    print("\n=== stage 2: initial mesh + case ===")
    initial_case, initial_cells = make_case(0, None)

    print("\n=== stage 3: adaptive loop ===")
    solve_cores = args.cores if args.cores and args.cores > 1 else 1
    params = AdaptiveParams(
        inlet_velocity=(1.0, 0.0, 0.0),
        solver_iterations=args.solve_iters,
        final_solver_iterations=args.final_iters,
        max_cycles=args.cycles,
        qoi_tolerance=0.02,
        max_cells=args.max_cells,
        solve_cores=solve_cores,
    )
    if solve_cores > 1:
        print(f"[adaptive] parallel solve on {solve_cores} cores "
              "(decomposePar/mpirun/reconstructPar)")
    refiner = SolutionAdaptiveRefiner(params=params, of_config=cfg,
                                      on_line=lambda m: print(m))
    result = refiner.run(
        initial_case_dir=initial_case,
        bounds=(0.0, 0.46, -0.05, 0.05, -0.05, 0.05),  # metres
        remesh_fn=lambda sf, cyc: make_case(cyc, sf),
        work_dir=work / "amr_work",
    )

    # ------------------------------------------------------------------
    print("\n=== stage 4: acceptance numbers ===")
    probe = physics_probe(initial_case)
    if probe:
        px, py, pz = probe["u_peak_xyz"]
        print(f"physics (cycle 0 solution): |U|max={probe['u_max']:.3f} m/s "
              f"at x={px:.4f} m {'<-- THROAT' if 0.15 <= px <= 0.35 else ''}; "
              f"|U|mean={probe['u_mean']:.3f} m/s (inlet=1.0, continuity "
              f"|U|throat~11)")
    assert isinstance(result, AdaptiveResult)
    th_lo, th_hi = THROAT_WINDOW[0] / 1000.0, THROAT_WINDOW[1] / 1000.0
    st_lo, st_hi = STRAIGHT_WINDOW[0] / 1000.0, STRAIGHT_WINDOW[1] / 1000.0

    # Cycle 0 window counts come from the FIRST case's solution export; the
    # last cycle's from the final case.
    before_throat = window_counts(initial_case, th_lo, th_hi)
    before_straight = window_counts(initial_case, st_lo, st_hi)

    num_cases = sorted(
        [d for d in work.glob("case_c*") if d.is_dir()
         and d.name[6:].isdigit()],
        key=lambda d: int(d.name[6:]),
    )
    final_case = num_cases[-1] if num_cases else initial_case
    after_throat = window_counts(final_case, th_lo, th_hi)
    after_straight = window_counts(final_case, st_lo, st_hi)
    after_total = count_cells(final_case)

    print(f"cells in throat window  : {before_throat:,} -> {after_throat:,} "
          f"({after_throat / max(before_throat, 1):.2f}x)")
    print(f"cells in straight window: {before_straight:,} -> {after_straight:,} "
          f"({after_straight / max(before_straight, 1):.2f}x)")
    print(f"throat/straight ratio   : {before_throat / max(before_straight, 1):.2f} "
          f"-> {after_throat / max(after_straight, 1):.2f}")
    print(f"total cell count        : {initial_cells:,} -> {after_total:,}")

    summary = {
        "workdir": str(work),
        "cycles": len(result.cycles),
        "stop_reason": result.stop_reason,
        "initial_cells": initial_cells,
        "final_cells": after_total,
        "before": {"throat": before_throat, "straight": before_straight,
                   "ratio": before_throat / max(before_straight, 1)},
        "after": {"throat": after_throat, "straight": after_straight,
                  "ratio": after_throat / max(after_straight, 1)},
        "cycle_details": [
            {
                "cycle": c.cycle,
                "cells_before": c.cells_before,
                "cells_after": c.cells_after,
                "indicator_max": c.indicator_max,
                "indicator_p95": c.indicator_p95,
                "peak_location": list(c.peak_location),
                "qoi_pressure_drop": c.qoi_pressure_drop,
                "qoi_delta_rel": c.qoi_delta_rel,
                "solve_time_s": c.solve_time_s,
                "remesh_time_s": c.remesh_time_s,
                "solver_converged": c.solver_converged,
            }
            for c in result.cycles
        ],
    }
    out = work / "venturi_report.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nreport: {out}")
    print(result.summary())


if __name__ == "__main__":
    main()