"""Mesh Conversion & Export Factory — output formats for every major CFD solver.

Supported formats:
  - OpenFOAM (native polyMesh)
  - CGNS (.cgns)
  - VTU/VTM (ParaView)
  - ANSYS Fluent (.cas/.msh)
  - STAR-CCM+ (.ccm)
  - Abaqus (.inp)
  - SU2 (.su2)
  - GMSH (.msh)
  - TetGen (.poly)
  - STL (surface)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)

# Supported export formats with metadata
# NOTE: named EXPORT_FORMAT_REGISTRY to avoid collision with
# core.mesh_export.EXPORT_FORMATS (different structure: dict of tuples).
EXPORT_FORMAT_REGISTRY: dict[str, dict[str, Any]] = {
    "openfoam": {"ext": "", "desc": "OpenFOAM polyMesh (native)", "requires_wsl": False},
    "cgns": {"ext": ".cgns", "desc": "CGNS — CFD General Notation System", "requires_wsl": True},
    "vtu": {"ext": ".vtu", "desc": "VTU — ParaView unstructured grid", "requires_wsl": False},
    "vtm": {"ext": ".vtm", "desc": "VTM — ParaView multi-block", "requires_wsl": False},
    "fluent_msh": {"ext": ".msh", "desc": "Fluent .msh (via meshio)", "requires_wsl": False},
    "abaqus_inp": {"ext": ".inp", "desc": "Abaqus .inp (via meshio)", "requires_wsl": False},
    "su2": {"ext": ".su2", "desc": "SU2 mesh format", "requires_wsl": False},
    "gmsh_msh": {"ext": ".msh", "desc": "GMSH .msh v4.1", "requires_wsl": False},
    "stl": {"ext": ".stl", "desc": "STL surface triangulation", "requires_wsl": False},
}

# Backward compatibility alias
EXPORT_FORMATS = EXPORT_FORMAT_REGISTRY


@dataclass
class ExportResult:
    success: bool = False
    format: str = ""
    output_path: str = ""
    cell_count: int = 0
    file_size_bytes: int = 0
    wall_time_s: float = 0.0
    error: str = ""


class MeshExporter:
    """Multi-format mesh export factory.

    Usage::

        exporter = MeshExporter()
        result = exporter.export(case_dir, fmt="cgns", output_path="mesh.cgns")
        print(f"Exported: {result.file_size_bytes} bytes")
    """

    def export(
        self, case_dir: Path | str, fmt: str,
        output_path: Path | str | None = None,
    ) -> ExportResult:
        """Export the mesh at *case_dir* to the specified format.

        Args:
            case_dir: OpenFOAM case directory with constant/polyMesh.
            fmt: Target format key from ``EXPORT_FORMAT_REGISTRY``.
            output_path: Optional output path. Auto-generated if None.

        Returns:
            ``ExportResult`` with success status and metadata.
        """
        case_dir = Path(case_dir)
        fmt = fmt.lower()

        if fmt not in EXPORT_FORMAT_REGISTRY:
            return ExportResult(
                success=False, format=fmt,
                error=f"Unsupported format '{fmt}'. "
                      f"Supported: {', '.join(EXPORT_FORMAT_REGISTRY)}",
            )

        fmt_info = EXPORT_FORMAT_REGISTRY[fmt]
        ext = fmt_info["ext"]
        start = datetime.now()

        if output_path is None:
            output_path = case_dir / f"mesh{ext}"

        output_path = Path(output_path)
        octo.log_event("exporter", "export_start", {
            "case_dir": str(case_dir),
            "format": fmt,
            "output": str(output_path),
        })

        try:
            exporter_fn = getattr(self, f"_export_{fmt}", None)
            if exporter_fn is None:
                return ExportResult(
                    success=False, format=fmt,
                    error=f"Export handler not implemented for '{fmt}'",
                )

            cell_count = exporter_fn(case_dir, output_path)
            file_size = output_path.stat().st_size if output_path.exists() else 0

            result = ExportResult(
                success=True,
                format=fmt,
                output_path=str(output_path.resolve()),
                cell_count=cell_count,
                file_size_bytes=file_size,
                wall_time_s=round((datetime.now() - start).total_seconds(), 2),
            )

            octo.log_event("exporter", "export_ok", {
                "format": fmt, "bytes": file_size, "cells": cell_count,
            })
            return result

        except Exception as exc:
            logger.exception("Export to %s failed", fmt)
            return ExportResult(
                success=False, format=fmt,
                error=str(exc),
                wall_time_s=round((datetime.now() - start).total_seconds(), 2),
            )

    # ------------------------------------------------------------------
    # Individual format handlers
    # ------------------------------------------------------------------
    @staticmethod
    def _count_cells(case_dir: Path) -> int:
        """Count cells from the polyMesh/owner file."""
        from cfmesh_autogui.core.boundary_reader import count_cells
        return count_cells(case_dir)

    @staticmethod
    def _export_openfoam(case_dir: Path, output: Path) -> int:
        """OpenFOAM — mesh is already in place; just validate."""
        poly_dir = case_dir / "constant" / "polyMesh"
        required = ["points", "faces", "owner", "boundary"]
        missing = [f for f in required if not (poly_dir / f).exists()]
        if missing:
            raise FileNotFoundError(
                f"OpenFOAM polyMesh incomplete — missing: {', '.join(missing)}"
            )
        # Create a marker file to indicate readiness
        output.write_text(f"OpenFOAM mesh ready at {poly_dir}\n")
        return MeshExporter._count_cells(case_dir)

    @staticmethod
    def _export_via_core(case_dir: Path, output: Path, core_fmt: str) -> int:
        """Delegate to core.mesh_export, the verified export path.

        This module used to hand-parse polyMesh/faces and hand it to meshio
        as if faces WERE cells — faces list point connectivity, not cell
        connectivity, so that produced the wrong topology entirely (and,
        separately, called a nonexistent `foamToCGNS` OpenFOAM utility for
        CGNS). core.mesh_export.export_mesh() is the actual working path,
        already used by the GUI's Export Mesh menu: foamToVTK -> PyVista
        (handles cfMesh's polyhedral cells, unlike meshio) -> triangulate ->
        meshio for the target format.
        """
        from cfmesh_autogui.core.mesh_export import export_mesh
        export_mesh(case_dir, core_fmt, output)
        return MeshExporter._count_cells(case_dir)

    @staticmethod
    def _export_cgns(case_dir: Path, output: Path) -> int:
        return MeshExporter._export_via_core(case_dir, output, "cgns")

    @staticmethod
    def _export_vtu(case_dir: Path, output: Path) -> int:
        return MeshExporter._export_via_core(case_dir, output, "vtu")

    @staticmethod
    def _export_su2(case_dir: Path, output: Path) -> int:
        return MeshExporter._export_via_core(case_dir, output, "su2")

    @staticmethod
    def _export_gmsh_msh(case_dir: Path, output: Path) -> int:
        return MeshExporter._export_via_core(case_dir, output, "gmsh")

    @staticmethod
    def _export_abaqus_inp(case_dir: Path, output: Path) -> int:
        return MeshExporter._export_via_core(case_dir, output, "abaqus")

    @staticmethod
    def _export_stl(case_dir: Path, output: Path) -> int:
        """STL surface export."""
        from cfmesh_autogui.core.geometry import load_geometry
        from cfmesh_autogui.core.stl_writer import export_multisolid_stl

        # Look for STL in constant/triSurface
        stl_path = case_dir / "constant" / "triSurface" / "surface.stl"
        if stl_path.exists():
            meshes = load_geometry(stl_path)
            export_multisolid_stl(meshes, output)
            return sum(len(m.faces) for m in meshes)
        raise FileNotFoundError(f"Surface STL not found: {stl_path}")
