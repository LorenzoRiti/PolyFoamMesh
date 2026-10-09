"""Export the case as a clean, self-contained OpenFOAM case folder.

Unlike the BaramFlow export this does NOT require 0/ fields or validate
patch/field consistency: it ships what exists (mesh + system + fields if
present) so the case can be taken to any OpenFOAM install (WSL, Linux)
and run directly.  App-generated artifacts are left out because they are
large and regenerable: the decomposePar output (processor*), the viewer's
VTK_view, the GMSH .msh and the pre-BL tet backup.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

REQUIRED_POLYMESH = ("points", "faces", "owner", "neighbour", "boundary")

_EXCLUDED_TOP_LEVEL_DIRS = {"VTK_view"}
_EXCLUDED_TOP_LEVEL_FILES = {"mesh.msh"}
_EXCLUDED_CONSTANT_DIRS = {"polyMesh_tet_backup"}


def export_openfoam_case(case_dir: Path | str, dest_parent: Path | str) -> Path:
    """Copy the case into *dest_parent*/<case name> as a runnable OF case.

    Raises ValueError when there is no mesh to export or the destination
    collides with the source.  Never raises for missing 0/ fields: a
    mesh-only export is valid for OpenFOAM.
    """
    case_dir = Path(case_dir).resolve()
    poly = case_dir / "constant" / "polyMesh"
    missing = [n for n in REQUIRED_POLYMESH if not (poly / n).is_file()]
    if missing:
        raise ValueError(
            "No OpenFOAM mesh to export (constant/polyMesh/ is missing "
            + ", ".join(missing)
            + "). Generate the mesh first."
        )

    dest = Path(dest_parent).resolve() / case_dir.name
    if dest == case_dir:
        raise ValueError("Destination must differ from the source case directory.")

    def _ignore(src, names):
        src_p = Path(src)
        if src_p == case_dir:
            return {
                n for n in names
                if n in _EXCLUDED_TOP_LEVEL_DIRS
                or n in _EXCLUDED_TOP_LEVEL_FILES
                or n.startswith("processor")
            }
        if src_p == case_dir / "constant":
            return {n for n in names if n in _EXCLUDED_CONSTANT_DIRS}
        return set()

    shutil.copytree(case_dir, dest, dirs_exist_ok=True, ignore=_ignore)
    logger.info("Exported OpenFOAM case to %s", dest)
    return dest
