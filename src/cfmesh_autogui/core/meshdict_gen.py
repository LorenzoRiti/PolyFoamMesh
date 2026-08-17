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

    Two zone schemas are accepted, so the pipeline-level zones (throat
    auto-refinements) and the 3D-viewer refinement boxes can coexist in the
    same list:

      - zone schema  ``{centre: (x,y,z), radius: float, cell_size: float}``
        — legacy/auto zones.  ``radius`` is the half-diagonal; emitted as a
        cube of side ``2*radius``.
      - box schema   ``{xmin,xmax,ymin,ymax,zmin,zmax, cell_size: float}``
        — placed with the in-viewer drag gizmo (``refinement_boxes.py``).
        Emitted as the real axis-aligned box it represents (centre = midpoint,
        lengthX/Y/Z = actual extents), so a non-cube box keeps its shape.

    A malformed entry (missing keys, a NaN/zero cell size, non-numeric coords)
    never aborts the run: it is skipped with a log warning and the valid zones
    are still written.  Returns empty list when nothing to refine.
    """
    if not high_curvature_regions:
        return []

    lines = ["objectRefinements", "{"]
    emitted = 0
    for region in high_curvature_regions:
        if not isinstance(region, dict):
            logger.warning(
                "Skipping malformed refinement zone: not a dict (%r)", region
            )
            continue
        cell_size = region.get("cell_size")
        if not cell_size or cell_size <= 0:
            logger.warning(
                "Skipping malformed refinement zone: bad cell_size (%r)", region
            )
            continue
        try:
            if region.get("type") == "box" or "xmin" in region:
                # 3D-viewer box: exact axis-aligned cuboid.
                xmin, xmax = float(region["xmin"]), float(region["xmax"])
                ymin, ymax = float(region["ymin"]), float(region["ymax"])
                zmin, zmax = float(region["zmin"]), float(region["zmax"])
                cx = (xmin + xmax) / 2.0
                cy = (ymin + ymax) / 2.0
                cz = (zmin + zmax) / 2.0
                len_x = abs(xmax - xmin)
                len_y = abs(ymax - ymin)
                len_z = abs(zmax - zmin)
            else:
                # Legacy zone: cube built from the half-diagonal radius.
                # Kept formatting-identical to the historical output (the
                # caller-provided numbers are emitted verbatim).
                cx, cy, cz = region["centre"]
                r = region["radius"]
                len_x = len_y = len_z = abs(float(r)) * 2.0
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Skipping malformed refinement zone: %s (%r)", exc, region
            )
            continue
        lines.append(f"    refinementBox_{emitted}")
        lines.append("    {")
        lines.append("        type    box;")
        lines.append(f"        centre  ({cx} {cy} {cz});")
        lines.append(f"        lengthX {len_x};")
        lines.append(f"        lengthY {len_y};")
        lines.append(f"        lengthZ {len_z};")
        lines.append(f"        cellSize {cell_size};")
        lines.append("    }")
        emitted += 1
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
    local_refinement: dict[str, dict] | None = None,
    edge_mesh_refinement: list[dict] | None = None,
    workflow_stop_after: str | None = None,
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
        object_refinements: list of dicts in either the zone schema
            {centre, radius, cell_size} or the 3D-box schema
            {type:"box", xmin, xmax, ymin, ymax, zmin, zmax, cell_size}.
            If None or empty, no local refinement. Malformed entries are
            skipped with a warning, never fatal.
        local_refinement: dict {patch_regex: {additional_refinement_levels: N,
            refinement_thickness: metres}} — refines the VOLUME of cells
            within refinement_thickness of the named patches by N octree
            levels (cfMesh's native currency: LEVELS, not absolute metres —
            see commit 9904d65 for why absolute sizes crash the octree).
            Distinct from patch_cell_size (which fixes size ON the faces).
            Each entry validated; malformed entries are skipped with a
            warning, never fatal.
        edge_mesh_refinement: list of dicts {edge_file: path, levels: N} —
            refines cells near feature edges read from an OpenFOAM edgeMesh
            file (eMesh/obj/vtk — NOT the .fms surface format, which
            cartesianMesh rejects: "Unknown edge format fms", verified
            against the installed binary). If None, no edge refinement.
        workflow_stop_after: step name to stop after (cfMesh workflowControls
            stopAfter; e.g. "refineBoundaryLayers"). If None, no workflow
            controls block is written.
    """
    lines: list[str] = [
        'FoamFile { version 2.0; format ascii; class dictionary; object meshDict; }',
        "",
        "keepCellsIntersectingBoundary 1;",
        "allowDisconnected 1;",
        "maxNumIterations 100;",
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
        # Optimisation parameters for boundary layer quality:
        # - nSmoothNormals: number of normal-direction smoothing passes
        # - maxNumIterations: max iterations for boundary layer optimisation
        #   (this is BL-specific, independent of the global maxNumIterations)
        # - featureSizeFactor: controls how much the BL follows surface features
        # - reCalculateNormals: recalculate normals every N iterations
        # - relThicknessTol: relative thickness tolerance for layer collapse
        lines.append("    optimisationParameters")
        lines.append("    {")
        lines.append("        nSmoothNormals 3;")
        lines.append("        maxNumIterations 10;")
        lines.append("        featureSizeFactor 0.5;")
        lines.append("        reCalculateNormals 2;")
        lines.append("        relThicknessTol 0.1;")
        lines.append("    }")
        lines.append("}")
        lines.append("")

    # objectRefinements: local refinement boxes around high-curvature regions
    # or small features.  cfMesh refines cells inside these boxes to the
    # specified cellSize, giving local resolution without global cell inflation.
    if object_refinements:
        lines.extend(build_object_refinements(object_refinements))

    # localRefinement: refines the VOLUME of cells within
    # `refinementThickness` of the named patches by `additionalRefinementLevels`
    # octree levels. This is the "make cells smaller near THIS wall" control
    # that patchCellSize (which fixes size ON the faces) does not provide.
    #
    # CRITICAL — work in cfMesh's native currency (LEVELS, not metres):
    # imposing absolute cell sizes on an octree that reasons in levels
    # crashed and slowed real user geometry (commit 9904d65, feature
    # disabled after that). additionalRefinementLevels N halves the cell
    # N times near the patch — a level-relative, crash-free formulation.
    # Verified against the installed cartesianMesh binary: the block is
    # accepted and applied ("Refining boundary boxes to the given size").
    if local_refinement:
        lines.append("localRefinement")
        lines.append("{")
        emitted = 0
        for patch_regex, cfg in local_refinement.items():
            try:
                levels = int(cfg.get("additional_refinement_levels", 1))
                thickness = float(cfg.get("refinement_thickness", 0.0))
                if levels < 1 or thickness <= 0.0:
                    raise ValueError(f"levels={levels} thickness={thickness}")
            except (AttributeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Skipping malformed localRefinement entry %r: %s",
                    patch_regex, exc,
                )
                continue
            lines.append(f'    "{patch_regex}"')
            lines.append("    {")
            lines.append(f"        additionalRefinementLevels {levels};")
            lines.append(f"        refinementThickness {thickness:.8g};")
            lines.append("    }")
            emitted += 1
        if emitted:
            lines.append("}")
            lines.append("")
        else:
            # nothing valid emitted — drop the now-empty block
            # (we appended the block name AND its opening brace)
            lines.pop()
            lines.pop()

    # edgeMeshRefinement: refines cells near feature edges read from an
    # OpenFOAM edgeMesh file.
    #
    # VERIFIED LIMITATION (against the installed binary): the file must be a
    # real edgeMesh format — eMesh / featureEdgeMesh / obj / vtk / ... — NOT
    # the .fms surface file that generate_fms() writes. cartesianMesh rejects
    # .fms outright: "Unknown edge format fms for file ... Valid types:
    # (bdf eMesh featureEdgeMesh nas nastran obj starcd vtk)". The .fms is
    # meant to be the *surfaceFile*, not an edge file. Generate an edge mesh
    # with surfaceFeatureExtract (writeFeatureEdgeMesh) if this path is used.
    if edge_mesh_refinement:
        lines.append("edgeMeshRefinement")
        lines.append("{")
        emitted = 0
        for entry in edge_mesh_refinement:
            if not isinstance(entry, dict):
                logger.warning(
                    "Skipping malformed edgeMeshRefinement entry: %r", entry
                )
                continue
            edge_file = entry.get("edge_file")
            levels = entry.get("levels")
            try:
                levels = int(levels) if levels is not None else 1
                if not edge_file or levels < 1:
                    raise ValueError(f"edge_file={edge_file!r} levels={levels}")
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "Skipping malformed edgeMeshRefinement entry: %s", exc
                )
                continue
            lines.append(f'    "edgeRefinement_{emitted}"')
            lines.append("    {")
            lines.append(f'        edgeFile "{edge_file}";')
            lines.append(f"        additionalRefinementLevels {levels};")
            lines.append("    }")
            emitted += 1
        if emitted:
            lines.append("}")
            lines.append("")
        else:
            lines.pop()
            lines.pop()

    # workflowControls: lets a run stop after a named phase (or restart from
    # the latest completed step on the next invocation). Exposed so the
    # meshing pipeline can stop after e.g. surface refinement to inspect the
    # partial mesh — the mechanism the pipeline front needs to diagnose a
    # core dump. Verified: cartesianMesh accepts the block and completes the
    # run with the mesh written to disk.
    if workflow_stop_after:
        lines.append("workflowControls")
        lines.append("{")
        lines.append(f'    stopAfter "{workflow_stop_after}";')
        lines.append("}")
        lines.append("")

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
    local_refinement: dict[str, dict] | None = None,
    edge_mesh_refinement: list[dict] | None = None,
    workflow_stop_after: str | None = None,
    max_cells_override: int | None = None,
) -> Path:
    case_dir = Path(case_dir)
    system_dir = case_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

    # RAM-aware coarsening safety net (the GMSH/tet path has had this via
    # gmsh_wrapper._hardware_budget for a while; cfMesh had nothing, so a
    # fine maxCellSize on a large domain could ask for more cells than fit
    # in memory with no warning at all until the process thrashed or was
    # killed). Best-effort: read the surface STL's own bounding box to
    # estimate domain volume — cheap (bounds only, no processing) and
    # needs no caller changes, so it protects every write_meshdict caller
    # (GUI, benchmarks, mesh_engine) uniformly, not just the GUI's own
    # validate_cell_sizes() call which most callers don't go through.
    try:
        stl_path = Path(surface_file)
        if not stl_path.is_absolute():
            stl_path = case_dir / stl_path
        if stl_path.exists():
            import trimesh as _trimesh

            from cfmesh_autogui.core.geometry import coarsen_for_ram_budget

            surf = _trimesh.load(str(stl_path), force="mesh", process=False)
            dx, dy, dz = (surf.bounds[1] - surf.bounds[0])
            domain_volume = float(max(dx * dy * dz, 1e-12))
            max_cell_size, ram_warnings = coarsen_for_ram_budget(
                max_cell_size, domain_volume, max_cells_override,
            )
            for w in ram_warnings:
                logger.warning("meshDict RAM safeguard: %s", w)
            if ram_warnings and min_cell_size > max_cell_size / 2.0:
                min_cell_size = max_cell_size / 2.0
        else:
            logger.warning(
                "meshDict RAM safeguard skipped: surface file not found at %s — "
                "the requested cell sizes will be written UNCHANGED, which can "
                "crash on a mesh that exceeds available RAM", stl_path,
            )
    except Exception:
        logger.exception(
            "meshDict RAM safeguard FAILED — requested cell sizes written "
            "UNCHANGED, run is at risk of an out-of-memory crash"
        )

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
        local_refinement=local_refinement,
        edge_mesh_refinement=edge_mesh_refinement,
        workflow_stop_after=workflow_stop_after,
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
