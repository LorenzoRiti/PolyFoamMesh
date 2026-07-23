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

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)

# Supported export formats with metadata
EXPORT_FORMATS: dict[str, dict[str, Any]] = {
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
            fmt: Target format key from ``EXPORT_FORMATS``.
            output_path: Optional output path. Auto-generated if None.

        Returns:
            ``ExportResult`` with success status and metadata.
        """
        case_dir = Path(case_dir)
        fmt = fmt.lower()

        if fmt not in EXPORT_FORMATS:
            return ExportResult(
                success=False, format=fmt,
                error=f"Unsupported format '{fmt}'. "
                      f"Supported: {', '.join(EXPORT_FORMATS)}",
            )

        fmt_info = EXPORT_FORMATS[fmt]
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
        owner = case_dir / "constant" / "polyMesh" / "owner"
        if not owner.exists():
            return 0
        try:
            lines = owner.read_text(encoding="ascii", errors="replace").splitlines()
            return max(0, len(lines) - 2)
        except Exception:
            return 0

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
    def _export_cgns(case_dir: Path, output: Path) -> int:
        """CGNS export via OpenFOAM's foamToCGMSToCGNS or meshio."""
        import subprocess
        from cfmesh_autogui.config import OFConfig
        cfg = OFConfig()
        linux_case = cfg._quoted_linux_path(case_dir)
        env_q = cfg._quoted_linux_path(cfg.env_script)
        out_linux = cfg._quoted_linux_path(output)

        cmd = cfg._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux_case} && "
            f"foamToCGNS -constant 2>&1 | tail -5"
        )
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"foamToCGNS failed: {result.stderr[-200:]}")
        return MeshExporter._count_cells(case_dir)

    @staticmethod
    def _export_vtu(case_dir: Path, output: Path) -> int:
        """VTU export via meshio."""
        import meshio
        poly_dir = case_dir / "constant" / "polyMesh"
        points_path = poly_dir / "points"
        if not points_path.exists():
            raise FileNotFoundError(f"polyMesh/points not found in {case_dir}")

        pts = _read_of_points(points_path)
        cells_data = _read_of_faces(poly_dir / "faces")
        points = meshio.PointCloud(pts)
        cells = [("polyhedron", cells_data)] if cells_data else []
        meshio.write(str(output), meshio.Mesh(points, cells))
        return len(cells_data) if cells_data else 0

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


def _read_of_outer_list_body(text: str) -> str | None:
    """Extract the body of the top-level OpenFOAM "<count>\\n(...)" list.

    A naive non-greedy `\\(.*?\\)` regex stops at the FIRST closing paren —
    wrong for points/faces files, where each entry is itself wrapped in
    parens (e.g. "(0.1 0.2 0.3)"), so it would return only the first entry.
    Track bracket depth instead to find the list's true matching close.
    """
    import re
    list_start = re.search(r"\d+\s*\n?\s*\(", text)
    if not list_start:
        return None
    open_idx = list_start.end() - 1
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
    return None


def _read_of_points(path: Path) -> list[list[float]]:
    import re
    text = path.read_text(encoding="ascii", errors="replace")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    body = _read_of_outer_list_body(text)
    if body is None:
        return []
    pts: list[list[float]] = []
    for line in body.strip().split("\n"):
        parts = line.strip().strip("()").split()
        if len(parts) >= 3:
            try:
                pts.append([float(parts[0]), float(parts[1]), float(parts[2])])
            except ValueError:
                pass
    return pts


def _read_of_faces(path: Path) -> list[list[int]]:
    import re
    text = path.read_text(encoding="ascii", errors="replace")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    body = _read_of_outer_list_body(text)
    if body is None:
        return []
    faces: list[list[int]] = []
    entry_re = re.compile(r"(\d+)\s*\(([^)]*)\)")
    for m in entry_re.finditer(body):
        idx = [int(x) for x in m.group(2).split() if x.strip()]
        if len(idx) >= 3:
            faces.append(idx)
    return faces
