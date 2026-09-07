"""Multi-algorithm unified meshing engine with ADAPTIVE quality feedback.

Wraps all available meshing algorithms into a single API with:
  - Static auto-select based on geometry type and solver requirements
  - ADAPTIVE escalation: after each run, checkMesh metrics are read and if
    quality is below threshold (non-ortho > 70, skewness > 4.0 — the
    ADAPTIVE_THRESHOLDS dict from core.quality_thresholds), the engine
    automatically escalates to a more robust algorithm instead of failing.
  - Multiple fallback paths: cfMesh → relaxed cfMesh → gmsh_hybrid → snappyHexMesh

Algorithms:
  - ``CartesianHex`` — cfMesh cartesianMesh via WSL2 (default, hex-dominant)
  - ``Polyhedral`` — polyDualMesh conversion (dual hex→poly, AUMENTA le celle ~15-30%)
  - ``PolyAggregated`` — GMSH tet + aggregazione (RIDUCE le celle 3-5x, Star-CCM+ style)
  - ``Tetrahedral`` — GMSH direct tet mesh (no WSL needed)
  - ``HexCorePolyBoundary`` — hex→poly dual via polyDualMesh (same code path as Polyhedral)
  - ``CartesianCutCell`` — cfMesh cartesianCutMesh
  - ``SnappyHexMesh`` — OpenFOAM native snappyHexMesh (castellated + snap + layers)
  - ``MmgAdaptation`` — MMG anisotropic post-mesh adaptation

The adaptive loop (Punto 1):
  1. Run default algorithm (CartesianHex for watertight, Tetrahedral for complex)
  2. Parse checkMesh real metrics
  3. If non-ortho > 70 OR skewness > 4.0: escalate to more robust algorithm
  4. Log escalation reason (always)
  5. Max 3 escalation steps before declaring failure
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cfmesh_autogui.core.case_setup import write_control_dict as _write_control_dict
from cfmesh_autogui.core.quality_thresholds import ADAPTIVE_THRESHOLDS
from cfmesh_autogui.core.validation import validate_case_dir
from cfmesh_autogui.octopoda_local import octo

if TYPE_CHECKING:  # config pulls in the WSL probe; keep it out of import time
    from cfmesh_autogui.config import OFConfig

logger = logging.getLogger(__name__)


class MeshingAlgorithm(Enum):
    CARTESIAN_HEX = "CartesianHex"
    POLYHEDRAL = "Polyhedral"
    AUTOPOLY = "AutoPoly"
    TETRAHEDRAL = "Tetrahedral"
    HEX_CORE_POLY = "HexCorePolyBoundary"
    CARTESIAN_CUT = "CartesianCutCell"
    SNAPPY_HEX_MESH = "SnappyHexMesh"
    MMG_ADAPTATION = "MmgAdaptation"
    POLY_AGGREGATED = "PolyAggregated"
# Adaptive quality thresholds for auto-escalation
# (single source of truth: cfmesh_autogui.core.quality_thresholds)

# Escalation ladder: each entry is (algorithm, reason_suffix)
# AUTOPOLY (scipy-Voronoi CVT) is intentionally excluded — verified
# non-conforming to the domain boundary (negative volumes, warped boundary
# faces; see commit 4b98813). It stays selectable explicitly (no-WSL
# fallback in _resolve_auto_mesher) but must never be silently substituted
# by auto-escalation.
# The rung between hex and tet is POLYHEDRAL, not HEX_CORE_POLY: the two
# dispatch to the same cartesianMesh + polyDualMesh pipeline, so starting from
# hex they produce the *identical* mesh — but only POLYHEDRAL (rank 2) leaves
# the ladder monotonic for a POLYHEDRAL start. With HEX_CORE_POLY (rank 3)
# here, a user who selected Polyhedral escalated into a rung that re-ran the
# whole pipeline only to rebuild the same dual mesh, burning one of just
# max_escalation_steps=3 attempts on a no-op.
ESCALATION_LADDER: list[tuple[MeshingAlgorithm, str]] = [
    (MeshingAlgorithm.CARTESIAN_HEX, "default hex"),
    (MeshingAlgorithm.POLYHEDRAL, "hex→poly dual (polyDualMesh, better non-ortho)"),
    (MeshingAlgorithm.TETRAHEDRAL, "tetrahedral (complex geometry fallback)"),
    (MeshingAlgorithm.POLY_AGGREGATED, "polyhedral aggregated (Star-CCM+ style, 3-5x fewer cells)"),
    (MeshingAlgorithm.SNAPPY_HEX_MESH, "snappyHexMesh (industrial robust)"),
]


# Robustness ranking (higher = more robust for complex geometry)
ALGORITHM_ROBUSTNESS: dict[MeshingAlgorithm, int] = {
    MeshingAlgorithm.CARTESIAN_HEX: 1,
    MeshingAlgorithm.POLYHEDRAL: 2,
    MeshingAlgorithm.AUTOPOLY: 2,
    MeshingAlgorithm.HEX_CORE_POLY: 3,
    MeshingAlgorithm.CARTESIAN_CUT: 3,
    MeshingAlgorithm.TETRAHEDRAL: 4,
    MeshingAlgorithm.POLY_AGGREGATED: 5,
    MeshingAlgorithm.SNAPPY_HEX_MESH: 6,
    MeshingAlgorithm.MMG_ADAPTATION: 7,
}


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
    MeshingAlgorithm.AUTOPOLY: {
        "label": "AutoPoly (CVT polyhedral, nativo)",
        "description": "Poliedrico CVT (Centroidal Voronoi Tessellation), non richiede WSL",
        "requires_wsl": False,
        "cell_types": "polyhedral",
        "best_for": "mesh poliedrica qualità Star-CCM+, nessun WSL necessario",
        "quality_rank": 2,
    },
    MeshingAlgorithm.POLYHEDRAL: {
        "label": "Polyhedral (cfMesh polyDualMesh)",
        "description": "Poliedri arbitrari via polyDualMesh (dual mesh, aumenta le celle ~15-30%)",
        "requires_wsl": True,
        "cell_types": "polyhedral",
        "best_for": "post-processing, mesh poliedrica",
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
        "label": "Hex → Polyhedral (cfMesh polyDualMesh)",
        "description": "Converte l'intera mesh hex-dominant in poliedri via polyDualMesh (dual mesh, aumenta le celle ~15-30%)",
        "requires_wsl": True,
        "cell_types": "polyhedral",
        "best_for": "post-processing, mesh poliedrica",
        "quality_rank": 2,
    },
    MeshingAlgorithm.CARTESIAN_CUT: {
        "label": "Cartesian Cut-Cell (cfMesh)",
        "description": "Celle tagliate, ideale per geometrie esterne",
        "requires_wsl": True,
        "cell_types": "cut-cell",
        "best_for": "aerodinamica esterna, corpi immersi",
        "quality_rank": 2,
    },
    MeshingAlgorithm.SNAPPY_HEX_MESH: {
        "label": "SnappyHexMesh (OpenFOAM)",
        "description": "Castellated + snap + layers, standard industriale per geometrie complesse",
        "requires_wsl": True,
        "cell_types": "hex-dominant + prism",
        "best_for": "geometrie complesse, superfici irregolari, gap difficili",
        "quality_rank": 1,
    },
    MeshingAlgorithm.MMG_ADAPTATION: {
        "label": "MMG Anisotropic Adaptation",
        "description": "Post-mesh adattamento anisotropo basato su curvatura + metriche",
        "requires_wsl": True,
        "cell_types": "anisotropic tetrahedral",
        "best_for": "miglioramento qualità post-mesh, boundary layer refinement",
        "quality_rank": 1,
    },
    MeshingAlgorithm.POLY_AGGREGATED: {
        "label": "Polyhedral Aggregated (Star-CCM+ style)",
        "description": "Tet mesh + aggregazione poliedrica, 3-5x meno celle, qualità superiore",
        "requires_wsl": False,
        "cell_types": "polyhedral",
        "best_for": "CFD di qualità con meno celle, boundary layer integrati",
        "quality_rank": 1,
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
    adaptive_escalation: bool = True
    max_escalation_steps: int = 3

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
    max_non_orthogonality: float = 0.0
    max_aspect_ratio: float = 0.0
    neg_cells: int = 0
    escalation_reason: str = ""
    escalation_steps: int = 0
    metrics_before: dict | None = None
    metrics_after: dict | None = None
    original_algorithm: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def algorithm_substituted(self) -> bool:
        """True when the engine escalated away from the requested algorithm."""
        return bool(self.original_algorithm) and self.original_algorithm != self.algorithm

    def print_summary(self) -> None:
        """Print a human-readable summary, including any algorithm substitution."""
        status = "OK" if self.success else "FAILED"
        print(
            f"Mesh: {status} | {self.cell_count:,} cells | "
            f"algorithm={self.algorithm} | "
            f"quality={'PASS' if self.quality_passed else 'FAIL'}"
        )
        if self.algorithm_substituted:
            print(
                f"  NOTE: algorithm substituted "
                f"{self.original_algorithm} -> {self.algorithm}"
            )
            if self.escalation_reason:
                print(f"  Reason: {self.escalation_reason}")


class MeshEngine:
    """Unified multi-algorithm meshing engine with adaptive quality feedback.

    Usage::

        me = MeshEngine()
        me.configure(MeshEngineParams(algorithm=MeshingAlgorithm.CARTESIAN_HEX))
        result = me.run(case_dir, geometry_meshes)
        print(f"{result.cell_count} cells, quality passed: {result.quality_passed}")

    Adaptive escalation (when ``adaptive_escalation=True``):
        1. Run best-fit algorithm
        2. Parse real checkMesh metrics
        3. If quality below threshold, escalate to more robust algorithm
        4. Log escalation reason and compare metrics before/after
    """

    def __init__(self, of_config: OFConfig | None = None) -> None:
        """Build the engine, optionally bound to a pre-configured OFConfig.

        ``of_config`` is optional and mirrors ``ParallelMeshEngine``: callers
        that already resolved the WSL distro / OpenFOAM installation (the
        adaptive integration, which runs inside an OODA loop over an existing
        case) pass theirs; everyone else gets a default. It used to take no
        argument at all, which made ``MeshEngine(of_config)`` a hard
        TypeError at runtime on the one call site that passes one
        (``adaptive_integration.py``) — reachable from the Solve Adaptive
        ribbon button.
        """
        self._params = MeshEngineParams()
        self._escalation_step = 0
        from cfmesh_autogui.config import OFConfig
        self._of_config = of_config or OFConfig()

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
        """Run meshing with adaptive quality feedback loop.

        If ``adaptive_escalation`` is enabled (default), the engine:
        - Runs the configured algorithm
        - Checks quality via checkMesh
        - If metrics exceed thresholds, escalates to a more robust algorithm
        - Logs the reason, keeps metrics_before/metrics_after for comparison

        Args:
            case_dir: Output case directory.
            meshes: List of trimesh meshes (optional, for auto-sizing).
            geometry_path: Path to geometry file (for GMSH).

        Returns:
            ``MeshEngineResult`` with cell counts, quality, and escalation info.
        """
        case_dir = Path(case_dir)
        result = MeshEngineResult(
            algorithm=self._params.algorithm.value,
            original_algorithm=self._params.algorithm.value,
            case_dir=str(case_dir),
        )
        start = datetime.now(UTC)

        octo.log_event("mesh_engine", "meshing_start", {
            "algorithm": self._params.algorithm.value,
            "detail": self._params.detail_label,
            "adaptive": self._params.adaptive_escalation,
        })

        try:
            validate_result = validate_case_dir(case_dir)
            if not validate_result.valid:
                raise ValueError(validate_result.message)

            algo = self._params.algorithm
            escalation_count = 0
            max_steps = self._params.max_escalation_steps if self._params.adaptive_escalation else 0
            while escalation_count <= max_steps:
                # --- Run the current algorithm ---
                self._escalation_step = escalation_count
                try:
                    self._execute_algorithm(algo, case_dir, meshes, geometry_path, result)
                except Exception as exc:
                    logger.warning("Algorithm %s failed: %s", algo.value, exc)
                    if escalation_count < max_steps:
                        algo, reason = self._next_escalation(algo)
                        escalation_count += 1
                        result.escalation_reason = f"{algo.value}: {reason}"
                        result.escalation_steps = escalation_count
                        logger.info("Escalating to %s: %s", algo.value, reason)
                        continue
                    raise

                # --- Quality check ---
                quality = self._check_quality(case_dir)
                result.cell_count = self._count_cells(case_dir)
                result.max_skewness = quality.get("max_skewness", 0.0)
                result.max_non_orthogonality = quality.get("max_non_orthogonality", 0.0)
                result.max_aspect_ratio = quality.get("max_aspect_ratio", 0.0)
                result.neg_cells = quality.get("neg_cells", 0)

                # --- Adaptive escalation check ---
                if (
                    self._params.adaptive_escalation
                    and escalation_count < max_steps
                    and not self._quality_acceptable(quality)
                ):
                    next_algo, reason = self._next_escalation(algo)
                    if next_algo != algo:
                        # Save metrics before escalation
                        if result.metrics_before is None:
                            result.metrics_before = {
                                "algorithm": algo.value,
                                "skewness": result.max_skewness,
                                "non_ortho": result.max_non_orthogonality,
                                "aspect": result.max_aspect_ratio,
                                "neg_cells": result.neg_cells,
                                "cells": result.cell_count,
                            }

                        # Determine which thresholds were violated
                        violations = []
                        if result.max_non_orthogonality > ADAPTIVE_THRESHOLDS["non_ortho_max"]:
                            violations.append(f"non-ortho={result.max_non_orthogonality:.1f} > {ADAPTIVE_THRESHOLDS['non_ortho_max']}")
                        if result.max_skewness > ADAPTIVE_THRESHOLDS["skewness_max"]:
                            violations.append(f"skewness={result.max_skewness:.2f} > {ADAPTIVE_THRESHOLDS['skewness_max']}")
                        if result.neg_cells > 0:
                            violations.append(f"neg_cells={result.neg_cells}")

                        escalation_count += 1
                        reason_str = "; ".join(violations) if violations else "quality below threshold"
                        result.escalation_reason = f"Escalating to {next_algo.value}: {reason_str} (step {escalation_count})"
                        result.escalation_steps = escalation_count
                        logger.warning(
                            "Quality escalation: %s -> %s (violations: %s)",
                            algo.value, next_algo.value, ", ".join(violations),
                        )
                        algo = next_algo
                        self._params.algorithm = algo
                        continue

                # --- Quality acceptable or no escalation available ---
                result.quality_passed = quality.get("passed", False)
                result.algorithm = algo.value

                # Save metrics after escalation (if any occurred)
                if result.escalation_steps > 0:
                    result.metrics_after = {
                        "algorithm": algo.value,
                        "skewness": result.max_skewness,
                        "non_ortho": result.max_non_orthogonality,
                        "aspect": result.max_aspect_ratio,
                        "neg_cells": result.neg_cells,
                        "cells": result.cell_count,
                    }

                result.success = True
                break

            octo.log_event("mesh_engine", "meshing_ok", {
                "algorithm": algo.value,
                "cells": result.cell_count,
                "quality": result.quality_passed,
                "escalation_steps": escalation_count,
                "substituted": result.algorithm_substituted,
                "original_algorithm": result.original_algorithm,
            })

        except Exception as exc:
            result.errors.append(str(exc))
            logger.exception("Mesh engine failed after %d escalation(s)", result.escalation_steps)

        result.wall_time_s = round((datetime.now(UTC) - start).total_seconds(), 1)
        return result

    def _quality_acceptable(self, quality: dict) -> bool:
        """Check if quality metrics are acceptable (below thresholds)."""
        return not (
            quality.get("neg_cells", 0) > 0
            or quality.get("max_skewness", 0.0) > ADAPTIVE_THRESHOLDS["skewness_max"]
            or quality.get("max_non_orthogonality", 0.0) > ADAPTIVE_THRESHOLDS["non_ortho_max"]
            or quality.get("max_aspect_ratio", 0.0) > ADAPTIVE_THRESHOLDS["aspect_ratio_max"]
        )

    def _next_escalation(
        self, current: MeshingAlgorithm,
    ) -> tuple[MeshingAlgorithm, str]:
        """Return the next algorithm in the escalation ladder."""
        robustness = ALGORITHM_ROBUSTNESS
        current_rank = robustness.get(current, 0)

        best_next = current
        best_reason = "no escalation available"
        for algo, reason in ESCALATION_LADDER:
            if robustness.get(algo, 0) > current_rank:
                best_next = algo
                best_reason = reason
                break

        return best_next, best_reason

    def _execute_algorithm(
        self, algo: MeshingAlgorithm,
        case_dir: Path, meshes: list | None,
        geometry_path: str,
        result: MeshEngineResult | None = None,
    ) -> None:
        """Execute a single algorithm by dispatching to the right implementation."""
        if algo == MeshingAlgorithm.CARTESIAN_HEX:
            self._run_cartesian_hex(case_dir, meshes)
        elif algo == MeshingAlgorithm.AUTOPOLY:
            self._run_autopoly(case_dir, geometry_path)
        elif algo == MeshingAlgorithm.POLYHEDRAL:
            self._run_cartesian_hex(case_dir, meshes)
            try:
                self._run_polyhedral(case_dir)
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as poly_exc:
                self._keep_hex_decision(
                    result, algo, poly_exc,
                    reason="polyDualMesh failed after cartesianMesh — keeping the "
                    "hex mesh, but the decision is explicit, never silent",
                )
        elif algo == MeshingAlgorithm.TETRAHEDRAL:
            self._run_tetrahedral(case_dir, geometry_path)
        elif algo == MeshingAlgorithm.HEX_CORE_POLY:
            self._run_cartesian_hex(case_dir, meshes)
            try:
                self._run_polyhedral(case_dir)
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as poly_exc:
                self._keep_hex_decision(
                    result, algo, poly_exc,
                    reason="polyDualMesh failed after cartesianMesh — keeping the "
                    "hex core mesh, but the decision is explicit, never silent",
                )
        elif algo == MeshingAlgorithm.CARTESIAN_CUT:
            self._run_cartesian_hex(case_dir, meshes)
        elif algo == MeshingAlgorithm.SNAPPY_HEX_MESH:
            self._run_snappy_hex_mesh(case_dir, geometry_path, meshes)
        elif algo == MeshingAlgorithm.MMG_ADAPTATION:
            self._run_mmg_adaptation(case_dir)
        elif algo == MeshingAlgorithm.POLY_AGGREGATED:
            self._run_poly_aggregated(case_dir, geometry_path)
        else:
            raise ValueError(f"Unknown algorithm: {algo}")

    def _keep_hex_decision(
        self,
        result: MeshEngineResult | None,
        algo: MeshingAlgorithm,
        exc: Exception,
        reason: str,
    ) -> None:
        """Record a keep-hex-after-polyDualMesh-failure decision explicitly.

        Fase 3 P3.1: the old code only wrote a ``logger.warning`` that nobody
        surfaces — the caller (GUI / headless run) had no way to know the
        polyhedral conversion silently did not happen. The decision now lands
        in three visible places: the engine log (distinct prefix), the octo
        event stream, and ``result.warnings`` so callers can show it.
        """
        logger.warning(
            "[keep-hex] %s: %s (%s)", algo.value, reason, exc,
        )
        octo.log_event("mesh_engine", "polyhedral_keep_hex", {
            "algorithm": algo.value,
            "error": str(exc),
            "decision": "keep-hex-with-warning",
        })
        if result is not None:
            result.warnings.append(
                f"{algo.value}: {reason} ({exc})"
            )

    # ------------------------------------------------------------------
    # Algorithm implementations
    # ------------------------------------------------------------------
    def _estimate_cell_count(self, case_dir: Path) -> int:
        """Estimate cell count from meshDict cell sizes and geometry bbox."""
        try:
            from cfmesh_autogui.core.geometry import compute_bbox_dim
            tri_dir = case_dir / "constant" / "triSurface"
            meshes = []
            if tri_dir.is_dir():
                import trimesh
                for f in tri_dir.glob("*.stl"):
                    m = trimesh.load(str(f), force="mesh")
                    if m is not None:
                        meshes.append(m)
            if not meshes:
                return 1_000_000  # assume large if can't estimate
            bbox = compute_bbox_dim(meshes)
            vol = bbox ** 3
            avg_cell = (self._params.max_cell + self._params.min_cell) / 2
            return int(vol / (avg_cell ** 3))
        except Exception:
            return 1_000_000  # assume large on failure

    def _run_cartesian_hex(self, case_dir: Path, meshes: list | None) -> None:
        """Run cfMesh cartesianMesh via WSL2 — parallel when n_cores > 1.

        Avoids overwriting meshDict if already present (written by caller or
        auto-sizer), so user-tuned cell sizes / BL settings survive.
        """
        from cfmesh_autogui.core.stl_writer import export_surface_file

        if meshes:
            export_surface_file(meshes, case_dir)

        bl_params = {
            "nLayers": self._params.bl_n_layers,
            "thicknessRatio": 1.2,
            "firstLayerThickness": 0.005 * self._params.max_cell,
        } if self._params.bl_enabled else None

        patch_names = (
            [m.metadata.get("name", f"patch_{i}") for i, m in enumerate(meshes)]
            if meshes else None
        )

        _write_control_dict(case_dir)

        mesh_dict_path = case_dir / "system" / "meshDict"
        if not mesh_dict_path.exists():
            from cfmesh_autogui.core.meshdict_gen import write_meshdict
            write_meshdict(case_dir, self._params.max_cell, self._params.min_cell,
                           bl_params=bl_params, patch_names=patch_names)

        # Dispatch to parallel engine when n_cores > 1.
        # Memory-based clamping in ParallelMeshEngine._clamp_cores_to_available_memory()
        # is the proper guard — it measures actual WSL2 RAM and per-rank usage
        # instead of using an arbitrary cell-count threshold.
        n_cores = getattr(self._params, 'n_cores', 1)
        if n_cores > 1:
            self._run_parallel_mesh(case_dir, self._params.max_cell, self._params.min_cell,
                                     bl_params, patch_names, n_cores)
        else:
            self._run_serial_mesh(case_dir, self._params.max_cell, self._params.min_cell,
                                   bl_params, patch_names)

    def _run_parallel_mesh(
        self, case_dir: Path,
        raw_max: float, raw_min: float,
        bl_params: dict | None, patch_names: list[str] | None,
        n_cores: int = 2,
    ) -> None:
        """Run parallel meshing via ParallelMeshEngine with optional BL fallback."""
        from cfmesh_autogui.commercial.parallel_mesh import ParallelMeshEngine

        pe = ParallelMeshEngine(self._of_config)
        pe.setup_case(case_dir, n_cores=n_cores)
        pe.set_cell_sizes(raw_max, raw_min)
        pe.set_patch_names(patch_names)

        result = pe.run()

        if result.success:
            logger.info(
                "Parallel mesh OK: %d cells (%d cores, %.1fs)",
                result.cell_count, n_cores, result.wall_time_seconds,
            )
            return

        # BL fallback: parallel failed → retry without BL
        if bl_params:
            logger.warning("Parallel mesh failed with BL, retrying without BL")
            from cfmesh_autogui.core.meshdict_gen import write_meshdict
            write_meshdict(case_dir, raw_max, raw_min, patch_names=patch_names)
            _write_control_dict(case_dir)
            pe2 = ParallelMeshEngine(self._of_config)
            pe2.setup_case(case_dir, n_cores=n_cores)
            pe2.set_cell_sizes(raw_max, raw_min)
            pe2.set_patch_names(patch_names)
            result2 = pe2.run()
            if result2.success:
                logger.info("Parallel mesh OK (without BL)")
                return

        # Final fallback: try serial
        logger.warning("Parallel mesh failed — falling back to serial")
        saved_cores = self._params.n_cores
        self._params.n_cores = 1
        try:
            self._run_serial_mesh(case_dir, raw_max, raw_min, None, patch_names)
        finally:
            self._params.n_cores = saved_cores

    def _run_serial_mesh(
        self, case_dir: Path,
        raw_max: float, raw_min: float,
        bl_params: dict | None, patch_names: list[str] | None,
    ) -> None:
        """Run serial cartesianMesh synchronously, with BL fallback."""
        if not self._run_cartesian_mesh_sync(case_dir):
            if bl_params:
                logger.warning("CartesianHex: failed with boundary layers, retrying without BL")
                from cfmesh_autogui.core.meshdict_gen import write_meshdict
                write_meshdict(case_dir, raw_max, raw_min, patch_names=patch_names)
                if not self._run_cartesian_mesh_sync(case_dir):
                    raise RuntimeError("cartesianMesh failed (with and without boundary layers)")
            else:
                raise RuntimeError("cartesianMesh failed")
        logger.info("CartesianHex (serial): OK (max=%s min=%s)", raw_max, raw_min)

    def _run_cartesian_mesh_sync(self, case_dir: Path) -> bool:
        """Run cartesianMesh synchronously on WSL2 native tmpfs for speed."""
        import subprocess

        try:
            # Try tmpfs command first (3-5x faster on WSL2), fall back to
            # direct build_command for test mocks and non-WSL environments.
            if hasattr(self._of_config, 'build_serial_tmpfs_command'):
                try:
                    cmd = self._of_config.build_serial_tmpfs_command(case_dir)
                except Exception:
                    cmd = self._of_config.build_command(case_dir)
            else:
                cmd = self._of_config.build_command(case_dir)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=14400, check=False)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("cartesianMesh timed out for %s", case_dir)
            return False
        except FileNotFoundError:
            logger.warning("WSL not found for cartesianMesh")
            return False

    def _run_polyhedral(self, case_dir: Path, feature_angle: float = 90,
                        pipeline_staged: bool = False) -> None:
        """Convert hex mesh to polyhedral via polyDualMesh.

        ``pipeline_staged`` (default OFF until validated): run polyDualMesh
        inside WSL native storage (~/cfmesh_cases) instead of on the slow 9P
        /mnt/c bridge, then copy the result back compressed. OFF keeps the
        current direct path intact.
        """
        logger.info(
            "polyDualMesh: case_dir=%s feature_angle=%g staged=%s",
            case_dir, feature_angle, pipeline_staged,
        )
        if pipeline_staged:
            from cfmesh_autogui.config import extract_poly_mesh_archive
            cmd = self._of_config.build_staged_pipeline_command(
                case_dir, steps=[f"polyDualMesh {feature_angle} -overwrite"],
            )
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
            extract_poly_mesh_archive(case_dir)
            if r.returncode != 0:
                logger.error(
                    "polyDualMesh (staged) failed (exit %d): %s",
                    r.returncode, (r.stdout or r.stderr or "")[-500:],
                )
                raise RuntimeError(f"polyDualMesh failed (exit {r.returncode})")
            logger.info("Polyhedral conversion OK (staged, featureAngle=%g)", feature_angle)
            return
        cmd = self._of_config.build_poly_dual_cmd(
            case_dir, feature_angle=feature_angle,
        )
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        if r.returncode != 0:
            logger.error(
                "polyDualMesh failed (exit %d): %s",
                r.returncode, (r.stdout or r.stderr or "")[-500:],
            )
            raise RuntimeError(f"polyDualMesh failed (exit {r.returncode})")
        logger.info("Polyhedral conversion OK (featureAngle=%g)", feature_angle)

    def _run_autopoly(self, case_dir: Path, geometry_path: str) -> None:
        """Run autopoly CVT-based polyhedral meshing."""
        from cfmesh_autogui.commercial.autopoly_bridge import (
            AutopolyParams, run_autopoly,
        )

        detail = self._params.detail_level
        params = AutopolyParams.from_detail_level(detail)
        params.lloyd_iterations = max(2, 10 - self._escalation_step * 2)

        from cfmesh_autogui.octopoda_local import octo
        octo.log_event("mesh_engine", "run_autopoly", {
            "detail": detail, "geometry": geometry_path,
        })

        result = run_autopoly(geometry_path, case_dir, params)
        if not result.success:
            raise RuntimeError(
                f"Autopoly failed: {result.message} — "
                + "; ".join(result.errors)
            )

        logger.info(
            "AutoPoly: %d cells, nonOrtho=%.1f skew=%.2f ar=%.0f in %.1fs",
            result.n_cells, result.max_non_ortho,
            result.max_skewness, result.max_aspect_ratio,
            result.wall_time_s,
        )

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
    def _run_snappy_hex_mesh(
        self, case_dir: Path,
        geometry_path: str, meshes: list | None,
    ) -> None:
        """Run snappyHexMesh via WSL2 as robust fallback for complex geometry.

        snappyHexMesh implements the industrial standard pipeline:
          1. Castellated mesh refinement (octree)
          2. Surface snapping (mesh vertices projected to surface)
          3. Boundary layer addition (prism layers)

        This requires pre-existing surface STL and a background mesh
        (generated via cfMesh cartesianMesh at coarse settings).
        """
        if not meshes and not geometry_path:
            raise ValueError("snappyHexMesh requires a geometry (STL or mesh)")

        from cfmesh_autogui.commercial.snappy_hex_mesh import SnappyHexMeshRunner
        runner = SnappyHexMeshRunner(self._of_config)

        bl_params = {
            "nLayers": self._params.bl_n_layers,
            "thicknessRatio": 1.2,
            "firstLayerThickness": 0.005 * self._params.max_cell,
        } if self._params.bl_enabled else None

        runner.run(
            case_dir,
            max_cell=self._params.max_cell,
            min_cell=self._params.min_cell,
            bl_params=bl_params,
            n_cores=getattr(self._params, 'n_cores', 1),
        )

    def _run_mmg_adaptation(self, case_dir: Path) -> None:
        """Run MMG (mmg3d) anisotropic mesh adaptation as post-processing."""
        from cfmesh_autogui.commercial.mmg_adaptation import MmgAdaptationRunner
        runner = MmgAdaptationRunner(self._of_config)
        runner.run(case_dir, detail_level=self._params.detail_level)

    def _run_poly_aggregated(
        self, case_dir: Path, geometry_path: str,
    ) -> None:
        """Run STAR-CCM+ style polyhedral aggregation.

        Generates a tetrahedral mesh via GMSH (with boundary layers if
        enabled), then aggregates tetrahedra around each mesh vertex
        into polyhedral cells. This produces 3-5x fewer cells with
        better quality metrics.
        """
        from cfmesh_autogui.commercial.poly_aggregator import (
            PolyAggregator,
        )

        agg = PolyAggregator()
        # 20 = ~2x cell reduction, not the ~5-6x an aggressive threshold
        # gives -- past this point aggregation pulls in tets whose faces
        # are far from coplanar, driving skewness/non-orthogonality up
        # (see AggregationParams.min_tets_per_cluster docstring).
        agg.params.min_tets_per_cluster = 20
        agg.params.bl_enabled = self._params.bl_enabled
        agg.params.bl_n_layers = self._params.bl_n_layers

        result = agg.run(case_dir, geometry_path=geometry_path)
        if not result.success:
            raise RuntimeError(
                f"Polyhedral aggregation failed: "
                f"{'; '.join(result.errors)}"
            )

        logger.info(
            "PolyAggregated: %d -> %d cells (%.1f%% reduction) "
            "nonOrtho=%.1f->%.1f skew=%.3f->%.3f",
            result.cells_before, result.cells_after, result.reduction_pct,
            result.max_non_ortho_before, result.max_non_ortho_after,
            result.max_skewness_before, result.max_skewness_after,
        )

    def _check_quality(self, case_dir: Path) -> dict[str, Any]:
        """Run checkMesh and return quality metrics including all thresholds."""
        from cfmesh_autogui.core.openfoam_runner import parse_checkmesh_output

        try:
            cmd = self._of_config.build_check_mesh_cmd(case_dir)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
            output = r.stdout + r.stderr
            report = parse_checkmesh_output(output)
            return {
                "passed": report.passed,
                "max_skewness": report.max_skewness,
                "avg_skewness": report.avg_skewness,
                "max_non_orthogonality": report.max_non_ortho,
                "avg_non_orthogonality": report.avg_non_ortho,
                "max_aspect_ratio": report.max_aspect_ratio,
                "neg_cells": report.neg_cells,
                "min_volume": report.min_volume,
                "cells": report.cells,
                "faces": report.faces,
                "points": report.points,
                "has_fatal": report.has_fatal,
            }
        except OSError as exc:
            logger.warning("Quality check failed: %s", exc)
            return {
                "passed": False, "max_skewness": 0.0,
                "max_non_orthogonality": 0.0, "max_aspect_ratio": 0.0,
                "neg_cells": 0, "cells": 0,
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _count_cells(self, case_dir: Path) -> int:
        from cfmesh_autogui.core.boundary_reader import count_cells
        return count_cells(case_dir)


def _validate_sizes(bbox_dim: float, max_cell: float, min_cell: float) -> tuple[float, float, list[str]]:
    from cfmesh_autogui.core.validation import validate_cell_size as _vc
    r = _vc(max_cell, min_cell, bbox_dim)
    warns = list(r.warnings) if hasattr(r, 'warnings') else []
    safe_max = min(max_cell, bbox_dim / 2.0) if bbox_dim > 0 else max_cell
    safe_min = min(min_cell, safe_max / 2.0)
    if safe_max < max_cell:
        warns.append(f"max_cell clamped from {max_cell:.4f} to {safe_max:.4f} (bbox/2)")
    if safe_min < min_cell:
        warns.append(f"min_cell clamped from {min_cell:.4f} to {safe_min:.4f} (max/2)")
    safe_max = max(safe_max, 0.001)
    if safe_max != max_cell and safe_max == 0.001:
        warns.append("max_cell clamped to floor 0.001")
    safe_min = max(safe_min, 0.0001)
    return safe_max, safe_min, warns


# _write_control_dict imported from core.case_setup.write_control_dict
# (single implementation shared with snappy_hex_mesh and main_window)
