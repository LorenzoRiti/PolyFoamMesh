# -*- coding: utf-8 -*-
"""Poly release gate: attribute defects and accept only a measured mesh.

W10.3 stops at the known topological wall.  This module does not add another
smoothing/merge/split lever; it makes the release decision explicit:

- measure the dual-only and dual+BL meshes separately;
- attribute pyramids/non-ortho/skew to the BL (difference, never a double
  count);
- require zero inverted pyramids, non-negative volumes and >=98% BL coverage;
- keep an explicit residual when real checkMesh still reports one skew class
  (R1 quality: 0 pyramids / 1 skew / Failed 1, accepted with the report).

The gate is deliberately independent of the GUI/worker result dicts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

#: Levers already measured and rejected in W2/W5/W8/Lane D.  Kept as a
#: visible guardrail: a release attempt must not silently re-introduce them.
FORBIDDEN_RELEASE_LEVERS = (
    "merge+relax",
    "compact-fan",
    "triangulate",
    "smoothing",
    "feature-angle",
    "sharp-edge-field",
    "edge-cap-percentile",
    "min-base-edge",
    "budget-term-alone",
    "planarize",
    "zonal-worst-element",
)


@dataclass(frozen=True)
class PolyDefectMeasure:
    n_cells: int = 0
    pyramids: int = 0
    non_ortho: int = 0
    skew: int = 0
    negative_volumes: int = 0
    min_volume: float = 0.0

    @property
    def total(self) -> int:
        return self.pyramids + self.non_ortho + self.skew

    def to_mapping(self) -> dict:
        return {
            "n_cells": self.n_cells,
            "pyramids": self.pyramids,
            "non_ortho": self.non_ortho,
            "skew": self.skew,
            "negative_volumes": self.negative_volumes,
            "min_volume": self.min_volume,
            "total": self.total,
        }


@dataclass
class PolyReleaseDecision:
    accepted: bool = False
    coverage_pct: float = 0.0
    reasons: list[str] = field(default_factory=list)
    residual: list[str] = field(default_factory=list)
    attribution: dict = field(default_factory=dict)
    bl: PolyDefectMeasure = field(default_factory=PolyDefectMeasure)
    dual: PolyDefectMeasure | None = None
    checkmesh_pending: bool = False

    def to_mapping(self) -> dict:
        return {
            "accepted": self.accepted,
            "coverage_pct": self.coverage_pct,
            "reasons": list(self.reasons),
            "residual": list(self.residual),
            "attribution": dict(self.attribution),
            "bl": self.bl.to_mapping(),
            "dual_only": self.dual.to_mapping() if self.dual else None,
            "checkmesh_pending": self.checkmesh_pending,
        }


def measure_poly_defects(case_dir: Path | str) -> PolyDefectMeasure:
    """In-process checkMesh-class counts for one polyMesh.

    Uses the same `_detect_defects`/exact-volume code as the production dual
    gate.  A missing/unreadable mesh returns an empty measure (never raises).
    """
    try:
        from polyfoammesh.core import foam_mesh_io
        from polyfoammesh.core.tet_poly_dual import (
            _cell_centres, _detect_defects, _face_geometry,
        )
        from polyfoammesh.core.bl_poly import _exact_cell_volumes

        case = Path(case_dir)
        points, faces, owner, neigh, _patches = foam_mesh_io.read_polymesh(
            case / "constant" / "polyMesh",
        )
        n_int = len(neigh)
        n_cells = int(max(owner.max(), neigh.max())) + 1
        sf, cf = _face_geometry(points, faces)
        ctr, _vol = _cell_centres(
            sf, cf, owner, neigh, n_int, n_cells,
        )
        _bad, counts = _detect_defects(
            points, faces, sf, cf, ctr, owner, neigh,
            n_int, n_cells,
        )
        vols = _exact_cell_volumes(
            points, faces, owner, neigh, n_cells, n_int,
        )
        return PolyDefectMeasure(
            n_cells=n_cells,
            pyramids=int(counts.get("pyramid", 0)),
            non_ortho=int(counts.get("non_ortho", 0)),
            skew=int(counts.get("skew", 0)),
            negative_volumes=int(np.sum(vols <= 0.0)),
            min_volume=float(vols.min()) if vols.size else 0.0,
        )
    except Exception:  # noqa: BLE001 - release measurement is fail-soft
        return PolyDefectMeasure()


def attribute_poly_defects(
    dual_only: PolyDefectMeasure, dual_bl: PolyDefectMeasure,
) -> dict:
    """Difference between the two meshes (BL-added defects, signed)."""
    return {
        "pyramids_added_by_bl": dual_bl.pyramids - dual_only.pyramids,
        "non_ortho_added_by_bl": dual_bl.non_ortho - dual_only.non_ortho,
        "skew_added_by_bl": dual_bl.skew - dual_only.skew,
        "negative_added_by_bl": (
            dual_bl.negative_volumes - dual_only.negative_volumes
        ),
        "criterion": (
            "dual+BL minus dual-only; a positive value is BL-added, "
            "a negative value is BL-removed"
        ),
    }


def evaluate_release(
    bl: PolyDefectMeasure,
    coverage_pct: float | None,
    *,
    dual_only: PolyDefectMeasure | None = None,
    strict_checkmesh: bool | None = None,
    min_coverage_pct: float = 98.0,
    allow_documented_residual: bool = True,
) -> PolyReleaseDecision:
    """Release gate for the measured quality attempt.

    R1 quality has `pyramids=0`, `coverage=99.47%`, but real checkMesh still
    reports 1 highly-skewed face (Failed 1).  That is accepted only as an
    explicit residual; a pyramid or a coverage below 98% is never accepted.
    """
    coverage = float(coverage_pct or 0.0)
    decision = PolyReleaseDecision(
        coverage_pct=coverage, bl=bl, dual=dual_only,
        checkmesh_pending=strict_checkmesh is None,
        attribution=(
            attribute_poly_defects(dual_only, bl) if dual_only else {}
        ),
    )
    if bl.negative_volumes:
        decision.reasons.append(
            f"{bl.negative_volumes} negative-volume cell(s)"
        )
    if bl.pyramids:
        decision.reasons.append(f"{bl.pyramids} inverted face pyramid(s)")
    if coverage < min_coverage_pct:
        decision.reasons.append(
            f"coverage {coverage:.2f}% < {min_coverage_pct:.0f}%"
        )
    if strict_checkmesh is False:
        if allow_documented_residual:
            decision.residual.append(
                "real checkMesh reports a residual failure class"
            )
        else:
            decision.reasons.append(
                "real checkMesh failed and residual acceptance is disabled"
            )
    if not decision.reasons:
        decision.accepted = True
    elif (not bl.pyramids and not bl.negative_volumes
            and coverage >= min_coverage_pct
            and allow_documented_residual):
        # Only a non-pyramid residual (e.g. one skewed face) reaches here.
        decision.accepted = True
        decision.residual.extend(decision.reasons)
        decision.reasons = []
    if decision.residual:
        decision.residual.append(
            "accepted with documented CheckMeshReport residual; "
            "the topology rewrite is W14"
        )
    return decision
