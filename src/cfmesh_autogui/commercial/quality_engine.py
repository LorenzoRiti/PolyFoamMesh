"""Quality engine — metrics, 3D heatmap data, histogram, auto-fix, PDF report.

Analyses mesh quality via checkMesh, generates structured data for
3D heatmap colouring (per-cell quality), matplotlib histogram,
intelligent auto-fix (max 3 iterations), and PDF report export.

Usage::

    qe = QualityEngine()
    report = qe.analyse(case_dir)
    print(report.summary())
    qe.auto_fix(report, case_dir, max_iterations=3)
    qe.export_pdf(report, "quality_report.pdf")
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from cfmesh_autogui.core.quality_thresholds import QUALITY_THRESHOLDS as THRESHOLDS
from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)


@dataclass
class QualityMetrics:
    """Individual quality metrics."""
    max_skewness: float = 0.0
    avg_skewness: float = 0.0
    max_non_orthogonality: float = 0.0
    avg_non_orthogonality: float = 0.0
    max_aspect_ratio: float = 0.0
    min_volume: float = 0.0
    neg_cells: int = 0
    cells: int = 0
    has_fatal: bool = False

    @property
    def passed(self) -> bool:
        if self.has_fatal or self.neg_cells > 0:
            return False
        return (self.max_skewness <= THRESHOLDS["skewness_max"]
                and self.max_non_orthogonality <= THRESHOLDS["non_ortho_max"]
                and self.max_aspect_ratio <= THRESHOLDS["aspect_ratio_max"])


@dataclass
class AutoFixAction:
    """Description of a single auto-fix action."""
    action: str = ""       # smooth, refine, remesh, split, disable_bl, local_refine
    target_metric: str = ""
    current_value: float = 0.0
    detail: str = ""
    # "local_refine" only: the objectRefinements-box dicts from
    # local_refinement_boxes_from_checkmesh_sets, applied by _apply_fix.
    payload: list[dict] = field(default_factory=list)


@dataclass
class QualityReport:
    """Complete quality report for a mesh."""
    metrics: QualityMetrics = field(default_factory=QualityMetrics)
    passed: bool = False
    status: str = ""
    heatmap_data: dict[str, list[float]] = field(default_factory=dict)
    histogram: dict[str, Any] = field(default_factory=dict)
    fixes_applied: list[AutoFixAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        m = self.metrics
        parts = [
            f"Cells: {m.cells:,}",
            f"Skewness: {m.max_skewness:.2f}",
            f"Non-ortho: {m.max_non_orthogonality:.1f}°",
            f"Aspect: {m.max_aspect_ratio:.0f}",
        ]
        if m.neg_cells:
            parts.append(f"NegVol: {m.neg_cells}")
        return " | ".join(parts) + f" | {'✅ PASS' if self.passed else '❌ FAIL'}"


# checkMesh writes these sets to constant/polyMesh/sets/<name> whenever it
# finds a problem, but nothing in this codebase ever reads them back --
# auto_fix only ever reacts to the aggregate metrics (max skewness, etc.)
# with a GLOBAL cell-size relax + a full cartesianMesh rerun, even when the
# actual defect is confined to a handful of cells. "cell" sets list cell
# indices directly; "face" sets list face indices, mapped to their owner
# cell below (an approximation -- good enough for a refinement box, which
# only needs the general neighbourhood, not the exact cell).
_CHECKMESH_PROBLEM_SETS: dict[str, str] = {
    "nonClosedCells": "cell",
    "zeroVolumeCells": "cell",
    "illegalCells": "cell",
    "highAspectRatioCells": "cell",
    "skewFaces": "face",
    "nonOrthoFaces": "face",
    "wrongOrientedFaces": "face",
    "outOfRangeFaces": "face",
}


def local_refinement_boxes_from_checkmesh_sets(
    case_dir: Path | str,
    cell_size: float,
    padding_factor: float = 0.2,
    max_boxes: int = 8,
) -> list[dict]:
    """Turn checkMesh's own problem-cell/face sets into ``objectRefinements``
    boxes (Fase 3: surgical repair instead of ``_relax_cell_sizes``'s global
    coarsen-and-rerun-everything).

    checkMesh already computes and writes EXACTLY where a mesh is bad
    (``constant/polyMesh/sets/skewFaces``, ``nonOrthoFaces``, ...) — this
    reads those back, maps each entry to a cell (face sets via their owner
    cell), gathers that cell's vertices from the mesh's own points/faces,
    and returns one padded axis-aligned bounding box per problem TYPE found
    (not a full point-cluster/k-means split — a single box per defect kind
    is a coarser but far simpler and more robust first cut; a defect
    scattered across the whole domain still produces a box, just a big
    one, which degrades gracefully towards the old global-relax behaviour
    rather than silently doing nothing).

    Returns an empty list (never raises) when the mesh/sets can't be read,
    a set is empty, or ``cell_size`` is not positive — callers should treat
    that as "no surgical fix available, fall back to the existing global
    relax", not as an error.
    """
    boxes: list[dict] = []
    if cell_size <= 0:
        return boxes
    case_dir = Path(case_dir)
    poly_dir = case_dir / "constant" / "polyMesh"
    sets_dir = poly_dir / "sets"
    if not sets_dir.is_dir():
        return boxes

    try:
        from cfmesh_autogui.core import foam_mesh_io

        points, faces, owner, _neighbour, _patches = foam_mesh_io.read_polymesh(poly_dir)
    except Exception:
        logger.exception("Local refinement: failed to read polyMesh in %s", case_dir)
        return boxes

    n_faces = len(faces)
    n_cells = int(owner.max()) + 1 if len(owner) else 0

    for set_name, kind in _CHECKMESH_PROBLEM_SETS.items():
        if len(boxes) >= max_boxes:
            break
        set_path = sets_dir / set_name
        if not set_path.exists():
            continue
        try:
            ids = foam_mesh_io.read_label_list(set_path)
        except Exception:
            logger.exception("Local refinement: failed to read set %s", set_path)
            continue
        if len(ids) == 0:
            continue

        if kind == "face":
            valid = (ids >= 0) & (ids < n_faces)
            cell_ids = np.unique(owner[ids[valid]])
        else:
            valid = (ids >= 0) & (ids < n_cells)
            cell_ids = np.unique(ids[valid])
        if len(cell_ids) == 0:
            continue

        # Gather every vertex of every face belonging to these cells --
        # cheaper than computing true cell centroids and sufficient for a
        # bounding box (which only needs the extent, not the centre of
        # mass).
        cell_id_set = set(cell_ids.tolist())
        pt_indices: list[int] = []
        for fid in range(n_faces):
            if int(owner[fid]) in cell_id_set:
                pt_indices.extend(faces[fid])
        if not pt_indices:
            continue
        pts = points[np.asarray(pt_indices, dtype=np.int64)]
        lo = pts.min(axis=0)
        hi = pts.max(axis=0)
        span = np.maximum(hi - lo, cell_size)  # floor: a single-cell defect
        pad = span * padding_factor
        lo = lo - pad
        hi = hi + pad
        boxes.append({
            "type": "box",
            "xmin": float(lo[0]), "xmax": float(hi[0]),
            "ymin": float(lo[1]), "ymax": float(hi[1]),
            "zmin": float(lo[2]), "zmax": float(hi[2]),
            "cell_size": float(cell_size),
            "source_set": set_name,
            "n_cells": len(cell_ids),
        })
        logger.info(
            "Local refinement: %s -> %d cells, box %s..%s (cellSize=%.5g)",
            set_name, len(cell_ids), lo.round(4).tolist(), hi.round(4).tolist(),
            cell_size,
        )
    return boxes


class QualityEngine:
    """Mesh quality analysis and auto-fix engine.

    Usage::

        qe = QualityEngine()
        report = qe.analyse(case_dir)
        if not report.passed:
            qe.auto_fix(case_dir, max_iterations=3)
    """

    def analyse(self, case_dir: Path | str) -> QualityReport:
        """Run quality analysis on a case directory.

        Parses checkMesh output and generates heatmap-ready data.
        """
        case_dir = Path(case_dir)
        report = QualityReport()

        octo.log_event("quality_engine", "analyse_start", {"case_dir": str(case_dir)})

        try:
            raw = self._get_checkmesh_output(case_dir)
            metrics = self._parse_metrics(raw)
            report.metrics = metrics
            report.passed = metrics.passed
            report.status = self._status_text(metrics)
            report.heatmap_data = self._generate_heatmap_data(raw, metrics)
            report.histogram = self._compute_histogram(report.heatmap_data)

            octo.log_event("quality_engine", "analyse_ok", {
                "passed": metrics.passed,
                "cells": metrics.cells,
            })
        except Exception as exc:
            report.warnings.append(f"Quality analysis failed: {exc}")
            logger.warning("Quality analysis failed for %s: %s", case_dir, exc)

        return report

    def auto_fix(
        self, case_dir: Path | str,
        report: QualityReport | None = None,
        max_iterations: int = 3,
    ) -> QualityReport:
        """Intelligent auto-fix loop.

        Applies corrective actions sequentially:
        - Skewness > 4.0 → relax cell sizes (increase max, decrease min)
        - Non-ortho > 70 → disable boundary layers
        - Negative volume → regenerate mesh with larger min cell
        - Aspect ratio > 1000 → split by reducing max cell

        Args:
            case_dir: Case directory to fix.
            report: Optional initial quality report. Fresh analysis if None.
            max_iterations: Maximum fix iterations.

        Returns:
            ``QualityReport`` with fixes applied.
        """
        case_dir = Path(case_dir)
        if report is None:
            report = self.analyse(case_dir)

        octo.log_event("quality_engine", "auto_fix_start", {"iterations": max_iterations})

        for i in range(max_iterations):
            if report.passed:
                logger.info("Quality OK at iteration %d", i)
                break

            fixes = self._decide_fixes(report.metrics, case_dir)
            if not fixes:
                logger.info("No applicable fix at iteration %d", i)
                break

            for fix in fixes:
                self._apply_fix(fix, case_dir)
                report.fixes_applied.append(fix)
                report.warnings.append(
                    f"Iteration {i + 1}: {fix.action} on {fix.target_metric} "
                    f"({fix.current_value:.2f})"
                )

            # _apply_fix only rewrites meshDict text; without re-running
            # cartesianMesh, analyse() below would just re-check the same
            # unchanged mesh on every iteration (same bug found and fixed in
            # commercial/optimizer.py's MeshOptimizer.optimize()).
            if not self._run_cartesian_mesh(case_dir):
                report.warnings.append(f"Iteration {i + 1}: cartesianMesh failed")
                break

            # Re-analyse after fix
            report = self.analyse(case_dir)
            if report.passed:
                break

        octo.log_event("quality_engine", "auto_fix_done", {
            "iterations": min(i + 1, max_iterations),
            "passed": report.passed,
            "fixes": len(report.fixes_applied),
        })
        return report

    def _run_cartesian_mesh(self, case_dir: Path) -> bool:
        """Run cartesianMesh synchronously so the fix loop actually re-meshes.

        Refuses (returns False) when the existing mesh is GMSH tet / tet→poly
        output — re-meshing would silently replace it with a cfMesh hex at
        different dimensions (Fase 3 P3.2 guard).
        """
        import subprocess
        from cfmesh_autogui.config import OFConfig
        from cfmesh_autogui.core.validation import mesh_remeshable

        ok, reason = mesh_remeshable(case_dir)
        if not ok:
            logger.warning("auto_fix: refusing cartesianMesh — %s", reason)
            return False

        try:
            cmd = OFConfig().build_command(Path(case_dir))
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("cartesianMesh timed out for %s", case_dir)
            return False
        except FileNotFoundError:
            logger.warning("WSL not found for cartesianMesh")
            return False
        except Exception as exc:
            logger.warning("cartesianMesh failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # checkMesh parsing
    # ------------------------------------------------------------------
    def _get_checkmesh_output(self, case_dir: Path) -> str:
        """Run checkMesh and return output."""
        import subprocess
        from cfmesh_autogui.config import OFConfig

        cfg = OFConfig()
        try:
            cmd = cfg.build_check_mesh_cmd(case_dir)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return r.stdout + r.stderr
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("checkMesh timed out after 120s") from exc
        except FileNotFoundError as exc:
            raise RuntimeError("WSL not found for checkMesh") from exc

    def _parse_metrics(self, raw: str) -> QualityMetrics:
        """Parse checkMesh output into QualityMetrics.

        Delegates to :func:`openfoam_runner.parse_checkmesh_output` — the
        single checkMesh parser (handles both the legacy ``=`` phrasing and
        the real OpenFOAM v2512 colon phrasing, with optional averages).
        """
        from cfmesh_autogui.core.openfoam_runner import parse_checkmesh_output

        r = parse_checkmesh_output(raw)
        return QualityMetrics(
            max_skewness=r.max_skewness,
            avg_skewness=r.avg_skewness,
            max_non_orthogonality=r.max_non_ortho,
            avg_non_orthogonality=r.avg_non_ortho,
            max_aspect_ratio=r.max_aspect_ratio,
            min_volume=r.min_volume,
            neg_cells=r.neg_cells,
            cells=r.cells,
            has_fatal=r.has_fatal,
        )

    @staticmethod
    def _status_text(m: QualityMetrics) -> str:
        if m.has_fatal: return "FATAL"
        if m.neg_cells > 0: return f"FAIL: {m.neg_cells} negative-volume cells"
        warns = []
        if m.max_skewness > THRESHOLDS["skewness_max"]:
            warns.append(f"skew={m.max_skewness:.2f}")
        if m.max_non_orthogonality > THRESHOLDS["non_ortho_max"]:
            warns.append(f"nonOrtho={m.max_non_orthogonality:.1f}")
        if m.max_aspect_ratio > THRESHOLDS["aspect_ratio_max"]:
            warns.append(f"aspect={m.max_aspect_ratio:.0f}")
        return "PASS" if not warns else "WARN: " + ", ".join(warns)

    # ------------------------------------------------------------------
    # Heatmap data
    # ------------------------------------------------------------------
    def _generate_heatmap_data(
        self, raw: str, metrics: QualityMetrics,
    ) -> dict[str, list[float]]:
        """Generate per-cell quality data for 3D heatmap colouring."""
        data: dict[str, list[float]] = {
            "skewness": [], "non_orthogonality": [], "aspect_ratio": [],
        }

        cell_pattern = re.compile(
            rf"Cell\s+(\d+):\s+skewness\s+({QualityEngine._OF_FLOAT}).*?"
            rf"non-ortho\s+({QualityEngine._OF_FLOAT}).*?"
            rf"aspect\s+({QualityEngine._OF_FLOAT})", re.IGNORECASE,
        )
        for m in cell_pattern.finditer(raw):
            try:
                data["skewness"].append(float(m.group(2)))
                data["non_orthogonality"].append(float(m.group(3)))
                data["aspect_ratio"].append(float(m.group(4)))
            except (ValueError, IndexError):
                # malformed checkMesh cell line: skip it, keep the rest
                logger.debug("quality_engine: unparseable cell line %r", m.group(0))
                continue

        # Fallback: use average metrics when per-cell data unavailable
        if not data["skewness"] and metrics.cells > 0:
            n = min(metrics.cells, 1000)
            data["skewness"] = [metrics.avg_skewness or metrics.max_skewness] * n
            data["non_orthogonality"] = [metrics.avg_non_orthogonality or metrics.max_non_orthogonality] * n
            data["aspect_ratio"] = [metrics.max_aspect_ratio] * n

        return data

    def _compute_histogram(self, heatmap: dict[str, list[float]],
                           bins: int = 20) -> dict[str, Any]:
        """Compute histogram from heatmap data."""
        skew = heatmap.get("skewness", [])
        if not skew:
            return {"counts": [], "edges": [], "mean": 0.0, "max": 0.0}

        import numpy as np
        arr = np.array(skew, dtype=np.float64)
        counts, edges = np.histogram(arr, bins=bins)
        return {
            "counts": counts.tolist(),
            "edges": edges.tolist(),
            "mean": round(float(arr.mean()), 4),
            "max": round(float(arr.max()), 4),
            "p95": round(float(np.percentile(arr, 95)), 4),
            "p99": round(float(np.percentile(arr, 99)), 4),
            "n": len(arr),
        }

    # ------------------------------------------------------------------
    # Auto-fix logic
    # ------------------------------------------------------------------
    @staticmethod
    def _read_min_cell_size(case_dir: Path) -> float | None:
        """Best-effort read of the current ``minCellSize`` from meshDict."""
        meshdict_path = case_dir / "system" / "meshDict"
        if not meshdict_path.exists():
            return None
        try:
            text = meshdict_path.read_text(encoding="ascii", errors="replace")
            m = re.search(rf"minCellSize\s+({QualityEngine._OF_FLOAT});", text)
            return float(m.group(1)) if m else None
        except Exception:
            logger.exception("Could not read minCellSize from %s", meshdict_path)
            return None

    def _local_refine_fix(
        self, metrics: QualityMetrics, case_dir: Path | None, target_metric: str,
    ) -> AutoFixAction | None:
        """Try a targeted fix from checkMesh's own problem sets before
        falling back to a global relax — see
        ``local_refinement_boxes_from_checkmesh_sets``. Only used when the
        affected cells are a small slice of the mesh (< 20%): a defect
        that widespread is better served by the existing global relax,
        since a "local" box covering most of the domain buys nothing over
        it while adding meshDict complexity.
        """
        if case_dir is None:
            return None
        min_cell = self._read_min_cell_size(case_dir)
        if not min_cell or min_cell <= 0:
            return None
        try:
            boxes = local_refinement_boxes_from_checkmesh_sets(
                case_dir, cell_size=min_cell * 0.5,
            )
        except Exception:
            logger.exception("Local refinement box detection failed for %s", case_dir)
            return None
        if not boxes:
            return None
        total_flagged = sum(b.get("n_cells", 0) for b in boxes)
        if metrics.cells > 0 and total_flagged > metrics.cells * 0.2:
            logger.info(
                "Local refinement: %d/%d cells flagged (>20%%), falling back "
                "to global relax", total_flagged, metrics.cells,
            )
            return None
        return AutoFixAction(
            action="local_refine",
            target_metric=target_metric,
            current_value=float(total_flagged),
            detail=f"{len(boxes)} targeted refinement box(es) "
                   f"({total_flagged} flagged cells) from checkMesh sets",
            payload=boxes,
        )

    def _decide_fixes(
        self, metrics: QualityMetrics, case_dir: Path | None = None,
    ) -> list[AutoFixAction]:
        """Decide which auto-fix actions to apply based on metrics."""
        fixes: list[AutoFixAction] = []
        thr = THRESHOLDS

        if metrics.max_skewness > thr["skewness_max"]:
            local_fix = self._local_refine_fix(metrics, case_dir, "skewness")
            if local_fix is not None:
                fixes.append(local_fix)
            else:
                # Skewness: relax cell sizes globally (proportional to
                # severity) when no small, targeted set of bad cells was
                # found to refine instead.
                severity = (metrics.max_skewness - thr["skewness_max"]) / thr["skewness_max"]
                relax_factor = 1.0 + min(severity * 0.5, 0.5)
                fixes.append(AutoFixAction(
                    action="relax",
                    target_metric="skewness",
                    current_value=metrics.max_skewness,
                    detail=f"Increase maxCell by {relax_factor:.0%}, decrease minCell by {relax_factor*0.5:.0%}",
                ))

        if metrics.max_non_orthogonality > thr["non_ortho_max"]:
            # Non-orthogonality: reduce BL layers first (preserves mesh near other walls),
            # only disable BL entirely as a second step if it recurs.
            fixes.append(AutoFixAction(
                action="reduce_bl",
                target_metric="non_orthogonality",
                current_value=metrics.max_non_orthogonality,
                detail="Halve BL nLayers and thicknessRatio",
            ))

        if metrics.neg_cells > thr["neg_vol_max"]:
            negatives = metrics.neg_cells
            if negatives <= 10:
                factor = 1.3
            else:
                factor = 1.5 + min(negatives * 0.01, 1.0)
            fixes.append(AutoFixAction(
                action="remesh",
                target_metric="neg_vol",
                current_value=float(negatives),
                detail=f"Coarsen cells by {factor:.0%}",
            ))

        if metrics.max_aspect_ratio > thr["aspect_ratio_max"]:
            severity = (metrics.max_aspect_ratio - thr["aspect_ratio_max"]) / thr["aspect_ratio_max"]
            reduction = 0.7 - min(severity * 0.1, 0.2)
            fixes.append(AutoFixAction(
                action="split",
                target_metric="aspect_ratio",
                current_value=metrics.max_aspect_ratio,
                detail=f"Reduce maxCellSize by {(1 - reduction):.0%}",
            ))
        return fixes

    def _apply_fix(self, fix: AutoFixAction, case_dir: Path) -> None:
        """Apply a single auto-fix action to the case."""
        meshdict_path = case_dir / "system" / "meshDict"
        if not meshdict_path.exists():
            logger.warning("meshDict not found at %s", meshdict_path)
            return

        text = meshdict_path.read_text(encoding="ascii", errors="replace")

        if fix.action == "relax":
            text = self._relax_cell_sizes(text, factor=1.2)
        elif fix.action == "reduce_bl":
            text = self._reduce_boundary_layers(text)
        elif fix.action == "disable_bl":
            text = self._disable_boundary_layers(text)
        elif fix.action == "remesh":
            text = self._coarsen_mesh(text, factor=1.5)
        elif fix.action == "split":
            text = self._reduce_max_cell(text, factor=0.7)
        elif fix.action == "local_refine":
            text = self._add_local_refinement_boxes(text, fix.payload)

        meshdict_path.write_text(text, encoding="ascii")
        logger.info("Applied fix: %s on %s", fix.action, fix.target_metric)

    @staticmethod
    def _add_local_refinement_boxes(text: str, boxes: list[dict]) -> str:
        """Insert/merge an ``objectRefinements`` block built from
        ``boxes`` (see ``local_refinement_boxes_from_checkmesh_sets``)
        into an existing meshDict's text.

        A meshDict already has an ``objectRefinements { ... }`` block
        whenever curvature/manual refinement zones were configured
        upfront — appending a SECOND top-level block of the same name
        would make cfMesh only honour whichever one it parses last
        (dictionary semantics, not a merge), silently dropping the
        pre-existing zones. Detect and merge into it instead; only
        append a brand-new block when none exists yet.
        """
        from cfmesh_autogui.core.meshdict_gen import build_object_refinements

        new_lines = build_object_refinements(boxes)
        if not new_lines:
            return text
        # New entries only: build_object_refinements always numbers from
        # refinementBox_0, so re-number past however many already exist to
        # avoid colliding with existing entries when merging.
        existing_count = len(re.findall(r"\brefinementBox_\d+\b", text)) if "objectRefinements" in text else 0
        if existing_count:
            new_lines = [
                re.sub(r"refinementBox_(\d+)", lambda m: f"refinementBox_{int(m.group(1)) + existing_count}", ln)
                for ln in new_lines
            ]

        m = re.search(r"objectRefinements\s*\{", text)
        if m is None:
            return text.rstrip() + "\n\n" + "\n".join(new_lines) + "\n"

        # Splice the new entries just before the block's closing brace —
        # find it by matching balanced braces from the opening one found
        # above (entries themselves contain nested { } pairs).
        depth = 0
        i = m.end() - 1  # position of the '{' just matched
        close_idx = None
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    close_idx = j
                    break
        if close_idx is None:
            # Malformed existing block (unbalanced braces) -- append a
            # fresh block rather than risk writing something cfMesh can't
            # even parse.
            return text.rstrip() + "\n\n" + "\n".join(new_lines) + "\n"
        entries_text = "\n".join(
            ln for ln in new_lines if ln not in ("objectRefinements", "{", "}", "")
        )
        return text[:close_idx] + entries_text + "\n" + text[close_idx:]

    _OF_FLOAT = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"

    @staticmethod
    def _relax_cell_sizes(text: str, factor: float = 1.2) -> str:
        """Increase maxCell by *factor*, decrease minCell by *factor*^-1."""
        def _relax_max(m: re.Match) -> str:
            val = float(m.group(1)) * factor
            return f"maxCellSize {val:.6f};"
        def _relax_min(m: re.Match) -> str:
            val = float(m.group(1)) / factor
            return f"minCellSize {val:.6f};"
        text = re.sub(rf"maxCellSize\s+({QualityEngine._OF_FLOAT});", _relax_max, text)
        text = re.sub(rf"minCellSize\s+({QualityEngine._OF_FLOAT});", _relax_min, text)
        return text

    @staticmethod
    def _reduce_boundary_layers(text: str) -> str:
        """Halve BL nLayers and thicknessRatio to ease non-orthogonality."""
        def _halve_layers(m: re.Match) -> str:
            val = max(int(float(m.group(1)) // 2), 1)
            return f"            nLayers                 {val};"
        def _halve_growth(m: re.Match) -> str:
            val = max(float(m.group(1)) * 0.5, 1.01)
            return f"            thicknessRatio          {val:.4f};"
        text = re.sub(r"nLayers\s+(\d+);", _halve_layers, text)
        text = re.sub(rf"thicknessRatio\s+({QualityEngine._OF_FLOAT});", _halve_growth, text)
        return text

    @staticmethod
    def _disable_boundary_layers(text: str) -> str:
        """Remove boundaryLayers block from meshDict."""
        return re.sub(r"\nboundaryLayers\s*\{[^}]*\}", "", text, flags=re.DOTALL)

    @staticmethod
    def _coarsen_mesh(text: str, factor: float = 1.5) -> str:
        """Increase both cell sizes by *factor*."""
        text = re.sub(rf"maxCellSize\s+({QualityEngine._OF_FLOAT});", lambda m, f=factor: f"maxCellSize {float(m.group(1))*f:.6f};", text)
        text = re.sub(rf"minCellSize\s+({QualityEngine._OF_FLOAT});", lambda m, f=factor: f"minCellSize {float(m.group(1))*f:.6f};", text)
        return text

    @staticmethod
    def _reduce_max_cell(text: str, factor: float = 0.7) -> str:
        """Reduce maxCell by *factor*."""
        return re.sub(
            rf"maxCellSize\s+({QualityEngine._OF_FLOAT});",
            lambda m, f=factor: f"maxCellSize {float(m.group(1))*f:.6f};",
            text,
        )

    def export_json(self, report: QualityReport, path: Path | str) -> None:
        """Export quality report as JSON."""
        Path(path).write_text(json.dumps({
            "metrics": {
                "cells": report.metrics.cells,
                "max_skewness": report.metrics.max_skewness,
                "avg_skewness": report.metrics.avg_skewness,
                "max_non_orthogonality": report.metrics.max_non_orthogonality,
                "avg_non_orthogonality": report.metrics.avg_non_orthogonality,
                "max_aspect_ratio": report.metrics.max_aspect_ratio,
                "min_volume": report.metrics.min_volume,
                "neg_cells": report.metrics.neg_cells,
            },
            "passed": report.passed,
            "histogram": report.histogram,
            "fixes": [{"action": f.action, "target": f.target_metric,
                        "value": f.current_value} for f in report.fixes_applied],
        }, indent=2, default=str))


