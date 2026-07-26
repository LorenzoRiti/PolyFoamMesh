"""Generate meshDict for cfMesh v2512.

This is the file cfMesh/cartesianMesh reads to decide:
  - what surface to mesh (surfaceFile)
  - global cell size constraints (maxCellSize, minCellSize)
  - per-patch cell size overrides (patchCellSize)
  - boundary refinement (boundaryCellSize + boundaryCellSizeRefinementThickness)
  - boundary layers (boundaryLayers > patchBoundaryLayers > regex > { nLayer, ... })

Syntax verified against the official OpenFOAM v2512 tutorials at
/usr/lib/openfoam/openfoam2512/tutorials/.../meshDict.
"""
from __future__ import annotations

import logging
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)


def infer_patch_type(patch_name: str) -> str:
    """Map a patch name to the OpenFOAM boundary type cfMesh should assign.

    cfMesh types EVERY patch as `wall` unless a renameBoundary block says
    otherwise. A patch typed `wall` cannot carry an inlet/outlet boundary
    condition — the solver (and BaramFlow) treat it as a solid surface — so
    without this the generated mesh is geometrically fine but physically
    unusable.
    """
    n = patch_name.lower()
    if "inlet" in n or "outlet" in n or "opening" in n or "farfield" in n:
        return "patch"
    if "symmetry" in n:
        return "symmetryPlane"
    if "empty" in n:
        return "empty"
    return "wall"


def build_object_refinements(
    high_curvature_regions: list[dict] | None = None,
) -> list[str]:
    """Build ``objectRefinements`` block for cfMesh meshDict.

    Each high-curvature region is a dict with:
      - centre (tuple[float,float,float])
      - radius (float) — half-diagonal of the refinement box
      - cell_size (float) — target cell size inside the box

    Emits box-shaped refinement regions.  Returns empty list when nothing
    to refine.
    """
    if not high_curvature_regions:
        return []

    lines = ["objectRefinements", "{"]
    for i, region in enumerate(high_curvature_regions):
        cx, cy, cz = region["centre"]
        r = region["radius"]
        cs = region["cell_size"]
        lines.append(f"    refinementBox_{i}")
        lines.append("    {")
        lines.append("        type    box;")
        lines.append(f"        min     ({cx - r} {cy - r} {cz - r});")
        lines.append(f"        max     ({cx + r} {cy + r} {cz + r});")
        lines.append(f"        cellSize {cs};")
        lines.append("    }")
    lines.append("}")
    lines.append("")
    return lines


