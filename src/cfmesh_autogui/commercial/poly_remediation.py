"""In-process quality remediation for the tet→poly dual mesh path.

Why this exists
---------------
The dual-poly path (``DualPolyWorker`` / ``TetPolyDualConverter``) cannot be
re-meshed by cfMesh: ``mesh_remeshable`` refuses when
``constant/polyMesh_tet_backup`` exists, so ``QualityEngine.auto_fix`` and
``MeshOptimizer.optimize`` — both of which drive their fix loop through a
cfMesh cartesianMesh re-run — analyse a defective poly mesh and then do
nothing. This module closes that gap with a strictly non-regressive,
in-process remediation loop:

- every candidate action is measured with the in-process checkMesh replica
  (``tet_poly_dual._detect_defects``, verified 895/895 against real checkMesh
  on the reference valve) BEFORE and AFTER;
- an action is accepted only when the total defect count strictly decreases;
  otherwise the mesh is left exactly as it was;
- the loop is bounded by an iteration cap and a wall-time cap;
- the documented unfixable defect class — concave-feature cells whose
  centroid falls outside one of their own boundary quads
  (docs/handoff_poly_bl_deepseek.md:81-100) — is detected and skipped, never
  "fixed" (every measured attempt was monotonically worse).

Strategies, cheapest and safest first (exactly one action per iteration):
  1. smooth — keep-best Laplacian smoothing of interior dual vertices with
     the boundary pinned (``core/poly_smoother.smooth_dual_mesh``).
  2. local refinement — DROPPED: ``local_refinement_boxes_from_checkmesh_sets``
     produces cfMesh ``objectRefinements`` boxes, which only make sense as
     input to a cfMesh re-mesh; the poly path has no re-mesh, so the boxes
     cannot be applied here. Not faked.
  3. degrade to tet — reported as an opt-in option when the poly mesh cannot
     be repaired and ``constant/polyMesh_tet_backup`` exists; never applied
     automatically.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PolyRemediationAction:
    """A single remediation action and its keep-best verdict."""

    strategy: str  # "smooth" | "degrade_to_tet"
    accepted: bool
    detail: str
    defects_before: int
    defects_after: int


@dataclass
class PolyRemediationResult:
    """Outcome of a poly-mesh remediation run."""

    iterations: int = 0
    actions: list[PolyRemediationAction] = field(default_factory=list)
    defects_before: int = 0
    defects_after: int = 0
    improved: bool = False
    unchanged: bool = False
    concave_defects_skipped: bool = False
    n_concave_defect_cells: int = 0
    tet_fallback_available: bool = False
    degraded_to_tet: bool = False
    message: str = ""


def _total_defects(counts: dict) -> int:
    return int(counts["pyramid"] + counts["non_ortho"] + counts["skew"])


def _read_mesh(case_dir: Path):
    """Read the polyMesh; returns (points, faces, owner, neighbour, patches,
    n_int, n_cells)."""
    from cfmesh_autogui.core import foam_mesh_io

    poly = case_dir / "constant" / "polyMesh"
    points, faces, owner, neighbour, patches = foam_mesh_io.read_polymesh(poly)
    n_int = len(neighbour)
    n_cells = int(max(owner.max(), neighbour.max())) + 1 if len(owner) else 0
    return points, faces, owner, neighbour, patches, n_int, n_cells


def _measure(points, faces, owner, neigh, n_int, n_cells):
    """In-process checkMesh replica: (bad_cell_mask, counts, volumes)."""
    from cfmesh_autogui.core.tet_poly_dual import (
        _cell_centres,
        _detect_defects,
        _face_geometry,
    )

    sf, cf = _face_geometry(points, faces)
    ctr, vol = _cell_centres(sf, cf, owner, neigh, n_int, n_cells)
    bad, counts = _detect_defects(
        points, faces, sf, cf, ctr, owner, neigh, n_int, n_cells,
    )
    return bad, counts, vol


def _concave_feature_cells(points, faces, owner, neigh, n_int, n_cells) -> np.ndarray:
    """Cells failing the face-pyramid test on a BOUNDARY face.

    The documented concave-feature defect (docs/handoff_poly_bl_deepseek.md
    :81-100): the dual cell wraps a concave CAD corner, its centroid falls
    outside one of its own boundary quads, and no vertex movement can fix it
    (the boundary is pinned and the cell is genuinely non-convex).
    """
    from cfmesh_autogui.core.tet_poly_dual import _cell_centres, _face_geometry

    sf, cf = _face_geometry(points, faces)
    ctr, _vol = _cell_centres(sf, cf, owner, neigh, n_int, n_cells)
    pv_own = (sf * (cf - ctr[owner])).sum(axis=1)
    m_own = pv_own <= 0.0
    bad = np.zeros(n_cells, dtype=bool)
    if n_int < len(faces):
        bad[owner[n_int:][m_own[n_int:]]] = True
    return bad


def _smooth(points, faces, owner, neigh, n_int, n_cells, log):
    """One keep-best smoothing pass on interior dual vertices (boundary pinned)."""
    from cfmesh_autogui.core import poly_smoother
    from cfmesh_autogui.core.tet_poly_dual import (
        _cell_centres,
        _detect_defects,
        _face_geometry,
    )

    return poly_smoother.smooth_dual_mesh(
        points, faces, owner, neigh, n_int, n_cells,
        _detect_defects, _face_geometry, _cell_centres,
        iterations=2,
        relaxation=0.5,
        log=log,
    )


def _write_mesh(case_dir: Path, points, faces, owner, neigh, patches) -> None:
    from cfmesh_autogui.core import foam_mesh_io

    foam_mesh_io.write_polymesh(
        case_dir / "constant" / "polyMesh",
        points, faces, owner, neigh, patches,
    )


def remediate_poly_mesh(
    case_dir: Path | str,
    max_iterations: int = 3,
    max_wall_time_s: float = 120.0,
    log=None,
) -> PolyRemediationResult:
    """Strictly non-regressive quality remediation for a tet→poly dual mesh.

    Reads the poly mesh at ``constant/polyMesh``, runs at most
    ``max_iterations`` keep-best actions (one per iteration, cheapest first),
    and writes the improved mesh back only when the total defect count
    strictly decreased. On any failure or plateau the original mesh is left
    byte-identical.

    Args:
        case_dir: OpenFOAM case directory with an existing polyMesh.
        max_iterations: Hard cap on remediation iterations (default 3).
        max_wall_time_s: Hard cap on total wall time (default 120 s).
        log: Optional ``callable(str)`` progress sink.

    Returns:
        ``PolyRemediationResult`` with the before/after defect counts, the
        per-action accept/reject verdicts, and whether the mesh was improved
        or left unchanged.
    """
    case_dir = Path(case_dir)
    result = PolyRemediationResult()
    start = time.monotonic()

    try:
        points, faces, owner, neigh, patches, n_int, n_cells = _read_mesh(case_dir)
    except Exception as exc:  # noqa: BLE001 - never destroy a mesh we can't read
        result.message = f"cannot read polyMesh: {exc}"
        result.unchanged = True
        return result

    if n_cells == 0:
        result.message = "mesh has no cells — nothing to remediate"
        result.unchanged = True
        return result

    bad, counts, _vol = _measure(points, faces, owner, neigh, n_int, n_cells)
    defects_before = _total_defects(counts)
    result.defects_before = defects_before

    if defects_before == 0:
        result.defects_after = 0
        result.unchanged = True
        result.message = "mesh has no defects — nothing to remediate"
        return result

    concave = _concave_feature_cells(points, faces, owner, neigh, n_int, n_cells)
    n_concave = int(concave.sum())
    result.n_concave_defect_cells = n_concave

    # All defects are concave-feature: documented unfixable, skip entirely.
    if n_concave > 0 and not (bad & ~concave).any():
        result.concave_defects_skipped = True
        result.defects_after = defects_before
        result.unchanged = True
        result.message = (
            f"all {defects_before} defect(s) are concave-feature cells "
            f"({n_concave} cell(s)) — documented unfixable, skipped"
        )
        return result

    result.tet_fallback_available = (
        case_dir / "constant" / "polyMesh_tet_backup"
    ).is_dir()

    cur_points = points
    cur_defects = defects_before
    best_points = points
    best_defects = defects_before
    iterations = 0

    for it in range(1, max_iterations + 1):
        if time.monotonic() - start > max_wall_time_s:
            result.message = "wall-time cap reached"
            break
        iterations = it

        cand_points, cand_counts = _smooth(
            cur_points, faces, owner, neigh, n_int, n_cells, log,
        )
        cand_defects = _total_defects(cand_counts)
        accepted = cand_defects < cur_defects
        result.actions.append(PolyRemediationAction(
            strategy="smooth",
            accepted=accepted,
            detail=f"smoothing pass {it} (interior dual vertices, boundary pinned)",
            defects_before=cur_defects,
            defects_after=cand_defects,
        ))
        if not accepted:
            # Plateau: no strictly-improving action — stop. The candidate is
            # never worse (keep-best), but the strict rule requires a
            # measurable gain to accept it.
            break
        cur_points = cand_points
        cur_defects = cand_defects
        if cur_defects < best_defects:
            best_points = cur_points
            best_defects = cur_defects
        if cur_defects == 0:
            break

    result.iterations = iterations
    result.defects_after = best_defects
    result.improved = best_defects < defects_before
    result.unchanged = not result.improved

    if result.improved:
        try:
            _write_mesh(case_dir, best_points, faces, owner, neigh, patches)
        except Exception as exc:  # noqa: BLE001 - atomic write keeps the original
            result.message = f"write failed — original mesh left intact: {exc}"
            result.improved = False
            result.unchanged = True
            result.defects_after = defects_before
            return result
        result.message = (
            f"poly remediation: defects {defects_before} -> {best_defects} "
            f"over {iterations} iteration(s)"
        )
    else:
        result.message = (
            f"poly remediation: no strictly-improving action found "
            f"(defects {defects_before} unchanged)"
        )

    # Last-resort degrade: reported as an opt-in option, never automatic.
    if result.tet_fallback_available and not result.improved:
        result.actions.append(PolyRemediationAction(
            strategy="degrade_to_tet",
            accepted=False,
            detail="tet backup at constant/polyMesh_tet_backup — opt-in "
                   "restore (never automatic)",
            defects_before=defects_before,
            defects_after=defects_before,
        ))

    return result


def restore_tet_mesh(case_dir: Path | str) -> bool:
    """Opt-in last-resort degrade: replace the poly mesh with the backed-up
    tet mesh (``constant/polyMesh_tet_backup``).

    Never called automatically — the caller must explicitly opt in. Returns
    True on success, False when no backup exists.
    """
    case_dir = Path(case_dir)
    backup = case_dir / "constant" / "polyMesh_tet_backup"
    poly = case_dir / "constant" / "polyMesh"
    if not backup.is_dir():
        return False
    import shutil

    if poly.exists():
        shutil.rmtree(poly)
    shutil.copytree(backup, poly)
    return True