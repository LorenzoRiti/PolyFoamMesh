# -*- coding: utf-8 -*-
"""Lane D: dump geometry of the worst-aspect cells (cap cluster specimen).

Usage: python tools/cylinder_worst_cell.py [case_dir] [cell_id]
"""
import sys

sys.path.insert(0, "src")

import numpy as np

from polyfoammesh.core import foam_mesh_io as fio

CASE = sys.argv[1] if len(sys.argv) > 1 else "C:/polybench2/cylinder_axis_both"
CELL = int(sys.argv[2]) if len(sys.argv) > 2 else 2108


def main():
    from pathlib import Path
    points, faces, owner, neigh, patches = fio.read_polymesh(
        Path(CASE) / "constant" / "polyMesh")
    n_int = len(neigh)
    own = np.asarray(owner)
    nb = np.asarray(neigh)
    my = [fi for fi in range(len(faces))
          if own[fi] == CELL or (fi < n_int and nb[fi] == CELL)]
    print(f"cell {CELL}: {len(my)} faces", flush=True)
    verts = sorted({v for fi in my for v in faces[fi]})
    P = np.asarray(points)[verts]
    print(f"  {len(verts)} vertices, extent x={P[:,0].ptp():.4f} "
          f"y={P[:,1].ptp():.4f} z={P[:,2].ptp():.4f}", flush=True)
    print(f"  z range [{P[:,2].min():.4f}, {P[:,2].max():.4f}], "
          f"r max {np.hypot(P[:,0], P[:,1]).max():.4f}", flush=True)
    for fi in my:
        side = "own" if own[fi] == CELL else "nb"
        print(f"  face {fi} ({side}, {len(faces[fi])}-gon)", flush=True)


if __name__ == "__main__":
    main()
