"""CLI entry point for the OODA adaptive meshing engine.

Enables headless batch execution::

    python -m cfmesh_autogui.commercial.adaptive_cli \\
        --geometry model.stl \\
        --case-dir ./case \\
        --max-iterations 5 \\
        --n-cores 4 \\
        --detail fine \\
        --export-report quality_report.json

Also supports parallel mode::

    python -m cfmesh_autogui.commercial.adaptive_cli \\
        --geometry model.stl --parallel --n-cores 8
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="OODA closed-loop adaptive meshing engine (headless)",
    )
    parser.add_argument(
        "--geometry", "-g", required=True,
        help="Path to geometry file (STEP/STL/IGES/BREP)",
    )
    parser.add_argument(
        "--case-dir", "-c", default="./ooda_case",
        help="OpenFOAM case directory (default: ./ooda_case)",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=5,
        help="Maximum OODA remediation iterations (default: 5)",
    )
    parser.add_argument(
        "--detail", choices=["very_coarse", "coarse", "medium", "fine", "very_fine"],
        default="medium",
        help="Mesh detail level (default: medium)",
    )
    parser.add_argument(
        "--parallel", action="store_true",
        help="Enable domain-decomposed parallel meshing",
    )
    parser.add_argument(
        "--n-cores", type=int, default=4,
        help="Number of cores for parallel mode (default: 4)",
    )
    parser.add_argument(
        "--export-report", "-r", default="",
        help="Path to export quality report JSON (default: case-dir/ooda_report.json)",
    )
    parser.add_argument(
        "--skip-geometry", action="store_true",
        help="Skip geometry import (use existing case directory)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    case_dir = Path(args.case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)

    # Load geometry
    meshes = None
    geometry_path = args.geometry
    if not args.skip_geometry:
        geo_file = Path(args.geometry)
        if not geo_file.exists():
            logger.error("Geometry file not found: %s", geo_file)
            return 1
        logger.info("Loading geometry: %s", geo_file)
        try:
            import trimesh
            if geo_file.suffix.lower() in (".stl", ".stlb"):
                meshes = [trimesh.load(str(geo_file))]
            else:
                from cfmesh_autogui.core.geometry import load_geometry
                loaded = load_geometry(str(geo_file))
                meshes = loaded if isinstance(loaded, list) else [loaded]
            logger.info("  Loaded %d mesh(es)", len(meshes) if meshes else 0)
        except Exception as exc:
            logger.error("Failed to load geometry: %s", exc)
            return 1

    # Configure OODA adapter
    from cfmesh_autogui.config import OFConfig
    from cfmesh_autogui.commercial.adaptive_integration import OODAWorkflowAdapter

    of_config = OFConfig()
    adapter = OODAWorkflowAdapter(of_config)
    adapter.configure(case_dir, meshes=meshes, geometry_path=geometry_path)

    # Progress callback
    def _on_progress(msg: str, frac: float):
        bar_len = 40
        filled = int(bar_len * frac)
        bar = "█" * filled + "░" * (bar_len - filled)
        sys.stdout.write(f"\r  [{bar}] {frac:.0%}  {msg}")
        sys.stdout.flush()

    # Run
    logger.info("Starting OODA loop (parallel=%s, cores=%d, max_iter=%d)",
                args.parallel, args.n_cores, args.max_iterations)

    try:
        if args.parallel:
            result = adapter.run_parallel(
                n_cores=args.n_cores,
                max_iterations=args.max_iterations,
                callback=_on_progress,
            )
        else:
            result = adapter.run(
                max_iterations=args.max_iterations,
                callback=_on_progress,
            )
    except Exception as exc:
        logger.error("\nOODA loop failed: %s", exc)
        return 1

    sys.stdout.write("\n")
    logger.info("OODA loop complete:")
    logger.info("  Status:      %s", "PASS" if result.success else "FAIL")
    logger.info("  Cells:       %d", result.cell_count)
    logger.info("  Skewness:    %.4f", result.max_skewness)
    logger.info("  Non-ortho:   %.1f°", result.max_non_orthogonality)
    logger.info("  Iterations:  %d", result.n_ooda_iterations)
    logger.info("  Time:        %.1fs", result.wall_time_s)

    # Export report
    report_path = Path(args.export_report) if args.export_report else case_dir / "ooda_report.json"
    if result.report:
        report_path.write_text(
            json.dumps(result.report, indent=2, default=str), encoding="utf-8",
        )
        logger.info("Report exported: %s", report_path)

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
