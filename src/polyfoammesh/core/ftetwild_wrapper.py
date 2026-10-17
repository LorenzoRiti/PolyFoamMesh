"""fTetWild tetrahedral meshing wrapper (experimental — issue #12).

Runs the whole experimental tet stage in ONE short-lived subprocess,
mirroring :mod:`core.gmsh_wrapper`'s CLI contract (one JSON result line
printed on stdout):

    python ftetwild_wrapper.py volume --step=<geom> --msh=<out.msh>
        --detail=<level> [--max-cell=<abs edge>] [--lr=<rel edge>]
        [--opt-iter=<n>]

Pipeline inside that subprocess:

1. GMSH surface mesh of the STEP/STL geometry
   (:func:`gmsh_wrapper.generate_surface_stl`) plus
   :func:`gmsh_wrapper.export_tagged_surface_npz` — the per-physical-group
   triangles that GMSH's own STL export loses (``gmsh.write`` emits a
   single "Created by Gmsh" solid, so patch names must be carried
   alongside the STL).
2. fTetWild tetrahedralization via the optional ``pytetwild`` package
   (``pip install polyfoammesh[ftetwild]``).
3. Boundary faces of the tet mesh assigned to the input patch names by
   nearest-centroid lookup (scipy cKDTree).
4. A Gmsh MSH 2.2 file with ``$PhysicalNames`` written for the existing,
   production gmshToFoam conversion (``gmsh_wrapper.py convert_to_foam``)
   — no custom polyMesh writer anywhere.

Motivation (issue #12): GMSH dominates the wall time of the tet stage;
fTetWild is a robust, fast alternative (see the feasibility spike:
venturi 16x faster than GMSH at better quality, robust on geometry GMSH
refuses to load).

Tunables (env): ``FTETWILD_LR`` overrides the relative edge length,
``FTETWILD_OPT_ITER`` the optimization-iteration budget.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

#: Detail level (GUI slider) -> target cells across the bounding box's
#: MIDDLE dimension (the physical cross-section on elongated parts). Same
#: philosophy and values as gmsh_wrapper._GMSH_DETAIL's ``cells_across`` —
#: the app's own resolution knob — so the Detail slider moves fTetWild the
#: same way it moves GMSH. Replaces an earlier bbox-DIAGONAL-relative
#: mapping (lr = diag/20): on a real elongated part (2 m long, 0.1 x 0.1
#: section) that gave ONE cell across the section — a target 20x coarser
#: than the input surface — and sent fTetWild's optimizer into an
#: unbounded edge-splitting runaway (measured: 884k splits in pass 0,
#: 5.1M tets, energy stuck at 31k instead of converging toward ~10).
DETAIL_CELLS_ACROSS: dict[str, int] = {
    "very_coarse": 8,
    "coarse": 13,
    "medium": 20,
    "fine": 32,
    "very_fine": 48,
}

DEFAULT_CELLS_ACROSS = 20

#: Bbox-DIAGONAL-relative edge length, used ONLY when explicitly requested
#: via the FTETWILD_LR env override (fTetWild's own paper default).
DEFAULT_LR = 0.05

#: Optimization-iteration budget (upstream default is 80). The spike
#: measured the upstream default on a geometry whose AMIPS energy stalls:
#: the mesh kept subdividing past 330k tets and never finished in 10 min,
#: while 15 iterations completed with better percentile quality than GMSH
#: on the same input. Keep this modest until a convergence-based budget
#: exists.
DEFAULT_OPT_ITER = 15

#: Tet face local vertex indices (VTK tetra ordering) and, for each face,
#: the index of the opposite vertex (used to orient faces outward).
_TET_FACES: tuple[tuple[int, int, int], ...] = (
    (0, 1, 2),
    (0, 1, 3),
    (0, 2, 3),
    (1, 2, 3),
)
_TET_OPPOSITE: tuple[int, ...] = (3, 2, 1, 0)

#: Characters an OpenFOAM patch name may not contain (kept in sync with
#: boundary_reader._INVALID_PATCH_NAME_RE's structural class plus spaces).
_INVALID_NAME_RE = re.compile(r'[;{}"/\\\s]')


def sanitize_patch_names(names) -> list[str]:
    """Make patch names valid OpenFOAM words, preserving order, deduped.

    Physical names coming out of a STEP import can contain spaces or
    slashes ("Shape/Open CASCADE STEP translator 7.9 1"); gmshToFoam
    silently falls back to ``patch0`` for those. Runs of invalid
    characters collapse to a single ``_``.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = _INVALID_NAME_RE.sub("_", str(raw)).strip("_") or "patch"
        base = name
        n = 2
        while name in seen:
            name = f"{base}_{n}"
            n += 1
        seen.add(name)
        out.append(name)
    return out


