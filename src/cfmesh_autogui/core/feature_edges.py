"""Feature-edge extraction into cfMesh's native FMS surface format.

cfMesh's octree rounds off sharp edges unless it is told where they are. Its own
`surfaceFeatureEdges` utility finds edges whose dihedral angle exceeds a
threshold and writes an FMS file carrying both the triangulation and those
feature edges; handing that FMS to `cartesianMesh` as the `surfaceFile` is what
makes corners come out crisp instead of melted.

The utility ships with OpenFOAM (verified present in this environment), so this
is a thin, well-defined wrapper rather than a reimplementation.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

from cfmesh_autogui.config import OFConfig

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_FEATURE_ANGLE", "extract_feature_edges"]

# Dihedral angle above which an edge counts as a feature. 30 deg was the old default
# but it fires on tessellated curves (a cylinder with 12 segments has 30° facet edges),
# producing a massive FMS that makes cartesianMesh crash. 45° is safe: it still catches
# chamfers and sharp corners but ignores typical tessellation artifacts.
DEFAULT_FEATURE_ANGLE = 45.0

_TIMEOUT_S = 300


def extract_feature_edges(
    stl_path: Path | str,
    fms_path: Path | str | None = None,
    angle_deg: float = DEFAULT_FEATURE_ANGLE,
    of_config: OFConfig | None = None,
) -> Path | None:
    """Run `surfaceFeatureEdges` on *stl_path*, returning the FMS path.

    Returns None (and logs) if the utility is unavailable or fails — callers
    should fall back to meshing the plain STL rather than aborting, since a
    slightly rounded mesh beats no mesh at all.
    """
    stl_path = Path(stl_path)
    if not stl_path.is_file():
        logger.warning("Feature-edge extraction skipped: %s not found", stl_path)
        return None

    fms_path = Path(fms_path) if fms_path else stl_path.with_suffix(".fms")
    cfg = of_config or OFConfig()

    # Run from the surface's own directory and pass bare filenames: OpenFOAM
    # chokes on spaces anywhere in the paths it touches (including the inherited
    # working directory — "C:\Users\Davide Valoroso\..." made it abort with
    # `fileName::stripInvalid()`), and the case tree itself is space-free.
    cmd = cfg._build_wsl_cmd(
        f"source {cfg._quoted_linux_path(cfg.env_script)} 2>/dev/null; "
        f"cd {cfg._quoted_linux_path(stl_path.parent)} && "
        f"surfaceFeatureEdges -angle {angle_deg:g} "
        f"{shlex.quote(stl_path.name)} {shlex.quote(fms_path.name)}"
    )

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        logger.warning("surfaceFeatureEdges timed out after %ds", _TIMEOUT_S)
        return None
    except FileNotFoundError:
        logger.warning("surfaceFeatureEdges unavailable (WSL not found)")
        return None

    if result.returncode != 0:
        logger.warning(
            "surfaceFeatureEdges failed (rc=%d): %s",
            result.returncode, (result.stderr or result.stdout)[-300:],
        )
        return None

    if not fms_path.is_file() or fms_path.stat().st_size == 0:
        logger.warning("surfaceFeatureEdges produced no usable FMS at %s", fms_path)
        return None

    fms_size = fms_path.stat().st_size

    # Read FMS header to count feature vertices. FMS format:
    #   line 0: patch name
    #   line 1: N_vertices
    #   lines 2..N_vertices+1: vertex coords
    # If feature vertices > 80% of STL vertices, ALL edges are features
    # and the FMS will make cartesianMesh crash or produce a huge mesh.
    try:
        fms_text = fms_path.read_text(encoding="ascii", errors="replace")
        fms_lines = [l.strip() for l in fms_text.splitlines() if l.strip()]
        if len(fms_lines) >= 2:
            n_fms_verts = int(fms_lines[1])
            # Hard cap: more than MAX_FMS_FEATURE_VERTS feature vertices
            # means the FMS captured facet edges, not real features.
            if n_fms_verts > MAX_FMS_FEATURE_VERTS:
                logger.warning(
                    "FMS has %d feature vertices (>%d cap) — "
                    "too many features (tessellated curve). "
                    "Falling back to plain STL.",
                    n_fms_verts, MAX_FMS_FEATURE_VERTS,
                )
                return None
            # Relative check: compare to STL vertex count
            stl_verts = _count_stl_vertices(stl_path)
            if stl_verts > 0 and n_fms_verts > stl_verts * 0.8:
                logger.warning(
                    "FMS has %d feature vertices (%.0f%% of STL's %d) — "
                    "all edges detected as features (tessellated curve). "
                    "Falling back to plain STL.",
                    n_fms_verts, 100 * n_fms_verts / stl_verts, stl_verts,
                )
                return None
    except (OSError, ValueError, IndexError):
        pass

    logger.info(
        "Feature edges extracted at %.0f deg: %s (%d bytes)",
        angle_deg, fms_path.name, fms_size,
    )
    return fms_path


MAX_FMS_FEATURE_VERTS = 2000  # Hard cap: beyond this, FMS is too dense

def _count_stl_vertices(stl_path: Path) -> int:
    """Count vertices in an STL file (ASCII or binary) using trimesh."""
    if not stl_path.is_file():
        return 0
    try:
        import trimesh as _tm
        mesh = _tm.load(str(stl_path), force="mesh")
        return len(mesh.vertices) if hasattr(mesh, "vertices") else 0
    except Exception:
        return 0
