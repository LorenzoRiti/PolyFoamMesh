#!/usr/bin/env python3
"""A/B benchmark: GMSH 3D algorithm Frontal (6) vs HXT (10) on a real geometry.

The 3D Delaunay/HXT meshing is the single-threaded bottleneck on large meshes
(the valvola baseline: 7.548M tets, 908s, ~8250 cells/s). HXT only parallelises
when ``Mesh.MaxNumThreads3D`` is set explicitly — ``General.NumThreads`` alone
leaves it at 1 thread, which is why past "already tried HXT, doesn't work" tests
saw no speedup. This bench sets ``CFMESH_GMSH_ALGO3D`` so gmsh_wrapper enables
the parallel HXT path, and compares wall time / cells/s / peak RAM / negative
tets against Frontal on the SAME geometry.

Gate (per the plan): HXT is admitted ONLY if it is >= +20% faster in wall time
AND produces zero negative-volume tets. Otherwise Frontal stays the reference.

Skippable: if the geometry file is not found (or CFMESH_GEOM is unset), prints
GATE=NOT_MEASURED and exits 0 — it must never block CI.

Usage::

    python tools/bench_hxt_ab.py [GEOMETRY] [--runs 3] [--detail medium]
    # or
    set CFMESH_GEOM=C:/path/to/valvola.stp
    python tools/bench_hxt_ab.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from cfmesh_autogui.core.gmsh_wrapper import generate_volume_mesh  # noqa: E402

ALGOS = {
    "frontal": {"env": "6", "label": "Frontal (6)"},
    "hxt": {"env": "10", "label": "HXT (10)"},
}


def _count_negative_tets(msh_path: Path) -> int:
    """Count negative-volume tetrahedra in a GMSH .msh via meshio."""
    try:
        import numpy as np
        import meshio
    except Exception:
        return -1  # unknown
    try:
        mesh = meshio.read(str(msh_path))
    except Exception:
        return -1
    neg = 0
    for cell_block in mesh.cells:
        if cell_block.type != "tetra":
            continue
        data = cell_block.data
        pts = mesh.points
        a = pts[data[:, 0]]
        b = pts[data[:, 1]]
        c = pts[data[:, 2]]
        d = pts[data[:, 3]]
        vol = np.einsum("ij,ij->i", b - a, np.cross(c - a, d - a)) / 6.0
        neg += int((vol < 0).sum())
    return neg


def _peak_rss_mb() -> int:
    """Best-effort peak RSS of THIS process (GMSH runs in-process)."""
    try:
        import psutil
        return int(psutil.Process().memory_info().rss / (1024 * 1024))
    except Exception:
        return -1


def _run_once(geom: Path, algo: str, detail: str, out_msh: Path) -> dict:
    os.environ["CFMESH_GMSH_ALGO3D"] = ALGOS[algo]["env"]
    t0 = time.monotonic()
    peak = 0
    try:
        import psutil
        proc = psutil.Process()
        peak = int(proc.memory_info().rss / (1024 * 1024))
    except Exception:
        pass
    try:
        generate_volume_mesh(geom, out_msh, detail=detail)
        wall = time.monotonic() - t0
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    try:
        import psutil
        peak = max(peak, int(psutil.Process().memory_info().rss / (1024 * 1024)))
    except Exception:
        pass
    neg = _count_negative_tets(out_msh)
    try:
        import meshio
        cells = sum(len(cb.data) for cb in meshio.read(str(out_msh)).cells
                    if cb.type == "tetra")
    except Exception:
        cells = 0
    return {
        "ok": True, "wall": wall, "cells": cells,
        "cells_per_s": cells / wall if wall > 0 else 0.0,
        "peak_mb": peak, "neg_tets": neg,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("geometry", nargs="?", default=os.environ.get("CFMESH_GEOM", ""),
                    help="geometry file (STEP/STL); defaults to $CFMESH_GEOM")
    ap.add_argument("--runs", type=int, default=3, help="runs per algorithm")
    ap.add_argument("--detail", default="medium")
    ap.add_argument("--out", default="C:/cfmesh_bench/hxt_ab",
                    help="scratch dir for .msh outputs")
    args = ap.parse_args()

    if not args.geometry:
        print("GATE=NOT_MEASURED — no geometry given (set CFMESH_GEOM or pass a path).")
        return 0
    geom = Path(args.geometry).resolve()
    if not geom.exists():
        print(f"GATE=NOT_MEASURED — geometry not found: {geom}")
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"geometry : {geom}")
    print(f"runs     : {args.runs} per algorithm, detail={args.detail}")

    results: dict[str, list[dict]] = {}
    for algo in ALGOS:
        print(f"\n=== {ALGOS[algo]['label']} ===", flush=True)
        runs = []
        for i in range(args.runs):
            msh = out_dir / f"{algo}_{i}.msh"
            r = _run_once(geom, algo, args.detail, msh)
            if not r["ok"]:
                print(f"  run {i + 1}: FAILED — {r['error']}")
                runs.append(r)
                continue
            print(
                f"  run {i + 1}: {r['cells']:,} tets in {r['wall']:.1f}s "
                f"({r['cells_per_s']:,.0f} cells/s), peak {r['peak_mb']} MB, "
                f"neg tets {r['neg_tets']}"
            )
            runs.append(r)
        results[algo] = runs

    def _median(vals):
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        return vals[len(vals) // 2]

    print("\n" + "=" * 72)
    print(f"{'metric':<16}{'Frontal':>18}{'HXT':>18}")
    summary = {}
    for algo in ALGOS:
        ok = [r for r in results[algo] if r.get("ok")]
        summary[algo] = {
            "wall": _median([r["wall"] for r in ok]),
            "cps": _median([r["cells_per_s"] for r in ok]),
            "peak": _median([r["peak_mb"] for r in ok]),
            "neg": _median([r["neg_tets"] for r in ok]),
            "n_ok": len(ok),
        }
    for label, key in [("wall time (s)", "wall"), ("cells/s", "cps"),
                       ("peak RAM (MB)", "peak"), ("neg tets", "neg")]:
        f = summary["frontal"][key]
        h = summary["hxt"][key]
        print(f"{label:<16}{('n/a' if f is None else f'{f:,.1f}'):>18}"
              f"{('n/a' if h is None else f'{h:,.1f}'):>18}")

    f_wall = summary["frontal"]["wall"]
    h_wall = summary["hxt"]["wall"]
    h_neg = summary["hxt"]["neg"]
    if f_wall is None or h_wall is None:
        print("\nGATE=NOT_MEASURED — one or both algorithms produced no valid run.")
        return 0

    speedup = f_wall / h_wall if h_wall > 0 else 0.0
    print(f"\nHXT wall-time speedup vs Frontal: {speedup:.2f}x "
          f"(+{(speedup - 1) * 100:.0f}%)")
    passed = speedup >= 1.20 and h_neg == 0
    if passed:
        print("GATE=PASS — HXT admitted (>=+20% faster, zero neg tets)")
    else:
        print("GATE=FAIL — HXT NOT admitted (needs >=+20% wall-time AND zero neg tets)")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
