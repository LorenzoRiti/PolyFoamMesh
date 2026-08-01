"""Run the barycentric-dual tet->poly converter on an OpenFOAM case.

The GUI is not wired to this converter yet (see docs/poly_dual_handoff.md), so
this is the way to try it on a case the GUI already produced.

    python tools/tet_poly_dual_cli.py <case_dir> [--no-backup] [--median-faces]
                                      [--split-rounds N] [--check]

<case_dir> must contain constant/polyMesh with a pure tetrahedral mesh.
By default the original tet mesh is copied to constant/polyMesh_tet_backup
before anything is written, so the conversion is undoable:

    rmdir /s /q constant\\polyMesh
    move constant\\polyMesh_tet_backup constant\\polyMesh

--check also runs checkMesh through WSL afterwards and prints the verdict.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfmesh_autogui.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402

OF_BASHRC = "/usr/lib/openfoam/openfoam2512/etc/bashrc"


def wsl_path(p: Path) -> str:
    s = str(Path(p).resolve())
    return "/mnt/" + s[0].lower() + s[2:].replace("\\", "/")


def run_check_mesh(case: Path) -> None:
    cmd = f"source {OF_BASHRC} && cd '{wsl_path(case)}' && checkMesh -constant"
    try:
        r = subprocess.run(
            ["wsl.exe", "-d", "Ubuntu", "--", "bash", "-lc", cmd],
            capture_output=True, text=True, errors="replace", timeout=3600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[check] could not run checkMesh: {exc}")
        return
    txt = r.stdout + r.stderr
    i = txt.find("Checking geometry")
    print("\n===== checkMesh =====")
    print(txt[i:] if i >= 0 else txt[-4000:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case", type=Path)
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--median-faces", action="store_true")
    ap.add_argument("--split-rounds", type=int, default=0)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    case = a.case.resolve()
    poly = case / "constant" / "polyMesh"
    if not (poly / "owner").exists():
        print(f"error: {poly} does not look like an OpenFOAM polyMesh")
        return 2

    if not a.no_backup:
        backup = case / "constant" / "polyMesh_tet_backup"
        if backup.exists():
            print(f"[backup] {backup.name} already exists, leaving it alone")
        else:
            shutil.copytree(poly, backup)
            print(f"[backup] original tet mesh copied to {backup.name}")

    res = TetPolyDualConverter(
        case,
        log=lambda m: print(m, flush=True),
        median_faces=a.median_faces,
        split_rounds=a.split_rounds,
    ).run()

    print("")
    if not res.success:
        print("CONVERSION FAILED — the original polyMesh was left untouched:")
        for e in res.errors:
            print(f"  {e}")
        return 1

    print(f"cells          {res.n_tets_before:,} tetrahedra -> {res.n_cells_after:,} polyhedra")
    print(f"poly coverage  {100 * res.poly_fraction:.1f}%  (residual tetrahedra: {res.n_residual_tets})")
    print(f"faces          {res.n_internal_faces:,} internal + {res.n_boundary_faces:,} boundary")
    print(f"points         {res.n_points_after:,}")
    print(f"volume         {res.volume_before:.9e} -> {res.volume_after:.9e}")
    print(f"min cell vol   {res.min_cell_volume:.3e}   closure {res.max_closure_error:.2e}")
    if res.residual_defects:
        d = res.defect_breakdown
        print(
            f"predicted checkMesh defects: {d.get('pyramid', 0)} inverted face "
            f"pyramids, {d.get('non_ortho', 0)} non-orthogonality errors, "
            f"{d.get('skew', 0)} skewness errors"
        )
    else:
        print("predicted checkMesh defects: none")
    print(f"time           {res.stage_times.get('total')} s  {res.stage_times}")

    if a.check:
        run_check_mesh(case)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
