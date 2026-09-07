"""Fast local mesh quality check via pyvista + foamToVTK.

Reads the foamToVTK output and computes cell quality metrics using
VTK's Verdict library (via pyvista). Skewness, aspect ratio, and
scaled Jacobian are available without running checkMesh.

Usage::

    q = vtk_quality_metrics(case_dir)
    print(q["skew"]["max"], q["aspect_ratio"]["max"])
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class VTKQualityReport:
    cells: int = 0
    skew_max: float = 0.0
    skew_mean: float = 0.0
    aspect_ratio_max: float = 0.0
    aspect_ratio_mean: float = 0.0
    scaled_jacobian_min: float = 1.0
    non_ortho_estimate: float = 0.0
    min_angle_min: float = 90.0
    max_angle_max: float = 90.0


def vtk_quality_metrics(case_dir: Path) -> VTKQualityReport | None:
    """Compute mesh quality metrics from foamToVTK output using pyvista.

    Requires foamToVTK to have been run on the case first (produces
    ``case_dir/constant/polyMesh/*.vtk`` or ``case_dir/VTK/*.vtk``).

    Returns ``None`` if pyvista is not installed, no VTK files found,
    or parsing fails.
    """
    try:
        import pyvista as pv
        import numpy as np
    except ImportError:
        logger.warning("pyvista not installed — VTK quality check unavailable")
        return None

    # Look for foamToVTK output
    vtk_dir = case_dir / "VTK"
    if not vtk_dir.exists():
        vtk_dir = case_dir / "constant" / "polyMesh"
    vtk_files = list(vtk_dir.glob("**/*.vtk")) + list(vtk_dir.glob("**/*.vtu"))
    if not vtk_files:
        logger.debug("No VTK files found in %s", case_dir)
        return None

    # Try loading the first internal mesh VTK file (largest)
    vtk_files.sort(key=lambda p: p.stat().st_size, reverse=True)
    try:
        mesh = pv.read(str(vtk_files[0]))
    except Exception as exc:
        logger.debug("Failed to load VTK file: %s", exc)
        return None

    if mesh.n_cells == 0:
        return None

    report = VTKQualityReport(cells=mesh.n_cells)

    try:
        qual = mesh.cell_quality(quality_measure="all_valid")
        cd = qual.cell_data

        if "skew" in cd:
            arr = np.asarray(cd["skew"])
            valid = arr[arr >= 0]
            if len(valid):
                report.skew_max = float(valid.max())
                report.skew_mean = float(valid.mean())

        if "aspect_ratio" in cd:
            arr = np.asarray(cd["aspect_ratio"])
            valid = arr[arr >= 0]
            if len(valid):
                report.aspect_ratio_max = float(valid.max())
                report.aspect_ratio_mean = float(valid.mean())

        if "scaled_jacobian" in cd:
            arr = np.asarray(cd["scaled_jacobian"])
            valid = arr[arr >= 0]
            if len(valid):
                report.scaled_jacobian_min = float(valid.min())

        if "min_angle" in cd:
            arr = np.asarray(cd["min_angle"])
            valid = arr[arr >= 0]
            if len(valid):
                report.min_angle_min = float(valid.min())

        if "max_angle" in cd:
            arr = np.asarray(cd["max_angle"])
            valid = arr[arr >= 0]
            if len(valid):
                report.max_angle_max = float(valid.max())

    except Exception as exc:
        logger.debug("Quality computation failed: %s", exc)
        return None

    return report


def run_foam_to_vtk(case_dir: Path, of_config) -> bool:
    """Run foamToVTK via WSL. Returns True on success."""
    try:
        cmd = of_config.build_wsl_cmd(
            f"source {of_config.env_script_quoted} 2>/dev/null && "
            f"cd {of_config.wsl_linux_case_path(case_dir)} && "
            f"foamToVTK -constant 2>&1 | tail -5"
        )
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.returncode == 0
    except Exception as exc:
        logger.warning("foamToVTK failed: %s", exc)
        return False
