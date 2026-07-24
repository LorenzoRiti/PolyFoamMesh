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

# Dihedral angle above which an edge counts as a feature. 30 deg is the common
# default across meshers (snappyHexMesh's surfaceFeatureExtract uses it too):
# low enough to catch chamfers, high enough not to fire on tessellated curves.
DEFAULT_FEATURE_ANGLE = 30.0

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

    logger.info(
        "Feature edges extracted at %.0f deg: %s (%d bytes)",
        angle_deg, fms_path.name, fms_path.stat().st_size,
    )
    return fms_path
