"""Automatic repair for non-watertight assembled geometry.

The most common real cause of "N open boundary edges" on a geometry that
looks fine visually is NOT a real hole in the CAD model — it's that each
patch (inlet/outlet/wall/...) was tessellated independently, so vertices
that are meant to sit exactly on a shared boundary end up a tiny floating-
point distance apart (e.g. 1e-9 m instead of exactly equal). trimesh's own
watertight check treats those as separate vertices, so the shared edge
looks "open" on both sides even though the geometry is, physically, closed.

This module offers two escalating repair strategies:

  1. snap_close_gaps() — round every vertex to a coarse-enough number of
     decimal digits (relative to the model's own size) that near-coincident
     vertices from different patches become bit-identical and merge. This
     preserves each patch's identity/name exactly, so it's always tried
     first and is safe to run automatically without asking the user.

  2. repair_with_meshfix() — a much stronger fallback for genuinely broken
     geometry (real missing faces, not just a tessellation mismatch), using
     the open-source PyMeshFix library (MIT-licensed Python wrapper around
     the MeshFix algorithm, the standard tool for this exact problem in the
     3D-printing/medical-imaging world: https://github.com/pyvista/pymeshfix).
     This operates on the combined surface and can add/move faces near the
     defect, so per-patch identity is only approximately preserved (each
     resulting face is re-attributed to whichever original patch had the
     nearest face centroid) — good enough to keep boundary-condition
     assignment usable, but flagged in the report so the user knows it
     happened.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

logger = logging.getLogger(__name__)


@dataclass
class RepairReport:
    attempted: bool = False
    watertight_before: bool = False
    watertight_after: bool = False
    open_edges_before: int = 0
    open_edges_after: int = 0
    method: str = ""  # "snap", "meshfix", or "" if untried/unnecessary
    snap_tolerance: float = 0.0
    operations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _count_open_edges(combined: trimesh.Trimesh) -> int:
    try:
        import trimesh.grouping as _grouping
        return int(len(combined.edges[
            _grouping.group_rows(combined.edges_sorted, require_count=1)
        ]))
    except Exception:
        return -1


_COMBINED_CACHE: dict[int, tuple[trimesh.Trimesh, int]] = {}


def _combined(meshes: list[trimesh.Trimesh]) -> trimesh.Trimesh:
    cache_key = id(meshes)
    face_count = sum(len(m.faces) for m in meshes)
    cached = _COMBINED_CACHE.get(cache_key)
    if cached is not None and cached[1] == face_count:
        return cached[0]
    combined = trimesh.util.concatenate(meshes)
    combined.merge_vertices()
    if len(_COMBINED_CACHE) > 5:
        _COMBINED_CACHE.clear()
    _COMBINED_CACHE[cache_key] = (combined, face_count)
    return combined


def snap_close_gaps(
    meshes: list[trimesh.Trimesh], bbox_dim: float,
) -> tuple[list[trimesh.Trimesh], RepairReport]:
    """Round vertices to close CAD-tessellation gaps between patches.

    *bbox_dim* is the model's own largest dimension (metres) — the snap
    tolerance is derived as a fraction of it (~1e-4) rather than a fixed
    absolute value, so it scales sensibly whether the model is a 10 mm
    fitting or a 10 m duct, and stays well below the size of any real
    feature (fillets, small holes) that should NOT be merged away.
    """
    report = RepairReport(attempted=True)
    if not meshes or bbox_dim <= 0:
        return meshes, report

    before = _combined(meshes)
    report.watertight_before = before.is_watertight
    report.open_edges_before = 0 if report.watertight_before else _count_open_edges(before)

    if report.watertight_before:
        report.watertight_after = True
        return meshes, report

    tolerance = bbox_dim * 1e-4
    digits = max(0, int(round(-np.log10(tolerance))))
    report.snap_tolerance = tolerance
    report.method = "snap"

    repaired = []
    for mesh in meshes:
        m = mesh.copy()
        try:
            m.merge_vertices(digits_vertex=digits)
        except Exception as exc:
            report.warnings.append(f"Snap failed on '{m.metadata.get('name', '?')}': {exc}")
        repaired.append(m)

    after = _combined(repaired)
    report.watertight_after = after.is_watertight
    report.open_edges_after = 0 if report.watertight_after else _count_open_edges(after)

    if report.watertight_after:
        report.operations.append(
            f"snapped vertices to {digits} decimal digits "
            f"(tolerance {tolerance:.2e} m) — geometry is now watertight"
        )
        return repaired, report

    report.operations.append(
        f"snapped vertices to {digits} decimal digits "
        f"(tolerance {tolerance:.2e} m) — "
        f"{report.open_edges_before} → {report.open_edges_after} open edges"
    )
    return repaired, report


def repair_with_meshfix(
    meshes: list[trimesh.Trimesh],
) -> tuple[list[trimesh.Trimesh], RepairReport]:
    """Stronger fallback: run PyMeshFix on the combined surface.

    Re-attributes each resulting face to whichever original patch had the
    nearest face centroid, so boundary-condition names survive — but since
    MeshFix can add/move faces near the actual defect, this is an
    approximation, not an exact per-patch split. Flagged in the report.
    """
    report = RepairReport(attempted=True, method="meshfix")

    try:
        from pymeshfix import MeshFix
    except ImportError:
        report.warnings.append("pymeshfix not installed — cannot attempt this repair.")
        return meshes, report

    if not meshes:
        return meshes, report

    before = _combined(meshes)
    report.watertight_before = before.is_watertight
    report.open_edges_before = 0 if report.watertight_before else _count_open_edges(before)
    if report.watertight_before:
        return meshes, report

    # Track which original patch each input face came from, and each
    # patch's face centroids, so repaired output faces can be reassigned.
    patch_names = [m.metadata.get("name", f"patch_{i}") for i, m in enumerate(meshes)]
    patch_centroids = [m.triangles_center for m in meshes]

    try:
        mf = MeshFix(before.vertices.copy(), before.faces.copy())
        mf.repair()
        fixed_verts, fixed_faces = mf.points, mf.faces
    except Exception as exc:
        report.warnings.append(f"MeshFix repair failed: {exc}")
        return meshes, report

    if len(fixed_faces) == 0:
        report.warnings.append("MeshFix returned an empty mesh — repair not usable.")
        return meshes, report

    fixed = trimesh.Trimesh(vertices=fixed_verts, faces=fixed_faces, process=False)
    fixed_centroids = fixed.triangles_center

    # Nearest-original-patch assignment per output face.
    all_centroids = np.concatenate(patch_centroids, axis=0)
    owner = np.concatenate([
        np.full(len(c), i) for i, c in enumerate(patch_centroids)
    ])
    try:
        from scipy.spatial import cKDTree
        tree = cKDTree(all_centroids)
        _, nearest_idx = tree.query(fixed_centroids)
    except ImportError:
        # No scipy: brute-force nearest neighbour (fine for typical
        # part-count meshes; this path only runs when the fast one is
        # unavailable).
        nearest_idx = np.array([
            int(np.argmin(np.linalg.norm(all_centroids - c, axis=1)))
            for c in fixed_centroids
        ])
    face_owner = owner[nearest_idx]

    repaired_meshes = []
    for i, name in enumerate(patch_names):
        face_mask = face_owner == i
        if not face_mask.any():
            report.warnings.append(f"Patch '{name}' lost all faces after repair.")
            continue
        sub = fixed.submesh([np.where(face_mask)[0]], append=True)
        sub.metadata["name"] = name
        repaired_meshes.append(sub)

    after = _combined(repaired_meshes)
    report.watertight_after = after.is_watertight
    report.open_edges_after = 0 if report.watertight_after else _count_open_edges(after)
    report.operations.append(
        "ran PyMeshFix on the combined surface and reassigned faces back to "
        f"their nearest original patch — {report.open_edges_before} → "
        f"{report.open_edges_after} open edges"
    )
    if report.watertight_after:
        report.warnings.append(
            "Geometry is now watertight, but patch boundaries near the "
            "repaired area are approximate — check patch assignment there."
        )
    return (repaired_meshes if repaired_meshes else meshes), report


def attempt_auto_repair(
    meshes: list[trimesh.Trimesh], bbox_dim: float,
) -> tuple[list[trimesh.Trimesh], list[RepairReport]]:
    """Run the escalating repair pipeline: snap first, MeshFix if needed.

    Returns the (possibly repaired) mesh list and the report(s) produced,
    in the order they were attempted, so the caller can log what actually
    happened rather than a single opaque pass/fail.
    """
    reports = []
    snapped, snap_report = snap_close_gaps(meshes, bbox_dim)
    reports.append(snap_report)
    if snap_report.watertight_after or not snap_report.attempted:
        return snapped, reports

    fixed, mf_report = repair_with_meshfix(snapped)
    reports.append(mf_report)
    return fixed, reports


def _main(argv: list[str]) -> int:
    """CLI entry point for --watertight mode (frozen exe or dev)."""
    import sys, json
    cmd = argv[0] if len(argv) > 0 else ""
    if cmd != "check":
        print(json.dumps({"success": False, "error": f"Unknown command: {cmd}"}))
        return 1
    return _run_watertight_check(argv[1:])


def _run_watertight_check(stl_paths: list[str]) -> int:
    import sys, json, trimesh, trimesh.grouping as _grouping
    from cfmesh_autogui.core.geometry import compute_bbox_dim
    meshes = [trimesh.load(sp) for sp in stl_paths]
    try:
        combined = trimesh.util.concatenate(meshes)
        combined.merge_vertices()
        if combined.is_watertight:
            print(json.dumps({
                "success": True, "watertight": True, "n_open_edges": 0,
                "reports": [],
            }))
            return 0
        boundary_edges = combined.edges[
            _grouping.group_rows(combined.edges_sorted, require_count=1)
        ]
        n_open = len(boundary_edges)
        bbox_dim = compute_bbox_dim(meshes)
        repaired, reports_list = attempt_auto_repair(meshes, bbox_dim)
        reports = [{"method": r.method, "operations": r.operations,
                    "warnings": r.warnings,
                    "watertight_after": r.watertight_after}
                   for r in reports_list]
        watertight = reports_list[-1].watertight_after if reports_list else False
        repaired_paths = []
        for i, m in enumerate(repaired):
            p = Path(stl_paths[0]).parent / f"repaired_{i}.stl"
            m.export(str(p))
            repaired_paths.append(str(p))
        print(json.dumps({
            "success": True, "watertight": watertight,
            "n_open_edges": n_open, "reports": reports,
            "repaired_paths": repaired_paths,
        }))
        return 0
    except Exception as e:
        import traceback; traceback.print_exc()
        print(json.dumps({"success": False, "error": str(e)}))
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
