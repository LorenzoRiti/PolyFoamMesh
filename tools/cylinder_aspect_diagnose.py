# -*- coding: utf-8 -*-
"""Lane D diagnosis: per-cell aspect ratio map on the dual cylinder.

Prints: max/percentiles, term breakdown (directional vs compactness) for
the worst cells, and (r, z) positions of the top-N cells to confirm or
refute the cap/wall cluster hypothesis.

Usage: python tools/cylinder_aspect_diagnose.py [case_dir] [top_n]
"""
import sys

sys.path.insert(0, "src")

import numpy as np

from polyfoammesh.core import foam_mesh_io as fio
from polyfoammesh.core.bl_poly import _face_geometry, _cell_centroids
from polyfoammesh.core.poly_smoother import _cell_aspect_ratio

CASE = sys.argv[1] if len(sys.argv) > 1 else "C:/polybench2/cylinder_axis_both"
TOPN = int(sys.argv[2]) if len(sys.argv) > 2 else 20


def main():
    from pathlib import Path
    points, faces, owner, neigh, patches = fio.read_polymesh(
        Path(CASE) / "constant" / "polyMesh")
    n_int = len(neigh)
    n_cells = int(max(owner.max(), neigh.max())) + 1
    print(f"cells={n_cells:,} faces={len(faces):,} n_int={n_int:,}", flush=True)
    sf, cf = _face_geometry(points, faces)
    ctr, vol = _cell_centroids(points, faces, owner, neigh, n_int, n_cells)
    ar = _cell_aspect_ratio(sf, owner, neigh, n_int, n_cells, vol)
    # term split for diagnosis
    ax = np.abs(sf)
    asum = np.zeros((n_cells, 3))
    np.add.at(asum, owner, ax)
    np.add.at(asum, neigh, ax[:n_int])
    dterm = asum.max(axis=1) / np.maximum(asum.min(axis=1), 1e-300)
    fa = np.linalg.norm(sf, axis=1)
    carea = np.zeros(n_cells)
    np.add.at(carea, owner, fa)
    np.add.at(carea, neigh, fa[:n_int])
    cterm = (1.0 / 6.0) * carea / np.power(np.maximum(vol, 1e-300), 2.0 / 3.0)
    print(f"aspect max={ar.max():.3f} p99={np.percentile(ar, 99):.3f} "
          f"p90={np.percentile(ar, 90):.3f} median={np.median(ar):.3f}",
          flush=True)
    order = np.argsort(-ar)[:TOPN]
    print(f"--- top {TOPN} cells: id, aspect, dir_term, compact_term, "
          "centroid(x,y,z), r ---", flush=True)
    for c in order:
        x, y, z = ctr[int(c)]
        print(f"{int(c):6d} {ar[int(c)]:7.3f} {dterm[int(c)]:7.3f} "
              f"{cterm[int(c)]:7.3f} ({x:+.4f},{y:+.4f},{z:+.4f}) "
              f"r={np.hypot(x, y):.4f}", flush=True)
    print(f"z range: [{points[:, 2].min():.4f}, {points[:, 2].max():.4f}] "
          f"r max: {np.hypot(points[:, 0], points[:, 1]).max():.4f}",
          flush=True)


if __name__ == "__main__":
    main()
