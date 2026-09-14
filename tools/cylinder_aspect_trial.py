# -*- coding: utf-8 -*-
"""Lane D trial: force-merge high-aspect slab cells with their interior
neighbour (thickening direction), reusing merge_cell_groups + the exact
safety gates of repair_concave_cells_if_safe (pyramid/skew/volume).

Works ONLY on a scratch copy, never the reference case. Acceptance per
pair: merged-cell aspect < victim aspect AND no gate regression.

Usage: python tools/cylinder_aspect_trial.py [case_copy] [threshold]
"""
import sys

sys.path.insert(0, "src")

import numpy as np

from polyfoammesh.core import foam_mesh_io as fio
from polyfoammesh.core.bl_poly import (
    _cell_metrics,
    _face_geometry,
    _pyramid_violations,
)
from polyfoammesh.core.poly_cell_merge import merge_cell_groups
from polyfoammesh.core.poly_smoother import _cell_aspect_ratio
from polyfoammesh.core.tet_poly_dual import (
    _cell_centres as _tet_centres,
    _detect_defects,
    _face_geometry as _tet_geometry,
)

CASE = sys.argv[1] if len(sys.argv) > 1 else "C:/polybench2/cyl_aspect_trial"
THR = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0


def _skew_count(pts, fcs, own, nbr, nint, ncell):
    sf, cf = _tet_geometry(pts, fcs)
    ctr, _ = _tet_centres(sf, cf, own, nbr, nint, ncell)
    _, counts = _detect_defects(pts, fcs, sf, cf, ctr, own, nbr, nint, ncell)
    return counts["skew"]


def _metrics(pts, fcs, own, nbr, nint, ncell):
    bad = _pyramid_violations(pts, fcs, own, nbr, nint, ncell)
    bad = int(bad[0]) if isinstance(bad, tuple) else int(bad)
    vol = float(_cell_metrics(pts, fcs, own, nbr, ncell, nint)[0].sum())
    sk = int(_skew_count(pts, fcs, own, nbr, nint, ncell))
    return bad, vol, sk


def main():
    from pathlib import Path
    poly = Path(CASE) / "constant" / "polyMesh"
    pts, faces, owner, neigh, patches = fio.read_polymesh(poly)
    faces = [list(f) for f in faces]
    n_int = len(neigh)
    n_cells = int(max(owner.max(), neigh.max())) + 1
    sf, _cf = _face_geometry(pts, faces)
    vols = _cell_metrics(pts, faces, owner, neigh, n_cells, n_int)[0]
    ar = _cell_aspect_ratio(sf, owner, neigh, n_int, n_cells, vols)
    bad0, vol0, skew0 = _metrics(pts, faces, owner, neigh, n_int, n_cells)
    print(f"start: cells={n_cells} maxAspect={ar.max():.3f} "
          f"pyr={bad0} skew={skew0}", flush=True)

    # shared-face areas for neighbour ranking
    area = np.linalg.norm(sf, axis=1)
    own_a = np.asarray(owner)
    nb_a = np.asarray(neigh)
    accepted = []
    stat = {"gate": 0, "local": 0, "global": 0, "tried": 0}
    # greedy loop with FRESH ranking every round: ids shift after merges.
    while True:
        order = np.argsort(-ar)
        c = int(order[0])
        if float(ar[c]) <= THR:
            break
        progressed = False
        # current aspect of c may have changed after earlier merges
        nbrs = {}
        for fi in range(n_int):
            o, n = int(own_a[fi]), int(nb_a[fi])
            if o == c:
                nbrs.setdefault(n, 0.0)
                nbrs[n] += float(area[fi])
            elif n == c:
                nbrs.setdefault(o, 0.0)
                nbrs[o] += float(area[fi])
        for n, _a in sorted(nbrs.items(), key=lambda kv: -kv[1]):
            stat["tried"] += 1
            f2, o2, nb2, ni2, nc2, p2 = merge_cell_groups(
                faces, own_a, nb_a, n_int, n_cells, patches, [{c, n}])
            b1, v1, s1 = _metrics(pts, f2, o2, nb2, ni2, nc2)
            if int(b1) > int(bad0) or int(s1) > int(skew0):
                stat["gate"] += 1
                continue
            if abs(v1 - vol0) / max(abs(vol0), 1e-30) > 1e-6:
                stat["gate"] += 1
                continue
            sf2, _ = _face_geometry(pts, f2)
            vols2 = _cell_metrics(pts, f2, o2, nb2, nc2, ni2)[0]
            ar2 = _cell_aspect_ratio(sf2, o2, nb2, ni2, nc2, vols2)
            # merged cell id: groups are appended after the compacted
            # ungrouped cells, so a single pair lands on the last id.
            merged_id = nc2 - 1
            # accept on LOCAL improvement (merged < victim) with no GLOBAL
            # worsening and clean gates: every improvable slab gets fixed,
            # not just the current global max.
            if not (float(ar2[merged_id]) < float(ar[c]) - 1e-9):
                stat["local"] += 1
                continue
            if not (float(ar2.max()) <= float(ar.max()) + 1e-9):
                stat["global"] += 1
                continue
            faces, own_a, nb_a = f2, o2, nb2
            n_int, n_cells, patches = ni2, nc2, p2
            ar, vols = ar2, vols2
            sf, area = sf2, np.linalg.norm(sf2, axis=1)
            bad0, vol0, skew0 = b1, v1, s1
            accepted.append((c, n, float(ar.max())))
            print(f"  merged {c}+{n}: newMax={ar.max():.3f}",
                  flush=True)
            progressed = True
            break
        if not progressed:
            print(f"  stalled at cell {c} aspect={ar[c]:.3f}: no neighbour "
                  f"merge improves it", flush=True)
            break
    print(f"done: accepted={len(accepted)} maxAspect={ar.max():.3f} "
          f"cells={n_cells} tried={stat['tried']} rejectGate={stat['gate']} "
          f"rejectLocal={stat['local']} rejectGlobal={stat['global']}",
          flush=True)
    if accepted:
        fio.write_polymesh(poly, np.asarray(pts), faces, np.asarray(own_a),
                           np.asarray(nb_a), patches)
        print("WROTE trial mesh", flush=True)
    else:
        print("no merge accepted: mesh untouched", flush=True)


if __name__ == "__main__":
    main()
