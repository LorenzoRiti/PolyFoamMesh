"""FASE 2 gate on the real valve — local layer termination.

Reuses C:/polybench/valve1 (tet backup intact), converts to the dual, runs
the BL on the wall-role patches (everything except the inlet/outlet caps
surface_77/surface_89) with the FASE 2 variable layer count, and reports
the engine verdict + checkMesh before/after.

Gate: the BL is VALID (engine validation passes with the relaxed pyramid
criterion — it must not ADD pyramid violations to the concave-feature
input) and checkMesh does not get worse.

Run:  python tools/bench_bl_valve_fase2.py [--n-layers 3] [--first-height 0.005]
"""
from __future__ import annotations

import argparse
import io
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from cfmesh_autogui.config import OFConfig  # noqa: E402
from cfmesh_autogui.core.bl_poly import PolyBoundaryLayerEngine  # noqa: E402
from cfmesh_autogui.core import foam_mesh_io as fio  # noqa: E402
from cfmesh_autogui.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402

CASE = Path("C:/polybench2/bl_valve_fase2")
TET_BACKUP = Path("C:/polybench/valve1/constant/polyMesh_tet_backup")
INLET, OUTLET = "surface_77", "surface_89"


def checkmesh(cfg, case):
    r = subprocess.run(
        cfg._build_wsl_cmd(
            f"source {shlex.quote(cfg.env_script)} 2>/dev/null; "
            f"cd {cfg._quoted_linux_path(case)} && checkMesh 2>&1"
        ),
        capture_output=True, text=True, timeout=900,
    )
    text = r.stdout + r.stderr
    import re

    def _num(pat):
        m = re.search(pat, text)
        return float(m.group(1).rstrip(".")) if m else 0.0

    def _int(pat):
        m = re.search(pat, text)
        return int(m.group(1).replace(",", "")) if m else 0

    return {
        "cells": _int(r"cells:\s+(\d+)"),
        "polyhedra": _int(r"polyhedra:\s+(\d+)"),
        "prisms": _int(r"hexahedra:\s+(\d+)"),
        "skew": _num(r"Max skewness\s*[:=]\s*([\d.]+)"),
        "non_ortho": _num(r"non-orthogonality\s+Max:\s*([\d.]+)"),
        "wrong_oriented": _int(r"incorrectly oriented"),
        "failed": _int(r"Failed (\d+) mesh checks"),
        "mesh_ok": "Mesh OK." in text,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-layers", type=int, default=3)
    ap.add_argument("--first-height", type=float, default=0.005)
    ap.add_argument("--skip-wsl", action="store_true")
    args = ap.parse_args()

    cfg = OFConfig()
    shutil.rmtree(CASE, ignore_errors=True)
    (CASE / "constant").mkdir(parents=True)
    poly = CASE / "constant" / "polyMesh"
    shutil.copytree(TET_BACKUP, poly)
    (CASE / "system").mkdir(parents=True)
    (CASE / "system" / "controlDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; "
        "object controlDict; }\napplication checkMesh;\n", encoding="ascii")

    t0 = time.monotonic()
    dres = TetPolyDualConverter(CASE, log=lambda m: None).run()
    print(f"dual: {dres.n_cells_after:,} cells ({time.monotonic() - t0:.1f}s)")

    if not args.skip_wsl:
        before = checkmesh(cfg, CASE)
        print(f"checkMesh BEFORE BL: cells={before['cells']} "
              f"wrong={before['wrong_oriented']} failed={before['failed']} "
              f"skew={before['skew']:.2f} NOmax={before['non_ortho']:.1f} "
              f"MeshOK={before['mesh_ok']}")
    else:
        before = {}

    patches = fio.read_polymesh(poly)[4]
    walls = [p["name"] for p in patches if p["name"] not in (INLET, OUTLET)]
    print(f"wall patches: {len(walls)} (all except {INLET}/{OUTLET})")

    def log(m): print(f"   {m}")
    t0 = time.monotonic()
    res = PolyBoundaryLayerEngine(CASE, log=log).run(
        n_layers=args.n_layers, first_height=args.first_height,
        growth_rate=1.2, patch_names=walls, apply_to_all=False,
    )
    dt = time.monotonic() - t0
    print(f"\nBL: success={res.success} ({dt:.1f}s)")
    print(f"  prisms={res.n_prism_cells} (local layers "
          f"{res.stats.get('layers_per_face_min')}.."
          f"{res.stats.get('layers_per_face_max')}) "
          f"thick={res.total_thickness:.5g} terminator="
          f"{res.stats.get('n_terminator_faces')}")
    if not res.success:
        print("  errors:", res.errors[:2])
        print("  warnings:", res.warnings[:3])

    if res.success and not args.skip_wsl:
        after = checkmesh(cfg, CASE)
        print(f"checkMesh AFTER BL: cells={after['cells']} "
              f"prisms={after['prisms']} poly={after['polyhedra']} "
              f"wrong={after['wrong_oriented']} failed={after['failed']} "
              f"skew={after['skew']:.2f} NOmax={after['non_ortho']:.1f} "
              f"MeshOK={after['mesh_ok']}")
        if before:
            print(f"\nGATE: BL valid={res.success} "
                  f"violations {before.get('wrong_oriented')} -> "
                  f"{after.get('wrong_oriented')} "
                  f"(BL must not ADD pyramid violations)")
    return 0 if res.success else 1


if __name__ == "__main__":
    sys.exit(main())
