"""Valve resolution check — Part 3.4 of the SAMR handoff.

Meshes the real valve cad ``Parte4.stp`` in the ADAPTIVE path (no explicit
max cell size => ``cells_across`` bulk sizing), at a given detail level,
reporting actual cell count, wall time, and then runs gmshToFoam + checkMesh.

Usage:
    PY tools/valve_resolution_check.py --detail medium
    PY tools/valve_resolution_check.py --detail fine
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
VALVE = Path(os.environ.get("CFMESH_GEOM", r"C:\Users\Davide Valoroso\Desktop\Report\Parte4.stp"))
WORK = Path(os.environ.get("CFMESH_WORK", r"C:\cfmesh_work\valve_resolution"))


def run_gmsh_volume(step_path: Path, msh_path: Path, detail: str,
                    timeout_s: int = 3600) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    env["GMSH_SOLUTION_SIZE_FIELD"] = ""
    cmd = [
        PY, "-m", "cfmesh_autogui.core.gmsh_wrapper", "volume",
        str(step_path), str(msh_path), detail,
        "0", "0", "1.2", "0", "0", "0",
    ]
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                       env=env, cwd=REPO, encoding="utf-8", errors="backslashreplace")
    dt = time.monotonic() - t0
    if r.returncode != 0:
        raise RuntimeError(f"GMSH {detail} failed:\n{r.stdout[-2000:]}\n{r.stderr[-800:]}")
    try:
        info = json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise RuntimeError(f"GMSH {detail} returned non-JSON output:\n{r.stdout[-2000:]}") from exc
    if not info.get("success"):
        raise RuntimeError(f"GMSH {detail} failed: {info.get('error')}")
    info["wall_time_s"] = dt
    return info


def count_msh_cells(msh_path: Path) -> int:
    import meshio
    m = meshio.read(str(msh_path))
    n = 0
    for block in m.cells:
        if block.data.shape[1] == 4 and block.type == "tetra":
            n += len(block.data)
    if n:
        return n
    # fallback: any 3D cell (pyramid/prism etc.)
    return sum(len(b.data) for b in m.cells if b.data.shape[1] >= 5)


def run_and_convert(detail: str) -> None:
    out_dir = WORK / f"valve_{detail}"
    out_dir.mkdir(parents=True, exist_ok=True)
    msh = out_dir / f"valve_{detail}.msh"

    if not msh.exists():
        print(f"[{detail}] meshing (adaptive path)...")
        info = run_gmsh_volume(VALVE, msh, detail)
        print(f"[{detail}] GMSH done in {info['wall_time_s']:.1f}s: {info}")
    else:
        print(f"[{detail}] reusing existing {msh.name}")

    n = count_msh_cells(msh)
    print(f"[{detail}] mesh cell count: {n:,}")

    # --> OpenFOAM case + checkMesh
    case_dir = out_dir / "case"
    (case_dir / "system").mkdir(parents=True, exist_ok=True)
    (case_dir / "constant").mkdir(parents=True, exist_ok=True)
    (case_dir / "system" / "controlDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; "
        "object controlDict; }\n"
        "application checkMesh;\n"
        "startFrom startTime; startTime 0;\n"
        "stopAt endTime; endTime 1;\n"
        "deltaT 1;\nwriteControl timeStep; writeInterval 1;\n"
        "purgeWrite 0; writeFormat binary; writePrecision 6;\n"
        "writeCompression on; timeFormat general; timePrecision 6;\n"
        "runTimeModifiable false;\n",
        encoding="ascii",
    )
    # checkMesh needs system/fvSchemes (it loads finiteVolume libraries).
    from cfmesh_autogui.core import case_setup as _cs
    (case_dir / "system" / "fvSchemes").write_text(
        _cs.FV_SCHEMES, encoding="ascii"
    )
    (case_dir / "system" / "fvSolution").write_text(
        _cs.FV_SOLUTION, encoding="ascii"
    )
    shutil_copy(msh, case_dir / msh.name)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    conv = subprocess.run(
        [PY, "-m", "cfmesh_autogui.core.gmsh_wrapper", "convert_to_foam",
         str(case_dir), msh.name],
        capture_output=True, text=True, timeout=600, env=env, cwd=REPO,
        encoding="utf-8", errors="backslashreplace",
    )
    if conv.returncode != 0 or not json.loads(conv.stdout.strip().splitlines()[-1]).get("success"):
        print(f"[{detail}] gmshToFoam failed:\n{conv.stdout[-1500:]}")
        return

    from cfmesh_autogui.config import OFConfig
    check = subprocess.run(
        OFConfig().build_check_mesh_cmd(case_dir),
        capture_output=True, text=True, timeout=900, encoding="utf-8",
        errors="backslashreplace",
    )
    from cfmesh_autogui.core.openfoam_runner import parse_checkmesh_output
    rep = parse_checkmesh_output(check.stdout + check.stderr)
    print(f"[{detail}] checkMesh: cells={rep.cells:,} passed={rep.passed} "
          f"maxNonOrtho={rep.max_non_ortho:.1f} maxSkew={rep.max_skewness:.3f} "
          f"neg={rep.neg_cells}")


def shutil_copy(src: Path, dst: Path) -> None:
    # .msh can be huge (~90 MB); hardlink would non work across drives, copy ok
    import shutil as _s
    _s.copy2(str(src), str(dst))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", default="medium",
                    choices=["coarse", "medium", "fine", "very_fine"])
    ap.add_argument("--geom", default=str(VALVE),
                    help="geometry file (default: $CFMESH_GEOM)")
    ap.add_argument("--work", default=str(WORK),
                    help="scratch dir (default: $CFMESH_WORK)")
    args = ap.parse_args()
    VALVE = Path(args.geom)
    WORK = Path(args.work)
    if not VALVE.exists():
        print(f"SKIP: geometry not found: {VALVE} (set CFMESH_GEOM)")
        raise SystemExit(0)
    run_and_convert(args.detail)