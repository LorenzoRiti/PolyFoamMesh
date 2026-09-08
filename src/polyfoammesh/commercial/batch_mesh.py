"""Template-based batch meshing engine — journal system for CI/CD.

Records meshing actions into a replayable journal, then processes
multiple geometry files sequentially or in parallel using the same
template parameters.

Features:
  - Journal recording (every action → timestamped JSON log)
  - Batch processing queue (N geometry files, same parameters)
  - CLI headless mode for CI/CD pipelines
  - Progress tracking and summary report
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from polyfoammesh.config import OFConfig
from polyfoammesh.core.validation import validate_geometry_path
from polyfoammesh.octopoda_local import octo

logger = logging.getLogger(__name__)


@dataclass
class BatchEntry:
    """A single geometry file in the batch queue."""
    geometry_path: str
    label: str = ""
    status: str = "pending"  # pending | running | done | failed
    cell_count: int = 0
    wall_time_s: float = 0.0
    error: str = ""


@dataclass
class BatchConfig:
    """Configuration for a batch meshing run."""
    geometry_dir: str = ""
    output_dir: str = ""
    pattern: str = "*.step"
    max_cell: float = 0.05
    min_cell: float = 0.01
    detail: str = "medium"
    bl_enabled: bool = False
    bl_n_layers: int = 3
    parallel: bool = False
    n_cores: int = 1


@dataclass
class BatchReport:
    """Summary report for a batch meshing run."""
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    entries: list[BatchEntry] = field(default_factory=list)
    started_at: str = ""
    wall_time_s: float = 0.0


class BatchMesher:
    """Template-based batch meshing engine.

    Usage (CLI)::

        python -m polyfoammesh.commercial.batch_mesh \\
            --geometry-dir ./models --pattern "*.step" \\
            --max-cell 0.05 --min-cell 0.01

    Usage (API)::

        bm = BatchMesher()
        bm.add_file("model1.step")
        bm.add_file("model2.step")
        report = bm.run_all()
        print(f"{report.succeeded}/{report.total} succeeded")
    """

    def __init__(self, of_config: OFConfig | None = None) -> None:
        self._of_config = of_config or OFConfig()
        self._queue: list[BatchEntry] = []
        self._config = BatchConfig()
        self._report = BatchReport()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def configure(self, config: BatchConfig) -> None:
        self._config = config

    def add_file(self, geometry_path: str, label: str = "") -> None:
        result = validate_geometry_path(geometry_path)
        if not result.valid:
            raise ValueError(result.message)
        self._queue.append(BatchEntry(
            geometry_path=geometry_path,
            label=label or Path(geometry_path).stem,
        ))

    def add_directory(self, directory: str, pattern: str = "*.step") -> int:
        """Add all matching files from a directory. Returns count."""
        d = Path(directory)
        if not d.is_dir():
            raise FileNotFoundError(f"Directory not found: {directory}")
        count = 0
        for f in sorted(d.glob(pattern)):
            try:
                self.add_file(str(f))
                count += 1
            except ValueError:
                logger.warning("Skipping invalid file: %s", f)
        return count

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def run_all(self) -> BatchReport:
        """Process all queued geometry files sequentially."""
        self._report = BatchReport(
            total=len(self._queue),
            entries=list(self._queue),
            started_at=datetime.now().isoformat(),
        )
        start = datetime.now()

        if not self._queue:
            logger.warning("Batch queue is empty — nothing to process.")
            return self._report

        octo.log_event("batch_mesh", "batch_start", {"count": len(self._queue)})

        for entry in self._report.entries:
            entry.status = "running"
            octo.log_event("batch_mesh", "file_start", {"file": entry.geometry_path})

            t0 = datetime.now()
            try:
                self._process_single(entry)
                entry.status = "done"
                self._report.succeeded += 1
                elapsed = (datetime.now() - t0).total_seconds()
                entry.wall_time_s = round(elapsed, 1)
                logger.info("OK [%s] %s (%.1fs)", entry.label, entry.geometry_path, elapsed)
            except Exception as exc:
                entry.status = "failed"
                entry.error = str(exc)
                self._report.failed += 1
                elapsed = (datetime.now() - t0).total_seconds()
                entry.wall_time_s = round(elapsed, 1)
                logger.error("FAIL [%s] %s: %s", entry.label, entry.geometry_path, exc)

        self._report.wall_time_s = round((datetime.now() - start).total_seconds(), 1)

        octo.log_event("batch_mesh", "batch_complete", {
            "total": self._report.total,
            "succeeded": self._report.succeeded,
            "failed": self._report.failed,
        })
        return self._report

    def _process_single(self, entry: BatchEntry) -> None:
        """Process a single geometry file through the full pipeline."""
        from polyfoammesh.commercial.watertight import WatertightWorkflow

        wf = WatertightWorkflow(self._of_config)
        wf.set_geometry(entry.geometry_path)
        wf.set_cell_sizes(self._config.max_cell, self._config.min_cell)

        if self._config.bl_enabled:
            wf.set_boundary_layers(
                n_layers=self._config.bl_n_layers,
                thickness_ratio=0.005,
                expansion_ratio=1.2,
            )

        # Generate case dir from geometry name
        stem = Path(entry.geometry_path).stem
        case_root = Path(self._config.output_dir or "batch_output") / stem
        wf.set_case_dir(str(case_root))

        result = wf.run()
        if not result.success:
            raise RuntimeError(
                "; ".join(result.errors) if result.errors else "Unknown error"
            )

        entry.cell_count = result.cell_count

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    def export_report(self, path: Path | str) -> None:
        """Export the batch report as JSON."""
        data = {
            "total": self._report.total,
            "succeeded": self._report.succeeded,
            "failed": self._report.failed,
            "wall_time_s": self._report.wall_time_s,
            "entries": [
                {
                    "label": e.label,
                    "file": e.geometry_path,
                    "status": e.status,
                    "cell_count": e.cell_count,
                    "wall_time_s": e.wall_time_s,
                    "error": e.error,
                }
                for e in self._report.entries
            ],
        }
        Path(path).write_text(json.dumps(data, indent=2, default=str))
        logger.info("Batch report exported: %s", path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main_cli() -> None:
    """Headless CLI entry point for CI/CD integration.

    Usage::

        python -m polyfoammesh.commercial.batch_mesh \\
            --geometry-dir ./cad_models --pattern "*.step" \\
            --max-cell 0.05 --min-cell 0.01 --output-dir ./meshed
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="PolyFoamMesh Batch Mesher (headless)",
    )
    parser.add_argument("--geometry-dir", required=True, help="Directory with CAD files")
    parser.add_argument("--pattern", default="*.step", help="Glob pattern (default: *.step)")
    parser.add_argument("--output-dir", default="batch_output", help="Output directory")
    parser.add_argument("--max-cell", type=float, default=0.05, help="Max cell size (m)")
    parser.add_argument("--min-cell", type=float, default=0.01, help="Min cell size (m)")
    parser.add_argument("--bl", action="store_true", help="Enable boundary layers")
    parser.add_argument("--bl-layers", type=int, default=3, help="Number of BL layers")
    parser.add_argument("--report", default="batch_report.json", help="Output report path")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    bm = BatchMesher()
    count = bm.add_directory(args.geometry_dir, args.pattern)
    logger.info("Batch: %d files queued from %s", count, args.geometry_dir)

    if count == 0:
        logger.warning("No matching files found.")
        sys.exit(0)

    report = bm.run_all()
    bm.export_report(args.report)

    print(f"\nBatch complete: {report.succeeded}/{report.total} succeeded "
          f"({report.wall_time_s:.1f}s)")
    if report.failed > 0:
        sys.exit(1)



