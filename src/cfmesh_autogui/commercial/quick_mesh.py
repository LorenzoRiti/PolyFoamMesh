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
from datetime import datetime
from pathlib import Path
from typing import Any

from cfmesh_autogui.core.validation import validate_geometry_path
from cfmesh_autogui.core.openfoam_runner import generate_fms
from cfmesh_autogui.octopoda_local import octo

# Lazy imports (avoid OCP DLL chain)
_geometry = None; _stl_writer = None; _meshdict_gen = None
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
def _lazy_md():
    global _meshdict_gen
    if _meshdict_gen is None:
        from cfmesh_autogui.core import meshdict_gen as _meshdict_gen
    return _meshdict_gen

logger = logging.getLogger(__name__)

# y+ target for automatic BL calculation (turbulent, kOmegaSST)
TARGET_YPLUS = 30.0


@dataclass
class QuickMeshResult:
    """Result of a quick mesh operation."""
    success: bool = False
    case_dir: str = ""
    algorithm: str = ""
    cell_count: int = 0
    max_skewness: float = 0.0
    quality_passed: bool = False
    wall_time_s: float = 0.0
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
        from cfmesh_autogui.config import OFConfig
        self._of_config = OFConfig()

    def run(
        self,
        geometry_path: str,
        output_dir: str | None = None,
        quality_target: str = "medium",
    ) -> QuickMeshResult:
        """Execute quick mesh from geometry file.

        Args:
            geometry_path: Path to geometry file (.step/.stp/.stl).
            output_dir: Output case directory. Auto-generated if None.
            quality_target: ``"draft"``, ``"medium"``, or ``"high"``.

        Returns:
            ``QuickMeshResult`` with cell count and quality.
        """
        result = QuickMeshResult()
        start = datetime.now()
        octo.log_event("quick_mesh", "start", {"file": geometry_path})

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

            from cfmesh_autogui.commercial.mesh_engine import (
                MeshEngine, MeshEngineParams, MeshingAlgorithm,
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
            # Scale by quality target
            quality_mult = {"draft": 1.5, "medium": 1.0, "high": 0.6}
            s_max *= quality_mult.get(quality_target, 1.0)
            s_min *= quality_mult.get(quality_target, 1.0)

            # 5. Auto BL: y+ target 30
            bl_params = self._auto_bl_params(meshes, bbox_dim, all_wt)

            # 6. Setup case
            if output_dir:
                case_dir = Path(output_dir)
            else:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                case_dir = Path.home() / "cfmesh_cases" / f"quick_mesh_{ts}"
            case_dir.mkdir(parents=True, exist_ok=True)
            result.case_dir = str(case_dir)

            # 7. Export surface and write meshDict
            _lazy_stl().export_surface_file(meshes, case_dir)
            # 7a. Generate FMS for feature-edge capture (best-effort)
            fms = generate_fms(case_dir, angle=60.0)
            surface_file = "constant/triSurface/surface.fms" if fms else "constant/triSurface/surface.stl"
            if fms:
                logger.info("QuickMesh: using FMS for feature-edge capture")
            _lazy_md().write_meshdict(
                case_dir, s_max, s_min, bl_params=bl_params,
                surface_file=surface_file,
            )
            _write_ctrl_dict(case_dir)

            # 8. Run meshing via engine
            params = MeshEngineParams(
                algorithm=algo, detail_level=detail,
                max_cell=s_max, min_cell=s_min,
                bl_enabled=bl_params is not None,
                bl_n_layers=bl_params.get("nLayers", 5) if bl_params else 0,
            )
            engine.configure(params)
            eng_result = engine.run(case_dir, meshes, geometry_path)
            result.cell_count = eng_result.cell_count
            result.max_skewness = eng_result.max_skewness
            result.quality_passed = eng_result.quality_passed

            # 9. If engine ran a different algorithm (fallback), update
            if eng_result.algorithm != algo.value:
                result.algorithm = eng_result.algorithm
                result.warnings.append(f"Fallback: {eng_result.algorithm}")

            result.success = True
            octo.log_event("quick_mesh", "complete", {
                "cells": result.cell_count,
                "quality": result.quality_passed,
                "algorithm": result.algorithm,
            })

        except Exception as exc:
            result.errors.append(str(exc))
            logger.exception("QuickMesh failed")

        result.wall_time_s = round((datetime.now() - start).total_seconds(), 1)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _import_geometry(self, path: Path, ext: str) -> list:
        """Import geometry to trimesh meshes."""
        if ext in (".step", ".stp"):
            from cfmesh_autogui.core.geometry import load_step, classify_faces, tessellate_patches
            shape = load_step(path)
            patches = classify_faces(shape)
            return tessellate_patches(patches)
        if ext == ".stl":
            from cfmesh_autogui.core.geometry import load_geometry
            return load_geometry(path)
        raise ValueError(f"Unsupported: {ext}")

    def _auto_bl_params(
        self, meshes: list, bbox_dim: float, all_wt: bool,
    ) -> dict | None:
        """Auto-calculate BL parameters with y+ target 30.

        Uses flat-plate correlation for first-layer height estimation.
        """
        if not all_wt or bbox_dim <= 0:
            return None

        # Estimate Reynolds number (assuming U=1 m/s, nu=1.5e-5)
        Re = 1.0 * bbox_dim / 1.5e-5
        if Re < 1000:
            return None  # Laminar — no BL needed

        # Flat-plate Cf: Cf = 0.027 / Re^(1/7)
        Cf = 0.027 / (Re ** (1.0 / 7.0))
        u_tau = 1.0 * (Cf / 2.0) ** 0.5

        if u_tau <= 0:
            return None

        first_layer = TARGET_YPLUS * 1.5e-5 / u_tau
        first_layer = max(first_layer, 1e-8)

        # Suggest layers based on ratio to cell size
        cell_size = bbox_dim / 50.0
        n_layers = min(max(int(cell_size / first_layer / 2), 1), 10)

        return {
            "nLayers": n_layers,
            "thicknessRatio": round(first_layer / max(cell_size, 1e-10), 6),
            "expansionRatio": 1.2,
        }


def _write_ctrl_dict(case_dir: Path) -> None:
    (case_dir / "system" / "controlDict").write_text(
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
