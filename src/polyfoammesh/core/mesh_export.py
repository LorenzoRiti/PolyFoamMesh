"""Export a meshed case to formats other CFD/pre/post tools read.

The GUI used to call `meshio.read(polyMesh_dir, file_format="openfoam")` for
CGNS/VTU export. That reader does not exist in meshio 5.3.5 (verified: it
raises `ReadError: Unknown file format 'openfoam'` on every call) — export was
completely broken regardless of format. Separately, even where meshio CAN read
a mesh, cfMesh's octree produces polyhedral cells at refinement transitions,
and meshio's own VTU reader refuses mixed polyhedra + standard cells
(`ValueError: Cannot handle combinations of polyhedra with other cells`) — so
naively routing through meshio fails on realistic geometry, not just simple ones.

The reliable path, verified against a real mesh with mixed hexahedron +
polyhedron cells:
  1. `foamToVTK` (OpenFOAM's own tool) writes the volume mesh as VTU — this
     handles every OpenFOAM/cfMesh cell type correctly, unlike meshio.
  2. PyVista (built on VTK, not meshio) reads that VTU natively, polyhedra
     included.
  3. For VTU output, that's already the answer — no conversion needed.
  4. For any other format, `.triangulate()` decomposes every cell (polyhedra
     included) into plain tetrahedra, which every downstream writer supports.
     Re-saved as VTU, meshio can then read it (single cell type) and write
     the target format.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
import tempfile
from pathlib import Path

from polyfoammesh.config import OFConfig

logger = logging.getLogger(__name__)

__all__ = ["EXPORT_FORMATS", "export_mesh"]

# fmt key -> (extension, meshio file_format, human description)
# meshio file_format=None means "no conversion" (native VTU write).
EXPORT_FORMATS: dict[str, tuple[str, str | None, str]] = {
    "vtu": (".vtu", None, "VTU — ParaView unstructured grid"),
    "cgns": (".cgns", "cgns", "CGNS — CFD General Notation System"),
    "su2": (".su2", "su2", "SU2 mesh format"),
    "gmsh": (".msh", "gmsh", "GMSH .msh v4.1"),
    "abaqus": (".inp", "abaqus", "Abaqus .inp"),
}

_FOAM_TO_VTK_TIMEOUT_S = 120


def _build_internal_vtu(case_dir: Path, of_config: OFConfig | None = None) -> Path:
    """Run foamToVTK and return the path to constant/internal.vtu.

    Raises RuntimeError with the underlying OpenFOAM error on failure, so
    callers can show the user something more useful than "export failed".
    """
    cfg = of_config or OFConfig()
    out_dir = case_dir / "VTK_export"
    cmd = cfg._build_wsl_cmd(
        f"source {shlex.quote(cfg.env_script)} 2>/dev/null; "
        f"cd {cfg._quoted_linux_path(case_dir)} && "
        f"foamToVTK -constant -noZero -no-fields -overwrite -name VTK_export"
    )
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_FOAM_TO_VTK_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"foamToVTK timed out after {_FOAM_TO_VTK_TIMEOUT_S}s"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError("WSL not found — cannot run foamToVTK") from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"foamToVTK failed: {(result.stderr or result.stdout)[-400:]}"
        )

    matches = list(out_dir.glob("*_0/internal.vtu"))
    if not matches:
        raise RuntimeError(f"foamToVTK produced no internal.vtu under {out_dir}")
    return matches[0]


def export_mesh(
    case_dir: Path | str,
    fmt: str,
    output_path: Path | str,
    of_config: OFConfig | None = None,
) -> Path:
    """Export the mesh in *case_dir* to *fmt*, writing to *output_path*.

    Args:
        case_dir: OpenFOAM case with a completed constant/polyMesh.
        fmt: one of EXPORT_FORMATS' keys.
        output_path: destination file path.

    Returns:
        output_path, on success.

    Raises:
        ValueError: unknown format.
        RuntimeError: foamToVTK failed, or the mesh could not be converted.
    """
    if fmt not in EXPORT_FORMATS:
        raise ValueError(
            f"Unknown export format '{fmt}'. Supported: {', '.join(EXPORT_FORMATS)}"
        )
    case_dir = Path(case_dir)
    output_path = Path(output_path)
    _ext, meshio_format, _desc = EXPORT_FORMATS[fmt]

    vtu_path = _build_internal_vtu(case_dir, of_config)

    import pyvista as pv

    grid = pv.read(str(vtu_path))
    if grid.n_cells == 0:
        raise RuntimeError("Exported mesh has zero cells — nothing to write.")

    if meshio_format is None:  # native VTU
        output_path.parent.mkdir(parents=True, exist_ok=True)
        grid.save(str(output_path))
        return output_path

    # Other formats don't support cfMesh's polyhedral cells (verified: meshio's
    # own VTU reader raises on mixed polyhedra + standard cells) — decompose to
    # tetrahedra first, which every writer below accepts.
    tri = grid.triangulate()

    import meshio

    with tempfile.TemporaryDirectory() as tmp:
        tmp_vtu = Path(tmp) / "triangulated.vtu"
        tri.save(str(tmp_vtu))
        mesh = meshio.read(str(tmp_vtu))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        meshio.write(str(output_path), mesh, file_format=meshio_format)

    logger.info(
        "Exported %s -> %s (%d cells, triangulated from %d)",
        fmt, output_path, tri.n_cells, grid.n_cells,
    )
    return output_path
