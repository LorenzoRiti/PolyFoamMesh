# -*- coding: utf-8 -*-
"""Lane A isolation harness: peak RSS per production stage on a hex box.

Stages (each with its own peak-RSS sampler):
  mesh       materialize faces/owner/neighbour + input metrics
  precompute BL _build_precompute (normals/fade/support/edge maps)
  build      BL _build, one scale (full output mesh copy + prisms)
  validate   BL _validate (closure/volume/pyramid passes)
  merge      repair_concave_cells_if_safe on the built mesh

Usage: python tools/ram_isolate_bl.py NX NY NZ
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "tools")

import psutil

from hex_box_mesh import build as build_box
from polyfoammesh.core.bl_poly import (
    PolyBoundaryLayerEngine,
    _cell_metrics,
    _pyramid_violations,
)
from polyfoammesh.core.poly_cell_merge import repair_concave_cells_if_safe

PROC = psutil.Process()


def rss_mb():
    return PROC.memory_info().rss / (1024.0 * 1024.0)


class PeakSampler:
    def __init__(self, interval=0.05):
        self.interval = interval
        self.peak = 0.0
        self._stop = threading.Event()

    def _loop(self):
        while not self._stop.is_set():
            v = rss_mb()
            if v > self.peak:
                self.peak = v
            time.sleep(self.interval)

    def __enter__(self):
        self.peak = rss_mb()
        self._stop.clear()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()
        v = rss_mb()
        if v > self.peak:
            self.peak = v


def main():
    nx, ny, nz = [int(a) for a in sys.argv[1:4]]
    print(f"box {nx}x{ny}x{nz} baseline_after_imports={rss_mb():.0f} MB",
          flush=True)

    with PeakSampler() as s:
        m = build_box(nx, ny, nz)
        points, faces = m["points"], m["faces"]
        owner, neigh = m["owner"], m["neighbour"]
        patches, n_int, n_cells = m["patches"], m["n_int"], m["n_cells"]
    print(f"[mesh] cells={n_cells:,} faces={len(faces):,} n_int={n_int:,} "
          f"peak={s.peak:.0f} MB", flush=True)

    with PeakSampler() as s:
        total_vol0 = float(
            _cell_metrics(points, faces, owner, neigh, n_cells, n_int)[0].sum()
        )
        _, pyr_idx = _pyramid_violations(
            points, faces, owner, neigh, n_int, n_cells, return_idx=True,
        )
        drop_bnd = {int(fi) - n_int for fi in pyr_idx
                    if n_int <= int(fi) < len(faces)}
    print(f"[input-metrics] vol={total_vol0:.6g} pyr_before={len(pyr_idx)} "
          f"peak={s.peak:.0f} MB", flush=True)

    eng = PolyBoundaryLayerEngine(
        Path(tempfile.mkdtemp(prefix="ram_iso_")),
        log=lambda msg: None, cancel=lambda: False,
    )
    n_bnd = len(faces) - n_int
    sel = list(range(n_bnd))
    with PeakSampler() as s:
        pre = eng._build_precompute(
            points, faces, owner, neigh, patches, n_int, sel, drop_bnd,
        )
    print(f"[precompute] wall_verts={len(pre['wall_verts']):,} "
          f"peak={s.peak:.0f} MB", flush=True)

    fh = m["spacing"] * 0.05
    with PeakSampler() as s:
        built = eng._build(
            points, faces, owner, neigh, patches, n_int, sel,
            2, fh, 1.2, 0.5, pyr_before=len(pyr_idx),
            drop_bnd=drop_bnd, pre=pre,
        )
    print(f"[build] prisms={built['n_prism_cells']:,} "
          f"faces_out={len(built['faces']):,} peak={s.peak:.0f} MB", flush=True)

    with PeakSampler() as s:
        ok, msg = eng._validate(built, total_vol0, 0.0)
    print(f"[validate] ok={ok} msg={msg} peak={s.peak:.0f} MB", flush=True)

    # production-faithful: openfoam_runner re-reads the mesh from disk, so
    # the input mesh and the precompute are dead before merge runs. Without
    # this, the harness overestimates the merge peak by the input mesh.
    import gc
    del points, faces, owner, neigh, patches, pre, m
    gc.collect()
    print(f"[freed-input] rss={rss_mb():.0f} MB", flush=True)
    bp, bf, bo, bn = (built["points"], built["faces"], built["owner"],
                      built["neigh"])
    bn_int = int(built["n_int"])
    bn_cells = int(max(bo.max(), bn.max())) + 1
    with PeakSampler() as s:
        _out = repair_concave_cells_if_safe(
            bp, bf, bo, bn, bn_int, bn_cells, built["patches"],
        )
    rep = _out[-1]
    print(f"[merge] applied={rep['applied']} concave={rep['concave_cells']} "
          f"peak={s.peak:.0f} MB", flush=True)


if __name__ == "__main__":
    main()
