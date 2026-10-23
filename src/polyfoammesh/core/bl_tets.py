"""Opt-in boundary layers directly on a tetrahedral polyMesh (W13).

The prototype deliberately reuses :class:`PolyBoundaryLayerEngine`: the same
engine that adds prisms to the dual poly mesh already knows how to replace
boundary faces, move the adjacent core vertices and gate closure/volume.  On
a *tet* polyMesh it therefore produces a conforming prism shell with the
remaining tetrahedra as the core, without ever building the dual boundary.

This module is an adapter, not a second BL engine.  It is opt-in through the
``bl_on_tets`` mesher key; no existing default is changed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .bl_poly import PolyBoundaryLayerEngine


@dataclass
class BlOnTetsResult:
    """Facts from a direct tet-boundary BL run (no dual conversion)."""

    success: bool
    case_dir: Path
    n_layers: int
    first_height: float
    growth_rate: float
    wall_patches: list[str] = field(default_factory=list)
    n_prism_cells: int = 0
    n_wall_faces: int = 0
    coverage: float = 0.0
    h1_median: float | None = None
    h1_ratio: float | None = None
    report: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    provenance: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)


def build_bl_on_tets(
    case_dir: Path | str,
    *,
    n_layers: int,
    first_height: float,
    growth_rate: float = 1.2,
    patch_names: list[str] | None = None,
    apply_to_all: bool = False,
    log: Callable[[str], None] | None = None,
) -> BlOnTetsResult:
    """Add a prism BL to a pure-tet case and keep the tet core.

    The caller must pass the BL parameters explicitly: this is a prototype,
    so there is no hidden default and no promotion of the experimental path.
    The existing engine performs the actual closure/volume validation and
    leaves the mesh untouched on failure.
    """
    case = Path(case_dir)
    poly = case / "constant" / "polyMesh"
    required = ("points", "faces", "owner", "neighbour", "boundary")
    missing = [name for name in required if not (poly / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"BL on tets needs a complete polyMesh; missing {missing} in {poly}"
        )

    selected = list(patch_names or [])
    if not apply_to_all and not selected:
        # Reuse the single patch-selection policy (wall names / inferred
        # roles / tier-3 exclusion).  No second selector lives here.
        from .openfoam_runner import poly_bl_patch_selection

        selected, _all_flag, _notes = poly_bl_patch_selection(case)
        if selected is None:
            raise ValueError(
                "BL on tets: no safe wall patch selected; refusing to "
                "extrude into the whole boundary"
            )

    started = time.monotonic()
    result = PolyBoundaryLayerEngine(case, log=log).run(
        n_layers=int(n_layers),
        first_height=float(first_height),
        growth_rate=float(growth_rate),
        apply_to_all=bool(apply_to_all),
        patch_names=selected,
    )
    elapsed = time.monotonic() - started

    wall_patches = list(getattr(result, "wall_patches", []) or [])
    report: dict = {}
    if result.success:
        try:
            from .openfoam_runner import boundary_layer_report

            report = boundary_layer_report(
                case,
                requested_h1=float(first_height),
                bl_patch_names=wall_patches,
            )
        except Exception as exc:  # noqa: BLE001 - metrics must not undo a build
            report = {"error": f"post-build report unavailable: {exc}"}

    return BlOnTetsResult(
        success=bool(result.success),
        case_dir=case,
        n_layers=int(n_layers),
        first_height=float(first_height),
        growth_rate=float(growth_rate),
        wall_patches=wall_patches,
        n_prism_cells=int(getattr(result, "n_prism_cells", 0) or 0),
        n_wall_faces=int(getattr(result, "n_wall_faces", 0) or 0),
        coverage=float(report.get("coverage", 0.0) or 0.0),
        h1_median=report.get("h1_median"),
        h1_ratio=report.get("h1_ratio"),
        report=report,
        errors=list(getattr(result, "errors", []) or []),
        warnings=list(getattr(result, "warnings", []) or []),
        elapsed_s=elapsed,
        provenance={
            "engine": "bl_poly",
            "boundary": "tet",
            "dual_used": False,
            "mode": "bl_on_tets",
            "patch_selection": (
                "all" if apply_to_all else "poly_bl_patch_selection"
            ),
        },
        stats=dict(getattr(result, "stats", {}) or {}),
    )


__all__ = ["BlOnTetsResult", "build_bl_on_tets"]
