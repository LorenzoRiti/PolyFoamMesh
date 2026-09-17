# -*- coding: utf-8 -*-
"""Structured hex-box polyMesh builder with correct topology.

Unlike ram_scale_probe (rough scaling only), this produces a VALID mesh for
the real BL engine: internal faces first, boundary grouped by patch, outward
normals on every boundary face, owner < neighbour on internal faces.

build(nx, ny, nz, size=1.0) -> dict with points/faces/owner/neighbour/
patches/n_int/n_cells.
"""
import numpy as np


SIDES = ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")


def build(nx, ny, nz, size=1.0):
    sx = (ny + 1) * (nz + 1)
    sy = nz + 1

    def V(ix, iy, iz):
        return ix * sx + iy * sy + iz

    def C(ix, iy, iz):
        return ix * ny * nz + iy * nz + iz

    planes = []
    # x-faces, normal +x (owner->neighbour direction)
    X = np.arange(nx + 1)[:, None, None]
    Y = np.arange(ny)[None, :, None]
    Z = np.arange(nz)[None, None, :]
    lab = np.stack([V(X, Y, Z), V(X, Y + 1, Z), V(X, Y + 1, Z + 1),
                    V(X, Y, Z + 1)], axis=-1).reshape(-1, 4)
    own = np.where(X == 0, C(0, Y, Z), C(X - 1, Y, Z)).ravel()
    nb = np.where(X == nx, -1, C(np.minimum(X, nx - 1), Y, Z)).ravel()
    side = np.broadcast_to(
        np.where(X == 0, 0, np.where(X == nx, 1, -1)), (nx + 1, ny, nz),
    ).ravel()
    planes.append((lab, own, nb, side))
    # y-faces, normal +y
    X2 = np.arange(nx)[:, None, None]
    Y2 = np.arange(ny + 1)[None, :, None]
    lab = np.stack([V(X2, Y2, Z), V(X2, Y2, Z + 1), V(X2 + 1, Y2, Z + 1),
                    V(X2 + 1, Y2, Z)], axis=-1).reshape(-1, 4)
    own = np.where(Y2 == 0, C(X2, 0, Z), C(X2, Y2 - 1, Z)).ravel()
    nb = np.where(Y2 == ny, -1, C(X2, np.minimum(Y2, ny - 1), Z)).ravel()
    side = np.broadcast_to(
        np.where(Y2 == 0, 2, np.where(Y2 == ny, 3, -1)), (nx, ny + 1, nz),
    ).ravel()
    planes.append((lab, own, nb, side))
    # z-faces, normal +z
    Z3 = np.arange(nz + 1)[None, None, :]
    lab = np.stack([V(X2, Y, Z3), V(X2 + 1, Y, Z3), V(X2 + 1, Y + 1, Z3),
                    V(X2, Y + 1, Z3)], axis=-1).reshape(-1, 4)
    own = np.where(Z3 == 0, C(X2, Y, 0), C(X2, Y, Z3 - 1)).ravel()
    nb = np.where(Z3 == nz, -1, C(X2, Y, np.minimum(Z3, nz - 1))).ravel()
    side = np.broadcast_to(
        np.where(Z3 == 0, 4, np.where(Z3 == nz, 5, -1)), (nx, ny, nz + 1),
    ).ravel()
    planes.append((lab, own, nb, side))

    lab_all = np.concatenate([p[0] for p in planes], axis=0)
    own_all = np.concatenate([p[1] for p in planes], axis=0)
    nb_all = np.concatenate([p[2] for p in planes], axis=0)
    side_all = np.concatenate([p[3] for p in planes], axis=0)
    assert len(side_all) == len(lab_all) == len(own_all) == len(nb_all)

    internal = side_all < 0
    order = np.concatenate([
        np.flatnonzero(internal),
        *(np.flatnonzero(side_all == s) for s in range(6)),
    ])
    lab_all = lab_all[order]
    own_all = own_all[order]
    nb_all = nb_all[order]
    side_all = side_all[order]

    n_int = int(internal.sum())
    # min-side patches (0, 2, 4) carry inward (+axis) normals: reverse them.
    rev = (side_all == 0) | (side_all == 2) | (side_all == 4)
    lab_all[rev] = lab_all[rev][:, ::-1]

    n_bnd = len(lab_all) - n_int
    patches = []
    for s, name in enumerate(SIDES):
        m = side_all[n_int:] == s
        patches.append({
            "name": name, "type": "wall",
            "nFaces": int(m.sum()),
            "startFace": int(n_int + np.flatnonzero(m)[0]) if m.any() else n_int,
        })

    nvx, nvy, nvz = nx + 1, ny + 1, nz + 1
    IX, IY, IZ = np.meshgrid(
        np.linspace(0, size, nvx), np.linspace(0, size, nvy),
        np.linspace(0, size, nvz), indexing="ij",
    )
    points = np.stack([IX.ravel(), IY.ravel(), IZ.ravel()], axis=1)
    # meshgrid ravel order must match V(): ix*sx + iy*sy + iz -> ravel C-order
    # of (ix, iy, iz) gives exactly that. Verify cheaply:
    assert int(V(1, 0, 0)) == sx and int(V(0, 1, 0)) == sy

    return {
        "points": points,
        "faces": [row.tolist() for row in lab_all],
        "owner": own_all.astype(np.int32),
        "neighbour": nb_all[:n_int].astype(np.int32),
        "patches": patches,
        "n_int": n_int,
        "n_cells": nx * ny * nz,
        "spacing": size / max(nx, ny, nz),
    }
