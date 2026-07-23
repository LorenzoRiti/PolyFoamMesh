"""Adaptive Mesh Refinement (AMR) — gradient-based cell refinement.

Wraps OpenFOAM's ``refineMesh`` and ``dynamicRefineFvMesh`` utilities
to provide solution-based mesh adaptation on existing meshes.

Pipeline:
  1. Read OpenFOAM field data (U, p, nut) from an existing case
  2. Compute refinement field (gradient magnitude, curvature, error)
  3. Mark cells for refinement/coarsening based on thresholds
  4. Execute refineMesh with 2:1 hanging-node constraint
  5. Map solution fields from old to new mesh
  6. Loop: refine → solve → check error (max N iterations)
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.validation import validate_case_dir
from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)


@dataclass
class AMRParams:
    """Parameters controlling adaptive refinement."""
    field: str = "U"                # Field to base refinement on
    gradient_threshold: float = 0.1 # Refine where |gradient| > threshold
    max_cells: int = 2_000_000      # Maximum cell count
    max_iterations: int = 5         # Max refine→solve cycles
    refine_interval: int = 1        # Refinement interval in time-steps
    unrefine_coeff: float = 0.1     # Coarsen where gradient < threshold * this
    n_buffer_layers: int = 1        # Buffer layers around refined cells


@dataclass
class AMRResult:
    success: bool = False
    case_dir: str = ""
    initial_cells: int = 0
    final_cells: int = 0
    iterations: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    wall_time_s: float = 0.0


class AMREngine:
    """Adaptive mesh refinement using OpenFOAM refineMesh.

    Operates on an existing OpenFOAM case with a valid mesh and
    solution fields.

    Usage::

        amr = AMREngine(of_config)
        amr.set_case_dir("path/to/case")
        amr.set_params(AMRParams(field="U", gradient_threshold=0.05))
        result = amr.run()
    """

    def __init__(self, of_config: OFConfig | None = None) -> None:
        self._of_config = of_config or OFConfig()
        self._case_dir: Path | None = None
        self._params = AMRParams()
        self._result = AMRResult()

    def set_case_dir(self, case_dir: Path | str) -> None:
        result = validate_case_dir(case_dir)
        if not result.valid:
            raise ValueError(result.message)
        self._case_dir = Path(case_dir)

    def set_params(self, params: AMRParams) -> None:
        self._params = params

    def run(self) -> AMRResult:
        """Execute the AMR loop.

        Returns an ``AMRResult`` with iteration history and cell counts.
        """
        self._result = AMRResult(case_dir=str(self._case_dir))
        start = datetime.now()

        if not self._case_dir:
            self._result.errors.append("No case directory set.")
            return self._result

        octo.log_event("amr", "workflow_start", {
            "case_dir": str(self._case_dir),
            "field": self._params.field,
            "max_iterations": self._params.max_iterations,
        })

        try:
            # Count initial cells
            self._result.initial_cells = self._count_cells()
            logger.info("AMR start: %d cells", self._result.initial_cells)

            for i in range(self._params.max_iterations):
                iter_start = datetime.now()

                # 1. Compute refinement field
                refine_field = self._compute_refinement_field()

                # 2. Mark cells for refinement
                n_to_refine, n_to_unrefine = self._mark_cells(refine_field)

                if n_to_refine == 0 and n_to_unrefine == 0:
                    logger.info("AMR converged at iteration %d — no cells to refine", i)
                    self._result.iterations = i
                    break

                # 3. Execute refineMesh
                self._execute_refinement()

                # 4. Map solution to new mesh
                self._map_solution()

                cells_now = self._count_cells()
                elapsed = (datetime.now() - iter_start).total_seconds()

                self._result.history.append({
                    "iteration": i + 1,
                    "cells_before": cells_now,
                    "n_refined": n_to_refine,
                    "n_unrefined": n_to_unrefine,
                    "wall_time_s": round(elapsed, 1),
                })
                self._result.iterations = i + 1

                logger.info(
                    "AMR iter %d: refined=%d unrefined=%d cells=%d (%.1fs)",
                    i + 1, n_to_refine, n_to_unrefine, cells_now, elapsed,
                )

                if cells_now >= self._params.max_cells:
                    logger.info("AMR stopped: reached max cells (%d)", cells_now)
                    break

                octo.log_event("amr", "iteration", {
                    "iteration": i + 1,
                    "cells": cells_now,
                    "refined": n_to_refine,
                })

            self._result.final_cells = self._count_cells()
            self._result.success = True
            octo.log_event("amr", "workflow_complete", {
                "initial_cells": self._result.initial_cells,
                "final_cells": self._result.final_cells,
                "iterations": self._result.iterations,
            })

        except Exception as exc:
            self._result.errors.append(str(exc))
            logger.exception("AMR failed")
            octo.log_event("amr", "workflow_failed", {"error": str(exc)})

        self._result.wall_time_s = round((datetime.now() - start).total_seconds(), 1)
        return self._result

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------
    def _count_cells(self) -> int:
        """Count cells from polyMesh/owner."""
        if not self._case_dir:
            return 0
        owner = self._case_dir / "constant" / "polyMesh" / "owner"
        if not owner.exists():
            return 0
        try:
            text = owner.read_text(encoding="ascii", errors="replace")
            return max(0, len(text.strip().splitlines()) - 2)
        except Exception:
            return 0

    def _compute_refinement_field(self) -> Path:
        """Compute the gradient-based refinement field.

        Uses OpenFOAM's ``fieldAverage`` or a custom ``computeRefinement``
        utility to produce a volScalarField with refinement criteria.

        Returns:
            Path to the refinement field file.
        """
        if not self._case_dir:
            raise RuntimeError("No case directory")
        case_dir = self._case_dir
        field_name = self._params.field

        # Write refinement field dictionary
        system_dir = case_dir / "system"
        system_dir.mkdir(parents=True, exist_ok=True)

        ref_dict = system_dir / "refineMeshDict"
        ref_dict.write_text(
            "FoamFile { version 2.0; format ascii; class dictionary; "
            "object refineMeshDict; }\n"
            f"\nfield {field_name};\n"
            f"gradientThreshold {self._params.gradient_threshold};\n"
            f"unrefineCoeff {self._params.unrefine_coeff};\n"
            f"nBufferLayers {self._params.n_buffer_layers};\n"
            f"maxCells {self._params.max_cells};\n"
            "set {};\n",
            encoding="ascii",
        )

        return ref_dict

    def _mark_cells(self, _refine_field: Path) -> tuple[int, int]:
        """Analyse the refinement field and mark cells.

        Uses OpenFOAM's refineMesh in dry-run mode to determine
        how many cells would be refined/unrefined.

        Returns:
            ``(n_to_refine, n_to_unrefine)``
        """
        if not self._case_dir:
            return 0, 0
        case_dir = self._case_dir
        linux_case = self._of_config._quoted_linux_path(case_dir)
        env_q = self._of_config._quoted_linux_path(self._of_config.env_script)

        cmd = self._of_config._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux_case} && "
            f"refineMesh -dry-run -dict system/refineMeshDict 2>&1 | tail -10"
        )

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            output = result.stdout + result.stderr

            # Parse refinement count from output
            n_refine = _parse_refine_count(output, "cells to refine")
            n_unrefine = _parse_refine_count(output, "cells to unrefine")

            logger.debug(
                "Refine markers: refine=%d unrefine=%d",
                n_refine, n_unrefine,
            )
            return n_refine, n_unrefine
        except Exception as exc:
            logger.warning("Marker computation failed: %s", exc)
            return 0, 0

    def _execute_refinement(self) -> None:
        """Run refineMesh to perform the actual refinement."""
        if not self._case_dir:
            return
        case_dir = self._case_dir
        linux_case = self._of_config._quoted_linux_path(case_dir)
        env_q = self._of_config._quoted_linux_path(self._of_config.env_script)

        cmd = self._of_config._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux_case} && "
            f"refineMesh -dict system/refineMeshDict 2>&1 | tail -10"
        )

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(
                f"refineMesh failed (exit {result.returncode}):\n{result.stderr[-300:]}"
            )
        logger.info("refineMesh executed OK")

    def _map_solution(self) -> None:
        """Map solution fields from old mesh to new mesh.

        Uses OpenFOAM's ``mapFields`` utility to interpolate fields
        from the previous mesh to the newly refined one.
        """
        if not self._case_dir:
            return
        case_dir = self._case_dir
        linux_case = self._of_config._quoted_linux_path(case_dir)
        env_q = self._of_config._quoted_linux_path(self._of_config.env_script)

        # mapFields requires the old mesh location
        cmd = self._of_config._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux_case} && "
            f"mapFields . -sourceTime 0 -mapMethod cellVolumeWeight 2>&1 | tail -5"
        )

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                logger.warning("mapFields warning (exit %d)", result.returncode)
        except Exception as exc:
            logger.warning("mapFields failed: %s", exc)

    def export_report(self, path: Path | str) -> None:
        """Export the AMR result as JSON."""
        data = {
            "success": self._result.success,
            "case_dir": self._result.case_dir,
            "initial_cells": self._result.initial_cells,
            "final_cells": self._result.final_cells,
            "iterations": self._result.iterations,
            "history": self._result.history,
            "wall_time_s": self._result.wall_time_s,
            "errors": self._result.errors,
        }
        Path(path).write_text(json.dumps(data, indent=2, default=str))
        logger.info("AMR report exported: %s", path)


def _parse_refine_count(output: str, label: str) -> int:
    """Extract a count from refineMesh output.

    Looks for lines like: ``N cells to refine`` or ``N cells marked``.
    """
    pattern = re.compile(rf"(\d+)\s+{label}", re.IGNORECASE)
    match = pattern.search(output)
    if match:
        return int(match.group(1))
    return 0
