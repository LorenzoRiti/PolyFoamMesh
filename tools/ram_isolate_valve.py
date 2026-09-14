# -*- coding: utf-8 -*-
"""Lane A dual-topology anchor: peak RSS per production stage on the real
valve dual fixture (562k cells, 1.24M faces, genuine concave defects).

Same stages as ram_isolate_bl.py; patches collapse to a single wall patch
(the fixture carries no patch table). Exercises merge repair WITH real
concave cells (unlike the clean hex box).

Usage: python tools/ram_isolate_valve.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "tools")

import numpy as np

from ram_isolate_bl import PeakSampler, rss_mb
from polyfoammesh.core.bl_poly import (
    PolyBoundaryLayerEngine,
    _cell_metrics,
    _pyramid_violations,
)
from polyfoammesh.core.poly_cell_merge import repair_concave_cells_if_safe


def main():
    import sys as _sys
    tiles = int(_sys.argv[1]) if len(_sys.argv) > 1 else 1
    print(f"valve-dual x{tiles} baseline_after_imports={rss_mb():.0f} MB",
          flush=True)
    with PeakSampler() as s:
        d = np.load("tests/fixtures/valve_dual.npz")
        points0 = d["points"].astype(np.float64)
        n_int0 = int(d["n_int"])
        face_verts = d["face_verts"]
        face_sizes = d["face_sizes"].tolist()
        face_offsets = d["face_offsets"].tolist()
        faces0 = []
        for i in range(len(face_sizes)):
            st = face_offsets[i]
            faces0.append(face_verts[st:st + face_sizes[i]].tolist())
        del face_verts, face_sizes, face_offsets
        owner0 = d["owner"].astype(np.int64)
        neigh0 = d["neighbour"].astype(np.int64)
        d.close()
        n_cells0 = int(max(owner0.max(), neigh0.max())) + 1
        # tile along x: disjoint union, each tile keeps its own closed
        # boundary -> the union is a valid multi-body mesh (closure holds
        # per cell; validity failures, if any, mirror the single valve).
        span = float(points0[:, 0].max() - points0[:, 0].min())
        n_pts0 = len(points0)
        points = np.vstack(
            [points0 + np.array([t * (span + 1.0), 0.0, 0.0]) for t in range(tiles)]
        )
        faces_all, owner_parts = [], []
        for t in range(tiles):
            vo, co = t * n_pts0, t * n_cells0
            faces_all.extend([[v + vo for v in f] for f in faces0])
            owner_parts.append(owner0 + co)
        owner_all = np.concatenate(owner_parts)
        # engine order: ALL internal faces first (tile-major), then boundary.
        nF0 = len(faces0)
        internal_idx = np.concatenate(
            [np.arange(t * nF0, t * nF0 + n_int0) for t in range(tiles)]
        )
        bnd_mask = np.ones(tiles * nF0, dtype=bool)
        bnd_mask[internal_idx] = False
        order = np.concatenate([internal_idx, np.flatnonzero(bnd_mask)])
        faces = [faces_all[i] for i in order.tolist()]
        owner = owner_all[order]
        n_int = tiles * n_int0
        _local = internal_idx % nF0
        _tile = internal_idx // nF0
        neigh = neigh0[_local] + _tile * n_cells0
        del faces0, faces_all, owner0, neigh0, owner_parts, order
        del internal_idx, bnd_mask, _local, _tile
        patches = [{
            "name": "wall", "type": "patch",
            "nFaces": len(faces) - n_int, "startFace": n_int,
        }]
        n_cells = int(max(owner.max(), neigh.max())) + 1
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
    print(f"[input-metrics] pyr_before={len(pyr_idx)} peak={s.peak:.0f} MB",
          flush=True)

    eng = PolyBoundaryLayerEngine(
        Path(tempfile.mkdtemp(prefix="ram_valve_")),
        log=lambda msg: None, cancel=lambda: False,
    )
    sel = list(range(len(faces) - n_int))
    with PeakSampler() as s:
        pre = eng._build_precompute(
            points, faces, owner, neigh, patches, n_int, sel, drop_bnd,
        )
    print(f"[precompute] wall_verts={len(pre['wall_verts']):,} "
          f"peak={s.peak:.0f} MB", flush=True)

    with PeakSampler() as s:
        built = eng._build(
            points, faces, owner, neigh, patches, n_int, sel,
            2, 1e-5, 1.2, 0.5, pyr_before=len(pyr_idx),
            drop_bnd=drop_bnd, pre=pre,
        )
    print(f"[build] prisms={built['n_prism_cells']:,} "
          f"faces_out={len(built['faces']):,} peak={s.peak:.0f} MB", flush=True)

    with PeakSampler() as s:
        ok, msg = eng._validate(built, total_vol0, 0.0)
    print(f"[validate] ok={ok} msg={msg} peak={s.peak:.0f} MB", flush=True)

    bp, bf, bo, bn = (built["points"], built["faces"], built["owner"],
                      built["neigh"])
    bn_cells = int(max(bo.max(), bn.max())) + 1
    with PeakSampler() as s:
        _out = repair_concave_cells_if_safe(
            bp, bf, bo, bn, int(built["n_int"]), bn_cells, built["patches"],
        )
    rep = _out[-1]
    print(f"[merge] applied={rep['applied']} concave={rep['concave_cells']} "
          f"peak={s.peak:.0f} MB", flush=True)


if __name__ == "__main__":
    main()