def boundary_faces(V: np.ndarray, T: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Faces used by exactly one tet, with their owner-tet index.

    Returns ``(faces, owner)`` where ``faces`` is ``(m, 3)`` int64 vertex
    ids (arbitrary winding) and ``owner`` the tet that references each
    face.
    """
    # allf is concatenated FACE-BLOCK-wise (all tets' face 0, then all
    # tets' face 1, ...) — so the per-row tet index is np.tile, NOT
    # np.repeat (repeat would pair each face with the wrong tet; caught by
    # the unit test, and the spike shipped the same slip, harmlessly only
    # because gmshToFoam re-orients from the volume elements anyway).
    allf = np.concatenate([T[:, list(f)] for f in _TET_FACES], axis=0)
    tet_idx = np.tile(np.arange(len(T)), len(_TET_FACES))
    key = np.sort(allf, axis=1)
    _uniq, inv, counts = np.unique(
        key, axis=0, return_inverse=True, return_counts=True
    )
    inv = np.asarray(inv).reshape(-1)  # numpy 2.x inverse-shape churn
    # A key with count==1 belongs to exactly ONE row, so that row IS the
    # boundary face — no first-occurrence index lookup needed.
    rows = np.nonzero(counts[inv] == 1)[0]
    return allf[rows], tet_idx[rows]


def orient_faces_outward(
    V: np.ndarray, T: np.ndarray, faces: np.ndarray, owner: np.ndarray
) -> np.ndarray:
    """Return ``faces`` with winding flipped so normals point out of the solid.

    A face is outward when ``normal . (owner_tet_centroid - vertex) < 0``
    for the right-handed winding; gmshToFoam places each surface face
    according to its own winding, so this cannot be left arbitrary.
    """
    out = faces.copy()
    a, b, c = V[faces[:, 0]], V[faces[:, 1]], V[faces[:, 2]]
    n = np.cross(b - a, c - a)
    tet_c = V[np.asarray(T)[np.asarray(owner)]].mean(axis=1)
    flip = np.einsum("ij,ij->i", n, tet_c - a) > 0
    if np.any(flip):
        out[flip, 1], out[flip, 2] = faces[flip, 2], faces[flip, 1]
    return out


def assign_patches(
    V: np.ndarray,
    faces: np.ndarray,
    src_tris: np.ndarray,
    src_codes: np.ndarray,
) -> tuple[np.ndarray, dict]:
    """Assign each output boundary face a patch code from the input surface.

    fTetWild output carries no physical groups, but its surface stays
    within the epsilon envelope of the input triangulation — so the
    nearest input triangle centroid is a reliable patch witness at the
    input's own sampling density (spike: mean assignment distance 5.6e-7
    vs 1.2e-6 mean centroid spacing on the same surface).

    Returns ``(codes, stats)``.
    """
    from scipy.spatial import cKDTree

    in_cent = src_tris.mean(axis=1)
    tree = cKDTree(in_cent)
    out_cent = V[faces].mean(axis=1)
    dist, nn = tree.query(out_cent)
    codes = src_codes[nn]
    dd, _ = tree.query(in_cent, k=2)
    stats = {
        "n_output_boundary_faces": int(len(faces)),
        "n_input_triangles": int(len(src_tris)),
        "input_mean_centroid_spacing": float(dd[:, 1].mean()),
        "assign_dist_mean": float(dist.mean()),
        "assign_dist_p99": float(np.percentile(dist, 99)),
        "assign_dist_max": float(dist.max()),
    }
    return codes, stats


def write_msh22(
    out_path: Path | str,
    V: np.ndarray,
    T: np.ndarray,
    faces: np.ndarray,
    face_phys: np.ndarray,
    patch_names: list[str],
) -> None:
    """Write a Gmsh MSH 2.2 file consumable by gmshToFoam.

    Layout mirrors what gmsh itself writes for the production path
    (verified in the spike): ``$PhysicalNames`` lists one dim-2 entry per
    patch, surface elements carry their patch's physical id in both tag
    slots, volume elements carry physical 1. MSH 2.2 — gmshToFoam
    segfaults on 4.x files with node-tag gaps (see gmsh_wrapper's
    MshFileVersion note), and the ids here are contiguous 1-based anyway.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="ascii") as fh:
        fh.write("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n")
        fh.write(f"$PhysicalNames\n{len(patch_names)}\n")
        for i, name in enumerate(patch_names, 1):
            fh.write(f'2 {i} "{name}"\n')
        fh.write("$EndPhysicalNames\n")
        fh.write(f"$Nodes\n{len(V)}\n")
        for i, (x, y, z) in enumerate(V, 1):
            fh.write(f"{i} {x:.17g} {y:.17g} {z:.17g}\n")
        fh.write("$EndNodes\n")
        fh.write(f"$Elements\n{len(faces) + len(T)}\n")
        eid = 1
        for face, phys in zip(faces, face_phys):
            fh.write(
                f"{eid} 2 2 {int(phys)} {int(phys)} "
                f"{face[0] + 1} {face[1] + 1} {face[2] + 1}\n"
            )
            eid += 1
        for tet in T:
            fh.write(
                f"{eid} 4 2 1 1 "
                f"{tet[0] + 1} {tet[1] + 1} {tet[2] + 1} {tet[3] + 1}\n"
            )
            eid += 1
        fh.write("$EndElements\n")


