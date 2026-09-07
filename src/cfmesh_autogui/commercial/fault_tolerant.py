"""Fault-tolerant (non-watertight) meshing workflow.

Inspired by ANSYS Fluent Meshing Fault-Tolerant Workflow.
Handles CAD with gaps, overlaps, missing faces, and small features
that prevent traditional watertight meshing.

Pipeline:
  1. Import geometry (even non-watertight STL with gaps/overlaps)
  2. Gap detection & analysis
  3. Gap closing (snap-merge / MeshFix repair)
  4. meshDict preparation (sizing + boundary layers)
  5. Volume fill (cartesianMesh, BL -> no-BL fallback)
  6. Quality check (checkMesh) — success requires a valid volume mesh
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.validation import validate_cell_size, validate_geometry_path
from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)

# Serial cartesianMesh wall-clock timeout for the volume-fill step. The
# project uses 14400 s for serial cartesianMesh elsewhere; this path is
# bounded to 3600 s so a hung WSL process cannot stall a batch run forever.
_VOLUME_FILL_TIMEOUT_S = 3600


@dataclass
class GapInfo:
    """Information about a detected gap in the geometry."""
    location: tuple[float, float, float]
    gap_size: float
    patch_name: str = ""


@dataclass
class FaultTolerantParams:
    """Parameters controlling the fault-tolerant workflow."""
    gap_h: float = 0.001           # Gap height threshold (m)
    wrap_coarsen: float = 0.5      # Coarsening factor for shrink-wrap
    occlude_internal: bool = True  # Fill internal cavities
    min_feature_size: float = 0.0005  # Minimum feature to preserve (m)
    merge_tolerance: float = 0.0001  # STL vertex merge tolerance (m)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_h": self.gap_h,
            "wrap_coarsen": self.wrap_coarsen,
            "occlude_internal": self.occlude_internal,
            "min_feature_size": self.min_feature_size,
            "merge_tolerance": self.merge_tolerance,
        }


@dataclass
class FaultTolerantResult:
    success: bool = False
    case_dir: str = ""
    gaps_found: int = 0
    min_gap: float = 0.0
    cells: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    wall_time_seconds: float = 0.0
    # New fields (defaults only — backwards compatible). `stage` tracks the
    # furthest pipeline stage reached so callers can report partial progress.
    stage: str = ""
    gaps_closed: int = 0
    quality: Any = None  # parsed checkMesh report (MeshQualityReport)


class FaultTolerantWorkflow:
    """Fault-tolerant meshing for non-watertight geometry.

    Usage::

        ft = FaultTolerantWorkflow(of_config)
        ft.set_geometry("path/to/dirty_geometry.stl")
        ft.set_params(FaultTolerantParams(gap_h=0.002))
        result = ft.run()
    """

    def __init__(self, of_config: OFConfig | None = None) -> None:
        self._of_config = of_config or OFConfig()
        self._geometry_path: str | None = None
        self._params = FaultTolerantParams()
        self._case_dir: Path | None = None
        self._max_cell: float = 0.05
        self._min_cell: float = 0.01
        self._bl_params: dict | None = None
        self._result = FaultTolerantResult()

    def set_geometry(self, path: str | Path) -> None:
        result = validate_geometry_path(path)
        if not result.valid:
            raise ValueError(result.message)
        self._geometry_path = str(path)

    def set_params(self, params: FaultTolerantParams) -> None:
        self._params = params

    def set_cell_sizes(self, max_cell: float, min_cell: float) -> None:
        result = validate_cell_size(max_cell, min_cell)
        if not result.valid:
            raise ValueError(result.message)
        self._max_cell = max_cell
        self._min_cell = min_cell

    def set_boundary_layers(self, n_layers: int, thickness_ratio: float,
                            expansion_ratio: float) -> None:
        self._bl_params = {
            "nLayers": n_layers,
            "thicknessRatio": thickness_ratio,
            "expansionRatio": expansion_ratio,
        }

    def run(self) -> FaultTolerantResult:
        """Execute the fault-tolerant meshing workflow.

        ``success=True`` is only set once a volume mesh actually exists on
        disk AND checkMesh confirms it (no fatal error, cells > 0). If
        checkMesh is unavailable the mesh-on-disk check alone is accepted,
        with a warning that quality is unverified.
        """
        start = datetime.now()
        self._result = FaultTolerantResult()
        octo.log_event("fault_tolerant", "workflow_start", {"geometry": self._geometry_path})

        try:
            self._result.stage = "import"
            self._step_import()
            self._result.stage = "gaps"
            self._step_detect_gaps()
            self._result.stage = "wrap"
            self._step_close_gaps()
            self._result.stage = "meshdict"
            self._step_prepare_meshdict()
            self._result.stage = "volume_fill"
            self._step_volume_fill()
            self._result.stage = "quality"
            self._step_quality()
            self._result.stage = "done"
            self._result.success = True
            octo.log_event("fault_tolerant", "workflow_complete", {
                "success": True,
                "cells": self._result.cells,
                "wall_time_s": round((datetime.now() - start).total_seconds(), 1),
            })
        except Exception as exc:
            self._result.errors.append(str(exc))
            logger.exception("Fault-tolerant workflow failed")
            octo.log_event("fault_tolerant", "workflow_failed", {"error": str(exc)})

        elapsed = (datetime.now() - start).total_seconds()
        self._result.wall_time_seconds = elapsed
        return self._result

    def _step_import(self) -> None:
        if not self._geometry_path:
            raise RuntimeError("No geometry set. Call set_geometry() first.")
        path = Path(self._geometry_path)
        if not path.exists():
            raise FileNotFoundError(f"Geometry file not found: {path}")

        from cfmesh_autogui.core.stl_writer import heal_mesh

        ext = path.suffix.lower()
        if ext in (".step", ".stp"):
            from cfmesh_autogui.core.geometry import load_step, classify_faces, tessellate_patches
            shape = load_step(path)
            patches = classify_faces(shape)
            meshes = tessellate_patches(patches)
        elif ext == ".stl":
            meshes = _load_stl_fault_tolerant(path, self._params.merge_tolerance)
        else:
            raise ValueError(f"Unsupported format: {ext}")

        if not meshes:
            raise RuntimeError(f"No geometry loaded from {path}")

        # Heal each mesh
        for m in meshes:
            heal_mesh(m)

        self._meshes = meshes
        self._n_watertight = sum(1 for m in meshes if m.is_watertight)

        # Auto case dir
        if self._case_dir is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._case_dir = Path.home() / "cfmesh_cases" / f"fault_tolerant_{ts}"
        self._case_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Imported %d patches (%d watertight)", len(meshes), self._n_watertight)

    def _step_detect_gaps(self) -> None:
        """Analyse geometry for gaps and report."""
        import numpy as np
        gaps: list[GapInfo] = []
        for mesh in self._meshes:
            if mesh.is_watertight:
                continue
            edges = mesh.edges_unique
            verts = mesh.vertices
            if len(edges) == 0:
                continue
            edge_lens = np.linalg.norm(verts[edges[:, 0]] - verts[edges[:, 1]], axis=1)
            large_edges = edge_lens[edge_lens > self._params.gap_h]
            if len(large_edges) > 0:
                gap_idx = int(np.argmax(edge_lens))
                gaps.append(GapInfo(
                    location=tuple(verts[edges[gap_idx, 0]].tolist()),
                    gap_size=float(edge_lens[gap_idx]),
                    patch_name=mesh.metadata.get("name", "?"),
                ))

        self._result.gaps_found = len(gaps)
        self._result.min_gap = min((g.gap_size for g in gaps), default=0.0)
        if gaps:
            logger.info("Found %d gaps (min=%.4f m)", len(gaps), self._result.min_gap)
            octo.log_event("fault_tolerant", "gaps_detected",
                           {"count": len(gaps), "min_gap": self._result.min_gap})

    def _step_close_gaps(self) -> None:
        """Attempt to close detected gaps before volume meshing.

        cfMesh/cartesianMesh needs a watertight surface, so before writing
        the meshDict we try the escalating repair pipeline from
        core/geometry_repair (vertex snap-merge first, PyMeshFix as a
        stronger fallback). Repair is best-effort: if it is unavailable or
        throws we log a warning and continue — the volume-fill step is the
        authority on whether meshing actually worked.
        """
        if self._result.gaps_found <= 0 and all(m.is_watertight for m in self._meshes):
            return

        try:
            from cfmesh_autogui.core.geometry_repair import attempt_auto_repair
            from cfmesh_autogui.core.geometry import compute_bbox_dim
        except Exception as exc:
            self._result.warnings.append(f"Gap repair unavailable: {exc}")
            logger.warning("Gap repair unavailable: %s", exc)
            return

        n_watertight_before = sum(1 for m in self._meshes if m.is_watertight)
        try:
            bbox_dim = compute_bbox_dim(self._meshes)
            # Snap tolerance derived from the measured gaps, not a hardcoded
            # constant: merge vertices closer than half the gap threshold
            # (they are tessellation seams, not real features) while leaving
            # genuine gaps (>= gap_h) intact for the volume fill.
            gap_ref = self._result.min_gap if self._result.min_gap > 0 else self._params.gap_h
            tolerance = min(gap_ref, self._params.gap_h) * 0.5
            repaired, _reports = attempt_auto_repair(self._meshes, bbox_dim, tolerance=tolerance)
        except Exception as exc:
            self._result.warnings.append(f"Gap repair failed: {exc}")
            logger.warning("Gap repair failed: %s", exc)
            return

        self._meshes = repaired
        self._n_watertight = sum(1 for m in repaired if m.is_watertight)
        self._result.gaps_closed = max(0, self._n_watertight - n_watertight_before)

        n_open = len(repaired) - self._n_watertight
        if n_open:
            self._result.warnings.append(
                f"{n_open} patch(es) still non-watertight after gap repair — "
                "volume fill may fail."
            )
        octo.log_event("fault_tolerant", "gaps_closed", {
            "closed": self._result.gaps_closed,
            "remaining_open": n_open,
        })

    def _step_prepare_meshdict(self) -> None:
        """Write meshDict with fault-tolerant friendly settings."""
        # meshDict's BL contract: thicknessRatio = growth ratio (>1),
        # firstLayerThickness = absolute metres. set_boundary_layers() stores
        # the caller's "thickness_ratio" as a first-layer FRACTION of the max
        # cell size and a separate "expansionRatio" for the real growth ratio
        # — the opposite of what write_meshdict expects (same bug found and
        # fixed in commercial/watertight.py). Passed straight through, a
        # sub-1 "growth ratio" gets clamped to a default and no absolute first
        # layer is ever emitted, silently discarding the caller's BL settings.
        mesh_bl_params = None
        if self._bl_params:
            first_layer_fraction = self._bl_params.get("thicknessRatio", 0.005)
            mesh_bl_params = dict(self._bl_params)
            mesh_bl_params["thicknessRatio"] = self._bl_params.get("expansionRatio", 1.2)
            mesh_bl_params["firstLayerThickness"] = first_layer_fraction * self._max_cell

        self._write_meshdict(mesh_bl_params)

    def _write_meshdict(self, bl_params: dict | None) -> None:
        """Export the surface STL and write meshDict + controlDict.

        Extracted from _step_prepare_meshdict so the BL -> no-BL fallback in
        _step_volume_fill can rewrite the meshDict without boundary layers.
        """
        from cfmesh_autogui.core.stl_writer import export_surface_file
        from cfmesh_autogui.core.meshdict_gen import write_meshdict

        export_surface_file(self._meshes, self._case_dir)

        write_meshdict(
            self._case_dir,
            max_cell_size=self._max_cell,
            min_cell_size=self._min_cell,
            bl_params=bl_params,
            patch_names=[m.metadata.get("name", f"patch_{i}") for i, m in enumerate(self._meshes)],
        )

        # Write controlDict
        (self._case_dir / "system" / "controlDict").write_text(
            "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
            "application cartesianMesh;\n"
            "startFrom startTime; startTime 0;\n"
            "stopAt endTime; endTime 1000;\n"
            "deltaT 1;\n"
            "writeControl timeStep; writeInterval 1;\n"
            "writeFrequency 1;\n"
            "purgeWrite 0; writeFormat binary; writePrecision 6;\n"
            "writeCompression on; timeFormat general; timePrecision 6;\n"
            "runTimeModifiable true;\n",
            encoding="ascii",
        )
        logger.info("meshDict written with fault-tolerant settings")

    def _step_volume_fill(self) -> None:
        """Run cartesianMesh to fill the volume — the core fix.

        Runs cartesianMesh as a direct synchronous subprocess, NOT through
        RetryRunner or any QThread/QObject worker: those deliver completion
        via a QueuedConnection signal that needs a live Qt event loop on the
        owning thread, and synchronous callers (e.g. batch_mesh.py) hang
        forever. Same root cause and fix as watertight.py's _step_volume_mesh.
        """
        if not self._meshes or not self._case_dir:
            raise RuntimeError("No geometry loaded for volume meshing.")

        poly_points = self._case_dir / "constant" / "polyMesh" / "points"
        if poly_points.exists():
            logger.info("polyMesh already present — skipping volume fill")
            return

        if not self._run_cartesian_mesh_sync():
            if self._bl_params:
                logger.warning("Volume mesh failed with boundary layers, retrying without BL")
                self._write_meshdict(None)
                if not self._run_cartesian_mesh_sync():
                    raise RuntimeError("Volume mesh failed (with and without boundary layers)")
            else:
                raise RuntimeError("Volume mesh failed")

    def _run_cartesian_mesh_sync(self) -> bool:
        """Run cartesianMesh synchronously and wait for it to actually finish.

        Returns True only if the process exited 0 AND a polyMesh was actually
        written — a zero return code with no mesh on disk is a failure.
        """
        import subprocess

        poly_points = self._case_dir / "constant" / "polyMesh" / "points"
        try:
            cmd = self._of_config.build_command(self._case_dir)
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=_VOLUME_FILL_TIMEOUT_S)
            return result.returncode == 0 and poly_points.exists()
        except subprocess.TimeoutExpired:
            msg = f"cartesianMesh timed out after {_VOLUME_FILL_TIMEOUT_S}s"
            logger.warning(msg)
            self._result.errors.append(msg)
            return False
        except FileNotFoundError:
            msg = "cartesianMesh failed: WSL not found"
            logger.warning(msg)
            self._result.errors.append(msg)
            return False

    def _step_quality(self) -> None:
        """Run checkMesh and parse the quality report — the final gate.

        success=True is only set by run() after this step confirms a mesh
        exists and checkMesh reports no fatal error with cells > 0. If
        checkMesh is missing or times out we fall back to "mesh exists on
        disk" and record a warning that quality is unverified.
        """
        if not self._case_dir:
            return

        poly_points = self._case_dir / "constant" / "polyMesh" / "points"
        if not poly_points.exists():
            self._result.warnings.append("polyMesh/points not found — quality check skipped.")
            return

        import subprocess
        from cfmesh_autogui.core.openfoam_runner import parse_checkmesh_output

        cmd = self._of_config.build_check_mesh_cmd(self._case_dir)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            full = result.stdout + "\n" + result.stderr
        except subprocess.TimeoutExpired:
            self._result.warnings.append("checkMesh timed out after 120s — quality unverified")
            return
        except FileNotFoundError:
            self._result.warnings.append("checkMesh failed: WSL not found — quality unverified")
            return
        except Exception as exc:
            self._result.warnings.append(f"checkMesh failed: {exc} — quality unverified")
            return

        report = parse_checkmesh_output(full)
        self._result.quality = report
        self._result.cells = report.cells
        logger.info("Quality: %s", report.status)
        octo.log_event("fault_tolerant", "quality_ok", {
            "cells": report.cells, "status": report.status,
        })

        # Final gate: a fatal checkMesh report (or a report with no cells)
        # means the mesh is not usable, even if cartesianMesh exited 0.
        if report.has_fatal or report.cells <= 0:
            raise RuntimeError(f"checkMesh reports a fatal problem: {report.status}")


def _load_stl_fault_tolerant(path: Path, merge_tol: float = 0.0001) -> list:
    """Load an STL and apply fault-tolerant preprocessing.

    - Merges close vertices (welding)
    - Removes degenerate faces
    - Returns list of meshes even if non-watertight
    """
    import trimesh
    scene = trimesh.load_mesh(str(path), force="mesh")
    if isinstance(scene, trimesh.Scene):
        meshes = list(scene.geometry.values())
    else:
        meshes = [scene]

    for m in meshes:
        m.metadata.setdefault("name", getattr(m, "name", path.stem))
        try:
            m.merge_vertices(merge_tol)
        except Exception as exc:
            logger.debug("merge_vertices failed for '%s': %s", m.metadata.get("name", "?"), exc)
        try:
            mask = m.nondegenerate_faces()
            m.update_faces(mask)
        except Exception as exc:
            logger.debug("nondegenerate_faces/update_faces failed for '%s': %s", m.metadata.get("name", "?"), exc)

    return meshes
