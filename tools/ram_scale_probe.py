# -*- coding: utf-8 -*-
"""RAM scale probe: measure per-face cost of the current mesh representation.

Builds small structured-hex boxes (vectorized topology, list-of-lists faces
exactly like foam_mesh_io.read_faces returns) and samples RSS deltas for:
compact arrays -> faces list -> vert_faces dict (BL precompute pattern).

Usage:
  python tools/ram_scale_probe.py [nx ny nz ...]
Each triple is one box; defaults are small and fast.
"""
import sys

import numpy as np
import psutil


def rss_mb():
    return psutil.Process().memory_info().rss / (1024.0 * 1024.0)


def build_hex_box(nx, ny, nz):
    # vertex id: ix*(ny+1)*(nz+1) + iy*(nz+1) + iz
    sy = nz + 1
    sx = (ny + 1) * (nz + 1)

    def V(ix, iy, iz):
        return ix * sx + iy * sy + iz

    # cell id: ix*ny*nz + iy*nz + iz
    def C(ix, iy, iz):
        return ix * ny * nz + iy * nz + iz

    n_cells = nx * ny * nz
    # Vectorized face vertex labels: x-faces, y-faces, z-faces.
    ix = np.arange(nx + 1)[:, None, None]
    iy = np.arange(ny)[None, :, None]
    iz = np.arange(nz)[None, None, :]
    Ixp, Iyp, Izp = ix + 1, iy + 1, iz + 1
    xf = np.stack([
        V(ix, iy, iz), V(ix, Iyp, iz), V(ix, Iyp, Izp), V(ix, iy, Izp),
    ], axis=-1).reshape(-1, 4)
    ix2 = np.arange(nx)[:, None, None]
    iy2 = np.arange(ny + 1)[None, :, None]
    yf = np.stack([
        V(ix2, iy2, iz), V(ix2, iy2, Izp), V(Ixp[:nx], iy2, Izp), V(Ixp[:nx], iy2, iz),
    ], axis=-1).reshape(-1, 4)
    Z3 = np.arange(nz + 1)[None, None, :]
    zf = np.stack([
        V(ix2, iy, Z3), V(Ixp[:nx], iy, Z3), V(Ixp[:nx], Iyp, Z3), V(ix2, Iyp, Z3),
    ], axis=-1).reshape(-1, 4)
    labels = np.concatenate([xf, yf, zf], axis=0).astype(np.int64)
    n_faces = len(labels)

    # owner/neighbour, vectorized over face planes.
    owner = np.empty(n_faces, dtype=np.int64)
    neigh = np.empty(n_faces, dtype=np.int64)
    # x-faces: plane ix=0..nx; owner cell ix-1 (or 0), neighbour ix (or -1)
    nxf = (nx + 1) * ny * nz
    X = np.arange(nx + 1)[:, None, None]
    Y = np.arange(ny)[None, :, None]
    Z = np.arange(nz)[None, None, :]
    own_x = np.where(X == 0, C(0, Y, Z), C(X - 1, Y, Z)).ravel()
    nb_x = np.where(X == nx, -1, C(np.minimum(X, nx - 1), Y, Z)).ravel()
    # y-faces
    nyf = nx * (ny + 1) * nz
    X2 = np.arange(nx)[:, None, None]
    Y2 = np.arange(ny + 1)[None, :, None]
    own_y = np.where(Y2 == 0, C(X2, 0, Z), C(X2, Y2 - 1, Z)).ravel()
    nb_y = np.where(Y2 == ny, -1, C(X2, np.minimum(Y2, ny - 1), Z)).ravel()
    # z-faces
    X3 = np.arange(nx)[:, None, None]
    Y3 = np.arange(ny)[None, :, None]
    Z3 = np.arange(nz + 1)[None, None, :]
    own_z = np.where(Z3 == 0, C(X3, Y3, 0), C(X3, Y3, Z3 - 1)).ravel()
    nb_z = np.where(Z3 == nz, -1, C(X3, Y3, np.minimum(Z3, nz - 1))).ravel()
    owner[:nxf] = own_x
    neigh[:nxf] = nb_x
    owner[nxf:nxf + nyf] = own_y
    neigh[nxf:nxf + nyf] = nb_y
    owner[nxf + nyf:] = own_z
    neigh[nxf + nyf:] = nb_z
    internal = neigh >= 0
    return labels, owner, neigh[internal], n_cells


def main():
    args = [int(a) for a in sys.argv[1:]]
    if args:
        assert len(args) % 3 == 0
        sizes = [tuple(args[i:i + 3]) for i in range(0, len(args), 3)]
    else:
        sizes = [(8, 8, 8), (16, 16, 16), (32, 32, 32), (48, 48, 48)]
    print(f"{'box':>12} {'cells':>10} {'faces':>10} "
          f"{'compactMB':>10} {'facesMB':>9} {'vertMB':>8} {'B/face':>8}")
    for nx, ny, nz in sizes:
        base = rss_mb()
        labels, owner, neigh, n_cells = build_hex_box(nx, ny, nz)
        after_compact = rss_mb()
        faces = [row.tolist() for row in labels]
        del labels
        after_faces = rss_mb()
        vert_faces = {}
        for fi, f in enumerate(faces):
            for v in f:
                vert_faces.setdefault(v, []).append(fi)
        after_vert = rss_mb()
        n_faces = len(faces)
        print(f"{nx}x{ny}x{nz:>6} {n_cells:>10,} {n_faces:>10,} "
              f"{after_compact - base:>10.1f} {after_faces - after_compact:>9.1f} "
              f"{after_vert - after_faces:>8.1f} "
              f"{(after_faces - after_compact) * 1048576 / n_faces:>8.0f}")
        del faces, vert_faces, owner, neigh


if __name__ == "__main__":
    main()
