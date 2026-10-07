"""Boundary-layer cell classification for viewer highlighting.

The viewer colours a loaded case by patch, so a boundary layer of a few
tenths of a millimetre on a centimetre-scale part is invisible.  This
module identifies which polyMesh *cells* belong to the prismatic boundary
layer so the viewer can paint them a warm colour over the grey core.

The classification is purely topological and name-agnostic:

1. ``read_polymesh`` loads points/faces/owner/neighbour/boundary.
2. ``infer_patch_roles`` decides which patches are walls (required because
   GMSH names every patch ``surface_N``); semantic names still win.
3. Seeds are the cells that own a boundary face on a wall patch — the
   first prism layer owns the wall base face.
4. A cell is *prism-like* when it has exactly two n-gon faces (n >= 3,
   the same vertex count on both) and every other face is a quad — the
   shape a boundary layer builds (m-gon base + m side quads + m-gon top),
   with m=3 (a wedge) tolerated for triangle-based walls.
5. A bounded flood fills the prism label from the seeds across internal
   faces to prism-like neighbours, so *every* layer of a stack is
   labelled, not just the first.

The function never raises: on any failure it logs and returns an empty
mask with a zero count.  No dependencies beyond numpy.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def _prism_like_mask(
    face_sizes: np.ndarray,
    owner: np.ndarray,
    neighbour: np.ndarray,
    n_cells: int,
) -> np.ndarray:
    """Boolean mask of cells shaped like a boundary-layer prism.

    Vectorised: for every cell, count its non-quad faces (size != 4) and
    their total pixel-sum; a cell with exactly two non-quad faces of the
    same size m >= 3 is a prism.  Equality of the two sizes is tested via
    ``sum**2 == 2 * sum_of_squares``, which holds iff the two sizes are
    equal, so no per-cell Python loop is needed.
    """
    owner64 = owner.astype(np.int64)
    neigh64 = neighbour.astype(np.int64)
    n_internal = int(neigh64.size)

    nonquad = face_sizes != 4
    nq_sizes = face_sizes[nonquad].astype(np.float64)

    nq_count = np.bincount(owner64[nonquad], minlength=n_cells)
    nq_sum = np.bincount(owner64[nonquad], weights=nq_sizes, minlength=n_cells)
    nq_sumsq = np.bincount(
        owner64[nonquad], weights=nq_sizes * nq_sizes, minlength=n_cells
    )
    if n_internal:
        nonquad_i = nonquad[:n_internal]
        nq_sizes_i = face_sizes[:n_internal][nonquad_i].astype(np.float64)
        nq_count = nq_count + np.bincount(neigh64[nonquad_i], minlength=n_cells)
        nq_sum = nq_sum + np.bincount(
            neigh64[nonquad_i], weights=nq_sizes_i, minlength=n_cells
        )
        nq_sumsq = nq_sumsq + np.bincount(
            neigh64[nonquad_i], weights=nq_sizes_i * nq_sizes_i, minlength=n_cells
        )

    equal_pair = np.abs(nq_sum * nq_sum - 2.0 * nq_sumsq) < 1e-9
    return (nq_count == 2) & equal_pair & (nq_sum >= 6.0)


def _wall_patches(case_dir: Path, patches: list[dict]) -> list[dict]:
    """The subset of *patches* whose inferred role is "wall".

    Roles come from ``case_setup.infer_patch_roles`` (geometric when the
    names are raw ``surface_N``, semantic when they are not).  If inference
    fails for any reason the name-based classifier is used as a fallback so
    a real wall called ``wall`` is still found.
    """
    from polyfoammesh.core.boundary_reader import PatchInfo
    from polyfoammesh.core.case_setup import infer_patch_roles

    patch_infos = [
        PatchInfo(
            name=str(p.get("name", "")),
            patch_type=str(p.get("type", "")),
            n_faces=int(p.get("nFaces", 0)),
            start_face=int(p.get("startFace", 0)),
        )
        for p in patches
    ]
    try:
        roles = infer_patch_roles(case_dir, patch_infos)
    except Exception:
        logger.warning(
            "infer_patch_roles failed for %s; falling back to name-based "
            "wall detection", case_dir, exc_info=True,
        )
        from polyfoammesh.core.patch_roles import is_wall_patch

        roles = {
            pi.name: ("wall" if is_wall_patch(pi.name) else "other")
            for pi in patch_infos
        }
    wall_names = {name for name, role in roles.items() if role == "wall"}
    return [p for p in patches if str(p.get("name", "")) in wall_names]


def classify_bl_cells(case_dir: Path | str) -> tuple[np.ndarray, int]:
    """Boolean mask over polyMesh cells: True for boundary-layer prisms.

    Returns ``(mask, count)``.  The mask has length == number of cells and
    is empty (length 0, count 0) when the case cannot be read or contains
    no detectable prism cells.  Never raises.
    """
    try:
        from polyfoammesh.core.foam_mesh_io import read_polymesh

        case_dir = Path(case_dir)
        poly_dir = case_dir / "constant" / "polyMesh"
        _points, faces, owner, neighbour, patches = read_polymesh(poly_dir)

        if len(owner) == 0 or len(faces) == 0:
            logger.info("classify_bl_cells: %s has no faces/cells", poly_dir)
            return np.zeros(0, dtype=bool), 0

        owner = np.asarray(owner, dtype=np.int64).ravel()
        neighbour = np.asarray(neighbour, dtype=np.int64).ravel()
        n_faces = min(len(owner), len(faces))
        n_internal = min(int(neighbour.size), n_faces)

        max_cell = int(owner.max())
        if n_internal:
            max_cell = max(max_cell, int(neighbour[:n_internal].max()))
        n_cells = max_cell + 1

        face_sizes = np.fromiter(
            (len(f) for f in faces[:n_faces]), dtype=np.int64, count=n_faces,
        )

        prism_like = _prism_like_mask(face_sizes, owner, neighbour[:n_internal], n_cells)

        wall_patches = _wall_patches(case_dir, patches)
        seed = np.zeros(n_cells, dtype=bool)
        for p in wall_patches:
            start = int(p.get("startFace", 0))
            nf = int(p.get("nFaces", 0))
            if nf <= 0:
                continue
            # Boundary faces live after the internal ones.
            s = max(start, n_internal)
            e = min(start + nf, n_faces)
            if e <= s:
                continue
            seed[owner[s:e]] = True

        labels = seed & prism_like
        if labels.any() and n_internal:
            labels = _flood(labels, prism_like, owner, neighbour[:n_internal], n_cells)

        count = int(labels.sum())
        return labels.astype(bool), count
    except Exception:
        logger.exception("classify_bl_cells failed for %s", case_dir)
        return np.zeros(0, dtype=bool), 0


def _flood(
    labels: np.ndarray,
    prism_like: np.ndarray,
    owner: np.ndarray,
    neighbour: np.ndarray,
    n_cells: int,
) -> np.ndarray:
    """Propagate *labels* across internal faces to prism-like neighbours.

    Iterates until stable (or a bounded pass count), so a stack of prism
    layers is labelled in full rather than only the wall-adjacent one.
    """
    owner_i = owner[:neighbour.size]
    neigh_i = neighbour
    max_passes = min(max(n_cells, 1), 2048)
    for _ in range(max_passes):
        add_neigh = labels[owner_i] & (~labels[neigh_i]) & prism_like[neigh_i]
        add_owner = labels[neigh_i] & (~labels[owner_i]) & prism_like[owner_i]
        if not add_neigh.any() and not add_owner.any():
            break
        if add_neigh.any():
            labels[neigh_i[add_neigh]] = True
        if add_owner.any():
            labels[owner_i[add_owner]] = True
    return labels
