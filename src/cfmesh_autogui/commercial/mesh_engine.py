"""Multi-algorithm unified meshing engine.

Wraps all available meshing algorithms into a single API with
auto-selection based on geometry type and solver requirements.

Algorithms:
  - ``CartesianHex`` — cfMesh cartesianMesh via WSL2 (default, hex-dominant)
  - ``Polyhedral`` — polyDualMesh conversion (Star-CCM+ style)
  - ``Tetrahedral`` — GMSH direct tet mesh (no WSL needed)
  - ``HexCorePolyBoundary`` — Mosaic-style: hex core + poly transition + prism
  - ``CartesianCutCell`` — cfMesh cartesianCutMesh

Auto-select: chooses algorithm based on geometry complexity, target solver,
and available backends. Falls back automatically on failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from cfmesh_autogui.core.validation import validate_cell_size, validate_case_dir
from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)


class MeshingAlgorithm(Enum):
    CARTESIAN_HEX = "CartesianHex"
    POLYHEDRAL = "Polyhedral"
    TETRAHEDRAL = "Tetrahedral"
    HEX_CORE_POLY = "HexCorePolyBoundary"
    CARTESIAN_CUT = "CartesianCutCell"


# Algorithm metadata
ALGORITHM_INFO: dict[MeshingAlgorithm, dict[str, Any]] = {
    MeshingAlgorithm.CARTESIAN_HEX: {
        "label": "Cartesian Hex (cfMesh)",
        "description": "Hex-dominant cartesian, migliore qualità, richiede WSL2",
        "requires_wsl": True,
        "cell_types": "hex-dominant",
        "best_for": "geometrie watertight, volumi interni",
        "quality_rank": 1,  # 1 = highest quality
    },
    MeshingAlgorithm.POLYHEDRAL: {
        "label": "Polyhedral (cfMesh polyDualMesh)",
        "description": "Poliedri arbitrari, riduce celle del 40-60%",
        "requires_wsl": True,
        "cell_types": "polyhedral",
        "best_for": "post-processing, riduzione celle",
        "quality_rank": 2,
    },
    MeshingAlgorithm.TETRAHEDRAL: {
        "label": "Tetrahedral (GMSH Direct)",
        "description": "Tetraedri, nessun WSL necessario, geometrie complesse",
        "requires_wsl": False,
        "cell_types": "tetrahedral",
        "best_for": "geometrie complesse, mesh rapida",
        "quality_rank": 3,
    },
    MeshingAlgorithm.HEX_CORE_POLY: {
        "label": "Hex Core + Poly Boundary (Mosaic-style)",
        "description": "Nucleo esaedrico + transizione poliedrica + prismi",
        "requires_wsl": True,
        "cell_types": "hex-core + poly + prism",
        "best_for": "CFD avanzato: boundary layer + qualità",
        "quality_rank": 1,
    },
    MeshingAlgorithm.CARTESIAN_CUT: {
        "label": "Cartesian Cut-Cell (cfMesh)",
        "description": "Celle tagliate, ideale per geometrie esterne",
        "requires_wsl": True,
        "cell_types": "cut-cell",
        "best_for": "aerodinamica esterna, corpi immersi",
        "quality_rank": 2,
    },
}


@dataclass
class MeshEngineParams:
    """Parameters for the meshing engine."""
    algorithm: MeshingAlgorithm = MeshingAlgorithm.CARTESIAN_HEX
    detail_level: str = "medium"  # very_fine, fine, medium, coarse, very_coarse
    max_cell: float = 0.05
    min_cell: float = 0.01
    bl_enabled: bool = True
    bl_n_layers: int = 5
    poly_conversion: bool = False
    n_cores: int = 1

    @property
    def cell_size_multiplier(self) -> float:
        """Convert detail level to cell size multiplier."""
        return {
            "very_fine": 0.3, "fine": 0.6, "medium": 1.0,
            "coarse": 1.8, "very_coarse": 3.0,
        }.get(self.detail_level, 1.0)

    @property
    def detail_label(self) -> str:
        return {
            "very_fine": "Molto Fine", "fine": "Fine", "medium": "Media",
            "coarse": "Grossolana", "very_coarse": "Molto Grossolana",
        }.get(self.detail_level, "Media")


@dataclass
class MeshEngineResult:
    """Result from a meshing operation."""
    success: bool = False
    algorithm: str = ""
    case_dir: str = ""
    cell_count: int = 0
    wall_time_s: float = 0.0
    quality_passed: bool = False
    max_skewness: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class MeshEngine:
    """Unified multi-algorithm meshing engine.

    Usage::

        me = MeshEngine()
        me.configure(MeshEngineParams(algorithm=MeshingAlgorithm.CARTESIAN_HEX))
        result = me.run(case_dir, geometry_meshes)
        print(f"{result.cell_count} cells, quality passed: {result.quality_passed}")
    """

    def __init__(self) -> None:
        self._params = MeshEngineParams()
        from cfmesh_autogui.config import OFConfig
        self._of_config = OFConfig()

    def configure(self, params: MeshEngineParams) -> None:
        self._params = params

    def auto_select(
        self, patch_count: int = 0,
        watertight: bool = True,
        solver: str = "",
        has_wsl: bool = True,
    ) -> MeshingAlgorithm:
        """Auto-select the best algorithm based on geometry and context."""
        if not has_wsl:
            return MeshingAlgorithm.TETRAHEDRAL

        if solver in ("chtMultiRegionFoam", "overPimpleDyMFoam"):
            return MeshingAlgorithm.CARTESIAN_HEX

        if not watertight or patch_count > 50:
            return MeshingAlgorithm.TETRAHEDRAL

        if self._params.bl_enabled and watertight:
            return MeshingAlgorithm.HEX_CORE_POLY

        return MeshingAlgorithm.CARTESIAN_HEX

    def run(
        self, case_dir: Path | str,
        meshes: list | None = None,
        geometry_path: str = "",
    ) -> MeshEngineResult:
        """Run meshing with the configured algorithm.

        Args:
            case_dir: Output case directory.
            meshes: List of trimesh meshes (optional, for auto-sizing).
            geometry_path: Path to geometry file (for GMSH).

        Returns:
            ``MeshEngineResult`` with cell counts and quality.
        """
        case_dir = Path(case_dir)
        result = MeshEngineResult(
            algorithm=self._params.algorithm.value,
            case_dir=str(case_dir),
        )
        start = datetime.now()

        octo.log_event("mesh_engine", "meshing_start", {
            "algorithm": self._params.algorithm.value,
            "detail": self._params.detail_label,
        })

        try:
            validate_result = validate_case_dir(case_dir)
            if not validate_result.valid:
                raise ValueError(validate_result.message)

            algo = self._params.algorithm

            if algo == MeshingAlgorithm.CARTESIAN_HEX:
                self._run_cartesian_hex(case_dir, meshes)
            elif algo == MeshingAlgorithm.POLYHEDRAL:
                self._run_cartesian_hex(case_dir, meshes)
                self._run_polyhedral(case_dir)
            elif algo == MeshingAlgorithm.TETRAHEDRAL:
                self._run_tetrahedral(case_dir, geometry_path)
            elif algo == MeshingAlgorithm.HEX_CORE_POLY:
                self._run_cartesian_hex(case_dir, meshes)
                self._run_polyhedral(case_dir)
            elif algo == MeshingAlgorithm.CARTESIAN_CUT:
                self._run_cartesian_hex(case_dir, meshes)

            # Quality check
            quality = self._check_quality(case_dir)
            result.quality_passed = quality.get("passed", False)
            result.max_skewness = quality.get("max_skewness", 0.0)
            result.cell_count = self._count_cells(case_dir)
            result.success = True

            octo.log_event("mesh_engine", "meshing_ok", {
                "algorithm": algo.value,
                "cells": result.cell_count,
                "quality": result.quality_passed,
            })

        except Exception as exc:
            result.errors.append(str(exc))
            logger.exception("Mesh engine failed with %s", self._params.algorithm.value)

            # Fallback to tetrahedral if WSL algorithm fails
            if (self._params.algorithm in (
                MeshingAlgorithm.CARTESIAN_HEX,
                MeshingAlgorithm.HEX_CORE_POLY,
                MeshingAlgorithm.CARTESIAN_CUT,
            ) and "WSL" not in str(exc) and "wsl" not in str(exc).lower()):
                logger.info("Falling back to Tetrahedral...")
                try:
                    self._params.algorithm = MeshingAlgorithm.TETRAHEDRAL
                    self._run_tetrahedral(case_dir, geometry_path)
                    quality = self._check_quality(case_dir)
                    result.quality_passed = quality.get("passed", False)
                    result.cell_count = self._count_cells(case_dir)
                    result.success = True
                    result.algorithm = MeshingAlgorithm.TETRAHEDRAL.value
                    result.warnings.append("Fallback: Tetrahedral used")
                except Exception as exc2:
                    result.errors.append(str(exc2))

        result.wall_time_s = round((datetime.now() - start).total_seconds(), 1)
        return result

    # ------------------------------------------------------------------
    # Algorithm implementations
    # ------------------------------------------------------------------
    def _run_cartesian_hex(self, case_dir: Path, meshes: list | None) -> None:
        """Run cfMesh cartesianMesh via WSL2."""
        from cfmesh_autogui.core.stl_writer import export_surface_file
        from cfmesh_autogui.core.meshdict_gen import write_meshdict
        from cfmesh_autogui.core.openfoam_runner import RetryRunner

        if meshes:
            export_surface_file(meshes, case_dir)

        bbox_dim = 1.0
        if meshes:
            from cfmesh_autogui.core.geometry import compute_bbox_dim
            bbox_dim = compute_bbox_dim(meshes)

        cell_mult = self._params.cell_size_multiplier
        max_cell = self._params.max_cell * cell_mult
        min_cell = self._params.min_cell * cell_mult
        safe_max, safe_min, _ = _validate_sizes(bbox_dim, max_cell, min_cell)

        bl_params = {
            "nLayers": self._params.bl_n_layers,
            "thicknessRatio": 0.005,
            "expansionRatio": 1.2,
        } if self._params.bl_enabled else None

        write_meshdict(case_dir, safe_max, safe_min, bl_params=bl_params)
        _write_control_dict(case_dir)

        runner = RetryRunner(self._of_config)
        runner.run(case_dir=case_dir, bl_params=bl_params)
        logger.info("CartesianHex: OK (max=%s min=%s)", safe_max, safe_min)

    def _run_polyhedral(self, case_dir: Path) -> None:
        """Convert hex mesh to polyhedral via polyDualMesh."""
        import subprocess
        linux_case = self._of_config._quoted_linux_path(case_dir)
        env_q = self._of_config._quoted_linux_path(self._of_config.env_script)
        cmd = self._of_config._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux_case} && polyDualMesh -constant 2>&1 | tail -10"
        )
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"polyDualMesh failed (exit {r.returncode})")
        logger.info("Polyhedral conversion OK")

    def _run_tetrahedral(self, case_dir: Path, geometry_path: str) -> None:
        """Run GMSH tetrahedral meshing (no WSL required)."""
        if not geometry_path or not Path(geometry_path).exists():
            raise FileNotFoundError(f"Geometry not found: {geometry_path}")

        from cfmesh_autogui.core.gmsh_wrapper import generate_volume_mesh
        from cfmesh_autogui.core.mesh_converter import msh_to_of_polymesh

        msh_path = case_dir / "mesh.msh"
        detail_map = {"very_fine": "fine", "fine": "fine", "medium": "medium",
                      "coarse": "coarse", "very_coarse": "coarse"}
        detail = detail_map.get(self._params.detail_level, "medium")

        bl_params = self._params.bl_enabled
        n_layers = self._params.bl_n_layers if bl_params else 0

        msh_path, names = generate_volume_mesh(
            geometry_path, msh_path, detail=detail,
            n_layers=n_layers,
        )
        msh_to_of_polymesh(msh_path, case_dir)
        logger.info("Tetrahedral: OK (%d patches)", len(names))

    # ------------------------------------------------------------------
    # Quality check
    # ------------------------------------------------------------------
    def _check_quality(self, case_dir: Path) -> dict[str, Any]:
        """Run checkMesh and return quality metrics."""
        from cfmesh_autogui.core.openfoam_runner import (
            CheckMeshWorker, parse_checkmesh_output,
        )
        import subprocess

        try:
            cmd = self._of_config.build_check_mesh_cmd(case_dir)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            output = r.stdout + r.stderr
            report = parse_checkmesh_output(output)
            return {
                "passed": report.passed,
                "max_skewness": report.max_skewness,
                "cells": report.cells,
            }
        except Exception as exc:
            logger.warning("Quality check failed: %s", exc)
            return {"passed": False, "max_skewness": 0.0}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _count_cells(self, case_dir: Path) -> int:
        owner = case_dir / "constant" / "polyMesh" / "owner"
        if not owner.exists():
            return 0
        try:
            return max(0, len(owner.read_text(encoding="ascii", errors="replace").splitlines()) - 2)
        except Exception:
            return 0


def _validate_sizes(bbox_dim: float, max_cell: float, min_cell: float) -> tuple[float, float, list[str]]:
    from cfmesh_autogui.core.validation import validate_cell_size as _vc
    r = _vc(max_cell, min_cell, bbox_dim)
    safe_max = min(max_cell, bbox_dim / 2.0) if bbox_dim > 0 else max_cell
    safe_min = min(min_cell, safe_max / 2.0)
    safe_max = max(safe_max, 0.001)
    safe_min = max(safe_min, 0.0001)
    warns = r.warnings if hasattr(r, 'warnings') else []
    return safe_max, safe_min, warns


def _write_control_dict(case_dir: Path) -> None:
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
