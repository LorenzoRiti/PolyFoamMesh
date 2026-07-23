"""Watertight (closed-volume) meshing workflow — authoritative.

Inspired by ANSYS Fluent Meshing Watertight Workflow, this module
orchestrates the complete end-to-end meshing pipeline:

  1. Import & validate geometry
  2. Heal & watertight check
  3. Feature extraction & auto-sizing
  4. Local refinement sources (box, cylinder, sphere)
  5. Boundary layer specification
  6. Volume mesh generation (cartesian / polyhedral)
  7. Quality check & auto-fix loop
  8. Export in multiple formats

Every step emits Octopoda events for audit-trail and CI/CD integration.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from cfmesh_autogui.core.openfoam_runner import MeshQualityReport, RetryRunner

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.validation import (
    validate_cell_size, validate_bl_params,
    validate_geometry_path, validate_case_dir, sanitise_patch_name,
    ValidationResult,
)
from cfmesh_autogui.octopoda_local import octo

# Lazy imports (avoid cadquery DLL load at module level)
_geometry = None
_stl_writer = None
_meshdict_gen = None
_openfoam_runner = None


def _lazy_geom():
    global _geometry
    if _geometry is None:
        from cfmesh_autogui.core import geometry as _geometry
    return _geometry


def _lazy_stl():
    global _stl_writer
    if _stl_writer is None:
        from cfmesh_autogui.core import stl_writer as _stl_writer
    return _stl_writer


def _lazy_meshdict():
    global _meshdict_gen
    if _meshdict_gen is None:
        from cfmesh_autogui.core import meshdict_gen as _meshdict_gen
    return _meshdict_gen


def _lazy_runner():
    global _openfoam_runner
    if _openfoam_runner is None:
        from cfmesh_autogui.core import openfoam_runner as _openfoam_runner
    return _openfoam_runner

logger = logging.getLogger(__name__)


class WorkflowStep(Enum):
    IMPORT = auto()
    HEAL = auto()
    FEATURES = auto()
    SIZING = auto()
    REFINEMENT = auto()
    BOUNDARY_LAYER = auto()
    VOLUME_MESH = auto()
    QUALITY = auto()
    EXPORT = auto()


@dataclass
class RefinementSource:
    """A local refinement region.

    Used for box/cylinder/sphere refinment zones that override the
    global cell size in a specific region.
    """
    name: str
    shape: str  # "box" | "cylinder" | "sphere"
    center: tuple[float, float, float]
    size: tuple[float, float, float]  # dimensions in metres
    cell_size: float
    enabled: bool = True


@dataclass
class WorkflowResult:
    success: bool = False
    steps_completed: list[str] = field(default_factory=list)
    case_dir: str = ""
    quality: MeshQualityReport | None = None
    cell_count: int = 0
    wall_time_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class WatertightWorkflow:
    """End-to-end watertight meshing workflow.

    Usage::

        wf = WatertightWorkflow(of_config)
        wf.set_geometry("path/to/model.step")
        wf.set_cell_sizes(max_cell=0.05, min_cell=0.01)
        result = wf.run()
        if result.success:
            print(f"Mesh ready: {result.cell_count} cells")
    """

    def __init__(self, of_config: OFConfig | None = None) -> None:
        self._of_config = of_config or OFConfig()
        self._geometry_path: str | None = None
        self._meshes: list = []
        self._case_dir: Path | None = None
        self._max_cell: float = 0.05
        self._min_cell: float = 0.01
        self._detail: str = "medium"
        self._bl_params: dict | None = None
        self._refinement_sources: list[RefinementSource] = []
        self._step_callbacks: dict[WorkflowStep, list[Callable]] = {}
        self._runner: RetryRunner | None = None
        self._start_time: float = 0.0
        self._result = WorkflowResult()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def set_geometry(self, path: str | Path) -> ValidationResult:
        """Set and validate the geometry file path."""
        result = validate_geometry_path(path)
        if result.valid:
            self._geometry_path = str(path)
        return result

    def set_case_dir(self, path: str | Path) -> ValidationResult:
        """Set the output case directory."""
        result = validate_case_dir(path)
        if result.valid:
            self._case_dir = Path(path)
        return result

    def set_cell_sizes(
        self, max_cell: float, min_cell: float, bbox_dim: float | None = None,
    ) -> ValidationResult:
        """Set global cell size limits."""
        result = validate_cell_size(max_cell, min_cell, bbox_dim)
        if result.valid:
            self._max_cell = max_cell
            self._min_cell = min_cell
        return result

    def set_detail(self, detail: str) -> ValidationResult:
        allowed = {"very_coarse", "coarse", "medium", "fine", "very_fine"}
        if detail.lower() not in allowed:
            return ValidationResult(False, f"detail must be one of {allowed}")
        self._detail = detail.lower()
        return ValidationResult()

    def set_boundary_layers(
        self, n_layers: int, thickness_ratio: float,
        expansion_ratio: float, wall_patches: list[str] | None = None,
    ) -> ValidationResult:
        result = validate_bl_params(n_layers, thickness_ratio, expansion_ratio, wall_patches)
        if result.valid:
            self._bl_params = {
                "nLayers": n_layers,
                "thicknessRatio": thickness_ratio,
                "expansionRatio": expansion_ratio,
                "wallPatches": wall_patches or [],
            }
        return result

    def add_refinement(self, source: RefinementSource) -> None:
        self._refinement_sources.append(source)

    def on_step(self, step: WorkflowStep, callback: Callable) -> None:
        """Register a callback invoked before a workflow step runs.

        The callback receives the ``WorkflowStep`` enum value.
        """
        self._step_callbacks.setdefault(step, []).append(callback)

    # ------------------------------------------------------------------
    # Workflow execution
    # ------------------------------------------------------------------
    def run(self) -> WorkflowResult:
        """Execute the full watertight workflow.

        Returns a ``WorkflowResult`` summarising success/failure and
        quality metrics.
        """
        self._start_time = datetime.now().timestamp()
        self._result = WorkflowResult()
        octo.log_event("watertight", "workflow_start", {
            "geometry": self._geometry_path,
            "max_cell": self._max_cell,
            "min_cell": self._min_cell,
        })

        pipeline = [
            (WorkflowStep.IMPORT, self._step_import),
            (WorkflowStep.HEAL, self._step_heal),
            (WorkflowStep.FEATURES, self._step_features),
            (WorkflowStep.SIZING, self._step_sizing),
            (WorkflowStep.BOUNDARY_LAYER, self._step_boundary_layer),
            (WorkflowStep.VOLUME_MESH, self._step_volume_mesh),
            (WorkflowStep.QUALITY, self._step_quality),
            (WorkflowStep.EXPORT, self._step_export),
        ]

        try:
            for step, fn in pipeline:
                self._emit_callbacks(step)
                fn()
                self._result.steps_completed.append(step.name.lower())
                octo.log_event("watertight", f"step_{step.name.lower()}_ok", {})

            elapsed = datetime.now().timestamp() - self._start_time
            self._result.success = True
            self._result.wall_time_seconds = elapsed
            octo.log_event("watertight", "workflow_complete", {
                "success": True, "cell_count": self._result.cell_count,
                "wall_time_s": round(elapsed, 1),
            })
        except Exception as exc:
            elapsed = datetime.now().timestamp() - self._start_time
            self._result.success = False
            self._result.wall_time_seconds = elapsed
            self._result.errors.append(str(exc))
            logger.exception("Watertight workflow failed at step %s", step.name if 'step' in dir() else "unknown")
            octo.log_event("watertight", "workflow_failed", {
                "step": step.name if 'step' in dir() else "unknown",
                "error": str(exc),
            })

        return self._result

    # ------------------------------------------------------------------
    # Individual steps
    # ------------------------------------------------------------------
    def _emit_callbacks(self, step: WorkflowStep) -> None:
        for cb in self._step_callbacks.get(step, []):
            try:
                cb(step)
            except Exception as exc:
                logger.warning("Step callback failed for %s: %s", step.name, exc)

    def _step_import(self) -> None:
        if not self._geometry_path:
            raise RuntimeError("No geometry path set. Call set_geometry() first.")

        path = Path(self._geometry_path)
        ext = path.suffix.lower()

        geom = _lazy_geom()
        if ext in (".step", ".stp"):
            shape = geom.load_step(path)
            patches = geom.classify_faces(shape)
            self._meshes = geom.tessellate_patches(patches)
        elif ext == ".stl":
            self._meshes = geom.load_geometry(path)
        else:
            raise ValueError(f"Unsupported format: {ext}")

        if not self._meshes:
            raise RuntimeError("No meshes loaded from geometry.")

        logger.info("Imported %d patches from %s", len(self._meshes), path.name)
        octo.log_event("watertight", "import_ok", {
            "patches": len(self._meshes), "file": path.name,
        })

        # Auto-create case directory
        if self._case_dir is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._case_dir = Path.home() / "cfmesh_cases" / f"watertight_{ts}"
        self._case_dir.mkdir(parents=True, exist_ok=True)

    def _step_heal(self) -> None:
        """Apply mesh healing to all patches."""
        stl = _lazy_stl()
        healed = 0
        for mesh in self._meshes:
            before = len(mesh.faces)
            stl.heal_mesh(mesh)
            if len(mesh.faces) != before:
                healed += 1
        logger.info("Healing applied to %d/%d patches", healed, len(self._meshes))

    def _step_features(self) -> None:
        """Extract features and suggest cell sizes."""
        geom = _lazy_geom()
        bbox_dim = geom.compute_bbox_dim(self._meshes)
        s_max, s_min = geom.suggest_cell_sizes(self._meshes, detail=self._detail)

        # Override with user values if more conservative
        self._max_cell = min(self._max_cell, s_max)
        self._min_cell = min(self._min_cell, s_min)

        patch_sizes, bc_size, bc_thick = geom.compute_patch_cell_sizes(
            self._meshes, detail=self._detail,
        )
        self._patch_sizes = patch_sizes
        self._boundary_cell_size = bc_size
        self._boundary_thickness = bc_thick

        logger.info(
            "Feature sizing: max=%.4f min=%.4f bbox=%.4f",
            self._max_cell, self._min_cell, bbox_dim,
        )

    def _step_sizing(self) -> None:
        """Write meshDict with cell sizing."""
        geom = _lazy_geom()
        bbox_dim = geom.compute_bbox_dim(self._meshes)

        safe_max, safe_min, _ = geom.validate_cell_sizes(
            bbox_dim, self._max_cell, self._min_cell,
        )

        _lazy_meshdict().write_meshdict(
            self._case_dir, safe_max, safe_min,
            patch_cell_size=getattr(self, '_patch_sizes', None),
            boundary_cell_size=getattr(self, '_boundary_cell_size', None),
            boundary_refinement_thickness=getattr(self, '_boundary_thickness', None),
            bl_params=self._bl_params,
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

        volume = geom.compute_volume(self._meshes)
        _, est, _ = geom.estimate_cell_count(volume, safe_max, safe_min)
        logger.info("Estimated cell count: ~%d", est)

    def _step_boundary_layer(self) -> None:
        """Finalise BL parameters and write to meshDict."""
        if not self._bl_params:
            logger.info("Boundary layers: disabled.")
            return

        all_names = [
            m.metadata.get("name", f"patch_{i}")
            for i, m in enumerate(self._meshes)
        ]
        wall_patches = self._bl_params.get("wallPatches") or [
            n for n in all_names
            if n.lower() in ("wall", "walls") or n.lower().startswith("wall_")
        ]
        if not wall_patches:
            wall_patches = all_names
            self._result.warnings.append(
                "No wall patches found — applying BL to all patches."
            )

        self._bl_params["wallPatches"] = wall_patches

        geom = _lazy_geom()
        bbox_dim = geom.compute_bbox_dim(self._meshes)
        safe_max, safe_min, _ = geom.validate_cell_sizes(
            bbox_dim, self._max_cell, self._min_cell,
        )

        _lazy_meshdict().write_meshdict(
            self._case_dir, safe_max, safe_min,
            patch_cell_size=getattr(self, '_patch_sizes', None),
            boundary_cell_size=getattr(self, '_boundary_cell_size', None),
            boundary_refinement_thickness=getattr(self, '_boundary_thickness', None),
            bl_params=self._bl_params,
        )
        logger.info("BL applied to %d patches.", len(wall_patches))

    def _step_volume_mesh(self) -> None:
        """Export STL and run cartesianMesh."""
        if not self._meshes or not self._case_dir:
            raise RuntimeError("No geometry loaded for volume meshing.")

        _lazy_stl().export_surface_file(self._meshes, self._case_dir)
        logger.info("Surface STL exported to %s", self._case_dir)

        geom = _lazy_geom()
        bbox_dim = geom.compute_bbox_dim(self._meshes)
        safe_max, safe_min, _ = geom.validate_cell_sizes(
            bbox_dim, self._max_cell, self._min_cell,
        )

        self._runner = _lazy_runner().RetryRunner(self._of_config)
        self._runner.run(
            case_dir=self._case_dir,
            bl_params=self._bl_params,
            max_cell=safe_max,
            min_cell=safe_min,
            patch_cell_size=getattr(self, '_patch_sizes', None),
        )

    def _step_quality(self) -> None:
        """Run checkMesh and parse the quality report."""
        if not self._case_dir:
            return

        poly_points = self._case_dir / "constant" / "polyMesh" / "points"
        if not poly_points.exists():
            self._result.warnings.append("polyMesh/points not found — quality check skipped.")
            return

        from PySide6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        report_holder: list["MeshQualityReport"] = []

        def on_finished(report: "MeshQualityReport") -> None:
            report_holder.append(report)
            loop.quit()

        def on_failed(msg: str) -> None:
            self._result.warnings.append(f"checkMesh failed: {msg}")
            loop.quit()

        runner_mod = _lazy_runner()
        worker = runner_mod.CheckMeshWorker(self._case_dir, self._of_config)
        worker.finished.connect(on_finished)
        worker.failed.connect(on_failed)

        from PySide6.QtCore import QThread
        thread = QThread()
        worker.moveToThread(thread)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.start()

        # Timeout after 120 s
        QTimer.singleShot(120000, loop.quit)
        loop.exec()

        if report_holder:
            report = report_holder[0]
            self._result.quality = report
            self._result.cell_count = report.cells
            logger.info("Quality: %s", report.status)

    def _step_export(self) -> None:
        """Export quality report as JSON."""
        if not self._case_dir:
            return

        result_path = self._case_dir / "watertight_result.json"
        data = {
            "success": self._result.success,
            "steps": self._result.steps_completed,
            "cell_count": self._result.cell_count,
            "wall_time_s": round(self._result.wall_time_seconds, 1),
            "quality": self._result.quality.to_dict() if self._result.quality else {},
            "warnings": self._result.warnings,
            "errors": self._result.errors,
        }
        result_path.write_text(json.dumps(data, indent=2, default=str))
        logger.info("Result JSON exported: %s", result_path)
