"""Generate the offline regression fixtures for the tet->poly defect detector.

Runs the barycentric dual on a real valve tet mesh, runs a real checkMesh,
and stores:
  - a compact npz of the CONVERTED dual poly mesh (points/faces/owner/neigh)
  - the full checkMesh output as text

The offline test then loads the npz, runs TetPolyDualConverter's in-process
`_detect_defects`, and asserts it reproduces exactly the pyramid /
non-orthogonality / skew counts that the recorded real checkMesh reported.
That correspondence (e.g. 895 predicted / 895 reported on the valve) is the
guard that makes every number this project publishes trustworthy without a
WSL round trip, and it must never silently drift.

    python tools/poly_fixture_builder.py --regression-fixture
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from cfmesh_autogui.config import OFConfig  # noqa: E402
from cfmesh_autogui.core.foam_mesh_io import (  # noqa: E402
    read_polymesh, write_polymesh,
)

FIXDIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
VALVE_TET = Path("C:/polybench/valve1/constant/polyMesh_tet_backup")
WORK = Path("C:/polybench2/fixture_valve")


def main() -> None:
    from cfmesh_autogui.core.tet_poly_dual import TetPolyDualConverter

    FIXDIR.mkdir(parents=True, exist_ok=True)
    if WORK.exists():
        shutil.rmtree(WORK)
    poly = WORK / "constant" / "polyMesh"
    sysd = WORK / "system"
    poly.mkdir(parents=True)
    sysd.mkdir(parents=True)
    for f in ("points", "faces", "owner", "neighbour", "boundary"):
        shutil.copy2(VALVE_TET / f, poly / f)
    (sysd / "controlDict").write_text(
        "FoamFile {\n    version 2.0; format ascii; class dictionary; object controlDict; }\n"
        "application checkMesh;\nstartFrom startTime;\nstartTime 0;\nstopAt endTime;\n"
        "endTime 1;\ndeltaT 1;\nwriteControl timeStep;\nwriteInterval 1;\nwriteFormat ascii;\n",
        encoding="ascii")
    (sysd / "fvSchemes").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
        "ddtSchemes { default steadyState; }\ngradSchemes { default Gauss linear; }\n",
        encoding="ascii")
    (sysd / "fvSolution").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
        "solvers { p { solver PCG; preconditioner DIC; tolerance 1e-6; relTol 0.1; } }\n",
        encoding="ascii")

    print("converting valve tet -> dual...", flush=True)
    conv = TetPolyDualConverter(WORK, log=lambda m: print(m, flush=True))
    res = conv.run()
    if not res.success:
        raise SystemExit(f"conversion failed: {res.errors}")
    print("converted:", res.n_cells_after, "cells", flush=True)

    pts, faces, own, nei, patches = read_polymesh(poly)
    n_int = len(nei)
    # compact npz
    sizes = np.array([len(f) for f in faces], dtype=np.int32)
    offsets = np.zeros(len(faces) + 1, dtype=np.int64)
    np.cumsum(sizes, out=offsets[1:])
    flat = np.concatenate([np.array(f, dtype=np.int32) for f in faces]) if faces else np.zeros(0, dtype=np.int32)
    np.savez_compressed(
        FIXDIR / "valve_dual.npz",
        points=pts.astype(np.float32),
        face_sizes=sizes,
        face_offsets=offsets,
        face_verts=flat,
        owner=own.astype(np.int32),
        neighbour=nei.astype(np.int32),
        n_int=np.int32(n_int),
    )
    print(f"npz -> {FIXDIR / 'valve_dual.npz'} ({ (FIXDIR/'valve_dual.npz').stat().st_size/1e6:.1f} MB)")

    cfg = OFConfig()
    env_q = shlex.quote(cfg.env_script)
    r = subprocess.run(
        cfg._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {cfg._quoted_linux_path(WORK)} && checkMesh 2>&1"
        ),
        capture_output=True, text=True, timeout=900,
    )
    text = r.stdout + r.stderr
    (FIXDIR / "valve_dual_checkmesh.txt").write_text(text, encoding="utf-8")
    print(f"checkMesh fixture -> {FIXDIR / 'valve_dual_checkmesh.txt'}")


if __name__ == "__main__":
    main()

if __name__ == '__main__':
    main()

