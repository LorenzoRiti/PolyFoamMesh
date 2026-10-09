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

_EXCLUDED_TOP_LEVEL_DIRS = {"VTK_view", "postProcessing"}
_EXCLUDED_TOP_LEVEL_FILES = {"mesh.msh", "local.cfg"}
_EXCLUDED_CONSTANT_DIRS = {"polyMesh_tet_backup"}

#: Extra exclusions for the BaramFlow export: BaramFlow writes its OWN
#: decomposeParDict and run markers when it configures a run, so stale
#: copies make it fail (classic "processor0 already exists" / np mismatch
#: -> solver starts but cannot write results, user report 2026-10-09).
_EXCLUDED_SYSTEM_FILES_BARAM = {"decomposeParDict"}


def case_copy_ignore(case_dir: Path, *, for_baramflow: bool = False):
    """``shutil.copytree`` ignore: strip app artifacts and stale run data.

    Shared by the direct OpenFOAM export and the BaramFlow export.  Leaves
    out everything regenerable or run-specific: decomposePar output
    (``processor*``), the viewer's ``VTK_view``, the GMSH ``.msh``, the
    pre-BL tet backup, ``postProcessing``, previous results (numeric time
    directories), and run markers (``local.cfg``, ``*.foam``).  For
    BaramFlow it also drops ``system/decomposeParDict``.
    """
    case_dir = Path(case_dir).resolve()

    def _is_numeric(name: str) -> bool:
        # NOTE: "0" is the initial-fields directory, NOT a result: never
        # treat it as a stale time directory (it must ship with the case).
        if name == "0":
            return False
        head = name.split(".", 1)[0]
        try:
            float(head)
        except ValueError:
            return False
        return True

    def _ignore(src, names):
        src_p = Path(src)
        if src_p == case_dir:
            return {
                n for n in names
                if n in _EXCLUDED_TOP_LEVEL_DIRS
                or n in _EXCLUDED_TOP_LEVEL_FILES
                or n.startswith("processor")
                or _is_numeric(n)
                or n.endswith(".foam")
            }
        if src_p == case_dir / "constant":
            return {n for n in names if n in _EXCLUDED_CONSTANT_DIRS}
        if for_baramflow and src_p == case_dir / "system":
            return {n for n in names if n in _EXCLUDED_SYSTEM_FILES_BARAM}
        return set()

    return _ignore


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

    shutil.copytree(
        case_dir, dest, dirs_exist_ok=True,
        ignore=case_copy_ignore(case_dir),
    )
    logger.info("Exported OpenFOAM case to %s", dest)
    return dest
