# -*- coding: utf-8 -*-
"""Lane C A/B: dual_convexity + area_weighted (A) vs + smoothed (B) on
pristine valve_baseline copies, with real checkMesh.

Usage: python tools/bench_bl_valve_dualsmooth.py
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

# NOTE: bench_bl_valve_combined wraps sys.stdout for UTF-8 on import;
# wrapping twice closes the buffer, so do NOT wrap again here.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(HERE))

from bench_bl_valve_combined import checkmesh  # noqa: E402 (verified OF2512 parser)
from polyfoammesh.config import OFConfig  # noqa: E402
from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine  # noqa: E402

BASE = Path("C:/polybench2/valve_baseline")
CASES = {
    "A_area_weighted": Path("C:/polybench2/bl_valve_dual_aw"),
    "B_smoothed": Path("C:/polybench2/bl_valve_dual_sm"),
}
METHOD = {"A_area_weighted": "area_weighted", "B_smoothed": "smoothed"}


def show(tag, cm):
    print(f"{tag} cells={cm['cells']} severeNO={cm['severe_non_ortho']} "
          f"NOmax={cm['non_ortho_max']:.2f} NOavg={cm['non_ortho_avg']:.4f} "
          f"skewMax={cm['skew_max']:.3f} skewFaces={cm['skew_faces']} "
          f"wrong={cm['wrong_oriented']} faceTets={cm['face_tets']} "
          f"aspect={cm['aspect_cells']} shortEdges={cm['short_edges']} "
          f"failed={cm['failed']} MeshOK={cm['mesh_ok']}", flush=True)


def main() -> int:
    cfg = OFConfig()
    ok = True
    for tag, case in CASES.items():
        shutil.rmtree(case, ignore_errors=True)
        shutil.copytree(BASE, case)
        if tag == "A_area_weighted":
            print("checkMesh BEFORE (pristine, shared baseline)...", flush=True)
            show("BEFORE", checkmesh(cfg, case))
        t0 = time.monotonic()
        res = PolyBoundaryLayerEngine(case, log=lambda m: None).run(
            n_layers=2, first_height=1e-5, growth_rate=1.2,
            apply_to_all=True, concavity_criterion="dual_convexity",
            normal_method=METHOD[tag], local_termination="decoupled_vertex",
        )
        dt = time.monotonic() - t0
        print(f"\nBL {tag}: success={res.success} ({dt:.0f}s) "
              f"excluded={res.stats.get('local_excluded_faces')} "
              f"scale={res.stats.get('scale')} "
              f"n_cells_after={res.stats.get('n_cells_after')} "
              f"prisms={res.n_prism_cells}", flush=True)
        if not res.success:
            print("  errors:", res.errors[:2], flush=True)
            ok = False
            continue
        print(f"checkMesh AFTER {tag}...", flush=True)
        show(f"AFTER-{tag}", checkmesh(cfg, case))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
