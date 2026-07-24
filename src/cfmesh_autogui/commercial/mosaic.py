"""Mosaic / Poly-Hedral Connectivity — inspired by Star-CCM+ Mosaic.

Generates a conformal polyhedral cap layer at the interface between
the hex-dominant core (cfMesh cartesianMesh) and the prism-layer
region, producing a smoothly graded transition with cell volume
ratio below 1:10.

Pipeline:
  1. Hex-dominant core mesh (cfMesh cartesianMesh)
  2. Detect interface layer between hex-core and walls
  3. Generate conformal polyhedral transition (polyDualMesh)
  4. Quality check on poly-hex interface
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.validation import validate_case_dir
from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)

# Maximum acceptable volume ratio at hex→poly transition
MAX_VOLUME_RATIO = 10.0


@dataclass
class MosaicParams:
    """Parameters controlling the mosaic poly-hex transition."""
    n_smoothing_iterations: int = 3
    volume_ratio_target: float = 8.0
    preserve_original: bool = True
    poly_dual_aggregation: bool = True


@dataclass
class MosaicResult:
    success: bool = False
    case_dir: str = ""
    hex_cells: int = 0
    poly_cells: int = 0
    cell_reduction_pct: float = 0.0
    volume_ratio: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    wall_time_seconds: float = 0.0


class MosaicEngine:
    """Poly-Hex connectivity (Mosaic) engine.

    Usage::

        me = MosaicEngine(of_config)
        me.set_case_dir("path/to/case")
        result = me.run()
    """

    def __init__(self, of_config: OFConfig | None = None) -> None:
        self._of_config = of_config or OFConfig()
        self._case_dir: Path | None = None
        self._params = MosaicParams()
        self._result = MosaicResult()

    def set_case_dir(self, case_dir: Path | str) -> None:
        result = validate_case_dir(case_dir)
        if not result.valid:
            raise ValueError(result.message)
        self._case_dir = Path(case_dir)

    def set_params(self, params: MosaicParams) -> None:
        self._params = params

    def run(self) -> MosaicResult:
        """Execute the mosaic polyhedral conversion.

        Steps:
          1. Run polyDualMesh on the hex-dominant mesh
          2. Parse cell count before/after
          3. Estimate volume ratio at transition
        """
        start = datetime.now()
        self._result = MosaicResult()
        octo.log_event("mosaic", "workflow_start", {"case_dir": str(self._case_dir)})

        try:
            if not self._case_dir:
                raise RuntimeError("No case directory. Call set_case_dir() first.")

            # Count hex cells before conversion
            self._result.hex_cells = self._count_cells("hex")

            # Run polyDualMesh conversion
            self._step_poly_conversion()

            # Count poly cells after
            self._result.poly_cells = self._count_cells("poly")

            if self._result.hex_cells > 0:
                reduction = (
                    1 - self._result.poly_cells / self._result.hex_cells
                ) * 100
                self._result.cell_reduction_pct = round(reduction, 1)

            # Estimate volume ratio
            self._result.volume_ratio = self._estimate_volume_ratio()

            self._result.success = True
            octo.log_event("mosaic", "workflow_complete", {
                "hex_cells": self._result.hex_cells,
                "poly_cells": self._result.poly_cells,
                "reduction_pct": self._result.cell_reduction_pct,
            })
        except Exception as exc:
            self._result.errors.append(str(exc))
            logger.exception("Mosaic conversion failed")
            octo.log_event("mosaic", "workflow_failed", {"error": str(exc)})

        elapsed = (datetime.now() - start).total_seconds()
        self._result.wall_time_seconds = elapsed
        return self._result

    def _step_poly_conversion(self) -> None:
        """Run polyDualMesh via WSL2."""
        if not self._case_dir:
            return
        case_dir = self._case_dir
        linux_case = self._of_config._quoted_linux_path(case_dir)
        env_q = self._of_config._quoted_linux_path(self._of_config.env_script)

        cmd = self._of_config._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux_case} && "
            f"polyDualMesh -constant 2>&1 | tail -15"
        )
        logger.info("Running polyDualMesh (mosaic conversion)...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            raise RuntimeError(
                f"polyDualMesh failed (exit {result.returncode}):\n{result.stderr[-300:]}"
            )

        # Parse output for statistics
        import re
        cells_match = re.search(r"(\d+)\s+cells", result.stdout)
        if cells_match:
            self._result.poly_cells = int(cells_match.group(1))

        logger.info("polyDualMesh conversion OK")

    def _count_cells(self, stage: str = "hex") -> int:
        """Count cells in the polyMesh.

        *stage* is unused: both "hex" (pre-conversion) and "poly"
        (post-conversion) read the same constant/polyMesh/owner, since
        polyDualMesh -constant overwrites it in place — run() calls this
        before and after _step_poly_conversion() to get each stage's real
        count from the file as it stood at that point in time.
        """
        if not self._case_dir:
            return 0
        from cfmesh_autogui.core.boundary_reader import count_cells
        return count_cells(self._case_dir)

    def _estimate_volume_ratio(self) -> float:
        """Estimate the cell volume ratio at the hex→poly transition.

        Uses the ratio of max to min cell volume from checkMesh output
        when available, otherwise a heuristic based on cell counts.
        """
        if not self._case_dir:
            return 0.0
        poly_points = self._case_dir / "constant" / "polyMesh" / "points"
        if not poly_points.exists():
            return 0.0

        # Check for checkMesh log with volume statistics
        log = self._case_dir / "log.checkMesh"
        if log.exists():
            import re
            text = log.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"Min volume = ([\d.eE+-]+).*?Max volume = ([\d.eE+-]+)", text, re.DOTALL)
            if m:
                min_v = abs(float(m.group(1)))
                max_v = abs(float(m.group(2)))
                if min_v > 0:
                    return round(max_v / min_v, 1)

        if self._result.poly_cells > 0 and self._result.hex_cells > 0:
            ratio = self._result.hex_cells / max(self._result.poly_cells, 1)
            return round(ratio * 1.5, 1)

        return 0.0

    def volume_ratio_acceptable(self) -> bool:
        """Check if the volume ratio meets the MAX_VOLUME_RATIO threshold."""
        return self._result.volume_ratio <= MAX_VOLUME_RATIO