def build_meshdict_lines(
    max_cell: float = 0.05,
    min_cell: float = 0.01,
    patch_cell_size: dict[str, float] | None = None,
    boundary_cell_size: float | None = None,
    boundary_refinement_thickness: float | None = None,
    surface_file: str = "constant/triSurface/surface.stl",
    bl_params: dict | None = None,
    patch_names: list[str] | None = None,
    patch_types: dict[str, str] | None = None,
    object_refinements: list[dict] | None = None,
) -> list[str]:
    """Build meshDict content for cfMesh v2512.

    Args:
        max_cell: global max cell size (m). Cells anywhere will be <= this.
        min_cell: global min cell size (m). Cells anywhere will be >= this.
        patch_cell_size: dict {patch_name: cell_size} for per-patch overrides.
            Patches NOT in this dict use the global max_cell/min_cell. Patches
            IN this dict get a tighter cell size (so e.g. a thin constriction
            gets small cells while a wide tube body keeps coarse cells).
        boundary_cell_size: cell size near boundaries (e.g. 0.5 * max_cell).
            If None, no boundary refinement is requested.
        boundary_refinement_thickness: distance (m) from the boundary within
            which boundaryCellSize applies. If None, no boundary refinement.
        surface_file: path to the surface STL.
        bl_params: dict with keys nLayers, thicknessRatio, expansionRatio,
            wallPatches (list of patch names). If None, no boundary layers.
        object_refinements: list of dicts with keys centre, radius, cell_size
            for local refinement boxes. If None or empty, no local refinement.
    """
    lines: list[str] = [
        'FoamFile { version 2.0; format ascii; class dictionary; object meshDict; }',
        "",
        "keepCellsIntersectingBoundary 1;",
        "allowDisconnected 1;",
        "maxNumIterations 100;",
        "",
        "optimisationParameters",
        "{",
        "    nSmoothNormals 3;",
        "    featureSizeFactor 0.5;",
        "    relThicknessTol 0.1;",
        "}",
        "",
        f'surfaceFile "{surface_file}";',
        f"maxCellSize {max_cell};",
        f"minCellSize {min_cell};",
    ]

    if patch_cell_size:
        # Each patch entry must be a SUB-DICTIONARY containing `cellSize`,
        # not a flat scalar — cfMesh's checkMeshDict::updatePatchCellSize()
        # reads patchCellSize/<patch>/cellSize. Writing `"wall" 0.25;`
        # (a flat primitive entry) crashes cartesianMesh on every real run
        # with per-patch sizing: "FOAM FATAL ERROR: Attempt to return
        # primitive entry ITstream ... as a sub-dictionary" — verified live
        # against real OpenFOAM 2512 (the error, and the fix, both
        # confirmed: `cellSize` is the exact key name compiled into
        # libmeshLibrary.so's updatePatchCellSize/patchRefinement symbols).
        lines.append("patchCellSize")
        lines.append("{")
        for name, size in patch_cell_size.items():
            lines.append(f'    "{name}"')
            lines.append("    {")
            lines.append(f"        cellSize {size};")
            lines.append("    }")
        lines.append("}")
        lines.append("")

    if boundary_cell_size is not None and boundary_refinement_thickness is not None:
        # The v2512 boundary refinement: cell size near a boundary is
        # `boundaryCellSize`, applied for a distance of
        # `boundaryRefinementThickness` from the boundary. After that
        # distance, cells grow to `maxCellSize`. This is how cfMesh
        # naturally produces smaller cells near walls without manually
        # classifying every patch.
        lines.append(f"boundaryCellSize {boundary_cell_size};")
        lines.append(
            f"boundaryCellSizeRefinementThickness "
            f"{boundary_refinement_thickness};"
        )
        lines.append("")

    if bl_params:
        wall_patches = bl_params.get("wallPatches") or ["wall"]
        if isinstance(wall_patches, str):
            wall_patches = [wall_patches]
        # v2512 syntax: boundaryLayers > patchBoundaryLayers > regex > { ... }
        # The regex matches one or more patch names with "|" alternation.
        regex = "|".join(wall_patches)

        # BL contract (unambiguous, so callers can't disagree):
        #   thicknessRatio      -> cfMesh's layer-to-layer GROWTH ratio (>1)
        #   firstLayerThickness -> ABSOLUTE first-layer height in metres
        # cfMesh has no `expansionRatio` key (it silently ignores it), and its
        # `thicknessRatio` is the growth ratio, NOT a first-layer fraction.
        # Accept the legacy `expansionRatio` as a growth fallback, and guard
        # against a fraction (<=1) being passed as the growth ratio — that used
        # to collapse the layers to nothing.
        growth = float(
            bl_params.get("thicknessRatio")
            or bl_params.get("expansionRatio")
            or 1.2
        )
        if growth <= 1.0:
            logger.warning(
                "BL growth ratio %.4g <= 1 (a first-layer fraction was likely "
                "passed as thicknessRatio); using 1.2.", growth,
            )
            growth = 1.2

        first_layer_abs = bl_params.get("firstLayerThickness")

        lines.append("boundaryLayers")
        lines.append("{")
        lines.append("    patchBoundaryLayers")
        lines.append("    {")
        lines.append(f'        "{regex}"')
        lines.append("        {")
        lines.append(f"            nLayers                 {bl_params['nLayers']};")
        lines.append(f"            thicknessRatio          {growth};")
        if first_layer_abs:
            # Without this, cfMesh sizes the first layer itself and the y+
            # target the app computed is never actually applied to the mesh.
            lines.append(f"            maxFirstLayerThickness  {float(first_layer_abs):.8g};")
            lines.append("            optimiseLayer           1;")
            lines.append("            untangleLayers          1;")
            # Protection against layer collapse on sharp convex edges:
            # - maxThicknessToMedialRatio caps layer growth near thin gaps
            # - reCalculateNormals recalculates extrusion direction near sharp angles
            # - featureAngle prevents BL on faces whose normals differ beyond this
            lines.append("            maxThicknessToMedialRatio 0.3;")
            lines.append("            reCalculateNormals       1;")
            lines.append("            maxBoundaryLayerAngle    60;")
            lines.append("        }")
        lines.append("    }")
        lines.append("}")
        lines.append("")

    # objectRefinements: local refinement boxes around high-curvature regions
    # or small features.  cfMesh refines cells inside these boxes to the
    # specified cellSize, giving local resolution without global cell inflation.
    if object_refinements:
        lines.extend(build_object_refinements(object_refinements))

    # renameBoundary: without this cfMesh types every patch as `wall`, so an
    # inlet/outlet cannot take a flow boundary condition downstream.
    names = list(patch_names or [])
    if patch_types:
        names = list(dict.fromkeys(names + list(patch_types)))
    if names:
        resolved = {n: (patch_types or {}).get(n) or infer_patch_type(n) for n in names}
        non_default = {n: t for n, t in resolved.items() if t != "wall"}
        if non_default:
            lines.append("renameBoundary")
            lines.append("{")
            lines.append("    defaultName     wall;")
            lines.append("    defaultType     wall;")
            lines.append("    newPatchNames")
            lines.append("    {")
            for name, ptype in non_default.items():
                lines.append(f'        "{name}"')
                lines.append("        {")
                lines.append(f"            newName     {name};")
                lines.append(f"            type        {ptype};")
                lines.append("        }")
            lines.append("    }")
            lines.append("}")
            lines.append("")

    return lines


