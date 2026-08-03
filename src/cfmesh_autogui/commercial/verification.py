"""Verification framework: compare mesh quality across algorithms on real geometries.

Implements a systematic comparison suite that runs checkMesh on the same
geometry with different algorithms and reports which produces the best
quality metrics.

Geometry test suite (Punto 4 requirement):
  1. **Condotto** — simple duct (internal flow, easy geometry)
  2. **Geometria con spigoli** — box with sharp edges (tests non-ortho)
  3. **Geometria complessa** — irregular surface (tests skewness)

Usage::

    v = VerificationSuite(of_config)
    report = v.run_comparison(case_dir, geometry_stl, algorithms=[...])
    print(report.best_algorithm)  # algorithm with best quality
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AlgorithmMetrics:
    """Quality metrics for a single algorithm run."""
    algorithm: str = ""
    cell_count: int = 0
    max_skewness: float = 0.0
    avg_skewness: float = 0.0
    max_non_ortho: float = 0.0
    avg_non_ortho: float = 0.0
    max_aspect_ratio: float = 0.0
    neg_cells: int = 0
    wall_time_s: float = 0.0
    passed: bool = False
    error: str = ""

    def quality_score(self) -> float:
        """Composite quality score (lower = better).

        Formula (heuristic):
          score = 0.3 * (skewness/0.9) + 0.3 * (non_ortho/70)
                + 0.2 * (aspect/1000) + 0.2 * neg_cells
        """
        skew_factor = min(self.max_skewness / 0.9, 2.0)
        northo_factor = min(self.max_non_ortho / 70.0, 2.0)
        aspect_factor = min(self.max_aspect_ratio / 1000.0, 2.0)
        return (
            0.30 * skew_factor
            + 0.30 * northo_factor
            + 0.20 * aspect_factor
            + 0.20 * self.neg_cells
        )

    def summary(self) -> str:
        """Return a one-line summary of metrics."""
        return (
            f"{self.algorithm:20s} | "
            f"cells={self.cell_count:>8,d} | "
            f"skew={self.max_skewness:.3f} | "
            f"nonOrtho={self.max_non_ortho:.1f} | "
            f"aspect={self.max_aspect_ratio:.0f} | "
            f"negVol={self.neg_cells} | "
            f"t={self.wall_time_s:.1f}s | "
            f"{'PASS' if self.passed else 'FAIL'}"
        )


@dataclass
class VerificationReport:
    """Complete comparison report."""
    geometry_name: str = ""
    algorithms_tested: list[str] = field(default_factory=list)
    results: list[AlgorithmMetrics] = field(default_factory=list)
    best_algorithm: str = ""
    best_score: float = float("inf")
    timestamp: str = ""

    def print_table(self) -> str:
        """Return a formatted comparison table."""
        hdr = (
            f"{'Algorithm':20s} | {'Cells':>10s} | {'Skewness':>8s} | "
            f"{'NonOrtho':>7s} | {'Aspect':>6s} | {'NegVol':>5s} | "
            f"{'Time':>7s} | {'Status':>5s}"
        )
        lines = [
            f"\n{'=' * 90}",
            f"  VERIFICATION REPORT: {self.geometry_name}",
            f"{'=' * 90}",
            hdr,
            "-" * 90,
        ]
        for r in self.results:
            lines.append(r.summary())
        lines.append("-" * 90)
        if self.best_algorithm:
            lines.append(f"  BEST: {self.best_algorithm} (score={self.best_score:.3f})")
        lines.append("=" * 90)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "geometry": self.geometry_name,
            "timestamp": self.timestamp,
            "best_algorithm": self.best_algorithm,
            "best_score": self.best_score,
            "results": [vars(r) for r in self.results],
        }


class VerificationSuite:
    """Systematic verification suite comparing algorithms on real geometries.

    Runs each algorithm on the same geometry and measures quality via
    checkMesh.  Produces a report showing which algorithm produces the
    best metrics for a given geometry type.
    """

    def __init__(self, of_config: Any) -> None:
        self._of_config = of_config

    def run_comparison(
        self, case_base: Path | str,
        geometry_stl: Path | str,
        algorithms: list[str] | None = None,
        detail_level: str = "medium",
        bl_enabled: bool = True,
    ) -> VerificationReport:
        """Run multiple algorithms on the same geometry and compare.

        Args:
            case_base: Base directory for temporary cases.
            geometry_stl: Path to the STL geometry file.
            algorithms: List of algorithm names to test. Defaults to
                [CartesianHex, Tetrahedral, gmsh_hybrid].
            detail_level: Mesh detail level.

        Returns:
            ``VerificationReport`` with per-algorithm metrics.
        """
        case_base = Path(case_base).resolve()
        geometry_stl = Path(geometry_stl).resolve()
        report = VerificationReport(
            geometry_name=geometry_stl.stem,
            timestamp=datetime.now(UTC).isoformat(),
        )

        if not geometry_stl.exists():
            raise FileNotFoundError(f"Geometry not found: {geometry_stl}")

        if algorithms is None:
            algorithms = ["CartesianHex", "Tetrahedral", "gmsh_hybrid"]

        report.algorithms_tested = algorithms

        for algo_name in algorithms:
            metrics = self._test_algorithm(
                case_base, geometry_stl, algo_name,
                detail_level, bl_enabled,
            )
            report.results.append(metrics)

            score = metrics.quality_score()
            if metrics.passed and score < report.best_score:
                report.best_score = score
                report.best_algorithm = algo_name

        if not any(r.passed for r in report.results):
            # No passed run: pick the one with lowest score anyway
            best = min(report.results, key=lambda r: r.quality_score())
            report.best_algorithm = best.algorithm
            report.best_score = best.quality_score()

        logger.info(
            "Verification: best=%s (score=%.3f) on %s",
            report.best_algorithm, report.best_score, geometry_stl.name,
        )
        return report

    def _test_algorithm(
        self, case_base: Path, geometry_stl: Path,
        algo_name: str, detail_level: str, bl_enabled: bool,
    ) -> AlgorithmMetrics:
        """Run a single algorithm and return its metrics."""
        import shutil

        case_dir = case_base / f"test_{algo_name}_{geometry_stl.stem}"
        if case_dir.exists():
            shutil.rmtree(case_dir)

        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "constant" / "triSurface").mkdir(parents=True, exist_ok=True)
        (case_dir / "system").mkdir(parents=True, exist_ok=True)

        # Copy STL
        dest_stl = case_dir / "constant" / "triSurface" / "surface.stl"
        shutil.copy2(geometry_stl, dest_stl)

        metrics = AlgorithmMetrics(algorithm=algo_name)
        start = datetime.now(UTC)

        try:
            from cfmesh_autogui.commercial.mesh_engine import (
                MeshEngine,
                MeshEngineParams,
                MeshingAlgorithm,
            )

            # Map algorithm name to enum
            algo_map = {
                "CartesianHex": MeshingAlgorithm.CARTESIAN_HEX,
                "Tetrahedral": MeshingAlgorithm.TETRAHEDRAL,
                "gmsh_hybrid": MeshingAlgorithm.TETRAHEDRAL,
                "Polyhedral": MeshingAlgorithm.POLYHEDRAL,
                "SnappyHexMesh": MeshingAlgorithm.SNAPPY_HEX_MESH,
            }
            algo = algo_map.get(algo_name, MeshingAlgorithm.CARTESIAN_HEX)

            engine = MeshEngine()
            engine.configure(MeshEngineParams(
                algorithm=algo,
                detail_level=detail_level,
                bl_enabled=bl_enabled,
                adaptive_escalation=False,  # test single algorithm
            ))

            result = engine.run(case_dir, geometry_path=str(geometry_stl))

            metrics.cell_count = result.cell_count
            metrics.max_skewness = result.max_skewness
            metrics.max_non_ortho = result.max_non_orthogonality
            metrics.max_aspect_ratio = result.max_aspect_ratio
            metrics.neg_cells = result.neg_cells
            metrics.wall_time_s = result.wall_time_s
            metrics.passed = result.quality_passed
            metrics.error = "; ".join(result.errors) if result.errors else ""

        except OSError as exc:
            metrics.error = str(exc)
            logger.warning("Algorithm %s failed: %s", algo_name, exc)

        metrics.wall_time_s = round((datetime.now(UTC) - start).total_seconds(), 1)
        return metrics

    def generate_test_geometries(self, output_dir: Path | str) -> dict[str, Path]:
        """Generate 3 test STL geometries for verification.

        Returns:
            Dict mapping geometry name → STL path.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        geometries: dict[str, Path] = {}

        # 1. Condotto — simple rectangular duct
        duct_stl = output_dir / "duct.stl"
        self._write_duct_stl(duct_stl)
        geometries["duct"] = duct_stl

        # 2. Geometria con spigoli — box with sharp edges
        box_stl = output_dir / "sharp_box.stl"
        self._write_sharp_box_stl(box_stl)
        geometries["sharp_box"] = box_stl

        # 3. Geometria complessa — pipe with bend
        pipe_stl = output_dir / "complex_pipe.stl"
        self._write_complex_pipe_stl(pipe_stl)
        geometries["complex_pipe"] = pipe_stl

        return geometries

    # ------------------------------------------------------------------
    # STL generators for test geometries
    # ------------------------------------------------------------------

    def _write_duct_stl(self, path: Path) -> None:
        """Generate a simple rectangular duct STL.

        A 1m x 0.5m x 0.5m box with inlet/outlet patches:
        - Inlet at x=0, Outlet at x=1
        - Walls on all other faces
        """
        l, w, h = 1.0, 0.5, 0.5
        verts = [
            (0, 0, 0), (l, 0, 0), (l, w, 0), (0, w, 0),
            (0, 0, h), (l, 0, h), (l, w, h), (0, w, h),
        ]
        faces = [
            # bottom (z=0) - solid wall
            ([0, 1, 2], (0, 0, -1)),
            ([0, 2, 3], (0, 0, -1)),
            # top (z=h) - solid wall
            ([4, 6, 5], (0, 0, 1)),
            ([4, 7, 6], (0, 0, 1)),
            # front (y=0) - solid wall
            ([0, 5, 1], (0, -1, 0)),
            ([0, 4, 5], (0, -1, 0)),
            # back (y=w) - solid wall
            ([3, 2, 6], (0, 1, 0)),
            ([3, 6, 7], (0, 1, 0)),
            # left (x=0) - inlet
            ([0, 7, 4], (-1, 0, 0)),
            ([0, 3, 7], (-1, 0, 0)),
            # right (x=l) - outlet
            ([1, 5, 6], (1, 0, 0)),
            ([1, 6, 2], (1, 0, 0)),
        ]
        self._write_stl(path, verts, faces)

    def _write_sharp_box_stl(self, path: Path) -> None:
        """Generate a box with sharp internal features (edges, step).

        A 0.5m cube with a step change in the middle.
        """
        s = 0.5
        step_h = 0.25
        verts = [
            (0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0),
            (0, 0, s), (s, 0, s), (s, s, s), (0, s, s),
            # step internal feature
            (s/2, 0, 0), (s/2, 0, step_h), (s/2, s, step_h), (s/2, s, 0),
        ]
        faces = [
            ([0, 1, 2], (0, 0, -1)), ([0, 2, 3], (0, 0, -1)),
            ([4, 6, 5], (0, 0, 1)), ([4, 7, 6], (0, 0, 1)),
            ([0, 5, 1], (0, -1, 0)), ([0, 4, 5], (0, -1, 0)),
            ([3, 2, 6], (0, 1, 0)), ([3, 6, 7], (0, 1, 0)),
            ([0, 7, 4], (-1, 0, 0)), ([0, 3, 7], (-1, 0, 0)),
            ([1, 5, 6], (1, 0, 0)), ([1, 6, 2], (1, 0, 0)),
            # step walls
            ([8, 9, 10], (1, 0, 0)), ([8, 10, 11], (1, 0, 0)),
        ]
        self._write_stl(path, verts, faces)

    def _write_complex_pipe_stl(self, path: Path) -> None:
        """Generate a pipe with a 90-degree bend (complex geometry).

        Approximated as an extruded L-shape.
        """
        r = 0.2
        triangles = []
        v = [
            (0, -r, -r), (1, -r, -r), (1, r, -r), (0, r, -r),
            (0, -r, r), (1, -r, r), (1, r, r), (0, r, r),
            # Vertical leg along y
            (1, -r, -r), (1.5, -r, -r), (1.5, r, -r), (1, r, -r),
            (1, -r, r), (1.5, -r, r), (1.5, r, r), (1, r, r),
        ]
        # Simple faces for each box
        tri_indices = [
            [0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6],
            [0, 5, 1], [0, 4, 5], [3, 2, 6], [3, 6, 7],
            [0, 3, 7], [0, 7, 4], [1, 5, 6], [1, 6, 2],
            # vertical extension
            [8, 9, 10], [8, 10, 11], [12, 14, 13], [12, 15, 14],
            [8, 13, 9], [8, 12, 13], [11, 10, 14], [11, 14, 15],
            [8, 11, 15], [8, 15, 12], [9, 13, 14], [9, 14, 10],
        ]
        for tri in tri_indices:
            triangles.append((tri, (0, 0, 0)))

        self._write_stl(path, v, triangles)

    def _write_stl(
        self, path: Path,
        verts: list[tuple[float, float, float]],
        faces: list[tuple[list[int], tuple[float, float, float]]],
    ) -> None:
        """Write a binary STL file."""
        import struct

        with open(path, "wb") as f:
            header = b"STL generated by CFMesh-AutoGUI verification"
            f.write(header.ljust(80, b"\x00"))
            f.write(struct.pack("<I", len(faces)))
            for tri, normal in faces:
                nx, ny, nz = normal
                f.write(struct.pack("<fff", nx, ny, nz))
                for i in range(3):
                    v = verts[tri[i]]
                    f.write(struct.pack("<fff", v[0], v[1], v[2]))
                f.write(struct.pack("<H", 0))

    def export_report(self, report: VerificationReport, path: Path | str) -> None:
        """Export verification report to JSON."""
        Path(path).write_text(
            json.dumps(report.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("Verification report exported to %s", path)
