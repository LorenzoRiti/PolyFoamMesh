# -*- coding: utf-8 -*-
"""Lane C2: normal_method=guided (alone, no retry) on a pristine
valve_baseline copy, with real checkMesh before/after.

Usage: python tools/bench_bl_valve_guided.py
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(HERE))

from bench_bl_valve_combined import checkmesh  # noqa: E402 (verified OF2512 parser)
from polyfoammesh.config import OFConfig  # noqa: E402
from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine  # noqa: E402

BASE = Path("C:/polybench2/valve_baseline")
CASE = Path("C:/polybench2/bl_valve_guided")


def show(tag, cm):
    print(f"{tag} cells={cm['cells']} severeNO={cm['severe_non_ortho']} "
          f"NOmax={cm['non_ortho_max']:.2f} NOavg={cm['non_ortho_avg']:.4f} "
          f"skewMax={cm['skew_max']:.3f} skewFaces={cm['skew_faces']} "
          f"wrong={cm['wrong_oriented']} faceTets={cm['face_tets']} "
          f"aspect={cm['aspect_cells']} shortEdges={cm['short_edges']} "
          f"failed={cm['failed']} MeshOK={cm['mesh_ok']}", flush=True)


def main() -> int:
    cfg = OFConfig()
    shutil.rmtree(CASE, ignore_errors=True)
    shutil.copytree(BASE, CASE)
    print("checkMesh BEFORE...", flush=True)
    show("BEFORE", checkmesh(cfg, CASE))
    t0 = time.monotonic()
    res = PolyBoundaryLayerEngine(CASE, log=lambda m: None).run(
        n_layers=2, first_height=1e-5, growth_rate=1.2, apply_to_all=True,
        normal_method="guided", local_termination="decoupled_vertex",
    )
    dt = time.monotonic() - t0
    print(f"\nBL guided: success={res.success} ({dt:.0f}s) "
          f"excluded={res.stats.get('local_excluded_faces')} "
          f"scale={res.stats.get('scale')} "
          f"n_cells_after={res.stats.get('n_cells_after')} "
          f"prisms={res.n_prism_cells}", flush=True)
    if not res.success:
        print("  errors:", res.errors[:2], flush=True)
        return 1
    print("checkMesh AFTER...", flush=True)
    show("AFTER-guided", checkmesh(cfg, CASE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