def write_meshdict(
    case_dir: Path | str,
    max_cell_size: float = 0.05,
    min_cell_size: float = 0.01,
    patch_cell_size: dict[str, float] | None = None,
    boundary_cell_size: float | None = None,
    boundary_refinement_thickness: float | None = None,
    surface_file: str = "constant/triSurface/surface.stl",
    bl_params: dict | None = None,
    patch_names: list[str] | None = None,
    patch_types: dict[str, str] | None = None,
    object_refinements: list[dict] | None = None,
) -> Path:
    case_dir = Path(case_dir)
    system_dir = case_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

    # Audit log: every write_meshdict call records caller, values, and timestamp
    _audit_log = Path.home() / ".cfmesh_meshdict_audit.log"
    _stack = "".join(traceback.format_stack(limit=8)[:-2])
    with open(_audit_log, "a", encoding="utf-8") as _af:
        _af.write(
            f"[max={max_cell_size} min={min_cell_size} bl={bl_params is not None} "
            f"dir={case_dir}]\n{_stack}\n"
        )

    lines = build_meshdict_lines(
        max_cell=max_cell_size,
        min_cell=min_cell_size,
        patch_cell_size=patch_cell_size,
        boundary_cell_size=boundary_cell_size,
        boundary_refinement_thickness=boundary_refinement_thickness,
        surface_file=surface_file,
        bl_params=bl_params,
        patch_names=patch_names,
        patch_types=patch_types,
        object_refinements=object_refinements,
    )

    if bl_params:
        wp = bl_params.get("wallPatches") or ["wall"]
        if isinstance(wp, str):
            wp = [wp]
        logger.info(
            "meshDict: BL enabled — regex='%s' nLayers=%d "
            "thicknessRatio=%.4e expansionRatio=%.2f",
            "|".join(wp),
            bl_params.get("nLayers", 3),
            bl_params.get("thicknessRatio", 0.005),
            bl_params.get("expansionRatio", 1.2),
        )
    else:
        logger.info("meshDict: no boundary layers.")
    if patch_cell_size:
        logger.info(
            "meshDict: per-patch cell sizes: %s",
            ", ".join(f"{n}={s:.4e}" for n, s in patch_cell_size.items()),
        )
    if boundary_cell_size is not None:
        logger.info(
            "meshDict: boundaryCellSize=%.4e, refinementThickness=%.4e",
            boundary_cell_size, boundary_refinement_thickness,
        )

    meshdict_path = system_dir / "meshDict"
    with open(meshdict_path, "w", encoding="ascii") as f:
        f.write("\n".join(lines) + "\n")
    return meshdict_path
