"""One-click Full Auto pipeline — geometry file to complete OpenFOAM case.

Orchestrates the entire P0 pipeline in sequence:
  1. Import & heal (geometry_pipeline)
  2. Auto-size + auto-algorithm (mesh_engine)
  3. Generate mesh (quick_mesh)
  4. Quality check + auto-fix (quality_engine)
  5. Boundary conditions (bc_editor)
  6. Solver setup (solver_setup / template_engine)
  7. Export report

Single input: geometry file path + quality target.
Single output: complete OpenFOAM case + quality report.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)


@dataclass
class FullAutoResult:
    """Result of a full auto pipeline run."""
    success: bool = False
    case_dir: str = ""
    geometry_file: str = ""
    quality_target: str = "medium"
    algorithm_used: str = ""
    cell_count: int = 0
    quality_passed: bool = False
    steps_completed: list[str] = field(default_factory=list)
    n_patches: int = 0
    bc_types: list[str] = field(default_factory=list)
    solver: str = ""
    report_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    wall_time_s: float = 0.0

    def summary(self) -> str:
        return (
            f"{'✅' if self.success else '❌'} "
            f"{self.cell_count:,} cells | "
            f"{self.n_patches} patches | "
            f"{'Q: PASS' if self.quality_passed else 'Q: FAIL'} | "
            f"{self.algorithm_used} | "
            f"{len(self.steps_completed)} steps | "
            f"{self.wall_time_s:.0f}s"
        )


class FullAutoPipeline:
    """End-to-end pipeline: geometry → mesh → BC → solver → report.

    Usage::

        pipeline = FullAutoPipeline()
        result = pipeline.run("model.step", quality_target="high")
        print(result.summary())
        print(f"Case at: {result.case_dir}")
    """

    STEP_IMPORT = "import_heal"
    STEP_MESH = "mesh"
    STEP_QUALITY = "quality_fix"
    STEP_BC = "bc"
    STEP_SOLVER = "solver"
    STEP_REPORT = "report"

    def __init__(self) -> None:
        self._result = FullAutoResult()
        from cfmesh_autogui.config import OFConfig
        self._of_config = OFConfig()

    def run(
        self,
        geometry_path: str,
        output_dir: str | None = None,
        quality_target: str = "medium",
        solver_template: str = "Internal Flow",
        auto_bc: bool = True,
    ) -> FullAutoResult:
        """Execute the full meshing pipeline.

        Args:
            geometry_path: Path to geometry file (.step/.stp/.stl).
            output_dir: Output case directory (auto-generated if None).
            quality_target: ``"draft"``, ``"medium"``, or ``"high"``.
            solver_template: Name of template to apply for solver setup.
            auto_bc: Auto-detect boundary condition types.

        Returns:
            ``FullAutoResult`` with full pipeline summary.
        """
        start = datetime.now()
        self._result = FullAutoResult(
            geometry_file=geometry_path,
            quality_target=quality_target,
        )
        octo.log_event("one_click", "start", {"file": geometry_path, "target": quality_target})

        try:
            # Step 1: Import + Heal + Features
            geo_pipeline = self._step_import_heal(geometry_path)
            if not geo_pipeline.success:
                raise RuntimeError("Import/heal failed: " + "; ".join(geo_pipeline.errors))
            meshes = geo_pipeline.meshes
            geo_info = geo_pipeline.geometry
            self._result.n_patches = geo_info.n_patches
            self._result.steps_completed.append(self.STEP_IMPORT)

            # Step 2: Generate mesh (quick_mesh)
            qm_result = self._step_mesh(
                geometry_path, meshes, quality_target, output_dir,
            )
            case_dir = qm_result.case_dir
            self._result.case_dir = case_dir
            self._result.cell_count = qm_result.cell_count
            self._result.algorithm_used = qm_result.algorithm
            self._result.quality_passed = qm_result.quality_passed
            self._result.warnings.extend(qm_result.warnings)
            self._result.steps_completed.append(self.STEP_MESH)

            if not qm_result.success:
                raise RuntimeError("Meshing failed: " + "; ".join(qm_result.errors))

            # Step 3: Quality check + auto-fix
            quality_report = self._step_quality_fix(case_dir)
            self._result.quality_passed = quality_report.passed
            self._result.steps_completed.append(self.STEP_QUALITY)

            # Step 4: Boundary conditions
            bc_patches = self._step_bc(case_dir, auto_bc)
            self._result.bc_types = list(set(p.bc_type for p in bc_patches))
            self._result.steps_completed.append(self.STEP_BC)

            # Step 5: Solver setup (via template)
            solver_files = self._step_solver(case_dir, solver_template)
            if solver_files:
                self._result.solver = solver_template
            self._result.steps_completed.append(self.STEP_SOLVER)

            # Step 6: Export report
            report_paths = self._step_report(case_dir, qm_result, quality_report)
            self._result.report_files = report_paths
            self._result.steps_completed.append(self.STEP_REPORT)

            self._result.success = True
            octo.log_event("one_click", "complete", {
                "cells": self._result.cell_count,
                "quality": self._result.quality_passed,
                "steps": len(self._result.steps_completed),
            })

        except Exception as exc:
            self._result.errors.append(str(exc))
            logger.exception("FullAuto pipeline failed")

        self._result.wall_time_s = round((datetime.now() - start).total_seconds(), 1)
        return self._result

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------
    def _step_import_heal(self, geometry_path: str):
        """Step 1: Import, heal, feature extraction."""
        from cfmesh_autogui.commercial.geometry_pipeline import GeometryPipeline
        gp = GeometryPipeline()
        result = gp.run(geometry_path, heal=True, extract_features=True, classify_patches=True)
        if not result.success:
            raise RuntimeError("; ".join(result.errors))
        logger.info("Import: %d patches, bbox=%s", result.geometry.n_patches, result.geometry.bbox)
        return result

    def _step_mesh(
        self, geometry_path: str, meshes: list,
        quality_target: str, output_dir: str | None,
    ):
        """Step 2: Generate mesh via QuickMesh."""
        from cfmesh_autogui.commercial.quick_mesh import QuickMesh
        qm = QuickMesh()
        result = qm.run(
            geometry_path,
            output_dir=output_dir,
            quality_target=quality_target,
        )
        if not result.success:
            raise RuntimeError("; ".join(result.errors))
        logger.info("Mesh: %d cells, algo=%s, quality=%s",
                     result.cell_count, result.algorithm, result.quality_passed)
        return result

    def _step_quality_fix(self, case_dir: str):
        """Step 3: Analyse quality and apply auto-fix if needed."""
        from cfmesh_autogui.commercial.quality_engine import QualityEngine
        qe = QualityEngine()
        report = qe.analyse(case_dir)
        if not report.passed:
            logger.info("Quality FAIL: %s - applying auto-fix...", report.status)
            report = qe.auto_fix(case_dir, report, max_iterations=3)
            if report.passed:
                logger.info("Auto-fix succeeded: quality now PASS")
            else:
                logger.warning("Auto-fix did not converge: %s", report.status)
        return report

    def _step_bc(self, case_dir: str, auto_bc: bool):
        """Step 4: Read boundary and auto-detect BC types."""
        from cfmesh_autogui.commercial.bc_editor import BCEditor
        editor = BCEditor()
        patches = editor.read_boundary(case_dir)
        if auto_bc and patches:
            changed = editor.auto_detect_types(patches)
            editor.export_fields(case_dir, patches)
            logger.info("BC: %d patches, %d auto-detected", len(patches), changed)
        return patches

    def _step_solver(self, case_dir: str, template_name: str):
        """Step 5: Apply solver template."""
        from cfmesh_autogui.commercial.template_engine import TemplateEngine
        te = TemplateEngine()
        tpl = te.get_template(template_name)
        if tpl is None:
            logger.warning("Template '%s' not found — using Internal Flow fallback", template_name)
            tpl = te.get_template("Internal Flow")
            if tpl is None:
                return []
        files = te.apply_template(tpl, case_dir)
        logger.info("Solver: %s (%d files)", tpl.metadata.solver, len(files))
        return files

    def _step_report(self, case_dir: str, mesh_result, quality_report):
        """Step 6: Export JSON report."""
        report_path = Path(case_dir) / "full_auto_report.json"
        data = {
            "success": True,
            "geometry": self._result.geometry_file,
            "target": self._result.quality_target,
            "cells": mesh_result.cell_count,
            "algorithm": mesh_result.algorithm,
            "quality_passed": quality_report.passed,
            "quality_metrics": {
                "max_skewness": quality_report.metrics.max_skewness,
                "max_non_orthogonality": quality_report.metrics.max_non_orthogonality,
                "max_aspect_ratio": quality_report.metrics.max_aspect_ratio,
                "neg_cells": quality_report.metrics.neg_cells,
            } if quality_report.metrics else {},
            "steps_completed": self._result.steps_completed,
            "patches": self._result.n_patches,
            "solver": self._result.solver,
            "wall_time_s": self._result.wall_time_s,
        }
        report_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

        # Also write a simple status file. summary() contains emoji, which
        # crashes under Windows' default cp1252 file encoding.
        status_path = Path(case_dir) / "full_auto_status.txt"
        status_path.write_text(self._result.summary() + "\n", encoding="utf-8")

        return [str(report_path), str(status_path)]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main_cli() -> None:
    """Headless CLI entry point: single geometry file to complete case.

    Usage::

        python -m cfmesh_autogui.commercial.one_click_run model.step \\
            --quality high --solver-template "Internal Flow"
    """
    import argparse
    import sys

    # summary() embeds emoji (see FullAutoResult.summary above); Windows'
    # console defaults to cp1252, which can't encode them and crashes print().
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="CFMesh-AutoGUI Full Auto (headless): geometry file to "
                     "complete OpenFOAM case, one command.",
    )
    parser.add_argument("geometry", help="Path to geometry file (.step/.stp/.stl)")
    parser.add_argument("--output-dir", default=None, help="Output case directory")
    parser.add_argument("--quality", choices=["draft", "medium", "high"],
                         default="medium", help="Quality target (default: medium)")
    parser.add_argument("--solver-template", default="Internal Flow",
                         help="Solver template name (default: Internal Flow)")
    parser.add_argument("--no-auto-bc", action="store_true",
                         help="Disable automatic boundary condition detection")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    pipeline = FullAutoPipeline()
    result = pipeline.run(
        args.geometry,
        output_dir=args.output_dir,
        quality_target=args.quality,
        solver_template=args.solver_template,
        auto_bc=not args.no_auto_bc,
    )

    print(f"\n{result.summary()}")
    print(f"Case: {result.case_dir}")
    if result.report_files:
        print(f"Report: {result.report_files[0]}")
    if result.warnings:
        for w in result.warnings:
            print(f"Warning: {w}")

    if not result.success:
        for err in result.errors:
            print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main_cli()
