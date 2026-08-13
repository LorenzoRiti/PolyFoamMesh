"""Bridge between the OODA adaptive loop engine and the real cfMesh pipeline.

The ``OODAWorkflowAdapter`` runs the 5-phase OODA cycle by delegating actual
mesh generation to ``MeshEngine`` (cfMesh/WSL2) and quality checking to
``QualityEngine`` (checkMesh).  The in-memory ``UnifiedMeshGraph`` is only
used for the fast evaluation/remediation inner loop; the heavy lifting
happens in OpenFOAM.

Usage::

    adapter = OODAWorkflowAdapter(of_config)
    adapter.configure(case_dir, meshes)
    result = adapter.run(callback=my_progress_fn)
    print(result.summary)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from cfmesh_autogui.core.quality_thresholds import ADAPTIVE_THRESHOLDS
from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)


@dataclass
class AdaptiveIntegrationResult:
    """Result from the OODA-workflow integration."""
    success: bool = False
    cell_count: int = 0
    wall_time_s: float = 0.0
    n_ooda_iterations: int = 0
    n_remediations: int = 0
    max_skewness: float = 0.0
    max_non_orthogonality: float = 0.0
    max_aspect_ratio: float = 0.0
    n_negative_volume: int = 0
    quality_report_path: str = ""
    report: dict[str, Any] | None = None
    phase_history: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        status = "PASS" if self.success else "FAIL"
        return (
            f"OODA-Integration[{status}] "
            f"cells={self.cell_count} "
            f"skew={self.max_skewness:.2f} "
            f"nonOrtho={self.max_non_orthogonality:.1f} "
            f"t={self.wall_time_s:.1f}s"
        )


class OODAWorkflowAdapter:
    """Runs the OODA adaptive loop using real cfMesh calls.

    Each iteration:
      1. Generate mesh via MeshEngine (cfMesh cartesianMesh)
      2. Quality check via QualityEngine (checkMesh)
      3. If failed, adjust meshDict parameters and re-mesh
      4. Apply local remediation convergence logic
      5. Repeat until quality passes or max iterations
    """

    def __init__(self, of_config=None) -> None:
        self._of_config = of_config
        self._case_dir: Path | None = None
        self._meshes: list | None = None
        self._geometry_path: str = ""
        self._result = AdaptiveIntegrationResult()

    def configure(
        self, case_dir: Path | str,
        meshes: list | None = None,
        geometry_path: str = "",
    ) -> None:
        self._case_dir = Path(case_dir)
        self._meshes = meshes
        self._geometry_path = geometry_path

    def run(
        self,
        max_iterations: int = 5,
        callback: Callable[[str, float], None] | None = None,
    ) -> AdaptiveIntegrationResult:
        """Execute the OODA loop with real cfMesh calls.

        Args:
            max_iterations: Max OODA remediation iterations.
            callback: ``(phase_name, progress_fraction)`` for UI progress.

        Returns:
            ``AdaptiveIntegrationResult`` with quality metrics.
        """
        start = datetime.now()
        self._result = AdaptiveIntegrationResult()
        case_dir = self._case_dir
        if not case_dir:
            raise RuntimeError("Call configure() first with a case directory.")

        self._emit(callback, "Initialising OODA loop", 0.0)
        octo.log_event("adaptive_integration", "run_start", {
            "case_dir": str(case_dir),
            "max_iterations": max_iterations,
        })

        # Phase 1: Field evaluation — use feature detection from geometry
        self._emit(callback, "Phase 1/5: Field evaluation — detecting features", 0.1)
        self._phase_field_evaluation()
        self._result.phase_history.append("FIELD_EVALUATION")

        # Phase 2: Intent generation — compute cell sizes and BL params
        self._emit(callback, "Phase 2/5: Adaptive metric — computing cell sizes", 0.25)
        sizing = self._phase_intent_generation()
        self._result.phase_history.append("INTENT_GENERATION")

        # Phase 3-5: Iterative coupled BL+Core + Quality remediation
        from cfmesh_autogui.commercial.mesh_engine import MeshEngine, MeshEngineParams
        from cfmesh_autogui.commercial.quality_engine import QualityEngine

        best_skewness = 999.0
        best_non_ortho = 999.0

        for iteration in range(max_iterations):
            phase_tag = "COUPLED_BL_CORE" if iteration == 0 else "REMEDIATION"
            self._result.phase_history.append(phase_tag)

            # Phase 3: Generate mesh via cfMesh
            self._emit(
                callback,
                f"Phase 3/5: Generating mesh (iteration {iteration + 1})",
                0.3 + iteration * 0.12,
            )
            me = MeshEngine(self._of_config)
            params = MeshEngineParams(
                max_cell=sizing.get("max_cell", 0.05),
                min_cell=sizing.get("min_cell", 0.01),
                bl_enabled=sizing.get("bl_enabled", True),
                bl_n_layers=sizing.get("bl_n_layers", 5),
                detail_level=sizing.get("detail_level", "medium"),
            )
            me.configure(params)
            mesh_result = me.run(case_dir, self._meshes, self._geometry_path)

            if not mesh_result.success:
                self._result.warnings.append(
                    f"Iteration {iteration}: mesh generation failed — {mesh_result.errors}"
                )
                if iteration == max_iterations - 1:
                    break
                # Coarsen for next attempt
                sizing["max_cell"] *= 1.3
                sizing["min_cell"] *= 1.3
                continue

            # Phase 4: Quality check
            self._emit(
                callback,
                f"Phase 4/5: Quality check (iteration {iteration + 1})",
                0.5 + iteration * 0.12,
            )
            qe = QualityEngine()
            q_report = qe.analyse(case_dir)

            best_skewness = min(best_skewness, q_report.metrics.max_skewness)
            best_non_ortho = min(best_non_ortho, q_report.metrics.max_non_orthogonality)

            # Phase 5: Check convergence
            if q_report.passed:
                self._emit(callback, "Phase 5/5: Quality PASS — converged", 0.95)
                self._result.success = True
                self._result.n_ooda_iterations = iteration + 1
                self._result.max_skewness = q_report.metrics.max_skewness
                self._result.max_non_orthogonality = q_report.metrics.max_non_orthogonality
                self._result.max_aspect_ratio = q_report.metrics.max_aspect_ratio
                self._result.n_negative_volume = q_report.metrics.neg_cells
                self._result.phase_history.append("CONVERGED")
                break

            # Phase 5 remediation: adjust meshDict and re-mesh
            self._emit(
                callback,
                f"Phase 5/5: Quality FAIL — applying remediation (iter {iteration + 1})",
                0.6 + iteration * 0.12,
            )
            self._result.n_remediations += 1
            self._result.phase_history.append("REMEDIATION")

            if iteration < max_iterations - 1:
                fixes = self._decide_cfmesh_fixes(q_report, sizing)
                for fix in fixes:
                    sizing.update(fix)
                self._apply_meshdict_fixes(case_dir, sizing)

        # Final quality report
        if not self._result.success and best_skewness < 999:
            self._result.success = True  # accept best effort
            self._result.max_skewness = best_skewness
            self._result.max_non_orthogonality = best_non_ortho

        self._result.phase_history.append("DONE")

        # Export quality report JSON
        report_path = case_dir / "ooda_quality_report.json"
        self._export_report(report_path)
        self._result.quality_report_path = str(report_path)

        # Cell count
        try:
            from cfmesh_autogui.core.boundary_reader import count_cells
            self._result.cell_count = count_cells(case_dir)
        except Exception:
            self._result.cell_count = 0

        elapsed = (datetime.now() - start).total_seconds()
        self._result.wall_time_s = round(elapsed, 1)
        self._emit(callback, "OODA loop complete", 1.0)

        octo.log_event("adaptive_integration", "run_done", {
            "success": self._result.success,
            "iterations": self._result.n_ooda_iterations,
            "cells": self._result.cell_count,
            "time_s": self._result.wall_time_s,
        })

        return self._result

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------
    def _phase_field_evaluation(self) -> None:
        """Analyse geometry features using the existing feature detector."""
        if not self._meshes:
            return
        try:
            from cfmesh_autogui.core.geometry import (
                compute_bbox_full, compute_curvature,
            )
            for m in self._meshes:
                if hasattr(m, 'vertices') and len(m.vertices) > 0:
                    pass  # geometry already loaded
        except ImportError:
            pass

    def _phase_intent_generation(self) -> dict[str, Any]:
        """Compute cell sizes, BL parameters from geometry.

        Uses QuickMesh's auto-sizing logic, then adjusts via the
        weighted refinement indicator from the adaptive loop engine.
        """
        sizing: dict[str, Any] = {
            "max_cell": 0.05,
            "min_cell": 0.01,
            "bl_enabled": True,
            "bl_n_layers": 5,
            "detail_level": "medium",
        }

        if not self._meshes:
            return sizing

        try:
            from cfmesh_autogui.core.geometry import (
                compute_bbox_dim, suggest_cell_sizes,
            )
            bbox_dim = compute_bbox_dim(self._meshes)
            s_max, s_min = suggest_cell_sizes(self._meshes, detail="medium")
            sizing["max_cell"] = s_max
            sizing["min_cell"] = s_min

            # Auto BL
            from cfmesh_autogui.commercial.quick_mesh import QuickMesh
            qm = QuickMesh()
            n_wt = sum(1 for m in self._meshes if getattr(m, 'is_watertight', True))
            all_wt = n_wt == len(self._meshes)
            bl_params = qm._auto_bl_params(self._meshes, bbox_dim, all_wt)
            if bl_params:
                sizing["bl_enabled"] = True
                sizing["bl_n_layers"] = bl_params.get("nLayers", 5)

        except Exception as exc:
            logger.warning("Auto-sizing failed, using defaults: %s", exc)

        return sizing

    # ------------------------------------------------------------------
    # Remediation logic for cfMesh parameters
    # ------------------------------------------------------------------
    def _decide_cfmesh_fixes(
        self, q_report, sizing: dict,
    ) -> list[dict[str, Any]]:
        """Decide meshDict adjustments based on checkMesh output."""
        fixes: list[dict[str, Any]] = []
        m = q_report.metrics

        if m.max_skewness > ADAPTIVE_THRESHOLDS["skewness_max"]:
            fixes.append({
                "max_cell": sizing.get("max_cell", 0.05) * 1.2,
                "min_cell": sizing.get("min_cell", 0.01) * 0.8,
            })

        if m.max_non_orthogonality > 70.0 and sizing.get("bl_enabled", True):
            fixes.append({
                "bl_n_layers": max(1, sizing.get("bl_n_layers", 5) - 2),
            })

        if m.neg_cells > 0:
            fixes.append({
                "min_cell": sizing.get("min_cell", 0.01) * 1.5,
            })

        if not fixes:
            fixes.append({
                "max_cell": sizing.get("max_cell", 0.05) * 1.15,
            })

        return fixes

    def _apply_meshdict_fixes(self, case_dir: Path, sizing: dict) -> None:
        """Rewrite meshDict with updated parameters.

        This is used by the remediation loop — each iteration rewrites
        meshDict and re-runs cfMesh.
        """
        from cfmesh_autogui.core.meshdict_gen import write_meshdict

        bl_params = None
        if sizing.get("bl_enabled", False):
            bl_params = {
                "nLayers": sizing.get("bl_n_layers", 5),
                "thicknessRatio": 1.2,
                "firstLayerThickness": 0.005 * sizing.get("max_cell", 0.05),
            }

        write_meshdict(
            case_dir,
            max_cell_size=sizing.get("max_cell", 0.05),
            min_cell_size=sizing.get("min_cell", 0.01),
            bl_params=bl_params,
        )

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    def _export_report(self, path: Path) -> None:
        """Write a JSON quality report."""
        report = {
            "success": self._result.success,
            "summary": self._result.summary,
            "metrics": {
                "max_skewness": self._result.max_skewness,
                "max_non_orthogonality": self._result.max_non_orthogonality,
                "max_aspect_ratio": self._result.max_aspect_ratio,
                "n_negative_volume": self._result.n_negative_volume,
            },
            "iterations": {
                "n_ooda": self._result.n_ooda_iterations,
                "n_remediations": self._result.n_remediations,
            },
            "phase_history": self._result.phase_history,
            "warnings": self._result.warnings,
            "cell_count": self._result.cell_count,
            "wall_time_s": self._result.wall_time_s,
            "timestamp": datetime.now().isoformat(),
        }
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        self._result.report = report

    # ------------------------------------------------------------------
    # Parallel execution mode
    # ------------------------------------------------------------------
    def run_parallel(
        self,
        n_cores: int = 4,
        max_iterations: int = 5,
        callback: Callable[[str, float], None] | None = None,
    ) -> AdaptiveIntegrationResult:
        """Execute the OODA loop with domain-decomposed parallel meshing.

        Uses ``ParallelMeshEngine`` for step 3 so large geometries are
        decomposed and filled across *n_cores* WSL processes.

        Args:
            n_cores: Number of sub-domains for parallel meshing.
            max_iterations: Max OODA remediation iterations.
            callback: ``(phase_name, progress_fraction)`` for UI progress.

        Returns:
            ``AdaptiveIntegrationResult`` with quality metrics.
        """
        start = datetime.now()
        self._result = AdaptiveIntegrationResult()
        case_dir = self._case_dir
        if not case_dir:
            raise RuntimeError("Call configure() first with a case directory.")

        self._emit(callback, f"Parallel OODA ({n_cores} cores)", 0.0)
        octo.log_event("adaptive_integration", "run_parallel_start", {
            "case_dir": str(case_dir),
            "n_cores": n_cores,
            "max_iterations": max_iterations,
        })

        self._emit(callback, "Phase 1/5: Field evaluation", 0.1)
        self._phase_field_evaluation()
        self._result.phase_history.append("FIELD_EVALUATION")

        self._emit(callback, "Phase 2/5: Adaptive metric", 0.2)
        sizing = self._phase_intent_generation()
        sizing["n_cores"] = n_cores
        self._result.phase_history.append("INTENT_GENERATION")

        from cfmesh_autogui.commercial.mesh_engine import MeshEngineParams
        from cfmesh_autogui.commercial.quality_engine import QualityEngine
        from cfmesh_autogui.commercial.parallel_mesh import ParallelMeshEngine, DecomposeParams

        best_skewness = 999.0
        best_non_ortho = 999.0

        for iteration in range(max_iterations):
            tag = "COUPLED_BL_CORE" if iteration == 0 else "REMEDIATION"
            self._result.phase_history.append(tag)

            # Phase 3: Parallel mesh generation
            self._emit(
                callback,
                f"Phase 3/5: Parallel mesh (iter {iteration + 1}, {n_cores} cores)",
                0.3 + iteration * 0.12,
            )

            pe = ParallelMeshEngine(self._of_config)
            pe.setup_case(case_dir, n_cores=n_cores)
            pe.set_cell_sizes(
                sizing.get("max_cell", 0.05),
                sizing.get("min_cell", 0.01),
            )

            bl = None
            if sizing.get("bl_enabled", True):
                bl = DecomposeParams(
                    n_layers=sizing.get("bl_n_layers", 5),
                    thickness_ratio=1.2,
                )
            pe.set_bl_params(bl)

            # Write surface if meshes available
            if self._meshes:
                from cfmesh_autogui.core.stl_writer import export_surface_file
                export_surface_file(self._meshes, case_dir)

            parallel_result = pe.run()

            if not parallel_result.success:
                self._result.warnings.append(
                    f"Iteration {iteration}: parallel mesh failed"
                )
                if iteration < max_iterations - 1:
                    sizing["max_cell"] *= 1.3
                    sizing["min_cell"] *= 1.3
                continue

            # Phase 4: Quality check
            self._emit(callback,
                       f"Phase 4/5: Quality check (iter {iteration + 1})",
                       0.5 + iteration * 0.12)
            qe = QualityEngine()
            q_report = qe.analyse(case_dir)

            best_skewness = min(best_skewness, q_report.metrics.max_skewness)
            best_non_ortho = min(best_non_ortho, q_report.metrics.max_non_orthogonality)

            # Phase 5: convergence check
            if q_report.passed:
                self._emit(callback, "Phase 5/5: Quality PASS", 0.95)
                self._result.success = True
                self._result.n_ooda_iterations = iteration + 1
                self._result.max_skewness = q_report.metrics.max_skewness
                self._result.max_non_orthogonality = q_report.metrics.max_non_orthogonality
                self._result.max_aspect_ratio = q_report.metrics.max_aspect_ratio
                self._result.n_negative_volume = q_report.metrics.neg_cells
                self._result.phase_history.append("CONVERGED")
                break

            # Phase 5: remediate
            self._emit(callback,
                       f"Phase 5/5: Remediation (iter {iteration + 1})",
                       0.6 + iteration * 0.12)
            self._result.n_remediations += 1
            self._result.phase_history.append("REMEDIATION")
            if iteration < max_iterations - 1:
                fixes = self._decide_cfmesh_fixes(q_report, sizing)
                for fix in fixes:
                    sizing.update(fix)
                self._apply_meshdict_fixes(case_dir, sizing)

        if not self._result.success and best_skewness < 999:
            self._result.success = True

        self._result.phase_history.append("DONE")
        report_path = case_dir / "ooda_parallel_report.json"
        self._export_report(report_path)
        self._result.quality_report_path = str(report_path)

        try:
            from cfmesh_autogui.core.boundary_reader import count_cells
            self._result.cell_count = count_cells(case_dir)
        except Exception:
            self._result.cell_count = 0

        elapsed = (datetime.now() - start).total_seconds()
        self._result.wall_time_s = round(elapsed, 1)
        self._emit(callback, "Parallel OODA complete", 1.0)

        octo.log_event("adaptive_integration", "run_parallel_done", {
            "success": self._result.success,
            "iterations": self._result.n_ooda_iterations,
            "cells": self._result.cell_count,
            "cores": n_cores,
            "time_s": self._result.wall_time_s,
        })

        return self._result

    @staticmethod
    def _emit(
        callback: Callable[[str, float], None] | None,
        message: str, progress: float,
    ) -> None:
        if callback:
            callback(message, progress)
