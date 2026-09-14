# -*- coding: utf-8 -*-
"""Lane C2 domain check: guided vs area_weighted on sharp-wall slot1.

Usage: python tools/bench_slot1_guided.py
"""
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(HERE))

from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine  # noqa: E402

BASE = Path("C:/polybench2/slot1_bench0")


def main() -> int:
    for tag, method in (("aw", "area_weighted"), ("gd", "guided")):
        case = Path(f"C:/polybench2/slot1_guided_{tag}")
        shutil.rmtree(case, ignore_errors=True)
        shutil.copytree(BASE, case)
        t0 = time.monotonic()
        res = PolyBoundaryLayerEngine(case, log=lambda m: None).run(
            n_layers=2, first_height=1e-5, growth_rate=1.2,
            apply_to_all=True, local_termination="decoupled_vertex",
            normal_method=method,
        )
        print(f"slot1 {tag}: success={res.success} ({time.monotonic()-t0:.0f}s) "
              f"excluded={res.stats.get('local_excluded_faces')} "
              f"scale={res.stats.get('scale')} "
              f"prisms={res.n_prism_cells}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
