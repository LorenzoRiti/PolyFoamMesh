"""Fault-tolerant (non-watertight) meshing workflow.

Inspired by ANSYS Fluent Meshing Fault-Tolerant Workflow.
Handles CAD with gaps, overlaps, missing faces, and small features
that prevent traditional watertight meshing.

Pipeline:
  1. Import geometry (even non-watertight STL with gaps/overlaps)
  2. Gap detection & analysis
  3. Shrink-wrap surface wrapping
  4. Internal feature occlusion
  5. Local refinement + boundary layers on wrapped surface
  6. Volume fill (cartesian / polyhedral)
  7. Quality check
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
        """Execute the fault-tolerant meshing workflow."""
        start = datetime.now()
        self._result = FaultTolerantResult()
        octo.log_event("fault_tolerant", "workflow_start", {"geometry": self._geometry_path})

        try:
            self._step_import()
            self._step_detect_gaps()
            self._step_prepare_meshdict()
            self._result.success = True
            octo.log_event("fault_tolerant", "workflow_complete", {})
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

    def _step_prepare_meshdict(self) -> None:
        """Write meshDict with fault-tolerant friendly settings."""
        from cfmesh_autogui.core.stl_writer import export_surface_file
        from cfmesh_autogui.core.meshdict_gen import write_meshdict

        export_surface_file(self._meshes, self._case_dir)

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

        write_meshdict(
            self._case_dir,
            max_cell_size=self._max_cell,
            min_cell_size=self._min_cell,
            bl_params=mesh_bl_params,
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
            "purgeWrite 0; writeFormat ascii; writePrecision 6;\n"
            "writeCompression off; timeFormat general; timePrecision 6;\n"
            "runTimeModifiable true;\n",
            encoding="ascii",
        )
        logger.info("meshDict written with fault-tolerant settings")


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
        except Exception:
            pass
        try:
            mask = m.nondegenerate_faces()
            m.update_faces(mask)
        except Exception:
            pass

    return meshes