def tetrahedralize(
    stl_path: Path | str,
    *,
    lr: float | None = None,
    la: float | None = None,
    opt_iter: int = DEFAULT_OPT_ITER,
    decimate: float | None = None,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Run fTetWild on a surface STL. Returns ``(V, T, stats)``.

    ``lr`` is the relative edge length (bbox fraction), ``la`` an absolute
    one (the GUI's manual Max Cell Size); ``la`` wins when both are set.
    ``decimate`` (fraction of faces to keep) pre-simplifies the surface
    with quadric decimation — see :func:`_decimation_reduction` for why
    fTetWild needs this outside its own pipeline. Import of pytetwild is
    deferred so every other function in this module stays usable without
    the optional dependency.
    """
    try:
        import pyvista as pv
        from pytetwild import tetrahedralize_pv
    except ImportError as exc:
        raise RuntimeError(
            "pytetwild non installato — opzionale sperimentale. "
            "Installarlo con: pip install pytetwild"
        ) from exc

    surf = pv.read(str(stl_path))
    if not isinstance(surf, pv.PolyData):
        surf = surf.extract_surface()
    surf = surf.triangulate()

    n_input_tris = int(surf.n_cells)
    n_decimated_tris = n_input_tris
    if decimate is not None and 0.0 < decimate < 1.0:
        try:
            # decimate_pro's parameter is the REDUCTION fraction (0.9
            # removes 90%); `decimate` is the fraction to KEEP.
            surf = surf.decimate_pro(1.0 - float(decimate))
            # Quadric decimation leaves irregular facets; fTetWild follows
            # them and the barycentric dual then builds warped boundary
            # polygons at feature edges (measured on a real part: 167
            # checkMesh-style pyramid violations, ALL within ~1.3 mm of a
            # >40 deg edge, none on smooth regions; the same measurement
            # after this pass: 46). Taubin smoothing (feature_smoothing=
            # False default: sharp edges preserved) regularizes the facets
            # without shrinking the shape.
            surf = surf.smooth_taubin(n_iter=20, pass_band=0.1)
            n_decimated_tris = int(surf.n_cells)
        except Exception:
            # Decimation is an optimization; the original surface is still
            # valid input for fTetWild (only slower).
            logger.warning("surface decimation failed - using the original", exc_info=True)

    t0 = time.perf_counter()
    # Progress visibility (user report: "si ferma e non so perché"): with
    # quiet=True the whole tetrahedralization prints NOTHING (pyvista
    # confirmed: no progress callbacks, the GIL is held by the call), so
    # the stage looked frozen. loglevel=3 streams a handful of phase
    # lines (loading / collapsing / performed / timings) without the
    # per-pass debug spam. FTETWILD_QUIET=1 restores silence.
    _quiet_env = os.environ.get("FTETWILD_QUIET", "")
    quiet = _quiet_env.strip().lower() in ("1", "true", "yes")
    try:
        loglevel = int(os.environ.get("FTETWILD_LOGLEVEL", "3"))
    except ValueError:
        loglevel = 3
    grid = tetrahedralize_pv(
        surf,
        edge_length_fac=DEFAULT_LR if lr is None else lr,
        edge_length_abs=la if la and la > 0 else None,
        quiet=quiet,
        loglevel=loglevel,
        num_opt_iter=int(opt_iter),
    )
    elapsed = time.perf_counter() - t0

    cells = grid.cells_dict
    if 10 not in cells:
        raise RuntimeError(
            f"fTetWild produced no tetrahedra (cell types: "
            f"{sorted(int(k) for k in cells)})"
        )
    T = np.asarray(cells[10], dtype=np.int64)
    V = np.asarray(grid.points, dtype=np.float64)
    stats = {
        "n_nodes": int(len(V)),
        "n_tets": int(len(T)),
        "tet_time_s": round(elapsed, 3),
        "lr": lr,
        "la": la,
        "opt_iter": int(opt_iter),
        "n_input_tris": n_input_tris,
        "n_decimated_tris": n_decimated_tris,
    }
    return V, T, stats


def load_surface_solids(stl_path: Path | str) -> tuple[np.ndarray, list[str]]:
    """``(triangles, per-triangle patch names)`` of an STL surface.

    ASCII multi-solid exports keep their ``solid NAME`` patch names — the
    format core/stl_writer.py writes. Binary STL (and single-solid files
    without a usable name) carry no patch identity to recover, so they
    collapse to one patch named after the file.
    """
    path = Path(stl_path)
    text: str | None
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, UnicodeError):
        text = None
    if text and text.lstrip().startswith("solid") and "facet normal" in text:
        tris_list: list[np.ndarray] = []
        per_block: list[tuple[str, int]] = []
        for blk in re.split(r"\bsolid\b", text)[1:]:
            lines = blk.splitlines()
            nm = (lines[0].strip() if lines else "") or path.stem
            if nm.split()[0] == "endsolid":
                continue
            verts = [
                list(map(float, ln.split()[1:4]))
                for ln in lines
                if ln.strip().startswith("vertex")
            ]
            if verts:
                tris_list.append(
                    np.asarray(verts, dtype=np.float64).reshape(-1, 3, 3)
                )
                per_block.append((nm, len(verts) // 3))
        if tris_list:
            names: list[str] = []
            for nm, n_tris in per_block:
                names.extend([nm] * n_tris)
            return np.concatenate(tris_list), names

    # Fallback: binary or name-less STL — one patch named after the file.
    import trimesh

    m = trimesh.load(str(path), force="mesh")
    tri = getattr(m, "triangles", None)
    tris = (
        np.asarray(tri, dtype=np.float64)
        if tri is not None
        else np.empty((0, 3, 3))
    )
    if tris.ndim != 3 or len(tris) == 0:
        raise RuntimeError(f"STL surface has no triangles: {path}")
    return tris, [path.stem] * len(tris)


def _median_edge_length(tris: np.ndarray) -> float:
    """Median triangle-edge length of a surface — the input's own sampling
    density, used as the sizing sanity reference in the run log.

    Shared edges appear twice (once per adjacent triangle); the median is
    robust to that and the duplicates only weight the distribution
    uniformly anyway."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    edges = np.concatenate(
        [
            np.linalg.norm(b - a, axis=1),
            np.linalg.norm(c - b, axis=1),
            np.linalg.norm(a - c, axis=1),
        ]
    )
    return float(np.median(edges))


def _default_target_edge(cross_scale: float, detail: str) -> float:
    """Default target edge from the Detail slider — the user's intent only.

    cells across the cross-section (DETAIL_CELLS_ACROSS), same resolution
    model as the GMSH path. This used to ALSO be clamped down to 2x the
    input surface density as an anti-runaway guard; that was wrong once
    surface decimation existed (measured: on a real 3 m part whose gmsh
    surface came out at ~1.4 mm median, the clamp dragged the target from
    12.75 mm to 2.85 mm — ~40M cells, a stall by construction). The
    density alignment belongs to _decimation_reduction, in the OTHER
    direction: the INPUT is adapted to the target, never the target to
    the input.
    """
    return cross_scale / DETAIL_CELLS_ACROSS.get(detail, DEFAULT_CELLS_ACROSS)


def _decimation_reduction(med_edge: float, target_edge: float) -> float | None:
    """Face-count fraction to keep when decimating an over-dense surface.

    fTetWild's own ``simplify`` removes detail only within the eps
    envelope — it does NOT decimate an over-dense input toward the target
    edge (fTetWild#93 / pytetwild#47; measured on a real 3 m part: a
    386k-triangle surface at ~1.4 mm against a 12.75 mm target had to be
    inserted and then collapsed element by element, blowing up the tet
    stage). Quadric decimation BEFORE the call brings the input to the
    density fTetWild is documented to work best with (target ~= input
    edge). This is the ONLY direction that may adapt densities: the
    INPUT goes to the target — never the target to the input (an earlier
    clamp that did the opposite dragged a 12.75 mm target down to 2.85 mm
    on that same part = ~40M cells, a stall by construction).

    Fires only when the input is >1.3x finer than the target; keeps a 1%
    floor so extreme inputs still leave a usable surface.
    """
    if med_edge <= 0 or target_edge <= 0:
        return None
    if med_edge * 1.3 > target_edge:
        return None
    return max((med_edge / target_edge) ** 2, 0.01)


def boundary_defects(V: np.ndarray, T: np.ndarray) -> tuple[int, int]:
    """Count the two defect classes the dual cannot close a cell for.

    Returns ``(n_nonmanifold_edges, n_bowtie_vertices)``:
    * non-manifold boundary edges: edges with != 2 incident boundary faces;
    * bowtie vertices: vertices whose boundary fan is disconnected — the
      tet one-ring splits into >1 face-connected corner, so the star is
      not a ball. This includes pure vertex pinches that carry NO
      non-manifold edge (the class the edge-only check misses).
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    allf = np.concatenate([T[:, list(f)] for f in _TET_FACES], axis=0)
    key = np.sort(allf, axis=1)
    _u, inv, counts = np.unique(
        key, axis=0, return_inverse=True, return_counts=True
    )
    inv = np.asarray(inv).reshape(-1)
    bfaces = allf[counts[inv] == 1]
    if len(bfaces) == 0:
        return 0, 0
    m = len(bfaces)
    bedges = np.concatenate(
        [bfaces[:, [0, 1]], bfaces[:, [1, 2]], bfaces[:, [2, 0]]]
    )
    be_key = np.sort(bedges, axis=1)
    # canonical edge id via unique rows
    bu, binv, bc = np.unique(be_key, axis=0, return_inverse=True, return_counts=True)
    binv = np.asarray(binv).reshape(-1)
    n_nm = int((bc != 2).sum())

    # face adjacency through edges whose count is exactly 2: in the
    # edge-sorted order those two entries are adjacent
    face_of_row = np.tile(np.arange(m), 3)
    order = np.argsort(binv, kind="stable")
    s_inv = binv[order]
    s_face = face_of_row[order]
    starts = np.flatnonzero(np.r_[True, s_inv[1:] != s_inv[:-1]])
    rows_f: list[int] = []
    cols_f: list[int] = []
    for eid in np.flatnonzero(bc == 2):
        a = starts[eid]
        rows_f.append(int(s_face[a]))
        cols_f.append(int(s_face[a + 1]))
    if rows_f:
        g = coo_matrix(
            (np.ones(len(rows_f) * 2, dtype=np.int8),
             (np.array(rows_f + cols_f), np.array(cols_f + rows_f))),
            shape=(m, m),
        )
        _nc, comp = connected_components(g, directed=False)
    else:
        comp = np.arange(m)
    # a bowtie vertex has faces from >1 component
    pair_v = bfaces.reshape(-1)
    pair_c = np.repeat(comp, 3)
    uniq_pairs = np.unique(
        np.stack([pair_v, pair_c], axis=1), axis=0
    )
    v_ids, v_counts = np.unique(uniq_pairs[:, 0], return_counts=True)
    n_bow = int((v_counts > 1).sum())
    return n_nm, n_bow


def split_nonmanifold_vertices(
    V: np.ndarray, T: np.ndarray, max_iter: int = 12
) -> tuple[np.ndarray, np.ndarray, int]:
    """Desingularize the boundary so every vertex star is a ball.

    fTetWild's output may have a non-manifold boundary where two sheets
    touch (its documented ``--manifold-surface`` class — pytetwild does
    not expose that flag, so the equivalent post-pass lives here). Two
    defect shapes occur, both measured on a real 3 m duct: edges with 4
    boundary faces (a V-pinch of 3 vertices) and pure VERTEX bowties
    whose edges are all manifold. The barycentric dual needs every star
    to be a ball — its cells are otherwise combinatorially open — so a
    vertex whose tet one-ring splits into >1 face-connected corner gets
    duplicated per corner (one copy per extra corner), with a micro
    offset (1e-2 x local edge, ~1% tet distortion) so the duplicates are
    not numerically coincident.

    Iterates to a fixpoint: splitting one bowtie can reveal/leave
    others. Returns ``(V, T, n_splits)``; inputs are not modified. The
    caller SHOULD re-check with :func:`boundary_defects` and log any
    residual honestly.
    """
    V = np.asarray(V, dtype=np.float64).copy()
    T = np.asarray(T, dtype=np.int64).copy()
    added: list[np.ndarray] = []
    n_splits = 0

    def _pos(idx: int) -> np.ndarray:
        idx = int(idx)
        return V[idx] if idx < len(V) else added[idx - len(V)]

    for _ in range(max_iter):
        n_nm, n_bow = boundary_defects(V, T)
        if n_nm == 0 and n_bow == 0:
            break
        # candidates: endpoints of non-manifold edges + bowtie vertices
        allf = np.concatenate([T[:, list(f)] for f in _TET_FACES], axis=0)
        key = np.sort(allf, axis=1)
        _u, inv, counts = np.unique(
            key, axis=0, return_inverse=True, return_counts=True
        )
        inv = np.asarray(inv).reshape(-1)
        bfaces = allf[counts[inv] == 1]
        bedges = np.sort(
            np.concatenate(
                [bfaces[:, [0, 1]], bfaces[:, [1, 2]], bfaces[:, [2, 0]]]
            ),
            axis=1,
        )
        eu, ec = np.unique(bedges, axis=0, return_counts=True)
        cand = set(int(x) for x in eu[ec != 2].ravel())
        # bowtie detection again locally (reuse boundary_defects' logic by
        # asking it for the count only would lose the ids; cheap enough to
        # recompute the pair list here)
        from scipy.sparse import coo_matrix as _coo
        from scipy.sparse.csgraph import connected_components as _cc

        m = len(bfaces)
        be_key = np.sort(
            np.concatenate(
                [bfaces[:, [0, 1]], bfaces[:, [1, 2]], bfaces[:, [2, 0]]]
            ),
            axis=1,
        )
        _bu, binv, bc = np.unique(
            be_key, axis=0, return_inverse=True, return_counts=True
        )
        binv = np.asarray(binv).reshape(-1)
        face_of_row = np.tile(np.arange(m), 3)
        order = np.argsort(binv, kind="stable")
        s_inv = binv[order]
        s_face = face_of_row[order]
        starts = np.flatnonzero(np.r_[True, s_inv[1:] != s_inv[:-1]])
        r_f, c_f = [], []
        for eid in np.flatnonzero(bc == 2):
            a = starts[eid]
            r_f.append(int(s_face[a]))
            c_f.append(int(s_face[a + 1]))
        if r_f:
            g = _coo(
                (np.ones(len(r_f) * 2, dtype=np.int8),
                 (np.array(r_f + c_f), np.array(c_f + r_f))),
                shape=(m, m),
            )
            _nc, comp = _cc(g, directed=False)
        else:
            comp = np.arange(m)
        pairs = np.unique(
            np.stack([bfaces.reshape(-1), np.repeat(comp, 3)], axis=1), axis=0
        )
        v_ids, v_counts = np.unique(pairs[:, 0], return_counts=True)
        cand.update(int(x) for x in v_ids[v_counts > 1])

        progress = 0
        for v in sorted(cand):
            local = np.nonzero((T == v).any(axis=1))[0]
            if len(local) == 0:
                continue
            parent = {int(t): int(t) for t in local}

            def find(x: int) -> int:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            face_map: dict[tuple, list[int]] = {}
            for t in local:
                tet = T[t]
                for f in _TET_FACES:
                    verts = tet[list(f)]
                    if v in verts:
                        k = tuple(sorted(int(x) for x in verts))
                        face_map.setdefault(k, []).append(int(t))
            for ts in face_map.values():
                root = find(ts[0])
                for t2 in ts[1:]:
                    parent[find(t2)] = root
            groups: dict[int, list[int]] = {}
            for t in local:
                groups.setdefault(find(int(t)), []).append(int(t))
            if len(groups) <= 1:
                continue

            local_verts = np.unique(T[local])
            others = local_verts[local_verts != v]
            ring_c = (
                np.array([_pos(i) for i in others]).mean(axis=0)
                if len(others)
                else _pos(v)
            )
            direction = _pos(v) - ring_c
            dn = float(np.linalg.norm(direction))
            direction = direction / dn if dn > 0 else np.array([1.0, 0.0, 0.0])
            tet_local = T[local]
            uniq_loc, inv_loc = np.unique(tet_local, return_inverse=True)
            pts_tab = np.array([_pos(i) for i in uniq_loc])
            tets_xyz = pts_tab[inv_loc].reshape(tet_local.shape + (3,))
            edge_len = np.linalg.norm(tets_xyz[:, 1] - tets_xyz[:, 0], axis=1)
            eps = 1e-2 * float(edge_len.mean()) if edge_len.size else 1e-9

            for root in sorted(groups)[1:]:
                new_id = len(V) + len(added)
                added.append(_pos(v) + direction * eps)
                for t in groups[root]:
                    T[t] = np.where(T[t] == v, new_id, T[t])
                n_splits += 1
                progress += 1
        if progress == 0:
            # no split achieved (a pathological configuration): stop and
            # let the caller report the residual defect honestly
            break

    if added:
        V = np.vstack([V, np.asarray(added, dtype=np.float64)])
    return V, T, n_splits


def drop_small_components(
    V: np.ndarray, T: np.ndarray, max_drop: int = 1024
) -> tuple[np.ndarray, np.ndarray, int]:
    """Drop tiny disconnected tet components from fTetWild's output.

    fTetWild can emit an outlier cell / micro-cluster near a contact
    (measured: "Number of regions: 2, region 1 with 1 cells" in the tet
    checkMesh of a real run; the dual then carries a 4-cell floating
    island and checkMesh reports 2 regions). Components are found by
    face-adjacency; only components of at most ``max_drop`` cells are
    removed — the measured artifacts are single cells / handfuls, while
    any legitimate multi-solid body in CFD parts is orders of magnitude
    larger. Dropping a disconnected closed complex keeps the main mesh
    closed. Returns ``(V, T, n_dropped_tets)``; vertex ids are compacted
    if anything was dropped.
    """
    V = np.asarray(V, dtype=np.float64)
    T = np.asarray(T, dtype=np.int64)
    if len(T) == 0:
        return V, T, 0
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    allf = np.concatenate([T[:, list(f)] for f in _TET_FACES], axis=0)
    tet_of_row = np.tile(np.arange(len(T)), len(_TET_FACES))
    key = np.sort(allf, axis=1)
    _u, inv, counts = np.unique(
        key, axis=0, return_inverse=True, return_counts=True
    )
    inv = np.asarray(inv).reshape(-1)
    # order rows by key so count==2 pairs are adjacent
    order = np.argsort(inv, kind="stable")
    s_inv = inv[order]
    s_tet = tet_of_row[order]
    starts = np.flatnonzero(np.r_[True, s_inv[1:] != s_inv[:-1]])
    r_l: list[int] = []
    c_l: list[int] = []
    for eid in np.flatnonzero(counts == 2):
        a = starts[eid]
        t1, t2 = int(s_tet[a]), int(s_tet[a + 1])
        if t1 != t2:
            r_l.append(t1)
            c_l.append(t2)
    if not r_l:
        return V, T, 0
    g = coo_matrix(
        (np.ones(2 * len(r_l), dtype=np.int8),
         (np.array(r_l + c_l), np.array(c_l + r_l))),
        shape=(len(T), len(T)),
    )
    _nc, lab = connected_components(g, directed=False)
    sizes = np.bincount(lab, minlength=lab.max() + 1)
    main = int(sizes.argmax())
    drop_mask = (lab != main) & (sizes[lab] <= max_drop)
    n_dropped = int(drop_mask.sum())
    if n_dropped == 0:
        return V, T, 0
    T2 = T[~drop_mask]
    used = np.unique(T2)
    remap = np.full(len(V), -1, dtype=np.int64)
    remap[used] = np.arange(len(used), dtype=np.int64)
    return V[used], remap[T2], n_dropped


def run_volume(
    step_path: Path | str,
    msh_path: Path | str,
    detail: str = "medium",
    max_cell: float = 0.0,
    lr: float | None = None,
    opt_iter: int | None = None,
) -> dict:
    """Full experimental tet stage: surface -> fTetWild -> patched .msh.

    Returns the CLI result dict (also printed as the final stdout JSON
    line by ``_cli_main``).
    """
    t_start = time.perf_counter()
    step_path = Path(step_path).resolve()
    msh_path = Path(msh_path).resolve()
    msh_path.parent.mkdir(parents=True, exist_ok=True)

    stl_path = msh_path.with_suffix(".surface.stl")
    tagged_path = msh_path.with_suffix(".surface.tagged.npz")

    if lr is None:
        env_lr = os.environ.get("FTETWILD_LR", "")
        try:
            lr = float(env_lr) if env_lr else None
        except ValueError:
            lr = None
    if opt_iter is None:
        env_iter = os.environ.get("FTETWILD_OPT_ITER", "")
        try:
            opt_iter = int(env_iter) if env_iter else None
        except ValueError:
            opt_iter = None
    if opt_iter is None:
        opt_iter = DEFAULT_OPT_ITER

    t0 = time.perf_counter()
    if step_path.suffix.lower() == ".stl":
        # STL inputs skip GMSH entirely: its sizing probe clears the model
        # and an STL-imported (discretized) surface cannot be re-meshed
        # afterwards — generate(2) yields nothing and gmsh.write produces
        # an EMPTY file (verified directly). The input already IS a
        # surface mesh, so feed it to fTetWild as-is and recover patch
        # names from its solids (binary STLs carry none -> one patch).
        stl_source = step_path
        src_tris, raw_names = load_surface_solids(step_path)
    else:
        from polyfoammesh.core.gmsh_wrapper import (
            export_tagged_surface_npz,
            generate_surface_stl,
        )

        generate_surface_stl(step_path, stl_path, detail=detail)
        export_tagged_surface_npz(tagged_path)
        tagged = np.load(tagged_path, allow_pickle=True)
        src_tris = tagged["tris"]
        raw_names = list(tagged["names"])
        stl_source = stl_path
    surface_time_s = time.perf_counter() - t0

    # Sizing diagnostics BEFORE the run: fTetWild can legitimately take
    # minutes on complex parts (its optimizer has no wall-clock bound) and
    # the subprocess is otherwise silent — seeing the target edge vs the
    # input surface density up front is what tells the user whether the
    # numbers are in a sane regime (measured: a target edge 20x larger
    # than the surface features sends the optimizer into an unbounded
    # edge-splitting runaway — one real part did 884k splits in pass 0).
    lo = src_tris.reshape(-1, 3).min(axis=0)
    hi = src_tris.reshape(-1, 3).max(axis=0)
    dims = hi - lo
    bbox_diag = float(np.linalg.norm(dims))
    # Cross-section reference for the default target: middle bbox dimension
    # (same choice the app's adaptive sizing makes for its own resolution
    # model). An elongated part's diagonal would be dominated by its
    # LENGTH; the cross-section is what decides cells across the passage.
    cross_scale = float(np.median(dims))
    med_edge = _median_edge_length(src_tris)
    la_used = max_cell if max_cell and max_cell > 0 else None
    if la_used is None and lr is None:
        la_used = _default_target_edge(cross_scale, detail)
    target_edge = la_used if la_used else (lr or DEFAULT_LR) * bbox_diag
    print(
        f"experimental tet stage: bbox_diag={bbox_diag:.4g} "
        f"cross_section={cross_scale:.4g} "
        f"target_edge={target_edge:.4g} input_median_edge={med_edge:.4g} "
        f"({len(src_tris):,} input triangles, {len(set(raw_names))} "
        "patches) - long runs possible on complex parts; Cancel stays available",
        flush=True,
    )
    # Over-dense input -> decimate BEFORE fTetWild: its own simplify only
    # removes detail within the eps envelope, it never decimates toward the
    # target edge (research: fTetWild#93 / pytetwild#47; measured 386k-
    # triangle input vs a 12.75 mm target blew the stage up). This is the
    # density-alignment direction that works: input -> target.
    reduction = _decimation_reduction(med_edge, target_edge)
    if reduction is not None:
        print(
            f"input surface is {target_edge / med_edge:.1f}x finer than the "
            f"target edge - decimating it to ~{reduction:.1%} of its "
            f"{len(src_tris):,} triangles before fTetWild "
            "(it does not decimate toward the target itself).",
            flush=True,
        )
    print(
        "starting fTetWild tetrahedralization now - the .stl above is only "
        "the SURFACE (its input); fTetWild runs without output by design, so "
        "the GUI 'still running (... CPU%)' heartbeat is the liveness signal "
        "until the result appears.",
        flush=True,
    )

    # fTetWild drops a __tracked_surface.stl debug artifact into the
    # CURRENT WORKING DIRECTORY (verified: it landed in the repo root/src
    # during testing) — run it from the case dir so the junk stays with
    # the disposable run output instead of polluting the source tree.
    prev_cwd = os.getcwd()
    os.chdir(msh_path.parent)
    try:
        V, T, tet_stats = tetrahedralize(
            stl_source, lr=lr, la=la_used,
            opt_iter=opt_iter,
            decimate=reduction,
        )
    finally:
        os.chdir(prev_cwd)

    # fTetWild may emit a non-manifold boundary where two sheets touch
    # (its --manifold-surface class; pytetwild exposes no such flag) —
    # the barycentric dual rejects it outright ("not a closed
    # tetrahedral volume") or produces combinatorially open cells.
    # Repair before tagging/writing.
    V, T, n_splits = split_nonmanifold_vertices(V, T)
    if n_splits:
        _nm, _bow = boundary_defects(V, T)
        _residual = (
            f"residual: {_nm} non-manifold edges, {_bow} bowtie vertices"
            if (_nm or _bow)
            else "boundary now fully manifold (every vertex star is a ball)"
        )
        print(
            f"boundary pinch repaired: {n_splits} vertex corner(s) "
            f"duplicated at non-manifold contact (fTetWild --manifold-surface "
            f"equivalent, micro-offset); {_residual}.",
            flush=True,
        )
    # Outlier micro-clusters (measured: a 1-cell region in the tet mesh at
    # a contact) become floating islands in the dual and "2 regions" in
    # checkMesh — drop them while they are still tets.
    V, T, n_dropped = drop_small_components(V, T)
    if n_dropped:
        print(
            f"dropped {n_dropped} outlier tet(s) in disconnected micro-"
            "component(s) (contact artifact; main mesh untouched).",
            flush=True,
        )

    # One entry per PATCH: sanitize operates on the unique class list so
    # $PhysicalNames stays one-name-per-id even though raw_names repeats
    # per triangle. names[i] is classes[i]'s sanitized twin (order
    # preserved), keeping element tags aligned with $PhysicalNames even
    # when two raw names sanitize onto one word (dedupe appends _2, ...).
    classes = _unique_keep_order(raw_names)
    names = sanitize_patch_names(classes)
    cls_idx = {nm: i for i, nm in enumerate(classes)}
    src_codes = np.array([cls_idx[nm] for nm in raw_names])
    class2phys = {cls: i + 1 for i, cls in enumerate(classes)}

    faces, owner = boundary_faces(V, T)
    faces = orient_faces_outward(V, T, faces, owner)
    codes, assign_stats = assign_patches(V, faces, src_tris, src_codes)
    face_phys = np.array([class2phys[classes[c]] for c in codes], dtype=np.int64)

    write_msh22(msh_path, V, T, faces, face_phys, names)

    total_s = time.perf_counter() - t_start
    return {
        "success": True,
        "path": str(msh_path),
        "names": names,
        "surface_time_s": round(surface_time_s, 3),
        "total_time_s": round(total_s, 3),
        "n_nodes": tet_stats["n_nodes"],
        "n_tets": tet_stats["n_tets"],
        "tet_time_s": tet_stats["tet_time_s"],
        "lr": lr,
        "la": la_used,
        "opt_iter": opt_iter,
        "target_edge_m": target_edge,
        "input_median_edge_m": med_edge,
        "bbox_diag_m": bbox_diag,
        "decimate_fraction": reduction,
        "n_input_tris": tet_stats.get("n_input_tris"),
        "n_decimated_tris": tet_stats.get("n_decimated_tris"),
        "n_manifold_splits": n_splits,
        "tagging": assign_stats,
    }


def _unique_keep_order(items) -> list:
    seen: set = set()
    out = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _cli_main(argv) -> None:
    """CLI entry point (same contract as gmsh_wrapper._cli_main)."""
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "volume":
        flags: dict[str, str] = {}
        for a in argv[2:]:
            if a.startswith("--") and "=" in a:
                k, v = a[2:].split("=", 1)
                flags[k] = v
        try:
            result = run_volume(
                step_path=flags.get("step", ""),
                msh_path=flags.get("msh", ""),
                detail=flags.get("detail", "medium"),
                max_cell=float(flags.get("max-cell", 0) or 0),
                lr=float(flags["lr"]) if flags.get("lr") else None,
                opt_iter=int(flags["opt-iter"]) if flags.get("opt-iter") else None,
            )
        except Exception as exc:  # noqa: BLE001 - CLI reports, never raises
            print(json.dumps({"success": False, "error": str(exc)}))
            sys.exit(1)
        print(json.dumps(result))
        return
    print(
        json.dumps(
            {
                "success": False,
                "error": f"unknown subcommand {cmd!r} (expected: volume)",
            }
        )
    )
    sys.exit(1)


if __name__ == "__main__":
    _cli_main(sys.argv)
