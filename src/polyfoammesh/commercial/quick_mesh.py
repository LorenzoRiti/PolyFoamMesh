"""Quick Mesh — one click from geometry to quality-checked mesh.

Accepts a geometry file, auto-selects algorithm, cell sizes, and BL
parameters, generates the mesh, runs quality check, and returns results.

Pipeline:
  1. Input: geometry file path
  2. Auto: import → heal → feature extraction → classify patches
  3. Auto: select algorithm (MeshEngine.auto_select)
  4. Auto: cell sizes from curvature + thickness + bounding box
  5. Auto: BL with y+ target 30
  6. Generate mesh
  7. Quality check (checkMesh)
  8. Output: mesh in constant/polyMesh + quality report
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from polyfoammesh.core.validation import validate_geometry_path
from polyfoammesh.octopoda_local import octo

# Lazy imports (avoid OCP DLL chain)
_geometry = None; _stl_writer = None; _meshdict_gen = None
def _lazy_geom():
    global _geometry
    if _geometry is None:
        from polyfoammesh.core import geometry as _geometry
    return _geometry
def _lazy_stl():
    global _stl_writer
    if _stl_writer is None:
        from polyfoammesh.core import stl_writer as _stl_writer
    return _stl_writer
def _lazy_md():
    global _meshdict_gen
    if _meshdict_gen is None:
        from polyfoammesh.core import meshdict_gen as _meshdict_gen
    return _meshdict_gen

logger = logging.getLogger(__name__)


@dataclass
class QuickMeshResult:
    """Result of a quick mesh operation."""
    success: bool = False
    case_dir: str = ""
    algorithm: str = ""
    cell_count: int = 0
    max_skewness: float = 0.0
    quality_passed: bool = False
    n_cores: int = 1
    wall_time_s: float = 0.0
    algorithm_substituted: bool = False
    original_algorithm: str = ""
    escalation_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class QuickMesh:
    """One-click meshing — geometry in, mesh out.

    Usage::

        qm = QuickMesh()
        result = qm.run("model.step")
        if result.success:
            print(f"Mesh ready: {result.cell_count} cells")
            print(f"Quality: {'PASS' if result.quality_passed else 'FAIL'}")
    """

    def __init__(self) -> None:
        from polyfoammesh.config import OFConfig
        self._of_config = OFConfig()

    def run(
        self,
        geometry_path: str,
        output_dir: str | None = None,
        quality_target: str = "medium",
        n_cores: int = 1,
        poly_aggregate: bool = False,
    ) -> QuickMeshResult:
        """Execute quick mesh from geometry file.

        Args:
            geometry_path: Path to geometry file (.step/.stp/.stl).
            output_dir: Output case directory. Auto-generated if None.
            quality_target: ``"draft"``, ``"medium"``, or ``"high"``.
            n_cores: Number of parallel cores (1 = serial, >1 = MPI).
            poly_aggregate: Apply STAR-CCM+ style polyhedral aggregation
                after the initial mesh (3-5x fewer cells, better quality).

        Returns:
            ``QuickMeshResult`` with cell count and quality.
        """
        result = QuickMeshResult(n_cores=n_cores)
        start = datetime.now(UTC)
        octo.log_event("quick_mesh", "start", {"file": geometry_path, "n_cores": n_cores})

        try:
            # 1. Validate geometry
            val = validate_geometry_path(geometry_path)
            if not val.valid:
                raise ValueError(val.message)

            path = Path(geometry_path)
            ext = path.suffix.lower()

            # 2. Import geometry (using geometry_pipeline or direct)
            meshes = self._import_geometry(path, ext)
            if not meshes:
                raise RuntimeError(f"No geometry loaded from {path}")

            bbox_dim = _lazy_geom().compute_bbox_dim(meshes)
            logger.info("QuickMesh: %d patches, bbox=%.4f m", len(meshes), bbox_dim)

            # 3. Auto-select algorithm
            has_wsl = self._of_config.validate()
            n_wt = sum(1 for m in meshes if m.is_watertight)
            all_wt = n_wt == len(meshes)

            from polyfoammesh.commercial.mesh_engine import (
                MeshEngine,
                MeshEngineParams,
            )

            engine = MeshEngine()
            detail_map = {"draft": "coarse", "medium": "medium", "high": "fine"}
            detail = detail_map.get(quality_target, "medium")
            algo = engine.auto_select(
                patch_count=len(meshes), watertight=all_wt, has_wsl=has_wsl,
            )
            result.algorithm = algo.value

            # 4. Auto cell sizes (curvature + thickness + BB)
            s_max, s_min = _lazy_geom().suggest_cell_sizes(meshes, detail=detail)
            quality_mult = {"draft": 1.5, "medium": 1.0, "high": 0.6}
            s_max *= quality_mult.get(quality_target, 1.0)
            s_min *= quality_mult.get(quality_target, 1.0)

            # 5. Auto BL: y+ target 30
            bl_params = self._auto_bl_params(meshes, bbox_dim, all_wt)

            # 6. Setup case
            if output_dir:
                case_dir = Path(output_dir)
            else:
                ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
                case_dir = Path.home() / "cfmesh_cases" / f"quick_mesh_{ts}"
            case_dir.mkdir(parents=True, exist_ok=True)
            result.case_dir = str(case_dir)

            # 7. Export surface and write meshDict
            _lazy_stl().export_surface_file(meshes, case_dir)
            from polyfoammesh.core.openfoam_runner import generate_fms
            fms = generate_fms(case_dir, angle=60.0)
            surface_file = "constant/triSurface/surface.fms" if fms else "constant/triSurface/surface.stl"
            if fms:
                logger.info("QuickMesh: using FMS for feature-edge capture")
            # Progressive boundary refinement for smooth cell size
            # transition (Star-CCM+ style).  boundaryCellSize provides
            # an intermediate refinement level between max and min.
            # The refinement extends boundaryCellSizeRefinementThickness
            # from walls, then cells grow gradually to maxCellSize.
            # Use the geometric mean as the boundary cell size for
            # optimal gradation ratio (~2:1 max-to-boundary).
            bc_size = (s_max * s_min) ** 0.5  # geometric mean
            bc_thick = bbox_dim * 0.15  # 15% of bounding box
            _lazy_md().write_meshdict(
                case_dir, s_max, s_min, bl_params=bl_params,
                boundary_cell_size=bc_size,
                boundary_refinement_thickness=bc_thick,
                surface_file=surface_file,
                patch_names=[m.metadata.get("name", f"patch_{i}") for i, m in enumerate(meshes)],
            )
            _write_ctrl_dict(case_dir)

            # 8. Run meshing via engine (parallel when n_cores > 1)
            params = MeshEngineParams(
                algorithm=algo, detail_level=detail,
                max_cell=s_max, min_cell=s_min,
                bl_enabled=bl_params is not None,
                bl_n_layers=bl_params.get("nLayers", 5) if bl_params else 0,
                n_cores=n_cores,
            )
            engine.configure(params)
            eng_result = engine.run(case_dir, meshes, geometry_path)
            result.cell_count = eng_result.cell_count
            result.max_skewness = eng_result.max_skewness
            result.quality_passed = eng_result.quality_passed
            result.algorithm_substituted = eng_result.algorithm_substituted
            result.original_algorithm = eng_result.original_algorithm
            result.escalation_reason = eng_result.escalation_reason

            # 9. Polyhedral aggregation (Star-CCM+ style, optional)
            if poly_aggregate and eng_result.success:
                try:
                    from polyfoammesh.commercial.poly_aggregator import PolyAggregator
                    agg = PolyAggregator()
                    # 20 = ~2x cell reduction (was 5 = ~5-6x, at the cost
                    # of much worse skewness/non-orthogonality -- see
                    # AggregationParams.min_tets_per_cluster docstring).
                    agg.params.min_tets_per_cluster = 20
                    agg_result = agg.run(case_dir, geometry_path)
                    if agg_result.success:
                        result.cell_count = agg_result.cells_after
                        result.max_skewness = agg_result.max_skewness_after
                        result.algorithm = "PolyAggregated"
                        result.warnings.append(
                            f"Polyhedral aggregation: {agg_result.cells_before} -> "
                            f"{agg_result.cells_after} cells "
                            f"({agg_result.reduction_pct:.0f}% reduction)"
                        )
                        logger.info(
                            "QuickMesh: polyhedral aggregation OK "
                            "(%d cells, %.1f%% reduction)",
                            agg_result.cells_after, agg_result.reduction_pct,
                        )
                    else:
                        errs = "; ".join(agg_result.errors)
                        result.warnings.append(f"Polyhedral aggregation skipped: {errs}")
                except (OSError, ValueError, RuntimeError) as agg_exc:
                    result.warnings.append(f"Polyhedral aggregation failed: {agg_exc}")

            # 10. If engine ran a different algorithm (fallback), update
            if eng_result.algorithm != algo.value:
                result.algorithm = eng_result.algorithm
                result.warnings.append(f"Fallback: {eng_result.algorithm}")
            if eng_result.errors:
                result.errors.extend(eng_result.errors)

            result.success = eng_result.success
            result.n_cores = params.n_cores
            octo.log_event("quick_mesh", "complete", {
                "cells": result.cell_count,
                "quality": result.quality_passed,
                "algorithm": result.algorithm,
                "n_cores": result.n_cores,
                "algorithm_substituted": result.algorithm_substituted,
                "original_algorithm": result.original_algorithm,
            })

        except Exception as exc:
            result.errors.append(str(exc))
            logger.exception("QuickMesh failed")

        result.wall_time_s = round((datetime.now(UTC) - start).total_seconds(), 1)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _import_geometry(self, path: Path, ext: str) -> list:
        """Import geometry to trimesh meshes."""
        if ext in (".step", ".stp"):
            from polyfoammesh.core.geometry import (
                classify_faces,
                load_step,
                tessellate_patches,
            )
            shape = load_step(path)
            patches = classify_faces(shape)
            return tessellate_patches(patches)
        if ext == ".stl":
            from polyfoammesh.core.geometry import load_geometry
            return load_geometry(path)
        raise ValueError(f"Unsupported: {ext}")

    def _auto_bl_params(
        self, meshes: list, bbox_dim: float, all_wt: bool,
    ) -> dict | None:
        """Auto-calculate BL parameters using the physics-based BLEngine.

        Uses flat-plate correlation for first-layer height and layer count.
        Returns cfMesh-compatible BL parameters (nLayers, thicknessRatio).
        """
        if not all_wt or bbox_dim <= 0:
            return None

        try:
            from polyfoammesh.commercial.bl_engine import BLEngine, FlowConditions
            bl_engine = BLEngine()
            flow = FlowConditions.from_velocity(
                reference_velocity=1.0,
                reference_length=bbox_dim,
                turbulence_model="kOmegaSST",
            )
            blp = bl_engine.calculate_from_flow(flow, growth_rate=1.2)
            if blp.n_layers < 1:
                return None
            return {
                "nLayers": blp.n_layers,
                "thicknessRatio": blp.growth_rate,
                # Absolute first-layer height for the target y+; without this
                # cfMesh picks its own and the y+ target is never met.
                "firstLayerThickness": blp.first_layer_height,
            }
        except (ImportError, ValueError, KeyError) as _blexc:
            logger.debug("Auto BL skipped: %s", _blexc)

        return None


def _write_ctrl_dict(case_dir: Path) -> None:
    (case_dir / "system" / "controlDict").write_text(
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
