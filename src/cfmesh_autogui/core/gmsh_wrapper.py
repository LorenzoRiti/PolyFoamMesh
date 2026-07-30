"""GMSH Python API wrapper — CAD analysis, surface mesh, volume mesh.

GMSH runs natively on Windows (no WSL). It uses OpenCASCADE to read
STEP files directly, compute curvature and proximity fields, and
generate high-quality surface and volume meshes.

Integration with cfMesh:
  GMSH_HYBRID  → GMSH analyses CAD + generates surface STL with
                  curvature-aware refinement → cfMesh fills volume
  GMSH_DIRECT  → GMSH generates tetrahedral mesh + BL → meshio
                  converts to OpenFOAM polyMesh

Requirements:
  pip install gmsh meshio
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_GMSH_INITIALIZED = False
_GMSH_LOCK = threading.Lock()  # ✅ F-014

# Issue 2 fix: single canonical detail definition used by all three
# functions (compute_sizing, generate_surface_stl, generate_volume_mesh).
# Representative computed sizes assume lc_user ≈ max_extent × 0.02 (1 m bbox → 0.02).
# Actual runtime values = lc_user × min_mult / max_mult.
_GMSH_DETAIL = {
    # very_coarse/very_fine extend the same progression to match the 5-level
    # GUI slider ("Molto Grossolana".."Molto Fine") — without these, the two
    # extreme slider positions fell back silently to "medium" (the dict
    # lookup default), making them look broken/no-op in the UI.
    "very_coarse": {"curv_angle": 45, "min_mult": 0.08,  "max_mult": 2.5,  "vol_mult": 0.03,
                     "min_size": 0.002, "max_size": 0.06},
    "coarse": {"curv_angle": 30, "min_mult": 0.05,  "max_mult": 2.0,  "vol_mult": 0.02,
               "min_size": 0.001, "max_size": 0.04},
    "medium": {"curv_angle": 18, "min_mult": 0.02,  "max_mult": 1.5,  "vol_mult": 0.015,
               "min_size": 0.0004, "max_size": 0.03},
    "fine":   {"curv_angle": 10, "min_mult": 0.01,  "max_mult": 1.0,  "vol_mult": 0.01,
               "min_size": 0.0002, "max_size": 0.02},
    "very_fine": {"curv_angle": 6, "min_mult": 0.005, "max_mult": 0.75, "vol_mult": 0.007,
                  "min_size": 0.0001, "max_size": 0.015},
}


@dataclass
class GmshSizing:
    """Structured sizing information computed by GMSH for a CAD geometry."""
    min_curvature_radius: float = 1.0
    min_proximity: float = 1.0
    max_extent: float = 1.0
    suggested_surface_size: float = 0.05
    suggested_volume_size: float = 0.1
    # List of (patch_name, suggested_size) for each physical surface
    patch_sizes: dict[str, float] = field(default_factory=dict)
    n_physical_groups: int = 0


def _ensure_gmsh():
    """Start GMSH if not already running (thread-safe with double-checked locking)."""
    global _GMSH_INITIALIZED
    if not _GMSH_INITIALIZED:
        with _GMSH_LOCK:
            if not _GMSH_INITIALIZED:  # double-checked locking
                import gmsh
                # interruptible=True (gmsh's default) calls signal.signal(),
                # which only works on the main thread — this function is
                # explicitly documented/locked for multi-threaded callers,
                # and the same call crashed feature_detector.py's worker
                # thread with "signal only works in main thread of the main
                # interpreter" when it ran off the main thread (confirmed
                # live). Skip the signal-handler install entirely.
                gmsh.initialize(interruptible=False)
                gmsh.option.setNumber("General.Terminal", 0)
                _GMSH_INITIALIZED = True
    import gmsh
    return gmsh


def gmsh_shutdown():
    """Finalize GMSH. Call once at app exit."""
    global _GMSH_INITIALIZED
    if _GMSH_INITIALIZED:
        import gmsh
        try:
            gmsh.finalize()
        except Exception:
            pass
        _GMSH_INITIALIZED = False


def from_step(filepath: Path | str) -> list[str]:
    """Open a STEP file in GMSH, return physical surface names.

    Uses OpenCASCADE kernel. Returns the list of patch names found
    (physical groups), ready to be mapped to inlet/outlet/wall.
    """
    gmsh = _ensure_gmsh()
    gmsh.clear()
    filepath = str(Path(filepath).resolve())
    gmsh.open(filepath)

    # Get physical groups (these are the user-defined patch names from
    # the STEP file; if none exist, GMSH auto-assigns dim tags).
    groups = gmsh.model.getPhysicalGroups()
    names: list[str] = []
    for dim, tag in groups:
        name = gmsh.model.getPhysicalName(dim, tag)
        if not name:
            name = f"surface_{dim}_{tag}"
        names.append(name)

    # If no physical groups, fall back to surface entities
    if not names:
        entities = gmsh.model.getEntities(2)
        names = [f"surface_{tag}" for _, tag in entities]
    return names


def compute_sizing(
    filepath: Path | str,
    detail: str = "medium",
    user_lc: float | None = None,
) -> GmshSizing:
    """Open a STEP/STP file in GMSH and compute curvature + proximity.

    Args:
        filepath: Path to STEP file.
        detail: 'coarse' | 'medium' | 'fine' — controls how aggressively
            small features are resolved.
        user_lc: Optional override for the global element size.

    Returns:
        GmshSizing with min curvature radius, min proximity, and
        suggested per-patch surface/volume sizes.
    """
    gmsh = _ensure_gmsh()
    gmsh.clear()
    filepath_str = str(Path(filepath).resolve())
    gmsh.open(filepath_str)

    # Get bounding box
    bbox = gmsh.model.getBoundingBox(-1, -1)
    dx, dy, dz = bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]
    max_extent = max(dx, dy, dz)

    # Default element size
    lc_user = user_lc or max_extent * 0.02

    # Detail multipliers
    df = _GMSH_DETAIL.get(detail, _GMSH_DETAIL["medium"])

    # Set mesh options for optimal surface extraction
    gmsh.option.setNumber("Mesh.CharacteristicLengthMin", lc_user * 0.01)
    gmsh.option.setNumber("Mesh.CharacteristicLengthMax", lc_user * 2)
    gmsh.option.setNumber("Mesh.CharacteristicLengthFromPoints", 0)
    gmsh.option.setNumber("Mesh.CharacteristicLengthFromCurvature", 1)
    gmsh.option.setNumber("Mesh.CharacteristicLengthExtendFromBoundary", 1)
    gmsh.option.setNumber("Mesh.MinimumCirclePoints", df["curv_angle"])

    # Compute min element size from curvature
    gmsh.option.setNumber("Mesh.CharacteristicLengthFromCurvature", 1)
    gmsh.option.setNumber("Mesh.MinimumElementsPerTwoPi", int(360 / df["curv_angle"]))

    # Get physical groups (patch names)
    groups = gmsh.model.getPhysicalGroups()
    physical_names: dict[int, str] = {}
    for dim, tag in groups:
        name = gmsh.model.getPhysicalName(dim, tag)
        if not name:
            name = f"surface_{dim}_{tag}"
        physical_names[tag] = name

    # Compute per-surface min curvature radius (approximation)
    from math import sqrt
    patch_sizes: dict[str, float] = {}
    min_curvature = max_extent
    min_proximity = max_extent

    surfaces = gmsh.model.getEntities(2)
    for _, tag in surfaces:
        # Get bounding box of this surface
        sbox = gmsh.model.getBoundingBox(2, tag)
        sx, sy, sz = sbox[3] - sbox[0], sbox[4] - sbox[1], sbox[5] - sbox[2]
        surf_diag = sqrt(sx*sx + sy*sy + sz*sz)

        # Approximate curvature from surface diagonal
        # (small diagonal = likely curved; large diagonal = likely flat)
        curv_est = max(0.001, surf_diag * df["min_mult"])
        if curv_est < min_curvature:
            min_curvature = curv_est

        # Surface element size
        surf_size = max(curv_est, lc_user * 0.1)
        name = physical_names.get(tag, f"surface_{tag}")
        patch_sizes[name] = round(surf_size, 6)

    # Volume element size (slightly larger than surface -> smooth growth)
    vol_size = max(min_curvature * 2.0, lc_user * 0.5)

    return GmshSizing(
        min_curvature_radius=round(min_curvature, 6),
        min_proximity=round(min_proximity, 6),
        max_extent=round(max_extent, 6),
        suggested_surface_size=round(min_curvature, 6),
        suggested_volume_size=round(max(vol_size, lc_user * 0.5), 6),
        patch_sizes=patch_sizes,
        n_physical_groups=len(groups),
    )


def generate_surface_stl(
    filepath: Path | str,
    output_stl: Path | str,
    detail: str = "medium",
    user_lc: float | None = None,
) -> list[str]:
    """Generate a curvature-adapted surface STL using GMSH.

    This is the *hybrid* flow:
      1. GMSH opens CAD (STEP)
      2. Computes curvature-aware sizing field
      3. Generates 2D surface mesh (triangles)
      4. Exports as ASCII STL → can be read by cfMesh for volume fill

    Returns:
        List of patch names found in the STL (suitable for inlet/outlet/wall mapping).
    """
    gmsh = _ensure_gmsh()
    gmsh.clear()
    filepath_str = str(Path(filepath).resolve())
    output_stl = str(Path(output_stl).resolve())
    gmsh.open(filepath_str)

    try:
        bbox = gmsh.model.getBoundingBox(-1, -1)
        dx = bbox[3] - bbox[0]
        lc_user = user_lc or max(abs(dx), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2])) * 0.02
    except Exception as e:
        raise RuntimeError(f"Failed to read geometry bounding box: {e}") from e

    # Detail settings
    df = _GMSH_DETAIL.get(detail, _GMSH_DETAIL["medium"])

    gmsh.option.setNumber("Mesh.CharacteristicLengthMin", lc_user * 0.01)
    gmsh.option.setNumber("Mesh.CharacteristicLengthMax", lc_user * df["max_mult"])
    gmsh.option.setNumber("Mesh.CharacteristicLengthFromCurvature", 1)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 1)
    gmsh.option.setNumber("Mesh.MinimumCirclePoints", df["curv_angle"])
    gmsh.option.setNumber("Mesh.MinimumElementsPerTwoPi", int(360 / df["curv_angle"]))
    gmsh.option.setNumber("Mesh.Algorithm3D", 1)  # Delaunay
    gmsh.option.setNumber("Mesh.Algorithm", 6)  # Frontal (good for surfaces)

    # Generate 2D mesh (surface)
    try:
        gmsh.model.mesh.generate(2)
    except Exception as e:
        raise RuntimeError(
            f"GMSH surface meshing failed: {e}. "
            "Try reducing max cell size or repairing the CAD geometry."
        ) from e

    # Get physical groups for patch names
    groups = gmsh.model.getPhysicalGroups()
    names: list[str] = []
    for dim, tag in groups:
        name = gmsh.model.getPhysicalName(dim, tag)
        if not name:
            name = f"surface_{dim}_{tag}"
        names.append(name)

    # If no physical groups, create named groups from surface entities
    if not names:
        entities = gmsh.model.getEntities(2)
        for dim, tag in entities:
            gmsh.model.addPhysicalGroup(2, [tag], tag)
            gmsh.model.setPhysicalName(2, tag, f"surface_{tag}")
            names.append(f"surface_{tag}")

    # Write STL (ASCII, multi-solid/named)
    stl_path = Path(output_stl)
    stl_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        gmsh.write(str(stl_path))
    except Exception as e:
        raise RuntimeError(f"GMSH STL export failed: {e}") from e

    # Verify STL output
    if not stl_path.exists() or stl_path.stat().st_size == 0:
        raise RuntimeError(
            f"GMSH STL export produced empty or missing file: {stl_path}. "
            "Check disk space and write permissions."
        )

    actual_min = lc_user * 0.01
    actual_max = lc_user * df["max_mult"]
    logger.info(
        "GMSH surface: %s  detail=%s curvAngle=%d "
        "minLC=%.6f maxLC=%.6f patches=%s",
        stl_path, detail, df["curv_angle"],
        actual_min, actual_max, names,
    )
    return names


def generate_volume_mesh(
    filepath: Path | str,
    output_msh: Path | str,
    detail: str = "medium",
    user_lc: float | None = None,
    n_layers: int = 0,
    bl_thickness: float | None = None,
    bl_expansion: float = 1.2,
) -> tuple[Path, list[str]]:
    """Generate a full tetrahedral volume mesh using GMSH (direct flow).

    This is the *direct* flow — no cfMesh needed. The mesh includes
    boundary layers if requested.

    Steps:
      1. Open CAD (STEP)
      2. Compute size field
      3. Generate 3D tetrahedral mesh with optional BL
      4. Export as MSH (.msh)
      5. Convert to OpenFOAM polyMesh via meshio

    Returns:
        (path_to_msh, list_of_patch_names)
    """
    gmsh = _ensure_gmsh()
    gmsh.clear()
    filepath_str = str(Path(filepath).resolve())
    output_msh = str(Path(output_msh).resolve())
    gmsh.open(filepath_str)

    if filepath_str.lower().endswith(".stl"):
        # A raw STL has no B-Rep topology — gmsh.open() only imports the
        # discrete surface triangles, so mesh.generate(3) below has no
        # volume to fill and silently produces 0 tetrahedra (surface
        # triangles pass through untouched). Reconstruct a real volume:
        # classify the discrete triangles into surface patches by feature
        # angle, give them a parametrization, then close a surface loop
        # into a volume — the standard GMSH STL-to-volume workflow (see
        # GMSH tutorial t13).
        gmsh.model.mesh.classifySurfaces(
            angle=40 * math.pi / 180,
            boundary=True,
            forReparametrization=False,
            curveAngle=180 * math.pi / 180,
        )
        gmsh.model.mesh.createGeometry()
        surfaces = gmsh.model.getEntities(2)
        if not surfaces:
            raise RuntimeError(
                f"GMSH found no surfaces after reconstructing STL topology: {filepath_str}"
            )
        surface_loop = gmsh.model.geo.addSurfaceLoop([tag for _, tag in surfaces])
        gmsh.model.geo.addVolume([surface_loop])
        gmsh.model.geo.synchronize()

    try:
        bbox = gmsh.model.getBoundingBox(-1, -1)
    except Exception as e:
        raise RuntimeError(f"Failed to read geometry bounding box: {e}") from e
    lc_user = user_lc or max(
        abs(bbox[3] - bbox[0]), abs(bbox[4] - bbox[1]), abs(bbox[5] - bbox[2])
    ) * 0.02

    df = _GMSH_DETAIL.get(detail, _GMSH_DETAIL["medium"])

    gmsh.option.setNumber("Mesh.CharacteristicLengthMin", lc_user * df["min_mult"])
    gmsh.option.setNumber("Mesh.CharacteristicLengthMax", lc_user * df["max_mult"])
    gmsh.option.setNumber("Mesh.CharacteristicLengthFromCurvature", 1)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 1)
    gmsh.option.setNumber("Mesh.MinimumCirclePoints", int(360 / df["curv_angle"]))
    gmsh.option.setNumber("Mesh.MinimumElementsPerTwoPi", int(360 / df["curv_angle"]))
    # HXT (10), not classic Delaunay (1) — Delaunay fails with "Invalid
    # boundary mesh (overlapping facets)" on the discrete/reparametrized
    # surfaces produced by the STL-reconstruction step above (long curved
    # patches parametrize badly); HXT meshes directly off the discrete
    # boundary triangulation and doesn't hit this.
    gmsh.option.setNumber("Mesh.Algorithm3D", 10)  # HXT
    gmsh.option.setNumber("Mesh.Algorithm", 6)  # Frontal
    gmsh.option.setNumber("Mesh.Optimize", 1)
    gmsh.option.setNumber("Mesh.OptimizeNetgen", 1)

    # Boundary layers
    if n_layers > 0 and bl_thickness is not None:
        surfaces = gmsh.model.getEntities(2)
        surface_tags = [tag for _, tag in surfaces]
        if surface_tags:
            gmsh.model.mesh.field.add("BoundaryLayer", 1)
            gmsh.model.mesh.field.setNumbers(1, "CurvesList", [])
            gmsh.model.mesh.field.setNumbers(1, "PointsList", [])
            gmsh.model.mesh.field.setNumber(1, "hwall_n", bl_thickness)
            gmsh.model.mesh.field.setNumber(1, "thickness", bl_thickness * n_layers)
            gmsh.model.mesh.field.setNumber(1, "ratio", bl_expansion)
            gmsh.model.mesh.field.setNumber(1, "Quads", 0)
            # A "BoundaryLayer" field only takes effect once activated via
            # setAsBoundaryLayer() — without this call the field is fully
            # configured but silently never applied, so BL settings the
            # user enables in the UI (n_layers/thickness/expansion) have
            # no effect on the generated volume mesh.
            gmsh.model.mesh.field.setAsBoundaryLayer(1)

    # Generate 3D mesh
    try:
        gmsh.model.mesh.generate(3)
    except Exception as e:
        raise RuntimeError(
            f"GMSH volume meshing failed: {e}. "
            "Try reducing max cell size or simplifying the CAD geometry."
        ) from e

    # Get patch names
    groups = gmsh.model.getPhysicalGroups()
    names: list[str] = []
    for dim, tag in groups:
        name = gmsh.model.getPhysicalName(dim, tag)
        if not name:
            name = f"surface_{dim}_{tag}"
        names.append(name)
    if not names:
        for dim, tag in gmsh.model.getEntities(2):
            gmsh.model.addPhysicalGroup(2, [tag], tag)
            gmsh.model.setPhysicalName(2, tag, f"surface_{tag}")
            names.append(f"surface_{tag}")

    msh_path = Path(output_msh)
    msh_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        gmsh.write(str(msh_path))
    except Exception as e:
        raise RuntimeError(f"GMSH MSH export failed: {e}") from e

    if not msh_path.exists() or msh_path.stat().st_size == 0:
        raise RuntimeError(
            f"GMSH volume export produced empty or missing file: {msh_path}"
        )

    logger.info(
        "GMSH volume: %s  detail=%s nLayers=%d thk=%s patches=%s",
        msh_path, detail, n_layers, bl_thickness, names,
    )
    return msh_path, names


if __name__ == "__main__":
    import sys, json
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "surface":
        geom_path = sys.argv[2]
        stl_out = Path(sys.argv[3])
        detail = sys.argv[4] if len(sys.argv) > 4 else "medium"
        stl_out.parent.mkdir(parents=True, exist_ok=True)
        try:
            names = generate_surface_stl(geom_path, stl_out, detail=detail)
            sizing = compute_sizing(geom_path, detail=detail)
            print(json.dumps({
                "success": True,
                "names": names,
                "suggested_volume_size": sizing.suggested_volume_size,
                "suggested_surface_size": sizing.suggested_surface_size,
                "min_curvature_radius": sizing.min_curvature_radius,
                "patch_sizes": sizing.patch_sizes,
            }))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))
            sys.exit(1)
    elif cmd == "volume":
        step_path = sys.argv[2]
        msh_path = Path(sys.argv[3])
        detail = sys.argv[4] if len(sys.argv) > 4 else "medium"
        n_layers = int(sys.argv[5]) if len(sys.argv) > 5 else 0
        bl_thickness = float(sys.argv[6]) if len(sys.argv) > 6 else None
        bl_expansion = float(sys.argv[7]) if len(sys.argv) > 7 else 1.2
        msh_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result_path, names = generate_volume_mesh(
                step_path, msh_path, detail=detail,
                n_layers=n_layers, bl_thickness=bl_thickness,
                bl_expansion=bl_expansion,
            )
            print(json.dumps({"success": True, "path": str(result_path), "names": names}))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))
            sys.exit(1)
