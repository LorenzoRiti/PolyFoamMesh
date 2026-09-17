# -*- coding: utf-8 -*-
"""Lane D follow-up: tail width + merge-gate response on the dual cylinder.

Usage: python tools/cylinder_aspect_followup.py [case_dir]
"""
import sys

sys.path.insert(0, "src")

import numpy as np

from polyfoammesh.core import foam_mesh_io as fio
from polyfoammesh.core.bl_poly import (
    _face_geometry,
    _cell_centroids,
    _pyramid_violations,
)
from polyfoammesh.core.poly_cell_merge import repair_concave_cells_if_safe
from polyfoammesh.core.poly_smoother import _cell_aspect_ratio

CASE = sys.argv[1] if len(sys.argv) > 1 else "C:/polybench2/cylinder_axis_both"


def main():
    from pathlib import Path
    points, faces, owner, neigh, patches = fio.read_polymesh(
        Path(CASE) / "constant" / "polyMesh")
    n_int = len(neigh)
    n_cells = int(max(owner.max(), neigh.max())) + 1
    sf, cf = _face_geometry(points, faces)
    ctr, vol = _cell_centroids(points, faces, owner, neigh, n_int, n_cells)
    ar = _cell_aspect_ratio(sf, owner, neigh, n_int, n_cells, vol)
    for thr in (4.0, 3.5, 3.0):
        print(f"cells with aspect > {thr}: {int((ar > thr).sum())}",
              flush=True)
    bad, idx = _pyramid_violations(
        points, faces, owner, neigh, n_int, n_cells, return_idx=True)
    print(f"pyramid violations: {bad}", flush=True)
    worst = set(np.argsort(-ar)[:25].tolist())
    bad_cells = set(int(owner[i]) for i in idx.tolist()) | set(
        int(neigh[i]) for i in idx.tolist() if int(i) < n_int)
    print(f"top-25 aspect cells that are ALSO pyramid-defective: "
          f"{len(worst & bad_cells)}", flush=True)
    out = repair_concave_cells_if_safe(
        points, faces, owner, neigh, n_int, n_cells, patches)
    rep = out[-1]
    print(f"merge: applied={rep['applied']} concave={rep['concave_cells']} "
          f"candidates={rep['candidates_found']} "
          f"reason={rep.get('reason')}", flush=True)


if __name__ == "__main__":
    main()
