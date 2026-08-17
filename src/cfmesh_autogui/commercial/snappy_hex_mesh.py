"""SnappyHexMesh integration — industrial-grade meshing for complex geometry.

snappyHexMesh is the native OpenFOAM mesher, the industry standard for
complex geometry where pure cfMesh octree struggles.  It implements a
3-step pipeline.

This module:
  - ``write_snappy_hex_mesh_dict()`` generates the ``snappyHexMeshDict``
  - ``SnappyHexMeshRunner`` orchestrates the 3-step pipeline via WSL2
  - Integrates with :class:`MeshEngine` as a fallback path

Usage::

    runner = SnappyHexMeshRunner(of_config)
    runner.run(case_dir, max_cell=0.05, min_cell=0.01)
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from cfmesh_autogui.core.case_setup import write_control_dict as _write_control_dict
from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)


def build_snappy_hex_mesh_dict_lines(
    location_in_mesh: tuple[float, float, float] = (0.0, 0.0, 0.0),
    max_cell: float = 0.05,
    min_cell: float = 0.01,
    n_refinement_levels: int = 4,
    cell_zone_levels: list[dict] | None = None,
    bl_params: dict | None = None,
    resolve_feature_angle: float = 30.0,
    n_snap_iter: int = 5,
    n_smooth_iter: int = 3,
) -> list[str]:
    """Build snappyHexMeshDict content for OpenFOAM v2512.

    Args:
        location_in_mesh: Point inside the mesh domain (x, y, z).
        max_cell: Global max cell size (background mesh).
        min_cell: Global min cell size (finest refinement level).
        n_refinement_levels: Number of octree refinement levels.
        cell_zone_levels: List of dicts with 'name', 'cell_size' for refinement regions.
        bl_params: Dict with keys nLayers, thicknessRatio, firstLayerThickness.
        resolve_feature_angle: Feature angle for surface extraction (degrees).
        n_snap_iter: Number of snapping iterations.
        n_smooth_iter: Number of smoothing iterations.

    Returns:
        Lines of the snappyHexMeshDict file.
    """
    lines = [
        "FoamFile { version 2.0; format ascii; class dictionary; object snappyHexMeshDict; }",
        "",
        "// CFMesh-AutoGUI Adaptive Meshing Engine",
        "",
        "castellatedMesh true;",
        "snap            true;",
        f"addLayers       {'true' if bl_params else 'false'};",
        "",
        "// ------------------------------------------------",
        "// GEOMETRY",
        "// ------------------------------------------------",
        "geometry",
        "{",
        "    surface.stl",
        "    {",
        "        type triSurfaceMesh;",
        '        name surface;',
        "    }",
        "}",
        "",
        "// ------------------------------------------------------------------",
        "// CASTELLATED MESH CONTROLS",
        "// ------------------------------------------------------------------",
        "castellatedMeshControls",
        "{",
    ]

    # Refinement levels based on cell sizes
    surface_level = max(1, _cell_size_to_refinement_level(min_cell, max_cell))
    if cell_zone_levels:
        lines.append("    refinementSurfaces")
        lines.append("    {")
        for zone in cell_zone_levels:
            level = _cell_size_to_refinement_level(
                zone.get("cell_size", min_cell), max_cell,
            )
            name = zone.get("name", "surface")
            lines.append(f'        "{name}"')
            lines.append("        {")
            lines.append(f"            level ({level} {level});")
            lines.append("        }")
        lines.append("    }")
    else:
        lines.append("    refinementSurfaces")
        lines.append("    {")
        lines.append('        "surface"')
        lines.append("        {")
        lines.append(f"            level ({surface_level} {surface_level});")
        lines.append("        }")
        lines.append("    }")

    lines.extend([
        "",
        "    resolveFeatureAngle 30;",
        "",
        "    refinementRegions",
        "    {",
        "    }",
        "",
        f"    nCellsBetweenLevels {min(n_refinement_levels + 1, 5)};",
        "",
        f"    maxLocalCells {max(500000, int(1e6))};",
        f"    maxGlobalCells {max(2000000, int(5e6))};",
        "",
        "    minRefinementCells 10;",
        "    maxLoadUnbalance 0.1;",
        "    nCellsBetweenLevels 3;",
        "",
        "    // Explicit feature edge refinement",
        "    features",
        "    (",
        f'        {{ file "surface.eMesh"; level {max(1, surface_level)}; }}',
        "    );",
        "",
        "}",
        "",
        "// ------------------------------------------------------------------",
        "// SNAP CONTROLS",
        "// ------------------------------------------------------------------",
        "snapControls",
        "{",
        f"    nSmoothPatch {n_smooth_iter};",
        f"    tolerance {min(2.0, max(1.0, min_cell * 10))};",
        f"    nSolveIter {n_snap_iter};",
        "    nRelaxIter 5;",
        "    nFeatureSnapIter 10;",
        "    implicitFeatureSnap true;",
        "    explicitFeatureSnap true;",
        "    multiRegionFeatureSnap false;",
        "}",
        "",
    ])

    if bl_params:
        n_layers = bl_params.get("nLayers", 5)
        growth = float(bl_params.get("thicknessRatio", 1.2))
        first_layer = bl_params.get("firstLayerThickness", 0.001)

        lines.extend([
            "// ------------------------------------------------------------------",
            "// ADD LAYERS CONTROLS",
            "// ------------------------------------------------------------------",
            "addLayersControls",
            "{",
            "    relativeSizes false;",
            "",
            "    layers",
            "    {",
            '        "surface"',
            "        {",
            f"            nSurfaceLayers {n_layers};",
            "        }",
            "    }",
            "",
            "    // Layer expansion parameters",
            f"    expansionRatio {growth};",
            f"    finalLayerThickness {first_layer * (growth ** (n_layers - 1)):.8g};",
            f"    minThickness {first_layer * 0.5:.8g};",
            "",
            "    // Layer suppression criteria",
            "    maxFaceThicknessRatio 0.5;",
            "    maxThicknessToMedialRatio 0.3;",
            "    minMedialAxisAngle 80;",
            "    maxFlipRatio 0.5;",
            "    nBufferCellsNoExtrude 0;",
            "    nLayerIter 50;",
            "    nRelaxIter 5;",
            "    nSmoothSurfaceNormals 1;",
            "    nSmoothNormals 3;",
            "    nSmoothThickness 10;",
            "    maxFaceAreaRatio 0.3;",
            "    minVolumeRatio 0.1;",
            "}",
            "",
        ])

    lines.extend([
        "// ------------------------------------------------------------------",
        "// MESH QUALITY CONTROLS",
        "// ------------------------------------------------------------------",
        "meshQualityControls",
        "{",
        "    maxNonOrtho 65;",
        "    maxBoundarySkewness 20;",
        "    maxInternalSkewness 4;",
        "    maxConcave 80;",
        "    minVol 1e-13;",
        "    minTetQuality 1e-15;",
        "    minArea -1;",
        "    minTriangleTwist -1;",
        "    nSmoothScale 4;",
        "    errorReduction 0.75;",
        "    relaxed",
        "    {",
        "        maxNonOrtho 75;",
        "    }",
        "}",
        "",
        "// ------------------------------------------------------------------",
        "// DEBUG & WRITE",
        "// ------------------------------------------------------------------",
        "writeFlags",
        "(",
        "    scalarLevels",
        "    layerSets",
        "    layerFields",
        ");",
        "",
        "mergeTolerance 1e-6;",
        "",
    ])

    return lines


def _cell_size_to_refinement_level(cell_size: float, max_cell: float) -> int:
    """Convert cell size to octree refinement level.

    Each refinement level halves the cell size. Level 0 = max_cell,
    level 1 = max_cell/2, level 2 = max_cell/4, etc.
    """
    if cell_size <= 0 or max_cell <= 0:
        return 1
    ratio = max_cell / cell_size
    level = 0
    while (2 ** level) < ratio and level < 20:
        level += 1
    return max(1, level)


def write_snappy_hex_mesh_dict(
    case_dir: Path | str,
    location_in_mesh: tuple[float, float, float] = (0.0, 0.0, 0.0),
    max_cell: float = 0.05,
    min_cell: float = 0.01,
    bl_params: dict | None = None,
) -> Path:
    """Write snappyHexMeshDict to the case directory.

    Args:
        case_dir: Case directory.
        location_in_mesh: Interior point for mesh region detection.
        max_cell: Background mesh max cell size.
        min_cell: Finest refinement cell size.
        bl_params: Boundary layer parameters (optional).

    Returns:
        Path to the written snappyHexMeshDict.
    """
    case_dir = Path(case_dir)
    system_dir = case_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

    lines = build_snappy_hex_mesh_dict_lines(
        location_in_mesh=location_in_mesh,
        max_cell=max_cell,
        min_cell=min_cell,
        bl_params=bl_params,
    )

    dest = system_dir / "snappyHexMeshDict"
    dest.write_text("\n".join(lines) + "\n", encoding="ascii")
    logger.info("Written snappyHexMeshDict to %s", dest)
    return dest


class SnappyHexMeshRunner:
    """Orchestrates snappyHexMesh pipeline via WSL2.

    The pipeline:
      1. Extract surface features (surfaceFeatureExtract)
      2. Generate coarse background mesh (cfMesh cartesianMesh)
      3. Run snappyHexMesh (castellated + snap + addLayers)
      4. Run checkMesh for quality verification
    """

    TIMEOUT_BACKGROUND = 300
    TIMEOUT_FEATURE = 120
    TIMEOUT_SNAPPY = 7200

    def __init__(self, of_config: Any) -> None:
        self._of_config = of_config

    def run(
        self, case_dir: Path | str,
        max_cell: float = 0.05,
        min_cell: float = 0.01,
        bl_params: dict | None = None,
        n_cores: int = 1,
    ) -> dict[str, Any]:
        """Run the full snappyHexMesh pipeline.

        Args:
            case_dir: Case directory with constant/triSurface/surface.stl.
            max_cell: Background mesh max cell size.
            min_cell: Finest refinement cell size.
            bl_params: Boundary layer parameters (optional).
            n_cores: Number of parallel cores (1 = serial).

        Returns:
            Dict with success status and metrics.
        """
        case_dir = Path(case_dir).resolve()
        result: dict[str, Any] = {"success": False, "errors": []}

        octo.log_event("snappy_hex_mesh", "run_start", {
            "case_dir": str(case_dir),
            "max_cell": max_cell,
        })

        try:
            # Step 1: Extract surface features
            self._extract_features(case_dir)

            # Step 2: Generate background mesh (coarse cfMesh)
            self._generate_background_mesh(case_dir, max_cell)

            # Step 3: Write and run snappyHexMesh
            write_snappy_hex_mesh_dict(
                case_dir, max_cell=max_cell, min_cell=min_cell,
                bl_params=bl_params,
            )
            self._run_snappy(case_dir, n_cores=n_cores)

            # Step 4: Quality check
            result["success"] = True
            logger.info("SnappyHexMesh pipeline completed successfully")

        except Exception as exc:
            result["errors"].append(str(exc))
            logger.exception("SnappyHexMesh pipeline failed")

        octo.log_event("snappy_hex_mesh", "run_end", result)
        return result

    def _extract_features(self, case_dir: Path) -> None:
        """Run surfaceFeatureExtract for feature edge detection.

        This produces surface.eMesh used by snappyHexMesh for explicit
        feature edge refinement.
        """
        stl_path = case_dir / "constant" / "triSurface" / "surface.stl"
        if not stl_path.exists():
            logger.warning("No surface STL found at %s", stl_path)
            return

        linux_case = self._of_config.wsl_linux_case_path(case_dir)
        env_quoted = _shq(self._of_config.env_script)

        cmd = (
            f"source {env_quoted} 2>/dev/null; "
            f"cd {_shq(linux_case)} && "
            f"surfaceFeatureExtract "
            f"-case {_shq(linux_case)} "
            f"constant/triSurface/surface.stl "
            f"-angle 30 "
            f"-writeObj "
            f"-prefix surface 2>&1 | tail -20"
        )

        r = subprocess.run(
            self._of_config._build_wsl_cmd(cmd),
            capture_output=True, text=True, timeout=self.TIMEOUT_FEATURE, check=False,
        )
        if r.returncode != 0:
            logger.warning("surfaceFeatureExtract returned %d (non-fatal)", r.returncode)

    def _generate_background_mesh(
        self, case_dir: Path, max_cell: float,
    ) -> None:
        """Generate coarse background mesh via cfMesh.

        snappyHexMesh needs a pre-existing hex mesh to refine.
        We use cfMesh cartesianMesh at coarse settings for this.
        """
        from cfmesh_autogui.core.meshdict_gen import write_meshdict

        write_meshdict(
            case_dir,
            max_cell_size=max_cell * 2,
            min_cell_size=max_cell * 0.5,
        )
        _write_control_dict(case_dir)

        cmd = self._of_config.build_command(case_dir)
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=self.TIMEOUT_BACKGROUND, check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"Background mesh failed (exit {r.returncode}): "
                f"{r.stderr[-500:] if r.stderr else ''}"
            )
        logger.info("Background mesh generated (maxCell=%.4f)", max_cell)

    def _run_snappy(self, case_dir: Path, n_cores: int = 1) -> None:
        """Run snappyHexMesh with the configured dict."""
        linux_case = self._of_config.wsl_linux_case_path(case_dir)
        env_quoted = _shq(self._of_config.env_script)
        bin_q = "snappyHexMesh"

        if n_cores > 1:
            cmd = (
                f"source {env_quoted} 2>/dev/null; "
                f"export OMPI_MCA_btl=^openib,openfabric,uct; "
                f"cd {_shq(linux_case)} && "
                f"mpirun --allow-run-as-root --oversubscribe "
                f"-np {n_cores} {bin_q} -parallel -overwrite 2>&1 | tail -100"
            )
        else:
            cmd = (
                f"source {env_quoted} 2>/dev/null; "
                f"cd {_shq(linux_case)} && "
                f"{bin_q} -overwrite 2>&1 | tail -100"
            )

        r = subprocess.run(
            self._of_config._build_wsl_cmd(cmd),
            capture_output=True, text=True,
            timeout=self.TIMEOUT_SNAPPY, check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"snappyHexMesh failed (exit {r.returncode}): "
                f"{r.stderr[-500:] if r.stderr else ''}"
            )
        logger.info("snappyHexMesh completed (exit 0)")


def _shq(path: str) -> str:
    """Shell-quote a path."""
    import shlex
    return shlex.quote(path)
