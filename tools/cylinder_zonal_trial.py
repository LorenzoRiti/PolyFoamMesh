# -*- coding: utf-8 -*-
"""Lane D2 trial: zonal worst-element aspect relaxation on the dual cylinder.

Control (smoother defaults) + zonal (worst_aspect_thr=3.5) driven directly
on cylinder_axis_both; writes two scratch copies for real checkMesh.

Usage: python tools/cylinder_zonal_trial.py
"""
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

import numpy as np

from polyfoammesh.core import foam_mesh_io as fio
from polyfoammesh.core.poly_smoother import (
    _cell_aspect_ratio,
    _current_objective,
    quality_objective,
    smooth_dual_mesh,
)
from polyfoammesh.core.tet_poly_dual import (
    _cell_centres,
    _detect_defects,
    _face_geometry,
)

BASE = Path("C:/polybench2/cylinder_axis_both")


def run_case(tag, dst, **kw):
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(BASE, dst)
    poly = dst / "constant" / "polyMesh"
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    n_int = len(neigh)
    n_cells = int(max(owner.max(), neigh.max())) + 1
    iters = int(kw.pop("trial_iters", 10))

    def aspect(pts):
        sf, _cf = _face_geometry(pts, faces)
        _ctr, vol = _cell_centres(sf, _cf, owner, neigh, n_int, n_cells)
        return _cell_aspect_ratio(sf, owner, neigh, n_int, n_cells, vol)

    a0 = aspect(np.asarray(points))
    from polyfoammesh.core.tet_poly_dual import _cell_centres as _cc
    _sf0, _cf0 = _face_geometry(np.asarray(points), faces)
    _ct0, _vo0 = _cc(_sf0, _cf0, owner, neigh, n_int, n_cells)
    _base = _current_objective(
        quality_objective, 65.0, _sf0, _cf0, _ct0, owner, neigh, n_int,
        np.asarray(points), faces, n_cells=n_cells, vol=_vo0)
    _ar0 = _cell_aspect_ratio(_sf0, owner, neigh, n_int, n_cells, _vo0)
    _zone0 = float(max(0.0, float(_ar0.max()) - 3.5) ** 2)
    print(f"objective scales: base={_base:.1f} zoneRawMaxExc2={_zone0:.3f} "
          f"=> lam_zone~{_base / max(_zone0, 1e-9):.0f} balances them",
          flush=True)
    t0 = time.monotonic()
    orig = np.asarray(points)
    out, _counts = smooth_dual_mesh(
        orig, faces, owner, neigh, n_int, n_cells,
        _detect_defects, _face_geometry, _cell_centres,
        iterations=iters, relaxation=0.5, **kw)
    dt = time.monotonic() - t0
    a1 = aspect(out)
    moved = int((np.abs(out - orig).max(axis=1) > 0).sum())
    print(f"{tag}: maxAspect {a0.max():.3f} -> {a1.max():.3f} "
          f"p99 {np.percentile(a0, 99):.3f} -> {np.percentile(a1, 99):.3f} "
          f"movedVerts={moved} ({dt:.0f}s)", flush=True)
    if moved:
        fio.write_polymesh(poly, out, faces, owner, neigh, patches)
        print(f"  WROTE {dst}", flush=True)
    else:
        print("  no vertex moved: mesh untouched", flush=True)


def main():
    import sys as _sys
    zonal_lam = float(_sys.argv[1]) if len(_sys.argv) > 1 else 10.0
    thr = float(_sys.argv[2]) if len(_sys.argv) > 2 else 3.5
    iters = int(_sys.argv[3]) if len(_sys.argv) > 3 else 10
    run_case("control-both", Path("C:/polybench2/cyl_zonal_ctrl"),
             use_compactness=True, use_axis_balance=True,
             trial_iters=iters)
    run_case("zonal", Path("C:/polybench2/cyl_zonal_trial"),
             use_compactness=True, use_axis_balance=True,
             worst_aspect_thr=thr, zonal_aspect_lam=zonal_lam,
             trial_iters=iters)


if __name__ == "__main__":
    main()
